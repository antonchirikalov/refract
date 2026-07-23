"""Asyncio scheduler, resume, reuse (SPEC §10.5).

A node is ready when all nodes sourcing its inputs (including binding deps) are
done/reused. Ready nodes run concurrently under per-provider semaphores.

Phase 0 scope: plain ``agent`` nodes, ``map`` nodes (collection fan-out: one
step per ok item, reassembled into an output collection), and ``builtin`` nodes
(e.g. scanner). A plain agent may consume a whole collection; producing one is
map's job (I6). ``map_over`` (models fan-out) and meta-nodes (loop/select) are
separate items and are rejected up front with a clear ``NotImplementedError``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from refract.artifacts import artifact_path, link_or_copy
from refract.builtins import BUILTINS
from refract.events import EventWriter, utcnow_iso
from refract.graph import DataRef, parse_ref
from refract.models.agent import AgentSpec
from refract.models.ledger import NodeStatus, RunStatus, StepOutcome, StepStatus
from refract.models.pipeline import AgentNode, BuiltinNode, Node, Pipeline
from refract.models.types import (
    CollectionItem,
    CollectionManifest,
    CollectionStats,
    CollectionStatus,
    ItemInfo,
)
from refract.registry import ArtifactRegistry, ResolvedType, make_collection
from refract.runtime.base import AgentRuntime
from refract.state import Ledger
from refract.steps import (
    AgentStepPlan,
    CollectionInput,
    DirAnyInput,
    FileInput,
    InputSpec,
    MapItemInput,
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
            refs = list(node.inputs.values())
            if node.map is not None:  # the mapped collection is a dependency too
                refs.append(node.map)
            for ref_s in refs:
                ref = parse_ref(ref_s)
                if isinstance(ref, DataRef) and ref.node_id in ids:
                    deps[node.id].add(ref.node_id)
    return deps


# --- input resolution (SPEC §10.1/§10.4) ------------------------------------


def _node_output_base(run_dir: Path, node: Node) -> Path:
    """Directory that holds a producer node's port outputs (SPEC §9/§10.3).

    Plain agent/builtin nodes write to ``steps/<id>/main/output/``; a map node
    assembles its output collection under ``steps/<id>/_out/``.
    """
    if isinstance(node, AgentNode) and node.map is not None:
        return run_dir / "steps" / node.id / "_out"
    return run_dir / "steps" / node.id / "main" / "output"


def _build_inputs(
    node: AgentNode,
    run_dir: Path,
    agents: dict[str, AgentSpec],
    registry: ArtifactRegistry,
    nodes: dict[str, "Node"],
) -> list[InputSpec]:
    """Resolve a node's non-mapped inputs (``node.inputs``) to materializable specs.

    The mapped port (for a map node) is bound per-element by the map loop, not here.
    """
    agent = agents[node.agent]
    consume_type = {p.port: p.type for p in agent.consumes}
    specs: list[InputSpec] = []
    for port, ref_s in node.inputs.items():
        ref = parse_ref(ref_s)
        if not isinstance(ref, DataRef):
            raise NotImplementedError(f"unsupported input ref {ref_s!r} on {node.id}")
        producer_out = _node_output_base(run_dir, nodes[ref.node_id])
        ptype = consume_type[port]
        if ptype.startswith("collection<"):
            # a plain agent may consume a whole collection (I6 forbids producing
            # one, not consuming). The producer wrote it under <base>/<producer_port>/.
            specs.append(CollectionInput(port=port, src=producer_out / ref.port))
            continue
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
    nodes: dict[str, Node],
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
        inputs=_build_inputs(node, run_dir, agents, registry, nodes),
        timeout_s=timeout,
        gate_retries=node.params.gate_retries,
        infra_retries=node.params.infra_retries,
    )


def _provider_of(model: str) -> str:
    return model.split("/", 1)[0]


# --- map fan-out (SPEC §10.3) -----------------------------------------------


@dataclass(frozen=True)
class _MapBinding:
    """Resolved binding for a map node: how elements come in and go out."""

    mapped_port: str  # consume port bound to one collection element
    input_dir: Path  # producer collection dir (holds _collection.json + slugs)
    out_port: str  # agent's primary produce port
    out_rtype: ResolvedType
    out_collection_type: str  # collection<primary produce type>


def _map_binding(
    node: AgentNode,
    agent: AgentSpec,
    nodes: dict[str, Node],
    registry: ArtifactRegistry,
    run_dir: Path,
) -> _MapBinding:
    assert node.map is not None
    ref = parse_ref(node.map)
    assert isinstance(ref, DataRef)
    # mapped port = the one consumes port NOT satisfied by node.inputs (validator
    # guarantees exactly one; the rest are shared inputs) (SPEC §8.1).
    mapped = [p for p in agent.consumes if p.port not in node.inputs]
    if len(mapped) != 1:
        raise ValueError(
            f"map node {node.id!r}: expected 1 mapped port, got {len(mapped)}"
        )
    primary = [p for p in agent.produces if not p.optional]
    if len(primary) != 1:
        raise ValueError(f"map node {node.id!r}: agent has no single primary output")
    out_rtype = registry.get(primary[0].type)
    if out_rtype is None:
        raise KeyError(f"unknown produce type {primary[0].type!r} on {node.id}")
    return _MapBinding(
        mapped_port=mapped[0].port,
        input_dir=_node_output_base(run_dir, nodes[ref.node_id]) / ref.port,
        out_port=primary[0].port,
        out_rtype=out_rtype,
        out_collection_type=make_collection(primary[0].type),
    )


def _read_collection(collection_dir: Path) -> CollectionManifest:
    raw = json.loads((collection_dir / "_collection.json").read_text("utf-8"))
    return CollectionManifest.model_validate(raw)


def _copy_element_payload(
    step_output: Path, slug_dir: Path, out_port: str, rtype: ResolvedType
) -> None:
    """Copy one element's produced artifact into its output-collection slug dir (§10.4)."""
    slug_dir.mkdir(parents=True, exist_ok=True)
    if rtype.kind.value == "file":
        src = artifact_path(step_output, out_port, rtype)
        link_or_copy(src, slug_dir / src.name)
    else:  # dir | any: copy the port dir's contents
        src_dir = step_output / out_port
        if src_dir.is_dir():
            for child in sorted(src_dir.iterdir()):
                link_or_copy(child, slug_dir / child.name)


