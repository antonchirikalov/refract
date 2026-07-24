"""Capability confirmation (SPEC §17 phase 3): a run pauses for a human to
approve a sensitive capability before the agent runs; approve → proceed.

Reuses the HITL waiting_human/answer machinery. MockRuntime only.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import yaml

from refract.cli import write_answer
from refract.events import EventWriter
from refract.graph import load_agents
from refract.models.agent import capability_tier, tier_at_least
from refract.models.ledger import NodeStatus, RunStatus
from refract.models.pipeline import Pipeline
from refract.registry import ArtifactRegistry
from refract.runtime.base import StepResult
from refract.scheduler import run_pipeline
from refract.state import Ledger

_TYPES = """
version: "0.1"
types:
  requirements@v1:
    kind: file
    format: markdown
    rules:
      - { rule: regex, pattern: "^# Requirements:", flags: "m" }
"""
DOC = "# Requirements: R\n- FR-1\n"


async def _no_sleep(_seconds: float) -> None:
    return None


def test_capability_tiers() -> None:
    assert capability_tier("read") == "safe"
    assert capability_tier("bash") == "dangerous"
    assert capability_tier("mcp:tavily-remote") == "moderate"
    assert tier_at_least("bash", "dangerous")
    assert not tier_at_least("read", "moderate")


def test_confirm_pauses_then_approval_proceeds(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    (lib / "types" / "schemas").mkdir(parents=True)
    (lib / "types" / "artifact_types.yaml").write_text(_TYPES, encoding="utf-8")
    d = lib / "agents" / "runner"
    d.mkdir(parents=True)
    (d / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "runner",
                "version": 1,
                "consumes": [],
                "produces": [{"port": "doc", "type": "requirements@v1"}],
                "needs": ["read", "edit", "bash"],
            }
        ),
        encoding="utf-8",
    )
    (d / "prompt.md").write_text("You are runner.", encoding="utf-8")
    agents, errs = load_agents(lib)
    assert errs == []
    reg = ArtifactRegistry.load(lib)
    pl = Pipeline.model_validate(
        {
            "version": "0.1",
            "name": "c",
            "nodes": [
                {"id": "run", "type": "agent", "agent": "runner@1", "params": {"model": "m/m"}}
            ],
        }
    )
    run_dir = tmp_path / "run"
    (run_dir / "snapshot" / "agents").mkdir(parents=True)
    shutil.copytree(lib / "agents" / "runner", run_dir / "snapshot" / "agents" / "runner@1")
    ledger = Ledger.create(
        run_dir, run_id="r", pipeline="c", node_ids=["run"], created_at="T0"
    )
    # a runtime that fails the test if invoked before approval
    calls = {"n": 0}

    class Guarded:
        async def run_step(self, spec, on_event):
            calls["n"] += 1
            (spec.workdir / "output").mkdir(parents=True, exist_ok=True)
            (spec.workdir / "output" / "doc.md").write_text(DOC, encoding="utf-8")
            (spec.workdir / "raw.txt").write_text("x", encoding="utf-8")
            (spec.workdir / "agent.events.jsonl").write_text("", encoding="utf-8")
            return StepResult(completed=True)

        async def close(self):
            return None

    def _run(led: Ledger, runtime: object) -> RunStatus:
        async def go():
            return await run_pipeline(
                run_dir,
                pipeline=pl,
                agents=agents,
                registry=reg,
                runtime=runtime,
                ledger=led,
                events=EventWriter(run_dir),
                confirm_capabilities={"bash"},
                clock=lambda: "T",
                sleeper=_no_sleep,
            )

        return asyncio.run(go())

    # turn 1: bash needs confirmation -> run parks, the agent never ran
    status1 = _run(ledger, Guarded())
    assert status1 is RunStatus.waiting_human
    assert ledger.get_node("run").status is NodeStatus.waiting_human
    assert calls["n"] == 0  # agent not invoked before approval
    assert (run_dir / "steps" / "run" / "main" / "confirm" / "pending").exists()

    # human approves, then resume -> node runs
    write_answer(run_dir, "run", "approved")
    assert not (run_dir / "steps" / "run" / "main" / "confirm" / "pending").exists()
    status2 = _run(Ledger.load(run_dir), Guarded())
    assert status2 is RunStatus.completed
    assert calls["n"] == 1
    assert (run_dir / "steps" / "run" / "main" / "output" / "doc.md").exists()
