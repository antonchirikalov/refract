"""Typer CLI (SPEC §14).

Commands: ``validate``, ``run``, ``status``, ``resume`` (Phase 0) plus
``agents list``. ``rerun`` is Phase 1. The synchronous CLI calls
``asyncio.run(...)`` at the boundary.

The command bodies are thin wrappers over pure ``*_impl`` functions that take an
explicit :class:`AppConfig` and a ``runtime_factory``. Tests drive those
directly with :class:`MockRuntime` and a fixed ``run_id``/clock — no network, no
``~/.refract``, no real opencode (I7 / SPEC §18 ``test_cli``).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import typer
import yaml

from refract.events import EventWriter, utcnow_iso
from refract.graph import ValidationContext, load_agents, load_pipeline
from refract.models.agent import AgentSpec
from refract.models.config import ProjectConfig, ProvidersFile
from refract.models.ledger import NodeStatus, RunState, RunStatus, StepStatus
from refract.models.pipeline import AgentNode, Pipeline
from refract.registry import ArtifactRegistry
from refract.runtime.base import AgentRuntime
from refract.scheduler import node_dependencies, run_pipeline
from refract.snapshot import build_snapshot
from refract.state import Ledger

# Exit codes (SPEC §14; test_cli asserts these).
EXIT_OK = 0
EXIT_RUN_FAILED = 1  # pipeline ran to a terminal `failed`
EXIT_VALIDATION = 2  # blocking validation errors
EXIT_CONFLICT = 3  # another active run in the project (.active.lock)
EXIT_USAGE = 4  # bad invocation / missing files

RuntimeFactory = Callable[["AppConfig", Pipeline], AgentRuntime]


# --- application configuration (~/.refract) ---------------------------------


@dataclass
class AppConfig:
    """Resolved application config: providers + library location (SPEC §7)."""

    library_path: Path
    providers: ProvidersFile = field(default_factory=ProvidersFile)

    @property
    def known_providers(self) -> set[str]:
        return set(self.providers.providers)

    @property
    def available_providers(self) -> set[str]:
        """A provider is available when its ``api_key_env`` var is non-empty (§7)."""
        return {
            name
            for name, p in self.providers.providers.items()
            if os.environ.get(p.api_key_env, "").strip()
        }

    @property
    def provider_limits(self) -> dict[str, int]:
        return {n: p.max_concurrent for n, p in self.providers.providers.items()}


def refract_home() -> Path:
    """``$REFRACT_HOME`` or ``~/.refract`` (overridable for tests)."""
    override = os.environ.get("REFRACT_HOME")
    return Path(override) if override else Path.home() / ".refract"


def load_app_config() -> AppConfig:
    """Load ``providers.yaml`` and resolve the library path (SPEC §7).

    ``library_path`` comes from ``$REFRACT_LIBRARY`` first, then
    ``providers.yaml``; missing → :class:`UsageError`.
    """
    home = refract_home()
    providers = ProvidersFile()
    providers_file = home / "providers.yaml"
    if providers_file.exists():
        raw = yaml.safe_load(providers_file.read_text("utf-8")) or {}
        providers = ProvidersFile.model_validate(raw)

    lib = os.environ.get("REFRACT_LIBRARY") or providers.library_path
    if not lib:
        raise UsageError(
            "no library_path configured "
            f"(set $REFRACT_LIBRARY or library_path in {providers_file})"
        )
    return AppConfig(library_path=Path(lib), providers=providers)


class UsageError(Exception):
    """A user-facing invocation error → exit code ``EXIT_USAGE``."""


# --- project + pipeline resolution ------------------------------------------


@dataclass
class LoadedProject:
    project_dir: Path
    config: ProjectConfig
    pipeline_name: str
    pipeline_path: Path

    @property
    def input_dir(self) -> Path:
        return (self.project_dir / self.config.input).resolve()

    @property
    def runs_dir(self) -> Path:
        return self.project_dir / "runs"


def resolve_project(project_dir: Path | str, pipeline: str | None) -> LoadedProject:
    """Load ``project.yaml`` and select the pipeline file (SPEC §7/§14).

    ``--pipeline`` is required when ``pipelines/`` holds more than one file.
    """
    project_dir = Path(project_dir)
    project_file = project_dir / "project.yaml"
    if not project_file.exists():
        raise UsageError(f"no project.yaml in {project_dir}")
    config = ProjectConfig.model_validate(
        yaml.safe_load(project_file.read_text("utf-8")) or {}
    )
    pipelines_dir = project_dir / "pipelines"
    available = (
        sorted(p for p in pipelines_dir.glob("*.yaml"))
        if pipelines_dir.is_dir()
        else []
    )
    if not available:
        raise UsageError(f"no pipelines in {pipelines_dir}")
    if pipeline is not None:
        path = pipelines_dir / f"{pipeline}.yaml"
        if not path.exists():
            raise UsageError(f"pipeline {pipeline!r} not found: {path}")
    elif len(available) == 1:
        path = available[0]
    else:
        names = ", ".join(p.stem for p in available)
        raise UsageError(f"--pipeline is required (choices: {names})")
    return LoadedProject(
        project_dir=project_dir,
        config=config,
        pipeline_name=path.stem,
        pipeline_path=path,
    )


def _load_snapshot(
    run_dir: Path, *, library_path: Path
) -> tuple[Pipeline, dict[str, AgentSpec]]:
    """Load the effective (resolved) pipeline + agents from a run's snapshot (§9).

    Execution and resume read ONLY the snapshot — ``resolved.yaml`` carries the
    effective ``model``/params, and ``snapshot/agents/<ref>/`` the locked packages.
    """
    snap = run_dir / "snapshot"
    pipeline = Pipeline.model_validate(
        yaml.safe_load((snap / "resolved.yaml").read_text("utf-8")) or {}
    )
    agents, _ = load_agents(snap)
    return pipeline, agents


def _build_context(
    app: AppConfig,
    *,
    registry: ArtifactRegistry,
    agents: dict[str, AgentSpec],
    default_model: str | None,
    model_overrides: dict[str, str],
) -> ValidationContext:
    return ValidationContext(
        registry=registry,
        agents=agents,
        known_providers=app.known_providers,
        available_providers=app.available_providers,
        default_model=default_model,
        model_overrides=model_overrides,
    )


# --- .active.lock (one active run per project, SPEC §9/§16) ------------------

_LOCK_NAME = ".active.lock"


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform liveness check (Windows + POSIX)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _active_run(runs_dir: Path) -> str | None:
    """Return the run_id of a live active run in the project, if any (§16.1)."""
    if not runs_dir.is_dir():
        return None
    for run_dir in sorted(runs_dir.iterdir()):
        lock = run_dir / _LOCK_NAME
        if not lock.exists():
            continue
        try:
            pid = int(lock.read_text("utf-8").strip() or "0")
        except ValueError:
            pid = 0
        if _pid_alive(pid):
            return run_dir.name
        lock.unlink(missing_ok=True)  # stale lock → reclaim
    return None


def _write_lock(run_dir: Path) -> None:
    (run_dir / _LOCK_NAME).write_text(str(os.getpid()), encoding="utf-8")


def _clear_lock(run_dir: Path) -> None:
    (run_dir / _LOCK_NAME).unlink(missing_ok=True)


# --- shared helpers ----------------------------------------------------------


def _parse_kv(items: Iterable[str], *, flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise UsageError(f"{flag} expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _apply_workers(pipeline: Pipeline, workers_for: dict[str, int]) -> None:
    """Apply ``--workers-for NODE=N`` onto the (already-validated) pipeline (§14)."""
    by_id = {n.id: n for n in pipeline.nodes}
    for nid, n in workers_for.items():
        node = by_id.get(nid)
        if node is None or not isinstance(node, AgentNode):
            raise UsageError(f"--workers-for: no agent node {nid!r}")
        node.params.workers = n


def _new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return "run_" + now.strftime("%Y%m%d_%H%M%S")


def _default_runtime_factory(app: AppConfig, pipeline: Pipeline) -> AgentRuntime:
    """Real runs use the opencode runtime — not wired until SPEC §12 lands."""
    raise UsageError(
        "the opencode runtime is not implemented yet (SPEC §12); "
        "runs are exercised via MockRuntime in tests"
    )


def _print_errors(errors: Sequence[object]) -> None:
    for e in errors:
        code = getattr(e, "code", None)
        node = getattr(e, "node_id", None)
        msg = getattr(e, "message", str(e))
        code_s = getattr(code, "value", str(code))
        where = f" [{node}]" if node else ""
        typer.echo(f"{code_s}{where}: {msg}", err=True)


# --- validate ----------------------------------------------------------------


def validate_impl(
    project_dir: Path | str,
    *,
    pipeline: str | None = None,
    app: AppConfig,
) -> int:
    """Load + validate a project's pipeline; return an exit code (SPEC §8/§14)."""
    proj = resolve_project(project_dir, pipeline)
    registry = ArtifactRegistry.load(app.library_path)
    agents, agent_errors = load_agents(app.library_path)
    ctx = _build_context(
        app,
        registry=registry,
        agents=agents,
        default_model=proj.config.defaults.model,
        model_overrides={},
    )
    graph = load_pipeline(proj.pipeline_path, ctx)
    errors = list(agent_errors) + list(graph.errors)
    warnings = [e for e in errors if getattr(e.code, "is_warning", False)]
    blocking = [e for e in errors if not getattr(e.code, "is_warning", False)]
    _print_errors(errors)
    if blocking:
        typer.echo(
            f"INVALID: {len(blocking)} error(s), {len(warnings)} warning(s)", err=True
        )
        return EXIT_VALIDATION
    typer.echo(f"OK: {proj.pipeline_name} ({len(warnings)} warning(s))")
    return EXIT_OK


