"""Tests for the asyncio scheduler (SPEC §10.5, Phase 0: plain agent nodes)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml

from refract.events import EventWriter
from refract.models.agent import AgentSpec
from refract.models.ledger import NodeStatus, RunStatus, StepOutcome
from refract.models.pipeline import Pipeline
from refract.runtime.base import EventCallback, StepResult, StepSpec
from refract.runtime.mock import MockRuntime, ScriptedResponse
from refract.scheduler import node_dependencies, run_pipeline
from refract.state import Ledger

from graph_fixtures import agent_spec, write_registry

# --- builders ----------------------------------------------------------------


def _clock_seq() -> "callable":
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"T{counter['n']}"

    return clock


async def _no_sleep(_seconds: float) -> None:
    return None


def _write_agent_pkg(run_dir: Path, ref: str) -> None:
    """Minimal on-disk agent package the scheduler reads a system prompt from."""
    pkg_dir = run_dir / "snapshot" / "agents" / ref
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "prompt.md").write_text(f"You are {ref}.", encoding="utf-8")


def _agents(*specs: AgentSpec) -> dict[str, AgentSpec]:
    return {s.ref: s for s in specs}


def _ledger(run_dir: Path, node_ids: list[str], *, pipeline_name: str = "p") -> Ledger:
    return Ledger.create(
        run_dir,
        run_id="run_test",
        pipeline=pipeline_name,
        node_ids=node_ids,
        created_at="T0",
    )


# --- 1. node_dependencies -----------------------------------------------------


class TestNodeDependencies:
    def test_chain_deps(self, tmp_path: Path) -> None:
        # SPEC §10.5
        pipeline = Pipeline.model_validate(
            yaml.safe_load(
                """
version: "0.1"
name: chain
nodes:
  - id: gen
    type: agent
    agent: gen_agent@1
  - id: mid
    type: agent
    agent: mid_agent@1
    inputs: { in: "gen.out" }
  - id: sink
    type: agent
    agent: sink_agent@1
    inputs: { in: "mid.out" }
"""
            )
        )
        deps = node_dependencies(pipeline)
        assert deps["gen"] == set()
        assert deps["mid"] == {"gen"}
        assert deps["sink"] == {"mid"}


# --- 2. happy path: chain feeding an edge, ledger + events -------------------


class TestHappyPathChain:
    async def test_two_node_chain_completes_and_wires_edge(
        self, tmp_path: Path
    ) -> None:
        # SPEC §10.5: a node is ready once its input sources are done; the
        # producer's output is materialized into the consumer's input dir.
        run_dir = tmp_path / "run"
        registry = write_registry(tmp_path)

        gen_agent = agent_spec(
            "gen_agent", produces=[{"port": "out", "type": "extract@v1"}]
        )
        sink_agent = agent_spec(
            "sink_agent",
            consumes=[{"port": "in", "type": "extract@v1"}],
            produces=[{"port": "doc", "type": "requirements@v1"}],
        )
        agents = _agents(gen_agent, sink_agent)
        for ref in agents:
            _write_agent_pkg(run_dir, ref)

        pipeline = Pipeline.model_validate(
            yaml.safe_load(
                """
version: "0.1"
name: chain
nodes:
  - id: gen
    type: agent
    agent: gen_agent@1
    params: { model: "mock/mock-1", gate_retries: 0 }
  - id: sink
    type: agent
    agent: sink_agent@1
    inputs: { in: "gen.out" }
    params: { model: "mock/mock-1", gate_retries: 0 }
