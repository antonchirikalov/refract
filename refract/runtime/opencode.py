"""OpencodeRuntime — real opencode adapter (SPEC §12).

Two halves:

* **Compilation** (:func:`compile_step`, covered by ``test_opencode_compile``):
  turns a snapshot agent package + step spec into the files opencode reads from
  a workdir — ``<workdir>/<AGENTS_SUBDIR>/<name>.md`` (frontmatter
  ``{model, tools}`` + body = system prompt) and ``<workdir>/opencode.json``
  (the model's provider, auto-approve permissions, and the MCP servers named by
  the agent's ``needs``). Secrets are ``{env:VAR}`` placeholders (I8).
* **Execution** (:class:`OpencodeRuntime`): one ``opencode serve`` per step with
  ``cwd`` = the step workdir (I1 confinement), driving the local HTTP API,
  emitting heartbeats and always killing the process. Not exercised by the
  automated suite (no real opencode, SPEC §18) — verified via
  ``docs/opencode-smoke.md``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

import yaml

from refract.models.config import McpFile, McpHttpServer, McpStdioServer, ProvidersFile
from refract.runtime.base import EventCallback, StepResult, StepSpec

# Pinned opencode version; a real run warns on mismatch (`opencode --version`).
# Verified against the 1.18.x headless HTTP API (GET /global/health; POST /session;
# POST /session/{id}/message -> {info, parts}); see docs/opencode-smoke.md.
OPENCODE_PINNED_VERSION = "1.18.4"
# Per-agent markdown dir opencode reads from the workdir. The exact leaf is
# version-dependent (``.opencode/agent`` vs ``.opencode/agents``); pinned here.
AGENTS_SUBDIR = ".opencode/agent"

# Base capability → opencode tool flags (SPEC §6 capabilities → §12 tools).
_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "read": ("read", "grep", "glob", "list"),
    "edit": ("write", "edit"),
    "bash": ("bash",),
    "webfetch": ("webfetch",),
    "vision": (),  # a model capability, not a tool
}


def _provider_of(model: str) -> str:
    return model.split("/", 1)[0]


def _agent_name(agent_dir: Path) -> str:
    """Bare agent name from a ``snapshot/agents/<name>@<ver>`` dir (§6)."""
    return agent_dir.name.split("@", 1)[0]


def tools_for_needs(needs: list[str]) -> dict[str, bool]:
    """Map an agent's ``needs`` to an opencode ``tools`` frontmatter block (§12).

    Base capabilities expand to their tool flags; ``mcp:<server>`` capabilities
    are wired through ``opencode.json`` (not here) so opencode exposes that
    server's tools. Unknown/vision capabilities contribute no tool flag.
    """
    tools: dict[str, bool] = {}
    for cap in needs:
        for tool in _TOOL_MAP.get(cap, ()):
            tools[tool] = True
    return tools


def _mcp_servers_for_needs(needs: list[str]) -> list[str]:
    return [cap[len("mcp:") :] for cap in needs if cap.startswith("mcp:")]


@dataclass(frozen=True)
class CompiledStep:
    """Paths written by :func:`compile_step` (SPEC §12)."""

    agent_md: Path
    opencode_json: Path


def render_agent_md(
    *, name: str, model: str, needs: list[str], system_prompt: str
) -> str:
    """The agent markdown: YAML frontmatter (model + tools) then the system prompt."""
    front = {"model": model, "tools": tools_for_needs(needs)}
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip("\n")
    body = system_prompt.rstrip("\n")
    return f"---\n{fm}\n---\n\n{body}\n"


def build_opencode_config(
    *, model: str, needs: list[str], providers: ProvidersFile, mcp: McpFile
) -> dict[str, object]:
    """The ``opencode.json`` document: model's provider + MCP servers from needs (§12).

    Secrets are referenced by env placeholder (``{env:VAR}``), never inlined —
    I8 keeps keys out of project folders/artifacts.
    """
    provider = _provider_of(model)
    # Auto-approve every permission opencode gates: the engine confines file
    # access to the workdir (I1, cwd), so interactive approval is neither
    # possible nor wanted in a headless run (SPEC §12).
    config: dict[str, object] = {
        "model": model,
        "permission": {
            "bash": "allow",
            "edit": "allow",
            "read": "allow",
            "webfetch": "allow",
            "websearch": "allow",
        },
    }
    pcfg = providers.providers.get(provider)
    if pcfg is not None:
        config["provider"] = {
            provider: {"options": {"apiKey": f"{{env:{pcfg.api_key_env}}}"}}
        }
    mcp_out: dict[str, object] = {}
    for server_name in _mcp_servers_for_needs(needs):
        server = mcp.servers.get(server_name)
        if server is None:
            continue
        if isinstance(server, McpStdioServer):
            mcp_out[server_name] = {
                "type": "local",
                "command": list(server.command),
                "environment": dict(server.env),
                "enabled": True,
            }
        elif isinstance(server, McpHttpServer):
            entry: dict[str, object] = {
                "type": "remote",
                "url": server.url,
                "enabled": True,
            }
            if server.token_env is not None:
                entry["headers"] = {
                    "Authorization": f"Bearer {{env:{server.token_env}}}"
                }
            mcp_out[server_name] = entry
    if mcp_out:
        config["mcp"] = mcp_out
    return config


def compile_step(
    spec: StepSpec, *, providers: ProvidersFile, mcp: McpFile
) -> CompiledStep:
    """Write ``<workdir>/<AGENTS_SUBDIR>/<name>.md`` + ``<workdir>/opencode.json`` (§12)."""
    name = _agent_name(spec.agent_dir)
    agent_dir = spec.workdir / AGENTS_SUBDIR
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_md = agent_dir / f"{name}.md"
    agent_md.write_text(
        render_agent_md(
            name=name,
            model=spec.model,
            needs=spec.needs,
            system_prompt=spec.system_prompt,
        ),
        encoding="utf-8",
    )
    opencode_json = spec.workdir / "opencode.json"
    config = build_opencode_config(
        model=spec.model, needs=spec.needs, providers=providers, mcp=mcp
    )
    opencode_json.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return CompiledStep(agent_md=agent_md, opencode_json=opencode_json)


def _free_port() -> int:
    """Grab a currently-free localhost TCP port (best effort)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _model_ref(model: str) -> dict[str, str]:
    provider, _, model_id = model.partition("/")
    return {"providerID": provider, "modelID": model_id}