# --- run ---------------------------------------------------------------------


def run_impl(
    project_dir: Path | str,
    *,
    pipeline: str | None = None,
    app: AppConfig,
    model_overrides: dict[str, str] | None = None,
    workers_for: dict[str, int] | None = None,
    runtime_factory: RuntimeFactory = _default_runtime_factory,
    run_id: str | None = None,
    force_nodes: list[str] | None = None,
    reuse_run_id: str | None = None,
    clock: Callable[[], str] = utcnow_iso,
) -> tuple[RunStatus, Path]:
    """Validate, snapshot and execute a pipeline; return ``(status, run_dir)``.

    Enforces one active run per project via ``.active.lock`` (§16.1). The runtime
    is built by ``runtime_factory`` so tests inject :class:`MockRuntime`. When
    ``reuse_run_id`` is given (via :func:`rerun_impl`) unchanged nodes are reused
    from that prior run and ``force_nodes`` seeds the recompute set (SPEC §10.5).
    """
    model_overrides = model_overrides or {}
    proj = resolve_project(project_dir, pipeline)
    registry = ArtifactRegistry.load(app.library_path)
    agents, agent_errors = load_agents(app.library_path)
    ctx = _build_context(
        app,
        registry=registry,
        agents=agents,
        default_model=proj.config.defaults.model,
        model_overrides=model_overrides,
    )
    graph = load_pipeline(proj.pipeline_path, ctx)
    blocking = [
        e
        for e in (list(agent_errors) + list(graph.errors))
        if not getattr(e.code, "is_warning", False)
    ]
    if graph.pipeline is None or blocking:
        _print_errors(list(agent_errors) + list(graph.errors))
        raise ValidationFailed()
    pipeline_obj = graph.pipeline
    if workers_for:
        _apply_workers(pipeline_obj, workers_for)

    for nid in force_nodes or []:
        if nid not in {n.id for n in pipeline_obj.nodes}:
            raise UsageError(f"--from: no node {nid!r} in pipeline")

    active = _active_run(proj.runs_dir)
    if active is not None:
        raise ActiveRunConflict(active)

    reuse_run_dir = proj.runs_dir / reuse_run_id if reuse_run_id else None
    if reuse_run_dir is not None and not (reuse_run_dir / "state.json").exists():
        raise UsageError(f"--reuse: run {reuse_run_id!r} not found in {proj.runs_dir}")

    run_id = run_id or _new_run_id()
    run_dir = proj.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    build_snapshot(
        run_dir,
        pipeline_path=proj.pipeline_path,
        pipeline=pipeline_obj,
        library_path=app.library_path,
        agents=agents,
        overrides=model_overrides,
        default_model=proj.config.defaults.model,
    )
    # Execute from the snapshot (I7/§9): resolved.yaml carries effective models.
    exec_pipeline, exec_agents = _load_snapshot(run_dir, library_path=app.library_path)
    ledger = Ledger.create(
        run_dir,
        run_id=run_id,
        pipeline=proj.pipeline_name,
        node_ids=[n.id for n in exec_pipeline.nodes],
        created_at=clock(),
        reuse_from=reuse_run_id,
        force_nodes=force_nodes,
    )
    events = EventWriter(run_dir, clock=clock)
    runtime = runtime_factory(app, exec_pipeline)

    verb = f"rerun {run_id} (reuse {reuse_run_id})" if reuse_run_id else f"run {run_id}"
    typer.echo(f"{verb}: {proj.pipeline_name} ({len(exec_pipeline.nodes)} nodes)")
    _write_lock(run_dir)
    try:
        status = asyncio.run(
            run_pipeline(
                run_dir,
                pipeline=exec_pipeline,
                agents=exec_agents,
                registry=registry,
                runtime=runtime,
                ledger=ledger,
                events=events,
                provider_limits=app.provider_limits,
                project_input_dir=proj.input_dir,
                reuse_run_dir=reuse_run_dir,
                clock=clock,
            )
        )
    finally:
        asyncio.run(runtime.close())
        _clear_lock(run_dir)
    _print_run_summary(ledger)
    return status, run_dir


