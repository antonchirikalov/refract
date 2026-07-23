"""Asyncio scheduler, resume, reuse (SPEC §10.5).

A node is ready when all nodes sourcing its inputs (including binding deps) are
done/reused. Ready nodes run concurrently under per-provider semaphores.

Phase 0 scope: plain ``agent`` nodes (one step each). Meta-nodes (loop/select),
map fan-out, and builtin nodes are separate items (SPEC §10.3, §13) and raise a
clear ``NotImplementedError`` here until their lifecycles land — they all reuse
``steps.execute_agent_step`` and plug into this same ready-set loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from refract.artifacts import artifact_path
from refract.events import EventWriter, utcnow_iso
from refract.graph import DataRef, parse_ref
from refract.models.agent import AgentSpec
from refract.models.ledger import NodeStatus, RunStatus, StepOutcome
from refract.models.pipeline import AgentNode, Pipeline
from refract.registry import ArtifactRegistry
from refract.runtime.base import AgentRuntime
from refract.state import Ledger
from refract.steps import (
    AgentStepPlan,
    DirAnyInput,
    FileInput,
    InputSpec,
    execute_agent_step,
)

_DEFAULT_PROVIDER_LIMIT = 4


# --- static graph shape (Phase 0: agent-node data edges) --------------------


def node_dependencies(pipeline: Pipeline) -> dict[str, set[str]]:
    """Map each node id to the set of node ids feeding its inputs (SPEC §10.5)."""
    ids = {n.id for n in pipeline.nodes}
    deps: dict[str, set[str]] = {n.id: set() for n in pipeline.nodes}
    for node in pipeline.nodes:
        if isinstance(node, AgentNode):
            for ref_s in node.inputs.values():
                ref = parse_ref(ref_s)
                if isinstance(ref, DataRef) and ref.node_id in ids:
                    deps[node.id].add(ref.node_id)
    return deps


# --- input resolution (SPEC §10.1/§10.4) ------------------------------------


def _step_output_dir(run_dir: Path, node_id: str) -> Path:
    """Where a plain node's single step writes its artifacts (SPEC §9 step table)."""
    return run_dir / "steps" / node_id / "main" / "output"


def _build_inputs(
    node: AgentNode,
    run_dir: Path,
    agents: dict[str, AgentSpec],
    registry: ArtifactRegistry,
) -> list[InputSpec]:
    agent = agents[node.agent]
    consume_type = {p.port: p.type for p in agent.consumes}
    specs: list[InputSpec] = []
    for port, ref_s in node.inputs.items():
        ref = parse_ref(ref_s)
        if not isinstance(ref, DataRef):
            raise NotImplementedError(f"unsupported input ref {ref_s!r} on {node.id}")
        producer_out = _step_output_dir(run_dir, ref.node_id)
        ptype = consume_type[port]
        if ptype.startswith("collection<"):
            # produced only by map/builtin nodes — deferred (SPEC §10.3/§13)
            raise NotImplementedError(
                f"collection input {port!r} on {node.id}: map/builtin not implemented"
            )
        rtype = registry.get(ptype)
        if rtype is None:
            raise KeyError(f"unknown type {ptype!r} for input {port!r} on {node.id}")
        if rtype.kind.value == "file":
            src = artifact_path(producer_out, ref.port, rtype)
            specs.append(FileInput(port=port, src=src, rtype=rtype))
        else:  # dir | any
            specs.append(DirAnyInput(port=port, src=producer_out / ref.port))
    return specs


# --- plan building ----------------------------------------------------------


def _agent_plan(
    node: AgentNode,
    *,
    run_dir: Path,
    agents: dict[str, AgentSpec],
    registry: ArtifactRegistry,
) -> AgentStepPlan:
    agent = agents[node.agent]
    model = node.params.model
    if model is None:
        raise ValueError(f"node {node.id!r} has no resolved model")
    timeout = node.params.timeout_s or agent.defaults.timeout_s
    return AgentStepPlan(
        step_id=node.id,
        node_id=node.id,
        workdir=run_dir / "steps" / node.id / "main",
        agent=agent,
        agent_dir=run_dir / "snapshot" / "agents" / node.agent,
        model=model,
        registry=registry,
        inputs=_build_inputs(node, run_dir, agents, registry),
        timeout_s=timeout,
        gate_retries=node.params.gate_retries,
        infra_retries=node.params.infra_retries,
    )


def _provider_of(model: str) -> str:
    return model.split("/", 1)[0]


# --- the run loop -----------------------------------------------------------