class OpencodeRuntime:
    """Real opencode adapter (SPEC §12).

    One ``opencode serve`` process per step, launched with ``cwd`` = the step
    workdir so the agent's file access is confined there (I1). The agent package
    and ``opencode.json`` are compiled into the workdir first (:func:`compile_step`);
    the task prompt is sent over the local HTTP API selecting the compiled agent.
    A heartbeat event is emitted every ~10 s. The serve process is always killed
    (``finally`` + :meth:`close`), even on crash. The engine judges success by the
    gate over ``output/`` — not by ``StepResult`` (I9): here ``completed=False``
    means an infra failure (server/transport) worth retrying.

    Not covered by the automated suite (no real opencode; SPEC §18) — see
    ``docs/opencode-smoke.md`` for the manual smoke recipe.
    """

    def __init__(
        self,
        *,
        providers: ProvidersFile,
        mcp: McpFile,
        exe: str | None = None,
        health_timeout_s: float = 60.0,
        heartbeat_s: float = 10.0,
    ) -> None:
        self._providers = providers
        self._mcp = mcp
        self._exe = exe or shutil.which("opencode") or "opencode"
        self._health_timeout_s = health_timeout_s
        self._heartbeat_s = heartbeat_s
        self._procs: set[asyncio.subprocess.Process] = set()
        self._version_checked = False

    async def run_step(self, spec: StepSpec, on_event: EventCallback) -> StepResult:
        import httpx  # local import: keeps httpx off the hot import path

        await self._check_version(on_event)
        compile_step(spec, providers=self._providers, mcp=self._mcp)
        (spec.workdir / "output").mkdir(parents=True, exist_ok=True)

        port = _free_port()
        proc = await asyncio.create_subprocess_exec(
            self._exe,
            "serve",
            "--port",
            str(port),
            cwd=str(spec.workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._procs.add(proc)
        base = f"http://127.0.0.1:{port}"
        heartbeat: asyncio.Task[None] | None = None
        agent_events: list[dict[str, object]] = []
        try:
            async with httpx.AsyncClient(
                base_url=base, timeout=spec.timeout_s
            ) as client:
                await self._await_health(client)
                heartbeat = asyncio.create_task(self._heartbeat(spec.step_id, on_event))
                session = await client.post("/session", json={"title": spec.step_id})
                session.raise_for_status()
                session_id = session.json()["id"]
                name = _agent_name(spec.agent_dir)
                resp = await client.post(
                    f"/session/{session_id}/message",
                    json={
                        "agent": name,
                        "model": _model_ref(spec.model),
                        "parts": [{"type": "text", "text": spec.prompt}],
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                # 1.18.x message response: {info: {error?, cost, tokens, ...}, parts}
                info = body.get("info", {})
                text = "".join(
                    p.get("text", "")
                    for p in body.get("parts", [])
                    if p.get("type") == "text"
                )
                agent_events = [
                    {
                        "type": "log",
                        "step_id": spec.step_id,
                        "message": "opencode message complete",
                    }
                ]
                self._write_trace(spec, text, agent_events)
                error = info.get("error")
                return StepResult(
                    completed=True,
                    agent_error=json.dumps(error) if error else None,
                    usage={"cost": info.get("cost"), "tokens": info.get("tokens")},
                )
        except Exception as exc:  # server/transport failure → infra error (retryable)
            self._write_trace(spec, f"[opencode infra error] {exc}", agent_events)
            return StepResult(completed=False, agent_error=None)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
            await self._terminate(proc)
            self._procs.discard(proc)

    async def _check_version(self, on_event: EventCallback) -> None:
        if self._version_checked:
            return
        self._version_checked = True
        try:
            proc = await asyncio.create_subprocess_exec(
                self._exe,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            version = out.decode("utf-8", "replace").strip()
        except OSError as exc:
            on_event({"type": "log", "message": f"opencode not runnable: {exc}"})
            return
        if OPENCODE_PINNED_VERSION not in version:
            on_event(
                {
                    "type": "log",
                    "message": (
                        f"opencode version {version!r} != pinned "
                        f"{OPENCODE_PINNED_VERSION!r}; proceeding"
                    ),
                }
            )

    async def _await_health(self, client: object) -> None:
        import httpx

        assert isinstance(client, httpx.AsyncClient)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._health_timeout_s
        while loop.time() < deadline:
            try:
                r = await client.get("/global/health")
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)
        raise RuntimeError("opencode serve did not become healthy in time")

    async def _heartbeat(self, step_id: str, on_event: EventCallback) -> None:
        elapsed = 0.0
        try:
            while True:
                await asyncio.sleep(self._heartbeat_s)
                elapsed += self._heartbeat_s
                on_event(
                    {
                        "type": "heartbeat",
                        "step_id": step_id,
                        "payload": {"elapsed_s": int(elapsed)},
                    }
                )
        except asyncio.CancelledError:
            return

    def _write_trace(
        self, spec: StepSpec, raw: str, events: list[dict[str, object]]
    ) -> None:
        """Persist ``raw.txt`` + ``agent.events.jsonl`` in the workdir (I9)."""
        (spec.workdir / "raw.txt").write_text(raw, encoding="utf-8")
        (spec.workdir / "agent.events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
            encoding="utf-8",
        )

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
            await proc.wait()

    async def close(self) -> None:
        """Kill any lingering serve processes (crash-path safety)."""
        for proc in list(self._procs):
            await self._terminate(proc)
        self._procs.clear()