def _resolve_reuse_run(runs_dir: Path, reuse: str) -> str:
    """Resolve ``--reuse RUN|last`` to a concrete run id (SPEC §14)."""
    if reuse != "last":
        return reuse
    candidates = sorted(
        p.name
        for p in (runs_dir.iterdir() if runs_dir.is_dir() else [])
        if p.is_dir() and (p / "state.json").exists()
    )
    if not candidates:
        raise UsageError(f"--reuse last: no prior runs in {runs_dir}")
    return candidates[-1]


def rerun_impl(
    project_dir: Path | str,
    *,
    from_node: str,
    reuse: str = "last",
    pipeline: str | None = None,
    app: AppConfig,
    runtime_factory: RuntimeFactory = _default_runtime_factory,
    run_id: str | None = None,
    clock: Callable[[], str] = utcnow_iso,
) -> tuple[RunStatus, Path]:
    """Rerun-from-node: a new run reusing unchanged nodes from a prior run (§10.5/§14)."""
    proj = resolve_project(project_dir, pipeline)
    reuse_run_id = _resolve_reuse_run(proj.runs_dir, reuse)
    return run_impl(
        project_dir,
        pipeline=pipeline,
        app=app,
        runtime_factory=runtime_factory,
        run_id=run_id,
        force_nodes=[from_node],
        reuse_run_id=reuse_run_id,
        clock=clock,
    )