def _assemble_map_output(
    node: AgentNode,
    spec: _MapBinding,
    in_manifest: CollectionManifest,
    results: dict[str, StepOutcome],
    *,
    run_dir: Path,
) -> int:
    """Assemble ``steps/<node>/_out/<port>/`` from element steps (§10.3). Returns ok count.

    Idempotent: the output dir is rebuilt from scratch so resume/re-assembly is safe.
    ``ok`` elements carry their payload; failed input items and failed steps are
    copied into the manifest with ``status: failed`` (payload absent).
    """
    out_dir = run_dir / "steps" / node.id / "_out" / spec.out_port
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    items: list[CollectionItem] = []
    ok = 0
    for item in in_manifest.items:
        step_ok = (
            item.status is CollectionStatus.ok
            and results.get(item.slug) is StepOutcome.ok
        )
        if step_ok:
            step_output = run_dir / "steps" / node.id / item.slug / "output"
            _copy_element_payload(
                step_output, out_dir / item.slug, spec.out_port, spec.out_rtype
            )
            status, error, ok = CollectionStatus.ok, None, ok + 1
        else:
            outcome = results.get(item.slug)
            status = CollectionStatus.failed
            error = (
                item.error
                if item.status is CollectionStatus.failed
                else (outcome.value if outcome is not None else "not executed")
            )
        items.append(
            CollectionItem(
                slug=item.slug,
                source=item.source,
                source_hash=item.source_hash,
                status=status,
                path=f"{item.slug}/",
                error=error,
            )
        )

    manifest = CollectionManifest(
        type=spec.out_collection_type,
        items=items,
        stats=CollectionStats(total=len(items), ok=ok, failed=len(items) - ok),
    )
    (out_dir / "_collection.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return ok


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
    project_input_dir: Path | str | None = None,
    clock: Callable[[], str] = utcnow_iso,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RunStatus:
    """Execute the pipeline to a terminal run status (SPEC §10.5, Phase 0)."""
    run_dir = Path(run_dir)
    limits = provider_limits or {}
    nodes = {n.id: n for n in pipeline.nodes}
    deps = node_dependencies(pipeline)

    # Reject unsupported node kinds up front so scheduling never starts a run it
    # cannot finish cleanly (I10). Supported: plain agent nodes + builtin/scanner.
    # map fan-out and loop/select meta-nodes are separate items (SPEC §10.3).
    for nid, node in nodes.items():
        # plain agent or map node (map: collection fan-out). map_over (models
        # fan-out) is Phase 1; loop/select are separate items.
        supported_agent = isinstance(node, AgentNode) and node.map_over is None
        supported_builtin = (
            isinstance(node, BuiltinNode)
            and BUILTINS.get(node.builtin_name) is not None
            and BUILTINS[node.builtin_name].run is not None
        )
        if not (supported_agent or supported_builtin):
            raise NotImplementedError(
                f"node {nid!r}: unsupported node kind in Phase 0 "
                "(SPEC §10.3: map/loop/select not yet)"
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

    async def run_builtin(node: BuiltinNode) -> NodeStatus:
        """Execute a builtin node: deterministic, no runner, ledger + outputs only (I9)."""
        bdef = BUILTINS[node.builtin_name]
        assert bdef.run is not None
        params = bdef.params_model.model_validate(node.params)
        port = bdef.produces[0].port
        input_override = getattr(params, "input", None)
        input_dir = (
            Path(input_override)
            if input_override
            else Path(project_input_dir)
            if project_input_dir is not None
            else run_dir / "input"
        )
        workdir = run_dir / "steps" / node.id / "main"
        output = workdir / "output"
        # Re-execution never overwrites in place (SPEC §10.2): rebuild output from
        # scratch so a resumed/re-run builtin is idempotent and never merges into a
        # partial prior run (crash recovery flips running→pending, then re-runs).
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        set_node(node.id, NodeStatus.running)
        started = clock()
        ledger.set_step(
            node.id,
            node=node.id,
            status=StepStatus.running,
            tries=0,
            started_at=started,
        )
        emit_event(
            {
                "type": "step_state_changed",
                "step_id": node.id,
                "payload": {"from": "pending", "to": "running"},
            }
        )
        try:
            bdef.run(params=params, input_dir=input_dir, output_dir=output, port=port)
        except OSError as exc:
            ledger.set_step(
                node.id,
                node=node.id,
                status=StepStatus.failed,
                outcome=StepOutcome.failed_infra,
                tries=1,
                started_at=started,
                finished_at=clock(),
                error=str(exc),
            )
            emit_event(
                {
                    "type": "step_state_changed",
                    "step_id": node.id,
                    "payload": {
                        "from": "running",
                        "to": "failed",
                        "outcome": "failed_infra",
                    },
                }
            )
            set_node(node.id, NodeStatus.failed, error=str(exc))
            return NodeStatus.failed
        ledger.set_step(
            node.id,
            node=node.id,
            status=StepStatus.done,
            outcome=StepOutcome.ok,
            tries=1,
            started_at=started,
            finished_at=clock(),
        )
        emit_event(
            {
                "type": "step_state_changed",
                "step_id": node.id,
                "payload": {"from": "running", "to": "done", "outcome": "ok"},
            }
        )
        set_node(node.id, NodeStatus.done)
        return NodeStatus.done

    async def run_map_node(node: AgentNode) -> NodeStatus:
        """Fan a map node out over its input collection, one step per ok item (§10.3)."""
        agent = agents[node.agent]
        model = node.params.model
        assert model is not None
        spec = _map_binding(node, agent, nodes, registry, run_dir)
        shared = _build_inputs(node, run_dir, agents, registry, nodes)
        manifest = _read_collection(spec.input_dir)

        set_node(node.id, NodeStatus.running)
        workers_sem = asyncio.Semaphore(max(1, node.params.workers))
        results: dict[str, StepOutcome] = {}

        async def run_item(item: CollectionItem) -> None:
            step_id = f"{node.id}:{item.slug}"
            existing = ledger.get_step(step_id)
            if existing is not None and existing.status is StepStatus.done:
                results[item.slug] = existing.outcome or StepOutcome.ok
                return
            item_input = MapItemInput(
                port=spec.mapped_port,
                src=spec.input_dir / item.slug,
                item=ItemInfo(
                    slug=item.slug, source=item.source, source_hash=item.source_hash
                ),
            )
            plan = AgentStepPlan(
                step_id=step_id,
                node_id=node.id,
                workdir=run_dir / "steps" / node.id / item.slug,
                agent=agent,
                agent_dir=run_dir / "snapshot" / "agents" / node.agent,
                model=model,
                registry=registry,
                inputs=[item_input, *shared],
                timeout_s=node.params.timeout_s or agent.defaults.timeout_s,
                gate_retries=node.params.gate_retries,
                infra_retries=node.params.infra_retries,
            )
            async with workers_sem, semaphore_for(model):
                step = await execute_agent_step(
                    plan,
                    runtime,
                    ledger,
                    on_event=emit_event,
                    clock=clock,
                    sleeper=sleeper,
                )
            results[item.slug] = step.outcome or StepOutcome.failed_infra

        ok_items = [i for i in manifest.items if i.status is CollectionStatus.ok]
        await asyncio.gather(*(run_item(i) for i in ok_items))

        # assemble the output collection (idempotent: node done only after this)
        ok_count = _assemble_map_output(node, spec, manifest, results, run_dir=run_dir)
        failed_count = len(manifest.items) - ok_count
        fail_node = ok_count < node.params.min_ok or (
            node.params.on_item_failure == "fail" and failed_count > 0
        )
        if fail_node:
            set_node(
                node.id,
                NodeStatus.failed,
                error=f"map: ok={ok_count} min_ok={node.params.min_ok} failed={failed_count}",
            )
            return NodeStatus.failed
        set_node(node.id, NodeStatus.done)
        return NodeStatus.done

    async def run_node(node_id: str) -> NodeStatus:
        node = nodes[node_id]
        if isinstance(node, BuiltinNode):
            return await run_builtin(node)
        assert isinstance(node, AgentNode)  # guaranteed by the up-front check
        if node.map is not None:
            return await run_map_node(node)
        plan = _agent_plan(
            node, run_dir=run_dir, agents=agents, registry=registry, nodes=nodes
        )
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

    # Seed from the ledger so resume continues rather than re-running (SPEC §10.5).
    # A fresh run has every node ``pending`` (Ledger.create), so this is a no-op
    # there; on resume, already-``done``/``reused`` nodes are pre-resolved and
    # skipped, ``failed``/``skipped`` nodes stay terminal (``--retry-failed`` flips
    # them back to ``pending`` in the ledger before this runs), and a node left
    # ``pending`` (incl. crash-recovered ``running → pending``) is re-executed —
    # a map node then reuses its already-``done`` element steps (§10.3).
    _TERMINAL = {
        NodeStatus.done: NodeStatus.done,
        NodeStatus.reused: NodeStatus.done,
        NodeStatus.failed: NodeStatus.failed,
        NodeStatus.skipped: NodeStatus.skipped,
    }
    pending: set[str] = set()
    resolved: dict[str, NodeStatus] = {}  # done | failed | skipped
    for nid in nodes:
        st = ledger.get_node(nid)
        mapped = _TERMINAL.get(st.status) if st is not None else None
        if mapped is None:
            pending.add(nid)
        else:
            resolved[nid] = mapped
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
