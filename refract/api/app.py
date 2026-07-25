"""FastAPI REST/WS API for the refract engine (SPEC §15).

The API is a thin adapter over the existing CLI ``*_impl`` functions — it does
NOT reimplement the engine. ``run_impl``/``resume_impl`` are synchronous (they
call ``asyncio.run`` internally), so runs are launched in the background via
``asyncio.create_task(asyncio.to_thread(...))`` and the API returns the
engine-generated ``run_id`` immediately (202). ``GET /api/runs/{id}`` reads the
run's ``state.json`` (I7 — CLI/UI render only ``state.json`` + ``events.jsonl``).

Errors map to HTTP: ``ValidationFailed`` → 422, ``ActiveRunConflict`` → 409,
``UsageError`` → 400, missing project/run → 404. Provider API-key *values* are
never echoed (I8); only the env-var name + availability flag are exposed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi import (
    Body,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from refract.cli import (
    AppConfig,
    RuntimeFactory,
    UsageError,
    _active_run,
    _default_runtime_factory,
    _new_run_id,
    resolve_project,
    refract_home,
    resume_impl,
    run_impl,
    write_answer,
)
from refract.catalog import build_catalog
from refract.events import EVENTS_FILENAME, utcnow_iso
from refract.graph import load_agents, load_pipeline
from refract.models.ledger import RunState
from refract.registry import ArtifactRegistry
from refract.templates_lib import (
    TEMPLATES_SUBDIR,
    find_template,
    list_templates,
    template_metadata,
)

# Statuses at which no further events flow until the client acts — the WS closes.
# waiting_human is "paused for an answer", not truly finished, but no events come
# until a resume, so the events socket closes on it too.
_TERMINAL = {"completed", "failed", "cancelled", "waiting_human"}


# --- request / response models ----------------------------------------------


class CreateProjectRequest(BaseModel):
    """New project: optionally from a template, pointed at a documents folder.

    ``input`` may live anywhere — the documents folder is referenced, never copied
    (SPEC-UI §2). ``model`` sets ``defaults.model`` for the project.
    """

    name: str
    template: str | None = None
    input: str | None = None
    model: str | None = None


class SaveTemplateRequest(BaseModel):
    """Save a project's pipeline as a user template (SPEC-UI §5)."""

    name: str
    from_project: str
    pipeline: str


class RunSummary(BaseModel):
    run_id: str
    status: str
    pipeline: str
    created_at: str
    finished_at: str | None = None


class ValidationError(BaseModel):
    code: str
    node_id: str | None = None
    message: str


class ValidateResponse(BaseModel):
    ok: bool
    errors: list[ValidationError] = Field(default_factory=list)


class StartRunRequest(BaseModel):
    pipeline: str
    overrides: dict[str, str] | None = None
    reuse_from: str | None = None
    force: list[str] | None = None


class StartRunResponse(BaseModel):
    run_id: str


class AnswerRequest(BaseModel):
    step_id: str
    answer: str


class PipelineText(BaseModel):
    name: str
    yaml: str
    hash: str = ""  # sha256 of the text — the client's next base_hash (§19.2)


class PipelineWriteResponse(BaseModel):
    """Result of a pipeline write (SPEC §19.2)."""

    name: str
    committed: bool
    hash: str  # sha256 of what is now on disk
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)


class ProviderInfo(BaseModel):
    name: str
    api_key_env: str
    available: bool
    max_concurrent: int


class FsEntry(BaseModel):
    name: str
    is_dir: bool


# --- in-memory run registry --------------------------------------------------


@dataclass
class RunRecord:
    run_dir: Path
    project_id: str
    task: asyncio.Task[Any] | None = None
    status: str = "running"
    error: str | None = None


@dataclass
class _State:
    projects_root: Path
    app_config: AppConfig
    runtime_factory: RuntimeFactory
    clock: Callable[[], str]
    runs: dict[str, RunRecord] = field(default_factory=dict)