async def run_pipeline(
    run_dir: Path | str,
    *,
    pipeline: Pipeline,
    agents: dict[str, AgentSpec],
    registry: ArtifactRegistry,
    runtime: AgentRuntime,
    ledger: Ledger,
    events: EventWriter,
    provider_limits: dict[str, int] | None = None,
    clock: Callable[[], str] = utcnow_iso,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RunStatus:
    """Execute the pipeline to a terminal run status (SPEC §10.5, Phase 0)."""
    run_dir = Path(run_dir)
    limits = provider_limits or {}
    nodes = {n.id: n for n in pipeline.nodes}
    deps = node_dependencies(pipeline)

    # Reject unsupported node kinds up front so scheduling never starts a run it
    # cannot finish cleanly (I10). loop/select/map/builtin are separate items.
    for nid, node in nodes.items():
        if not isinstance(node, AgentNode) or node.map is not None:
            raise NotImplementedError(
                f"node {nid!r}: only plain agent nodes are implemented "
                "(SPEC §10.3/§13: map/loop/select/builtin not yet)"
            )
    semaphores: dict[str, asyncio.Semaphore] = {}

    def emit_event(event: dict[str, object]) -> None:
        events.emit(event)

    def semaphore_for(model: str) -> asyncio.Semaphore:
        provider = _provider_of(model)
        if provider not in semaphores:
            limit = limits.get(provider, _DEFAULT_PROVIDER_LIMIT)
            semaphores[provider] = asyncio.Semaphore(max(1, limit))
        return semaphores[provider]

    ledger.set_run_status(RunStatus.running)
    events.emit(
        {"type": "run_state_changed", "payload": {"from": "created", "to": "running"}}
    )

    def set_node(node_id: str, status: NodeStatus, *, error: str | None = None) -> None:
        prev = ledger.get_node(node_id)
        from_status = prev.status.value if prev is not None else "pending"
        ledger.set_node_status(node_id, status, error=error)
        events.emit(
            {
                "type": "node_state_changed",
                "payload": {
                    "node_id": node_id,
                    "from": from_status,
                    "to": status.value,
                },
            }
        )

    async def run_node(node_id: str) -> NodeStatus:
        node = nodes[node_id]
        assert isinstance(node, AgentNode)  # guaranteed by the up-front check
        plan = _agent_plan(node, run_dir=run_dir, agents=agents, registry=registry)
        async with semaphore_for(plan.model):
            # flip to running only once actually executing, not while queued
            set_node(node_id, NodeStatus.running)
            step = await execute_agent_step(
                plan,
                runtime,
                ledger,
                on_event=emit_event,
                clock=clock,
                sleeper=sleeper,
            )
        if step.outcome is StepOutcome.ok:
            set_node(node_id, NodeStatus.done)
            return NodeStatus.done
        set_node(node_id, NodeStatus.failed, error=step.error)
        return NodeStatus.failed

    pending = set(nodes)
    resolved: dict[str, NodeStatus] = {}  # done | failed | skipped
    tasks: dict[asyncio.Task[NodeStatus], str] = {}

    def ready() -> list[str]:
        out = []
        for nid in pending:
            if all(d in resolved and resolved[d] is NodeStatus.done for d in deps[nid]):
                out.append(nid)
        return sorted(out)

    def skip_unreachable() -> None:
        # iterate to a fixpoint: a node whose blocker sorts after it must still
        # be skipped in the same call, transitively across multi-hop chains.
        changed = True
        while changed:
            changed = False
            for nid in sorted(pending):
                if any(
                    resolved.get(d) in (NodeStatus.failed, NodeStatus.skipped)
                    for d in deps[nid]
                ):
                    pending.discard(nid)
                    resolved[nid] = NodeStatus.skipped
                    set_node(nid, NodeStatus.skipped, error="upstream failed")
                    changed = True

    try:
        while pending or tasks:
            skip_unreachable()
            for nid in ready():
                pending.discard(nid)
                tasks[asyncio.ensure_future(run_node(nid))] = nid
            if not tasks:
                break
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                nid = tasks.pop(task)
                resolved[nid] = task.result()
    except BaseException:
        # never leak in-flight tasks; leave the run in a terminal failed state
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        ledger.set_run_status(RunStatus.failed, finished_at=clock())
        events.emit(
            {
                "type": "run_state_changed",
                "payload": {"from": "running", "to": "failed"},
            }
        )
        raise

    failed = any(s is NodeStatus.failed for s in resolved.values())
    status = RunStatus.failed if failed else RunStatus.completed
    ledger.set_run_status(status, finished_at=clock())
    events.emit(
        {
            "type": "run_state_changed",
            "payload": {"from": "running", "to": status.value},
        }
    )
    return status
