"""The ONE step lifecycle (SPEC §10.2).

Materialize inputs (I1) → assemble prompt (§11) → run the runtime with a
timeout (infra-error backoff retries, counter separate from the gate) → HITL
check (phases 0–2: a valid question artifact → failed_agent) → gate (existence
+ schema + rules); on gate failure, archive the attempt to ``attempts/<n>/``
and retry with gate_feedback up to ``gate_retries`` extra times → done/ok.

Meta-nodes (loop/select) and map REUSE this; never duplicate it. Timestamps and
sleep are injectable so tests stay deterministic and network-free.
"""

from __future__ import annotations

import asyncio
import json
import random
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from refract.artifacts import (
    GatePort,
    GateReport,
    artifact_path,
    materialize_collection,
    materialize_dir_or_any,
    materialize_file,
    materialize_map_item,
    run_gate,
    write_gate_report,
)
from refract.models.agent import AgentSpec
from refract.models.ledger import StepOutcome, StepState, StepStatus
from refract.models.types import ItemInfo
from refract.prompt import RevisionContext, build_task_prompt
from refract.registry import ArtifactRegistry, ResolvedType
from refract.runtime.base import AgentRuntime, EventCallback, StepResult, StepSpec
from refract.state import Ledger

_QUESTION_TYPE = "question@v1"
_ARCHIVED = ("prompt.md", "raw.txt", "agent.events.jsonl", "gate_report.json")


# --- input specs (what to materialize into the step workdir) ---------------


@dataclass(frozen=True)
class FileInput:
    port: str
    src: Path
    rtype: ResolvedType


@dataclass(frozen=True)
class DirAnyInput:
    port: str
    src: Path


@dataclass(frozen=True)
class CollectionInput:
    port: str
    src: Path


@dataclass(frozen=True)
class MapItemInput:
    port: str
    src: Path
    item: ItemInfo


InputSpec = FileInput | DirAnyInput | CollectionInput | MapItemInput


@dataclass
class AgentStepPlan:
    """Everything needed to run one agent step (SPEC §10.2)."""

    step_id: str
    node_id: str
    workdir: Path
    agent: AgentSpec
    agent_dir: Path
    model: str
    registry: ArtifactRegistry
    inputs: list[InputSpec] = field(default_factory=list)
    timeout_s: int = 3600
    gate_retries: int = 2
    infra_retries: int = 2
    revision: RevisionContext | None = None


# --- helpers ----------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backoff_delay(infra_attempt: int) -> float:
    """base 2s, ×2, jitter, max 60s (SPEC §10.2 step 3)."""
    base = min(60.0, 2.0 * (2 ** (infra_attempt - 1)))
    return float(base * (0.5 + random.random() * 0.5))


def _gate_ports(agent: AgentSpec, registry: ArtifactRegistry) -> list[GatePort]:
    ports: list[GatePort] = []
    for p in agent.produces:
        rtype = registry.get(p.type)
        if rtype is not None:
            ports.append(GatePort(port=p.port, rtype=rtype, optional=p.optional))
    return ports


def _has_valid_question(
    agent: AgentSpec, registry: ArtifactRegistry, output_dir: Path
) -> bool:
    """HITL detection (SPEC §10.2 step 4): a valid question@v1 artifact present."""
    qtype = registry.get(_QUESTION_TYPE)
    if qtype is None:
        return False
    for p in agent.produces:
        if not (p.optional and p.type == _QUESTION_TYPE):
            continue
        path = artifact_path(output_dir, p.port, qtype)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # validate_json returns a list of problems; empty means the artifact is valid
        if qtype.validate_json(data) == []:
            return True
    return False


def _format_feedback(report: GateReport) -> str:
    lines = [
        f"- {pr.port}: {problem}"
        for pr in report.ports
        if not pr.ok
        for problem in pr.problems
    ]
    return "\n".join(lines)


def _materialize(inputs: list[InputSpec], input_root: Path) -> None:
    for spec in inputs:
        if isinstance(spec, FileInput):
            materialize_file(spec.src, input_root, spec.port, spec.rtype)
        elif isinstance(spec, DirAnyInput):
            materialize_dir_or_any(spec.src, input_root, spec.port)
        elif isinstance(spec, CollectionInput):
            materialize_collection(spec.src, input_root, spec.port)
        elif isinstance(spec, MapItemInput):
            materialize_map_item(spec.src, input_root, spec.port, spec.item)


def _archive_attempt(workdir: Path, n: int) -> None:
    """Move the completed attempt's artifacts to ``attempts/<n>/`` (SPEC §10.2)."""
    dest = workdir / "attempts" / str(n)
    # never nest into or clobber an existing archive (resume / re-exec safety)
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"attempt archive already populated: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    for name in _ARCHIVED:
        src = workdir / name
        if src.exists():
            shutil.move(str(src), str(dest / name))
    output = workdir / "output"
    if output.exists():
        shutil.move(str(output), str(dest / "output"))