def create_app(
    *,
    projects_root: Path,
    app_config: AppConfig,
    runtime_factory: RuntimeFactory | None = None,
    clock: Callable[[], str] = utcnow_iso,
) -> FastAPI:
    """Build the refract REST/WS API app (SPEC §15).

    ``projects_root`` holds one directory per project (each with a
    ``project.yaml``). ``runtime_factory`` defaults to the real opencode
    runtime; tests inject a MockRuntime factory.
    """
    st = _State(
        projects_root=Path(projects_root),
        app_config=app_config,
        runtime_factory=runtime_factory or _default_runtime_factory,
        clock=clock,
    )
    api = FastAPI(title="refract", version="0.2")

    # --- helpers -------------------------------------------------------------

    def _project_dir(project_id: str) -> Path:
        pdir = st.projects_root / project_id
        if not (pdir / "project.yaml").exists():
            raise HTTPException(status_code=404, detail=f"no project {project_id!r}")
        return pdir

    def _find_run_dir(run_id: str) -> Path:
        rec = st.runs.get(run_id)
        if rec is not None and (rec.run_dir / "state.json").exists():
            return rec.run_dir
        # fall back to scanning projects_root/*/runs/{run_id}
        for pdir in st.projects_root.iterdir():
            candidate = pdir / "runs" / run_id
            if (candidate / "state.json").exists():
                return candidate
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")

    def _validate_text(
        project_dir: Path, name: str, text: str
    ) -> tuple[list[ValidationError], list[ValidationError]]:
        """Validate pipeline TEXT without touching the project's file (§19.2).

        The candidate is written to a temp dir and loaded from there, so a failed
        write never leaves a half-valid pipeline behind and a concurrent request
        cannot see a scratch file in ``pipelines/``.
        """
        from refract.cli import _build_context
        from refract.models.config import ProjectConfig

        raw = yaml.safe_load((project_dir / "project.yaml").read_text("utf-8")) or {}
        config = ProjectConfig.model_validate(raw)
        registry = ArtifactRegistry.load(st.app_config.library_path)
        agents, agent_errors = load_agents(st.app_config.library_path)
        ctx = _build_context(
            st.app_config,
            registry=registry,
            agents=agents,
            default_model=config.defaults.model,
            model_overrides={},
        )
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / f"{name}.yaml"
            candidate.write_text(text, encoding="utf-8")
            graph = load_pipeline(candidate, ctx)
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []
        for e in list(agent_errors) + list(graph.errors):
            item = ValidationError(
                code=getattr(e.code, "value", str(e.code)),
                node_id=getattr(e, "node_id", None),
                message=getattr(e, "message", str(e)),
            )
            if getattr(e.code, "is_warning", False):
                warnings.append(item)
            else:
                errors.append(item)
        return errors, warnings

    def _collect_errors(
        project_dir: Path, pipeline: str, overrides: dict[str, str]
    ) -> list[ValidationError]:
        proj = resolve_project(project_dir, pipeline)
        registry = ArtifactRegistry.load(st.app_config.library_path)
        agents, agent_errors = load_agents(st.app_config.library_path)
        from refract.cli import _build_context

        ctx = _build_context(
            st.app_config,
            registry=registry,
            agents=agents,
            default_model=proj.config.defaults.model,
            model_overrides=overrides,
        )
        graph = load_pipeline(proj.pipeline_path, ctx)
        out: list[ValidationError] = []
        for e in list(agent_errors) + list(graph.errors):
            if getattr(e.code, "is_warning", False):
                continue
            code = getattr(e.code, "value", str(e.code))
            out.append(
                ValidationError(
                    code=code,
                    node_id=getattr(e, "node_id", None),
                    message=getattr(e, "message", str(e)),
                )
            )
        return out

    def _artifact_base(run_dir: Path, step_id: str) -> Path:
        """Resolve a step's output dir (plain/builtin ``main/output`` or ``_out``)."""
        out = run_dir / "steps" / step_id / "main" / "output"
        if out.is_dir():
            return out
        alt = run_dir / "steps" / step_id / "_out"
        if alt.is_dir():
            return alt
        raise HTTPException(
            status_code=404, detail=f"no artifacts for step {step_id!r}"
        )

    # --- projects ------------------------------------------------------------

    @api.get("/api/projects")
    def list_projects() -> list[str]:
        if not st.projects_root.is_dir():
            return []
        return sorted(
            p.name for p in st.projects_root.iterdir() if (p / "project.yaml").exists()
        )

    @api.post("/api/projects", status_code=201)
    def create_project(req: CreateProjectRequest) -> dict[str, str]:
        """Create a project, optionally from a template (SPEC-UI §5).

        The documents folder is referenced, not copied: ``input`` goes into
        ``project.yaml`` as given, so it may point anywhere on disk. Without it the
        project gets its own empty ``input/``.
        """
        name = req.name.strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            raise HTTPException(status_code=400, detail=f"bad project name {name!r}")
        pdir = st.projects_root / name
        if pdir.exists():
            raise HTTPException(status_code=409, detail=f"project {name!r} exists")

        pipeline_text: str | None = None
        if req.template is not None:
            ref = find_template(
                req.template, st.app_config.library_path, refract_home()
            )
            if ref is None:
                available = ", ".join(
                    t.name
                    for t in list_templates(st.app_config.library_path, refract_home())
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown template {req.template!r} (available: {available})",
                )
            pipeline_text = ref.path.read_text("utf-8")

        (pdir / "pipelines").mkdir(parents=True)
        if pipeline_text is not None:
            (pdir / "pipelines" / f"{req.template}.yaml").write_text(
                pipeline_text, encoding="utf-8"
            )
        config: dict[str, Any] = {"version": "0.1", "name": name}
        if req.input:
            config["input"] = req.input
        else:
            (pdir / "input").mkdir()
            config["input"] = "./input"
        if req.model:
            config["defaults"] = {"model": req.model}
        (pdir / "project.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return {"id": name}

    @api.get("/api/projects/{project_id}/runs")
    def list_project_runs(project_id: str) -> list[RunSummary]:
        """Runs of a project, newest first — read from each ledger (I7)."""
        pdir = _project_dir(project_id)
        runs_dir = pdir / "runs"
        out: list[RunSummary] = []
        for candidate in (
            sorted(
                (p for p in runs_dir.iterdir() if (p / "state.json").exists()),
                reverse=True,
            )
            if runs_dir.is_dir()
            else []
        ):
            state = RunState.model_validate_json(
                (candidate / "state.json").read_text("utf-8")
            )
            out.append(
                RunSummary(
                    run_id=state.run_id,
                    status=state.status.value,
                    pipeline=state.pipeline,
                    created_at=state.created_at,
                    finished_at=state.finished_at,
                )
            )
        return out

    # --- templates -----------------------------------------------------------

    @api.get("/api/templates")
    def list_templates_endpoint() -> list[dict[str, Any]]:
        """Template gallery: shipped + user templates with derived metadata."""
        agents, _ = load_agents(st.app_config.library_path)
        return [
            template_metadata(ref, agents=agents)
            for ref in list_templates(st.app_config.library_path, refract_home())
        ]

    @api.post("/api/templates", status_code=201)
    def save_template(req: SaveTemplateRequest) -> dict[str, str]:
        """Save a project's pipeline as a user template (SPEC-UI §5)."""
        name = req.name.strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            raise HTTPException(status_code=400, detail=f"bad template name {name!r}")
        pdir = _project_dir(req.from_project)
        source = pdir / "pipelines" / f"{req.pipeline}.yaml"
        if not source.exists():
            raise HTTPException(
                status_code=404, detail=f"no pipeline {req.pipeline!r} in that project"
            )
        existing = find_template(name, st.app_config.library_path, refract_home())
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"template {name!r} already exists ({existing.source})",
            )
        target_dir = refract_home() / TEMPLATES_SUBDIR
        target_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(target_dir / f"{name}.yaml", source.read_text("utf-8"))
        return {"name": name, "source": "user"}

    @api.get("/api/projects/{project_id}/pipelines")
    def list_pipelines(project_id: str) -> list[str]:
        pdir = _project_dir(project_id)
        pipelines_dir = pdir / "pipelines"
        if not pipelines_dir.is_dir():
            return []
        return sorted(p.stem for p in pipelines_dir.glob("*.yaml"))

    @api.get("/api/projects/{project_id}/pipelines/{name}")
    def get_pipeline(project_id: str, name: str) -> PipelineText:
        pdir = _project_dir(project_id)
        path = pdir / "pipelines" / f"{name}.yaml"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"no pipeline {name!r}")
        text = path.read_text("utf-8")
        return PipelineText(name=name, yaml=text, hash=_text_hash(text))

    @api.put("/api/projects/{project_id}/pipelines/{name}")
    def put_pipeline(
        project_id: str,
        name: str,
        body: str = Body(..., media_type="text/plain"),
        allow_invalid: bool = Query(False),
        base_hash: str | None = Query(None),
    ) -> PipelineWriteResponse:
        """Replace a pipeline: verify, then commit atomically (SPEC §19.2).

        Blocking validation errors mean nothing is written — the editor gets the
        full report back. ``allow_invalid`` saves the draft anyway (and still
        reports). ``base_hash`` is optimistic locking against the text the client
        read; omit it and no such check happens.
        """
        pdir = _project_dir(project_id)
        active = _active_run(pdir / "runs")
        if active is not None:
            raise HTTPException(
                status_code=409, detail=f"project has an active run ({active})"
            )
        path = pdir / "pipelines" / f"{name}.yaml"
        if base_hash is not None and _text_hash(_read_or_empty(path)) != base_hash:
            raise HTTPException(
                status_code=409,
                detail="stale base_hash: the pipeline changed since you read it",
            )

        errors, warnings = _validate_text(pdir, name, body)
        if errors and not allow_invalid:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "pipeline not written: validation failed",
                    "errors": [e.model_dump() for e in errors],
                    "warnings": [w.model_dump() for w in warnings],
                },
            )
        path.parent.mkdir(exist_ok=True)
        _atomic_write(path, body)
        return PipelineWriteResponse(
            name=name,
            committed=True,
            hash=_text_hash(body),
            errors=errors,
            warnings=warnings,
        )

    @api.get("/api/catalog")
    def get_catalog() -> dict[str, Any]:
        """The authoring catalog: what a pipeline can be built from (SPEC §19.1)."""
        return build_catalog(st.app_config.library_path)

    @api.post("/api/projects/{project_id}/pipelines/{name}/validate")
    def validate_pipeline(project_id: str, name: str) -> ValidateResponse:
        pdir = _project_dir(project_id)
        try:
            errors = _collect_errors(pdir, name, {})
        except UsageError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return ValidateResponse(ok=not errors, errors=errors)

    # --- runs ----------------------------------------------------------------

    @api.post("/api/projects/{project_id}/runs", status_code=202)
    async def start_run(project_id: str, req: StartRunRequest) -> StartRunResponse:
        pdir = _project_dir(project_id)
        overrides = req.overrides or {}
        # Pre-flight synchronously so we can return proper HTTP codes; run_impl
        # repeats these checks in the background thread (idempotent).
        try:
            errors = _collect_errors(pdir, req.pipeline, overrides)
        except UsageError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"errors": [e.model_dump() for e in errors]},
            )
        active = _active_run(pdir / "runs")
        if active is not None:
            raise HTTPException(
                status_code=409, detail=f"run {active} is already active"
            )

        run_id = _unique_run_id(pdir / "runs", st.runs)
        run_dir = pdir / "runs" / run_id
        rec = RunRecord(run_dir=run_dir, project_id=project_id)
        st.runs[run_id] = rec

        async def _launch() -> None:
            try:
                status, _ = await asyncio.to_thread(
                    run_impl,
                    pdir,
                    pipeline=req.pipeline,
                    app=st.app_config,
                    model_overrides=overrides,
                    runtime_factory=st.runtime_factory,
                    run_id=run_id,
                    force_nodes=req.force,
                    reuse_run_id=req.reuse_from,
                    clock=st.clock,
                )
                rec.status = status.value
            except asyncio.CancelledError:
                rec.status = "cancelled"
                raise
            except Exception as e:  # noqa: BLE001 — surface as run status
                rec.status = "failed"
                rec.error = str(e)

        rec.task = asyncio.create_task(_launch())
        return StartRunResponse(run_id=run_id)

    @api.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> RunState:
        run_dir = _find_run_dir(run_id)
        return RunState.model_validate(
            json.loads((run_dir / "state.json").read_text("utf-8"))
        )

    @api.get("/api/runs/{run_id}/steps/{step_id}/artifacts")
    def list_artifacts(run_id: str, step_id: str) -> list[str]:
        run_dir = _find_run_dir(run_id)
        base = _artifact_base(run_dir, step_id)
        return sorted(
            p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()
        )

    @api.get("/api/runs/{run_id}/steps/{step_id}/artifacts/{path:path}")
    def get_artifact(run_id: str, step_id: str, path: str) -> FileResponse:
        run_dir = _find_run_dir(run_id)
        base = _artifact_base(run_dir, step_id).resolve()
        target = (base / path).resolve()
        if base not in target.parents and target != base:
            raise HTTPException(status_code=400, detail="path traversal rejected")
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"no artifact {path!r}")
        return FileResponse(target)

    @api.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, str]:
        rec = st.runs.get(run_id)
        if rec is None:
            # ensure the run at least exists on disk
            _find_run_dir(run_id)
            return {"status": "cancelled"}
        if rec.task is not None and not rec.task.done():
            rec.task.cancel()
        rec.status = "cancelled"
        return {"status": "cancelled"}

    @api.post("/api/runs/{run_id}/pause")
    def pause_run(run_id: str) -> None:
        raise HTTPException(status_code=501, detail="pause not implemented (phase 3)")

    @api.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict[str, str]:
        run_dir = _find_run_dir(run_id)
        rec = st.runs.get(run_id) or RunRecord(
            run_dir=run_dir, project_id=run_dir.parent.parent.name
        )
        st.runs[run_id] = rec

        async def _launch() -> None:
            try:
                status = await asyncio.to_thread(
                    resume_impl,
                    run_dir,
                    app=st.app_config,
                    runtime_factory=st.runtime_factory,
                    clock=st.clock,
                )
                rec.status = status.value
            except asyncio.CancelledError:
                rec.status = "cancelled"
                raise
            except Exception as e:  # noqa: BLE001
                rec.status = "failed"
                rec.error = str(e)

        rec.status = "running"
        rec.task = asyncio.create_task(_launch())
        return {"run_id": run_id}

    @api.post("/api/runs/{run_id}/answers")
    async def answer_run(run_id: str, req: AnswerRequest) -> dict[str, str]:
        run_dir = _find_run_dir(run_id)
        try:
            write_answer(run_dir, req.step_id, req.answer)
        except UsageError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        rec = st.runs.get(run_id) or RunRecord(
            run_dir=run_dir, project_id=run_dir.parent.parent.name
        )
        st.runs[run_id] = rec

        async def _launch() -> None:
            try:
                status = await asyncio.to_thread(
                    resume_impl,
                    run_dir,
                    app=st.app_config,
                    runtime_factory=st.runtime_factory,
                    clock=st.clock,
                )
                rec.status = status.value
            except asyncio.CancelledError:
                rec.status = "cancelled"
                raise
            except Exception as e:  # noqa: BLE001
                rec.status = "failed"
                rec.error = str(e)

        rec.status = "running"
        rec.task = asyncio.create_task(_launch())
        return {"run_id": run_id}

    # --- models + fs ---------------------------------------------------------

    @api.get("/api/models")
    def list_models() -> list[ProviderInfo]:
        available = st.app_config.available_providers
        out: list[ProviderInfo] = []
        for name, p in sorted(st.app_config.providers.providers.items()):
            out.append(
                ProviderInfo(
                    name=name,
                    api_key_env=p.api_key_env,
                    available=name in available,
                    max_concurrent=p.max_concurrent,
                )
            )
        return out

    @api.get("/api/fs/browse")
    def browse(path: str = Query("")) -> list[FsEntry]:
        root = st.projects_root.resolve()
        target = (root / path).resolve() if path else root
        if root not in target.parents and target != root:
            raise HTTPException(status_code=400, detail="outside sandbox")
        if not target.is_dir():
            raise HTTPException(status_code=404, detail=f"no directory {path!r}")
        return [
            FsEntry(name=child.name, is_dir=child.is_dir())
            for child in sorted(target.iterdir())
        ]

    # --- WS events -----------------------------------------------------------

    @api.websocket("/api/runs/{run_id}/events")
    async def stream_events(
        ws: WebSocket, run_id: str, from_seq: int = Query(0)
    ) -> None:
        await ws.accept()
        try:
            run_dir = _resolve_run_dir_ws(st, run_id)
        except FileNotFoundError:
            await ws.close(code=4404)
            return
        events_path = run_dir / EVENTS_FILENAME
        state_path = run_dir / "state.json"
        sent = max(from_seq - 1, 0)  # highest seq already delivered
        try:
            while True:
                sent = await _flush(events_path, sent, ws)
                if _terminal(state_path):
                    # final drain to catch any events appended after we checked
                    await _flush(events_path, sent, ws)
                    break
                await asyncio.sleep(0.5)
            await ws.close()
        except WebSocketDisconnect:
            return

    return api


