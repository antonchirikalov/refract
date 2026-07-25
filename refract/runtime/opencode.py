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
import contextlib
import json
import shutil
import socket
import sys
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
        options: dict[str, object] = {"apiKey": f"{{env:{pcfg.api_key_env}}}"}
        if pcfg.base_url:
            options["baseURL"] = pcfg.base_url
        pblock: dict[str, object] = {"options": options}
        if pcfg.npm:  # custom / OpenAI-compatible providers need the ai-sdk package
            pblock["npm"] = pcfg.npm
        if pcfg.models:  # opencode wants explicit model entries for such providers
            pblock["models"] = {m: {} for m in pcfg.models}
        config["provider"] = {provider: pblock}
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


class _TurnSnapshot:
    """Best known state of the agent's turn, refreshed while it runs.

    Filled from ``GET /session/{id}/message`` polls and finally from the POST
    body if that ever returns. Keeps whichever parts list is richer, so a POST
    body that carries none cannot erase what polling already saw.
    """

    def __init__(self) -> None:
        self.info: dict[str, object] = {}
        self.parts: list[dict[str, object]] = []

    @property
    def text(self) -> str:
        return "".join(
            str(p.get("text", ""))
            for p in self.parts
            if isinstance(p, dict) and p.get("type") == "text"
        )

    @property
    def completed(self) -> bool:
        """True once the server reports the assistant turn finished."""
        time_info = self.info.get("time")
        return bool(isinstance(time_info, dict) and time_info.get("completed"))

    def absorb(self, info: object, parts: object) -> None:
        if isinstance(info, dict):
            self.info = info
        if isinstance(parts, list) and len(parts) >= len(self.parts):
            self.parts = [p for p in parts if isinstance(p, dict)]


def _events_from_parts(
    step_id: str,
    parts: list[dict[str, object]],
    *,
    fallback: str = "opencode message complete",
) -> list[dict[str, object]]:
    """Turn an opencode message's parts into trace events (I9 / SPEC §9).

    Tool parts become ``tool_call`` events (so the real tool invocations show up
    in events.jsonl and agent.events.jsonl); text/reasoning become ``log`` events.
    Robust to unknown part shapes — never raises.
    """
    events: list[dict[str, object]] = []
    for part in parts:
        ptype = part.get("type") if isinstance(part, dict) else None
        if ptype == "tool":
            raw_state = part.get("state")
            state = raw_state if isinstance(raw_state, dict) else {}
            events.append(
                {
                    "type": "tool_call",
                    "step_id": step_id,
                    "payload": {
                        "tool": part.get("tool") or part.get("name") or "?",
                        "summary": str(state.get("status") or state.get("title") or "")[
                            :200
                        ],
                    },
                }
            )
        elif ptype in ("text", "reasoning"):
            snippet = str(part.get("text", ""))[:500]
            if snippet:
                events.append(
                    {
                        "type": "log",
                        "step_id": step_id,
                        "payload": {"level": "info", "message": f"[{ptype}] {snippet}"},
                    }
                )
    if not events:
        events.append(
            {
                "type": "log",
                "step_id": step_id,
                "payload": {"level": "info", "message": fallback},
            }
        )
    return events


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
        poll_s: float = 5.0,
    ) -> None:
        self._providers = providers
        self._mcp = mcp
        self._exe = exe or shutil.which("opencode") or "opencode"
        self._health_timeout_s = health_timeout_s
        self._heartbeat_s = heartbeat_s
        self._poll_s = poll_s
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
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._procs.add(proc)
        base = f"http://127.0.0.1:{port}"
        heartbeat: asyncio.Task[None] | None = None
        snapshot = _TurnSnapshot()
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
                post = asyncio.create_task(
                    client.post(
                        f"/session/{session_id}/message",
                        json={
                            "agent": name,
                            "model": _model_ref(spec.model),
                            "parts": [{"type": "text", "text": spec.prompt}],
                        },
                    )
                )
                # The POST is not a reliable completion signal: on a live Extract
                # run it stayed open long after the turn had written its outputs,
                # burning the whole step timeout. So poll the session's messages
                # alongside it — that both (a) keeps a usable trace even when the
                # POST never returns (I9) and (b) lets a turn the server reports
                # as completed finish the step.
                try:
                    while True:
                        done, _ = await asyncio.wait({post}, timeout=self._poll_s)
                        await self._poll_turn(client, session_id, snapshot)
                        if done:
                            resp = post.result()
                            resp.raise_for_status()
                            body = resp.json()
                            # {info: {error?, cost, tokens, ...}, parts}; the POST
                            # body can carry no parts, so keep the polled ones.
                            snapshot.absorb(body.get("info"), body.get("parts"))
                            break
                        if snapshot.completed or snapshot.info.get("error"):
                            break  # no point waiting out the timeout on an error
                finally:
                    if not post.done():
                        post.cancel()

                agent_events = _events_from_parts(spec.step_id, snapshot.parts)
                for tool_call in (e for e in agent_events if e["type"] == "tool_call"):
                    on_event(tool_call)
                self._write_trace(spec, snapshot.text, agent_events)
                info = snapshot.info
                error = info.get("error")
                return StepResult(
                    completed=True,
                    agent_error=json.dumps(error) if error else None,
                    usage={"cost": info.get("cost"), "tokens": info.get("tokens")},
                )
        except asyncio.CancelledError:
            # The step timeout (§10.2) cancels us. CancelledError is a
            # BaseException, so without this branch the one trace you most need
            # — the step that hung — was the only one never written (I9).
            self._write_trace(
                spec,
                snapshot.text or "[opencode: step timed out with no reply]",
                _events_from_parts(
                    spec.step_id,
                    snapshot.parts,
                    fallback=(
                        "opencode step timed out; the server recorded no assistant "
                        "reply (seen when the provider stalls, e.g. a quota error "
                        "opencode never surfaces)"
                    ),
                ),
            )
            raise
        except Exception as exc:  # server/transport failure → infra error (retryable)
            self._write_trace(
                spec,
                f"[opencode infra error] {exc}",
                _events_from_parts(spec.step_id, snapshot.parts),
            )
            return StepResult(completed=False, agent_error=None)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
            await self._terminate(proc)
            self._procs.discard(proc)

    async def _poll_turn(
        self, client: object, session_id: str, snapshot: _TurnSnapshot
    ) -> None:
        """Refresh ``snapshot`` from the session's latest assistant message.

        Never raises: a failed poll only means this tick learned nothing — the
        POST task, the step timeout and the gate remain the deciding signals.
        """
        import httpx

        assert isinstance(client, httpx.AsyncClient)
        try:
            resp = await client.get(f"/session/{session_id}/message", timeout=30)
            if resp.status_code != 200:
                return
            messages = resp.json()
        except (httpx.HTTPError, ValueError):
            return
        if not isinstance(messages, list):
            return
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            info = message.get("info")
            if isinstance(info, dict) and info.get("role") == "assistant":
                snapshot.absorb(info, message.get("parts"))
                return

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
        if sys.platform == "win32":
            # ``opencode serve`` spawns a child process tree; ``terminate()`` only
            # signals the launcher and leaves the server alive. Kill the whole
            # tree by pid (CLAUDE.md gotcha: processes MUST be killed).
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
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