def _system_prompt(agent_dir: Path) -> str:
    path = agent_dir / "prompt.md"
    return path.read_text("utf-8") if path.exists() else ""


# --- the lifecycle ----------------------------------------------------------


async def execute_agent_step(
    plan: AgentStepPlan,
    runtime: AgentRuntime,
    ledger: Ledger,
    *,
    on_event: EventCallback | None = None,
    clock: Callable[[], str] = _utcnow_iso,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> StepState:
    """Run one agent step to a terminal outcome, persisting to the ledger (§10.2)."""
    emit = on_event or (lambda _e: None)
    workdir = Path(plan.workdir)
    output_dir = workdir / "output"
    input_root = workdir / "input"

    started_at = clock()
    ledger.set_step(
        plan.step_id,
        node=plan.node_id,
        status=StepStatus.running,
        tries=0,
        started_at=started_at,
    )
    emit(
        {
            "type": "step_state_changed",
            "step_id": plan.step_id,
            "payload": {"from": "pending", "to": "running"},
        }
    )

    input_root.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _materialize(plan.inputs, input_root)  # inputs are immutable across gate retries

    gate_ports = _gate_ports(plan.agent, plan.registry)
    system_prompt = _system_prompt(plan.agent_dir)

    def finish(
        outcome: StepOutcome, *, tries: int, error: str | None = None
    ) -> StepState:
        status = StepStatus.done if outcome is StepOutcome.ok else StepStatus.failed
        ledger.set_step(
            plan.step_id,
            node=plan.node_id,
            status=status,
            outcome=outcome,
            tries=tries,
            started_at=started_at,
            finished_at=clock(),
            error=error,
        )
        emit(
            {
                "type": "step_state_changed",
                "step_id": plan.step_id,
                "payload": {
                    "from": "running",
                    "to": status.value,
                    "outcome": outcome.value,
                },
            }
        )
        step = ledger.get_step(plan.step_id)
        assert step is not None
        return step

    # tries counts COMPLETED gate attempts; timeout/failed_infra report the
    # count reached before the terminal failure (0 if it never completed a run).
    tries = 0
    gate_feedback: str | None = None
    while True:
        if tries > 0:  # gate retry: archive the prior completed attempt first
            _archive_attempt(workdir, tries)
            output_dir.mkdir(parents=True, exist_ok=True)

        task_prompt = build_task_prompt(
            agent=plan.agent,
            registry=plan.registry,
            workdir=workdir,
            revision=plan.revision,
            gate_feedback=gate_feedback,
        )
        full_prompt = (
            f"{system_prompt}\n\n{task_prompt}" if system_prompt else task_prompt
        )
        (workdir / "prompt.md").write_text(full_prompt, encoding="utf-8")

        spec = StepSpec(
            step_id=plan.step_id,
            agent_dir=plan.agent_dir,
            model=plan.model,
            workdir=workdir,
            prompt=task_prompt,
            system_prompt=system_prompt,
            needs=list(plan.agent.needs),
            timeout_s=plan.timeout_s,
        )

        # step 3: run with timeout + infra-error backoff (separate counter)
        result = await _run_with_infra_retries(
            runtime, spec, plan.infra_retries, emit, sleeper
        )
        if result is None:
            return finish(StepOutcome.timeout, tries=tries, error="timeout")
        if not result.completed:
            return finish(
                StepOutcome.failed_infra, tries=tries, error="infra retries exhausted"
            )

        tries += 1

        if result.agent_error is not None:
            return finish(
                StepOutcome.failed_agent, tries=tries, error=result.agent_error
            )

        # step 4: HITL not supported in phases 0–2
        if _has_valid_question(plan.agent, plan.registry, output_dir):
            return finish(
                StepOutcome.failed_agent,
                tries=tries,
                error="interactive not supported yet",
            )

        # step 5: gate
        report = run_gate(output_dir, gate_ports)
        write_gate_report(workdir, report)
        if report.ok:
            return finish(StepOutcome.ok, tries=tries)
        if tries >= plan.gate_retries + 1:
            return finish(
                StepOutcome.failed_validation,
                tries=tries,
                error=_format_feedback(report),
            )
        gate_feedback = _format_feedback(report)


async def _run_with_infra_retries(
    runtime: AgentRuntime,
    spec: StepSpec,
    infra_retries: int,
    emit: EventCallback,
    sleeper: Callable[[float], Awaitable[None]],
) -> StepResult | None:
    """Run the step; retry infra errors with backoff. None means timeout (§10.2)."""
    infra_attempt = 0
    while True:
        try:
            result = await asyncio.wait_for(
                runtime.run_step(spec, emit), timeout=spec.timeout_s
            )
        except asyncio.TimeoutError:
            return None
        if result.completed:
            return result
        infra_attempt += 1
        if infra_attempt > infra_retries:
            return result
        await sleeper(_backoff_delay(infra_attempt))