# --- module-level file helpers ------------------------------------------------


def _text_hash(text: str) -> str:
    """sha256 of pipeline text — the editor's optimistic-locking token (§19.2)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_or_empty(path: Path) -> str:
    return path.read_text("utf-8") if path.exists() else ""


def _atomic_write(path: Path, text: str) -> None:
    """Write via tmp + os.replace, like the ledger (I3 / §19.2).

    A crash mid-write must not leave a truncated pipeline.yaml behind.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --- module-level WS helpers -------------------------------------------------


async def _flush(path: Path, sent: int, ws: WebSocket) -> int:
    """Send every event record with ``seq > sent``; return the new high-water seq."""
    if not path.exists():
        return sent
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        seq = int(record.get("seq", 0))
        if seq > sent:
            await ws.send_json(record)
            sent = seq
    return sent


def _terminal(state_path: Path) -> bool:
    if not state_path.exists():
        return False
    try:
        status = json.loads(state_path.read_text("utf-8")).get("status")
    except (OSError, json.JSONDecodeError):
        return False
    return status in _TERMINAL


def _resolve_run_dir_ws(st: _State, run_id: str) -> Path:
    rec = st.runs.get(run_id)
    if rec is not None and rec.run_dir.exists():
        return rec.run_dir
    for pdir in st.projects_root.iterdir():
        candidate = pdir / "runs" / run_id
        if candidate.exists():
            return candidate
    raise FileNotFoundError(run_id)


def _unique_run_id(runs_dir: Path, registry: dict[str, RunRecord]) -> str:
    base = _new_run_id()
    run_id = base
    n = 2
    while run_id in registry or (runs_dir / run_id).exists():
        run_id = f"{base}_{n}"
        n += 1
    return run_id