"""
            )
        )

        ledger = _ledger(run_dir, ["gen", "sink"], pipeline_name="chain")
        events = EventWriter(run_dir, clock=_clock_seq())

        runtime = MockRuntime(
            {
                "gen": [ScriptedResponse(files={"out.json": json.dumps({"x": 1})})],
                "sink": [
                    ScriptedResponse(
                        files={"doc.md": "# Requirements:\nbody text here."}
                    )
                ],
            }
        )

        status = await run_pipeline(
            run_dir,
            pipeline=pipeline,
            agents=agents,
            registry=registry,
            runtime=runtime,
            ledger=ledger,
            events=events,
            clock=_clock_seq(),
            sleeper=_no_sleep,
        )

        assert status is RunStatus.completed

        assert ledger.get_node("gen").status is NodeStatus.done
        assert ledger.get_node("sink").status is NodeStatus.done
        assert ledger.get_step("gen").outcome is StepOutcome.ok
        assert ledger.get_step("sink").outcome is StepOutcome.ok
        assert ledger.state.status is RunStatus.completed

        # edge wiring: sink's workdir received gen's output under input/in/
        sink_input = run_dir / "steps" / "sink" / "main" / "input" / "in"
        assert sink_input.is_dir()
        assert (sink_input / "in.json").exists()
        assert json.loads((sink_input / "in.json").read_text("utf-8")) == {"x": 1}

        # events: run_state_changed created->running, running->completed;
        # node_state_changed for both nodes.
        lines = (run_dir / "events.jsonl").read_text("utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        run_events = [r for r in records if r["type"] == "run_state_changed"]
        assert {(e["payload"]["from"], e["payload"]["to"]) for e in run_events} == {
            ("created", "running"),
            ("running", "completed"),
        }
        node_events = [r for r in records if r["type"] == "node_state_changed"]
        node_ids_seen = {e["payload"]["node_id"] for e in node_events}
        assert node_ids_seen == {"gen", "sink"}
        for nid in ("gen", "sink"):
            to_values = [
                e["payload"]["to"]
                for e in node_events
                if e["payload"]["node_id"] == nid
            ]
            assert "done" in to_values


# --- 3. failure propagation: gate fail -> downstream skipped -----------------


class TestFailurePropagation:
    async def test_gate_failure_fails_upstream_and_skips_downstream(
        self, tmp_path: Path
    ) -> None:
        # SPEC §10.5: a failed node's dependents are skipped, not attempted.
        run_dir = tmp_path / "run"
        registry = write_registry(tmp_path)

        gen_agent = agent_spec(
            "gen_agent", produces=[{"port": "out", "type": "extract@v1"}]
        )
        sink_agent = agent_spec(
            "sink_agent",
            consumes=[{"port": "in", "type": "extract@v1"}],
            produces=[{"port": "doc", "type": "requirements@v1"}],
        )
        agents = _agents(gen_agent, sink_agent)
        for ref in agents:
            _write_agent_pkg(run_dir, ref)

        pipeline = Pipeline.model_validate(
            yaml.safe_load(
                """
version: "0.1"
name: chain
nodes:
  - id: gen
    type: agent
    agent: gen_agent@1
    params: { model: "mock/mock-1", gate_retries: 0 }
  - id: sink
    type: agent
    agent: sink_agent@1
    inputs: { in: "gen.out" }
    params: { model: "mock/mock-1", gate_retries: 0 }
"""
            )
        )

        ledger = _ledger(run_dir, ["gen", "sink"], pipeline_name="chain")
        events = EventWriter(run_dir, clock=_clock_seq())

        # gen never writes the required "out" artifact -> gate fails.
        runtime = MockRuntime({"gen": [ScriptedResponse(files={})]})

        status = await run_pipeline(
            run_dir,
            pipeline=pipeline,
            agents=agents,
            registry=registry,
            runtime=runtime,
            ledger=ledger,
            events=events,
            clock=_clock_seq(),
            sleeper=_no_sleep,
        )

        assert status is RunStatus.failed
        assert ledger.get_node("gen").status is NodeStatus.failed
        assert ledger.get_node("sink").status is NodeStatus.skipped
        assert ledger.state.status is RunStatus.failed

    async def test_multi_hop_skip_is_transitive_regardless_of_id_order(
        self, tmp_path: Path
    ) -> None:
        # Regression: skip must reach a fixpoint even when an intermediate node
        # sorts BEFORE its blocker. Chain z -> m -> a with a<m<z by id order;
        # z fails, so both m and a must end skipped (not left pending).
        run_dir = tmp_path / "run"
        registry = write_registry(tmp_path)

        z_agent = agent_spec("z_agent", produces=[{"port": "out", "type": "extract@v1"}])
        m_agent = agent_spec(
            "m_agent",
            consumes=[{"port": "in", "type": "extract@v1"}],
            produces=[{"port": "out", "type": "extract@v1"}],
        )
        a_agent = agent_spec(
            "a_agent",
            consumes=[{"port": "in", "type": "extract@v1"}],
            produces=[{"port": "doc", "type": "requirements@v1"}],
        )
        agents = _agents(z_agent, m_agent, a_agent)
        for ref in agents:
            _write_agent_pkg(run_dir, ref)

        pipeline = Pipeline.model_validate(
            yaml.safe_load(
                """
version: "0.1"
name: chain3
nodes:
  - id: a
    type: agent
    agent: a_agent@1
    inputs: { in: "m.out" }
    params: { model: "mock/mock-1", gate_retries: 0 }
  - id: m
    type: agent
    agent: m_agent@1
    inputs: { in: "z.out" }
    params: { model: "mock/mock-1", gate_retries: 0 }
  - id: z
    type: agent
    agent: z_agent@1
    params: { model: "mock/mock-1", gate_retries: 0 }