# --- resume ------------------------------------------------------------------


def resume_impl(
    run_dir: Path | str,
    *,
    app: AppConfig,
    retry_failed: bool = False,
    force_step: str | None = None,
    runtime_factory: RuntimeFactory = _default_runtime_factory,
    clock: Callable[[], str] = utcnow_iso,
) -> RunStatus:
    """Resume a run from its snapshot; execution reads ONLY the snapshot (§9/§10.5)."""
    run_dir = Path(run_dir)
    if not (run_dir / "state.json").exists():
        raise UsageError(f"no state.json in {run_dir}")
    pipeline_obj, agents = _load_snapshot(run_dir, library_path=app.library_path)
    registry = ArtifactRegistry.load(app.library_path)

    active = _active_run(run_dir.parent)  # one active run per project (§16.1)
    if active is not None and active != run_dir.name:
        raise ActiveRunConflict(active)

    ledger = Ledger.load(run_dir)  # crash recovery: running → pending
    if force_step is not None:
        _force_step(ledger, run_dir, force_step, pipeline_obj)
    if retry_failed:
        _retry_failed(ledger)
    ledger.save()

    events = EventWriter(run_dir, clock=clock)
    runtime = runtime_factory(app, pipeline_obj)
    _write_lock(run_dir)
    try:
        status = asyncio.run(
            run_pipeline(
                run_dir,
                pipeline=pipeline_obj,
                agents=agents,
                registry=registry,
                runtime=runtime,
                ledger=ledger,
                events=events,
                provider_limits=app.provider_limits,
                project_input_dir=_recover_project_input(run_dir),
                clock=clock,
            )
        )
    finally:
        asyncio.run(runtime.close())
        _clear_lock(run_dir)
    _print_run_summary(ledger)
    return status


