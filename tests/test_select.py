"""Select meta-node execution (SPEC §10.3 / §18 ``test_select``).

MockRuntime only. Candidates are produced by scanner→map so the collection is
real; the selector picks a winner. Mirrors tests/test_map.py's harness.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import yaml

from refract.events import EventWriter
from refract.graph import load_agents
from refract.models.ledger import NodeStatus, RunStatus
from refract.models.pipeline import Pipeline
from refract.registry import ArtifactRegistry, model_slug
from refract.runtime.mock import MockRuntime, ScriptedResponse
from refract.scheduler import run_pipeline
from refract.state import Ledger

DOC = "# Requirements: x\n- FR-1\n"

_TYPES = """
version: "0.1"
types:
  source@v1: { kind: any }
  requirements@v1:
    kind: file
    format: markdown
    rules:
      - { rule: regex, pattern: "^# Requirements:", flags: "m" }
"""


async def _no_sleep(_seconds: float) -> None:
    return None


def _mk_agent(lib: Path, name: str, consumes: list[dict], produces: list[dict]) -> None:
    d = lib / "agents" / name
    d.mkdir(parents=True)
    (d / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": 1,
                "consumes": consumes,
                "produces": produces,
                "needs": ["read"],
            }
        ),
        encoding="utf-8",
    )
    (d / "prompt.md").write_text(f"You are {name}.", encoding="utf-8")


def _library(tmp_path: Path) -> tuple:
    lib = tmp_path / "library"
    (lib / "types" / "schemas").mkdir(parents=True)
    (lib / "types" / "artifact_types.yaml").write_text(_TYPES, encoding="utf-8")
    _mk_agent(
        lib,
        "proc",
        [{"port": "src", "type": "source@v1"}],
        [{"port": "doc", "type": "requirements@v1"}],
    )
    _mk_agent(
        lib,
        "sel",
        [{"port": "cands", "type": "collection<requirements@v1>"}],
        [{"port": "choice", "type": "selection@v1"}],
    )
    agents, errs = load_agents(lib)
    assert errs == []
    return lib, agents, ArtifactRegistry.load(lib)


def _pipeline(
    *, fallback: str, min_ok: int = 1, on_item_failure: str = "skip"
) -> Pipeline:
    return Pipeline.model_validate(
        {
            "version": "0.1",
            "name": "pick",
            "nodes": [
                {"id": "scan", "type": "builtin/scanner"},
                {
                    "id": "proc",
                    "type": "agent",
                    "agent": "proc@1",
                    "map": "scan.sources",
                    "params": {
                        "model": "kimi/kimi-k3",
                        "workers": 2,
                        "min_ok": min_ok,
                        "on_item_failure": on_item_failure,
                    },
                },
                {
                    "id": "choose",
                    "type": "select",
                    "candidates": "proc.doc",
                    "selector": {"agent": "sel@1", "model": "kimi/kimi-k3"},
                    "params": {"fallback": fallback},
                },
            ],
        }
    )


def _run(
    tmp_path: Path,
    pipeline: Pipeline,
    agents: dict,
    registry: ArtifactRegistry,
    scenario: dict,
    *,
    n_inputs: int = 2,
) -> tuple[RunStatus, Ledger, Path]:
    lib = tmp_path / "library"
    proj_in = tmp_path / "input"
    proj_in.mkdir()
    for i in range(n_inputs):
        (proj_in / f"{chr(ord('a') + i)}.txt").write_text(f"src {i}", encoding="utf-8")
    run_dir = tmp_path / "run"
    (run_dir / "snapshot" / "agents").mkdir(parents=True)
    for ref in agents:
        shutil.copytree(
            lib / "agents" / ref.split("@")[0], run_dir / "snapshot" / "agents" / ref
        )
    ledger = Ledger.create(
        run_dir,
        run_id="r",
        pipeline="pick",
        node_ids=[n.id for n in pipeline.nodes],
        created_at="T0",
    )
    events = EventWriter(run_dir)
    runtime = MockRuntime(scenario)
    status = asyncio.run(
        run_pipeline(
            run_dir,
            pipeline=pipeline,
            agents=agents,
            registry=registry,
            runtime=runtime,
            ledger=ledger,
            events=events,
            project_input_dir=proj_in,
            clock=lambda: "T",
            sleeper=_no_sleep,
        )
    )
    return status, ledger, run_dir


def _sel(winner: str) -> str:
    return json.dumps({"winner": winner})


def test_single_candidate_skips_selector(tmp_path: Path) -> None:
    _, agents, reg = _library(tmp_path)
    pl = _pipeline(fallback="first_ok")
    status, ledger, run_dir = _run(
        tmp_path,
        pl,
        agents,
        reg,
        {"proc:*": [ScriptedResponse(files={"doc.md": DOC})]},
        n_inputs=1,
    )
    assert status is RunStatus.completed
    assert ledger.get_node("choose").status is NodeStatus.done
    assert "choose.selector" not in ledger.state.steps  # no selector step at n=1
    assert ledger.get_node("choose").winner == "a-txt"
    assert (run_dir / "steps" / "choose" / "_out" / "out.md").read_text("utf-8") == DOC


def test_multiple_candidates_valid_winner(tmp_path: Path) -> None:
    _, agents, reg = _library(tmp_path)
    pl = _pipeline(fallback="first_ok")
    status, ledger, run_dir = _run(
        tmp_path,
        pl,
        agents,
        reg,
        {
            "proc:*": [ScriptedResponse(files={"doc.md": DOC})],
            "choose.selector": [ScriptedResponse(files={"choice.json": _sel("b-txt")})],
        },
    )
    assert status is RunStatus.completed
    assert ledger.get_node("choose").winner == "b-txt"
    assert (
        ledger.get_node("choose").winner_model is None
    )  # candidates from map, not map_over
    assert (run_dir / "steps" / "choose" / "_out" / "out.md").read_text("utf-8") == DOC


def test_invalid_winner_falls_back(tmp_path: Path) -> None:
    _, agents, reg = _library(tmp_path)
    pl = _pipeline(fallback="first_ok")
    status, ledger, run_dir = _run(
        tmp_path,
        pl,
        agents,
        reg,
        {
            "proc:*": [ScriptedResponse(files={"doc.md": DOC})],
            "choose.selector": [ScriptedResponse(files={"choice.json": _sel("nope")})],
        },
    )
    assert status is RunStatus.completed
    assert ledger.get_node("choose").winner == "a-txt"  # first ok
    assert ledger.get_step("choose.selector").tries > 1  # exhausted gate retries
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text("utf-8").splitlines()
    ]
    assert any(
        e["type"] == "log" and e["payload"].get("level") == "warning" for e in events
    )


def test_invalid_winner_fallback_fail(tmp_path: Path) -> None:
    _, agents, reg = _library(tmp_path)
    pl = _pipeline(fallback="fail")
    status, ledger, _ = _run(
        tmp_path,
        pl,
        agents,
        reg,
        {
            "proc:*": [ScriptedResponse(files={"doc.md": DOC})],
            "choose.selector": [ScriptedResponse(files={"choice.json": _sel("nope")})],
        },
    )
    assert status is RunStatus.failed
    assert ledger.get_node("choose").status is NodeStatus.failed


def test_zero_ok_candidates_fails(tmp_path: Path) -> None:
    # every map element fails (agent_error) with min_ok=0 so the map node still
    # completes but the candidate collection has no ok items → select fails.
    _, agents, reg = _library(tmp_path)
    pl = _pipeline(fallback="first_ok", min_ok=0, on_item_failure="skip")
    status, ledger, _ = _run(
        tmp_path,
        pl,
        agents,
        reg,
        {"proc:*": [ScriptedResponse(agent_error="boom")]},
    )
    assert status is RunStatus.failed
    assert ledger.get_node("choose").status is NodeStatus.failed
    assert ledger.get_node("choose").error == "no ok candidates"


def test_model_slug_mapping() -> None:
    # winner_model reconstruction relies on this (used once map_over lands).
    assert model_slug("kimi/kimi-k3") == "kimi_kimi-k3"


def test_dir_any_winner_assembled_under_out_dir(tmp_path: Path) -> None:
    # SPEC §10.3/§10.4: for a dir/any element type X, the winner payload must land
    # at _out/out/ (the port dir), where a downstream consumer of <select>.out
    # looks — not scattered into _out/ root. Candidates = scanner's
    # collection<source@v1> (source@v1 is kind: any).
    lib = tmp_path / "library"
    (lib / "types" / "schemas").mkdir(parents=True)
    (lib / "types" / "artifact_types.yaml").write_text(_TYPES, encoding="utf-8")
    _mk_agent(
        lib,
        "srcsel",
        [{"port": "cands", "type": "collection<source@v1>"}],
        [{"port": "choice", "type": "selection@v1"}],
    )
    agents, errs = load_agents(lib)
    assert errs == []
    reg = ArtifactRegistry.load(lib)
    pl = Pipeline.model_validate(
        {
            "version": "0.1",
            "name": "pick",
            "nodes": [
                {"id": "scan", "type": "builtin/scanner"},
                {
                    "id": "choose",
                    "type": "select",
                    "candidates": "scan.sources",
                    "selector": {"agent": "srcsel@1", "model": "kimi/kimi-k3"},
                    "params": {"fallback": "first_ok"},
                },
            ],
        }
    )
    status, ledger, run_dir = _run(
        tmp_path,
        pl,
        agents,
        reg,
        {"choose.selector": [ScriptedResponse(files={"choice.json": _sel("b-txt")})]},
        n_inputs=2,
    )
    assert status is RunStatus.completed
    assert ledger.get_node("choose").winner == "b-txt"
    out = run_dir / "steps" / "choose" / "_out" / "out"
    assert out.is_dir()  # the port dir, not scattered into _out/ root
    assert (out / "b.txt").exists()