"""
            )
        )

        ledger = _ledger(run_dir, ["a", "m", "z"], pipeline_name="chain3")
        events = EventWriter(run_dir, clock=_clock_seq())
        runtime = MockRuntime({"z": [ScriptedResponse(files={})]})  # z gate fails

        status = await run_pipeline(
            run_dir,
            pipeline=pipeline,
            agents=agents,
            registry=registry,
            runtime=runtime,
            ledger=ledger,
            events=events,
            clock=_clock_seq(),
            sleeper=_no_sleep,
        )

        assert status is RunStatus.failed
        assert ledger.get_node("z").status is NodeStatus.failed
        assert ledger.get_node("m").status is NodeStatus.skipped
        assert ledger.get_node("a").status is NodeStatus.skipped


# --- 4. provider semaphore: bounded concurrency per provider -----------------


class _ConcurrencyTrackingRuntime:
    """A runtime that writes the same outputs MockRuntime would, but records
    the maximum number of concurrent ``run_step`` calls observed (used to test
    the per-provider semaphore, since MockRuntime alone can't observe overlap)."""

    def __init__(self, files_by_step: dict[str, dict[str, str]]) -> None:
        self._files_by_step = files_by_step
        self._current = 0
        self.max_concurrency = 0

    async def run_step(self, spec: StepSpec, on_event: EventCallback) -> StepResult:
        self._current += 1
        self.max_concurrency = max(self.max_concurrency, self._current)
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            output_dir = spec.workdir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            for rel, content in self._files_by_step[spec.step_id].items():
                (output_dir / rel).write_text(content, encoding="utf-8")
            (spec.workdir / "raw.txt").write_text("mock", encoding="utf-8")
            (spec.workdir / "agent.events.jsonl").write_text("", encoding="utf-8")
            return StepResult(completed=True)
        finally:
            self._current -= 1

    async def close(self) -> None:
        return None


def _independent_two_node_pipeline() -> Pipeline:
    return Pipeline.model_validate(
        yaml.safe_load(
            """
version: "0.1"
name: parallel
nodes:
  - id: a
    type: agent
    agent: agent_a@1
    params: { model: "mock/mock-1", gate_retries: 0 }
  - id: b
    type: agent
    agent: agent_b@1
    params: { model: "mock/mock-1", gate_retries: 0 }
"""
        )
    )


class TestProviderSemaphore:
    async def test_limit_one_serializes_independent_nodes(self, tmp_path: Path) -> None:
        # SPEC §10.5: provider_limits bound concurrency per provider.
        run_dir = tmp_path / "run"
        registry = write_registry(tmp_path)

        agent_a = agent_spec(
            "agent_a", produces=[{"port": "out", "type": "extract@v1"}]
        )
        agent_b = agent_spec(
            "agent_b", produces=[{"port": "out", "type": "extract@v1"}]
        )
        agents = _agents(agent_a, agent_b)
        for ref in agents:
            _write_agent_pkg(run_dir, ref)

        pipeline = _independent_two_node_pipeline()
        ledger = _ledger(run_dir, ["a", "b"], pipeline_name="parallel")
        events = EventWriter(run_dir, clock=_clock_seq())

        runtime = _ConcurrencyTrackingRuntime(
            {
                "a": {"out.json": json.dumps({"x": 1})},
                "b": {"out.json": json.dumps({"x": 2})},
            }
        )

        status = await run_pipeline(
            run_dir,
            pipeline=pipeline,
            agents=agents,
            registry=registry,
            runtime=runtime,
            ledger=ledger,
            events=events,
            provider_limits={"mock": 1},
            clock=_clock_seq(),
            sleeper=_no_sleep,
        )

        assert status is RunStatus.completed
        assert runtime.max_concurrency == 1

    async def test_limit_two_allows_full_concurrency(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        registry = write_registry(tmp_path)

        agent_a = agent_spec(
            "agent_a", produces=[{"port": "out", "type": "extract@v1"}]
        )
        agent_b = agent_spec(
            "agent_b", produces=[{"port": "out", "type": "extract@v1"}]
        )
        agents = _agents(agent_a, agent_b)
        for ref in agents:
            _write_agent_pkg(run_dir, ref)

        pipeline = _independent_two_node_pipeline()
        ledger = _ledger(run_dir, ["a", "b"], pipeline_name="parallel")
        events = EventWriter(run_dir, clock=_clock_seq())

        runtime = _ConcurrencyTrackingRuntime(
            {
                "a": {"out.json": json.dumps({"x": 1})},
                "b": {"out.json": json.dumps({"x": 2})},
            }
        )

        status = await run_pipeline(
            run_dir,
            pipeline=pipeline,
            agents=agents,
            registry=registry,
            runtime=runtime,
            ledger=ledger,
            events=events,
            provider_limits={"mock": 2},
            clock=_clock_seq(),
            sleeper=_no_sleep,
        )

        assert status is RunStatus.completed
        assert runtime.max_concurrency == 2