def _recover_project_input(run_dir: Path) -> Path | None:
    """Best-effort project input dir for resume: ``<project>/runs/<run>`` → project.

    Needed when a builtin that reads project input (e.g. scanner) must re-run on
    resume; the snapshot doesn't carry project input, but the run dir's location
    reveals the project root. ``None`` if the layout doesn't match.
    """
    project_dir = run_dir.parent.parent
    project_file = project_dir / "project.yaml"
    if run_dir.parent.name != "runs" or not project_file.exists():
        return None
    config = ProjectConfig.model_validate(
        yaml.safe_load(project_file.read_text("utf-8")) or {}
    )
    return (project_dir / config.input).resolve()


def _retry_failed(ledger: Ledger) -> None:
    """``--retry-failed``: failed steps → pending and failed nodes → pending (§10.5)."""
    ledger.reset_failed_steps()
    for nid in ledger.node_ids():
        node = ledger.get_node(nid)
        if node is not None and node.status in (NodeStatus.failed, NodeStatus.skipped):
            ledger.set_node_status(nid, NodeStatus.pending, error=None)


def _force_step(
    ledger: Ledger, run_dir: Path, step_id: str, pipeline: Pipeline
) -> None:
    """``--force-step STEP_ID``: archive the step, reset it + its node + all
    downstream nodes to pending so their outputs are rebuilt on resume (§10.5)."""
    step = ledger.get_step(step_id)
    if step is None:
        raise UsageError(f"--force-step: unknown step {step_id!r}")
    node_id, _, leaf = step_id.partition(":")
    step_dir = run_dir / "steps" / node_id / (leaf or "main")
    if step_dir.is_dir():
        _archive_step_dir(step_dir)
    ledger.set_step(step_id, node=step.node, status=StepStatus.pending)
    for nid in {step.node, *_descendants(pipeline, step.node)}:
        node = ledger.get_node(nid)
        if node is not None:
            ledger.set_node_status(nid, NodeStatus.pending, error=None)


def _descendants(pipeline: Pipeline, node_id: str) -> set[str]:
    """All nodes transitively downstream of ``node_id`` (edges via the graph deps)."""
    deps = node_dependencies(pipeline)  # child -> set(parents)
    children: dict[str, set[str]] = {n.id: set() for n in pipeline.nodes}
    for child, parents in deps.items():
        for parent in parents:
            children.setdefault(parent, set()).add(child)
    out: set[str] = set()
    stack = list(children.get(node_id, set()))
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, set()))
    return out


def _archive_step_dir(step_dir: Path) -> None:
    """Move a step dir under ``attempts/<n>/`` without clobbering (mirrors §10.2)."""
    attempts = step_dir / "attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    n = 1
    while (attempts / str(n)).exists():
        n += 1
    dest = attempts / str(n)
    dest.mkdir()
    for child in sorted(step_dir.iterdir()):
        if child.name == "attempts":
            continue
        child.rename(dest / child.name)


# --- status ------------------------------------------------------------------


def status_impl(run_dir: Path | str) -> int:
    typer.echo(render_status(run_dir))
    return EXIT_OK


def render_status(run_dir: Path | str) -> str:
    """Render the run status table from ``state.json`` only (I7)."""
    run_dir = Path(run_dir)
    state_file = run_dir / "state.json"
    if not state_file.exists():
        raise UsageError(f"no state.json in {run_dir}")
    state = RunState.model_validate(json.loads(state_file.read_text("utf-8")))
    lines = [
        f"run:      {state.run_id}",
        f"pipeline: {state.pipeline}",
        f"status:   {state.status.value}",
        "nodes:",
    ]
    for nid, node in state.nodes.items():
        err = f"  ({node.error})" if node.error else ""
        lines.append(f"  {nid:<20} {node.status.value}{err}")
    if state.steps:
        lines.append("steps:")
        for sid, step in state.steps.items():
            out = f" {step.outcome.value}" if step.outcome else ""
            lines.append(f"  {sid:<28} {step.status.value}{out} (tries={step.tries})")
    return "\n".join(lines)


def _print_run_summary(ledger: Ledger) -> None:
    for nid in ledger.node_ids():
        node = ledger.get_node(nid)
        if node is not None:
            typer.echo(f"  {nid:<20} {node.status.value}")


# --- exceptions mapped to exit codes ----------------------------------------


class ValidationFailed(Exception):
    pass


class ActiveRunConflict(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


# --- Typer wiring ------------------------------------------------------------

app = typer.Typer(
    add_completion=False, help="refract — declarative agent pipeline engine"
)
agents_app = typer.Typer(help="agent package commands")
app.add_typer(agents_app, name="agents")


def _run_cli(fn: Callable[[], int]) -> None:
    """Run a command body, mapping known errors to exit codes."""
    try:
        raise typer.Exit(code=fn())
    except UsageError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from e
    except ValidationFailed as e:
        typer.echo("INVALID: pipeline has blocking errors", err=True)
        raise typer.Exit(code=EXIT_VALIDATION) from e
    except ActiveRunConflict as e:
        typer.echo(f"error: run {e.run_id} is already active in this project", err=True)
        raise typer.Exit(code=EXIT_CONFLICT) from e


@app.command()
def validate(
    project_dir: Path = typer.Argument(..., help="project directory"),
    pipeline: str | None = typer.Option(None, "--pipeline", help="pipeline name"),
) -> None:
    """Validate a project's pipeline (exit 0 ok, 2 invalid)."""
    _run_cli(
        lambda: validate_impl(project_dir, pipeline=pipeline, app=load_app_config())
    )


@app.command()
def run(
    project_dir: Path = typer.Argument(..., help="project directory"),
    pipeline: str | None = typer.Option(None, "--pipeline"),
    model_for: list[str] = typer.Option([], "--model-for", help="KEY=MODEL"),
    workers_for: list[str] = typer.Option([], "--workers-for", help="NODE=N"),
) -> None:
    """Run a pipeline end-to-end."""

    def body() -> int:
        overrides = _parse_kv(model_for, flag="--model-for")
        workers = {
            k: int(v) for k, v in _parse_kv(workers_for, flag="--workers-for").items()
        }
        status, _ = run_impl(
            project_dir,
            pipeline=pipeline,
            app=load_app_config(),
            model_overrides=overrides,
            workers_for=workers,
        )
        return EXIT_OK if status is RunStatus.completed else EXIT_RUN_FAILED

    _run_cli(body)


@app.command()
def rerun(
    project_dir: Path = typer.Argument(..., help="project directory"),
    from_node: str = typer.Option(..., "--from", help="node id to recompute from"),
    reuse: str = typer.Option("last", "--reuse", help="RUN id or 'last'"),
    pipeline: str | None = typer.Option(None, "--pipeline"),
) -> None:
    """Rerun from a node, reusing unchanged upstream nodes from a prior run."""

    def body() -> int:
        status_, _ = rerun_impl(
            project_dir,
            from_node=from_node,
            reuse=reuse,
            pipeline=pipeline,
            app=load_app_config(),
        )
        return EXIT_OK if status_ is RunStatus.completed else EXIT_RUN_FAILED

    _run_cli(body)


@app.command()
def status(run_dir: Path = typer.Argument(..., help="run directory")) -> None:
    """Print a run's status from state.json."""
    _run_cli(lambda: status_impl(run_dir))


@app.command()
def resume(
    run_dir: Path = typer.Argument(..., help="run directory"),
    retry_failed: bool = typer.Option(False, "--retry-failed"),
    force_step: str | None = typer.Option(None, "--force-step", help="STEP_ID"),
) -> None:
    """Resume a run from its snapshot."""

    def body() -> int:
        status_ = resume_impl(
            run_dir,
            app=load_app_config(),
            retry_failed=retry_failed,
            force_step=force_step,
        )
        return EXIT_OK if status_ is RunStatus.completed else EXIT_RUN_FAILED

    _run_cli(body)


@agents_app.command("list")
def agents_list() -> None:
    """List agent packages found in the library."""

    def body() -> int:
        app_cfg = load_app_config()
        agents, errors = load_agents(app_cfg.library_path)
        _print_errors(list(errors))
        for ref in sorted(agents):
            spec = agents[ref]
            typer.echo(
                f"{ref:<28} {spec.description.strip().splitlines()[0] if spec.description else ''}"
            )
        return EXIT_OK

    _run_cli(body)


if __name__ == "__main__":
    app()
