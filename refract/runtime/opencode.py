"""OpencodeRuntime — real opencode adapter (SPEC §12).

Phase 0 implements the *compilation* half: turning a snapshot agent package +
step spec into the two files opencode reads from a workdir —
``<workdir>/<AGENTS_SUBDIR>/<name>.md`` (frontmatter ``{model, tools}`` + body =
system prompt) and ``<workdir>/opencode.json`` (the model's provider + the MCP
servers named by the agent's ``needs``). This is pure file generation and is the
only part covered by tests (``test_opencode_compile``); actually spawning
opencode, confining file access to the workdir (I1), heartbeats and
auto-approve are the execution half and land in Phase 1 (see the manual smoke
recipe requirement, SPEC §12/§17).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from refract.models.config import McpFile, McpHttpServer, McpStdioServer, ProvidersFile
from refract.runtime.base import EventCallback, StepResult, StepSpec

# Pinned opencode version; a real run warns on mismatch (`opencode --version`).
OPENCODE_PINNED_VERSION = "0.4.0"
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
    config: dict[str, object] = {"model": model}
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


class OpencodeRuntime:
    """Real opencode adapter. Compilation is implemented (Phase 0); executing
    opencode (I1 confinement, heartbeats, auto-approve) is Phase 1."""

    def __init__(self, *, providers: ProvidersFile, mcp: McpFile) -> None:
        self._providers = providers
        self._mcp = mcp

    async def run_step(self, spec: StepSpec, on_event: EventCallback) -> StepResult:
        compile_step(spec, providers=self._providers, mcp=self._mcp)
        raise NotImplementedError(
            "OpencodeRuntime execution is Phase 1 (SPEC §12/§17); "
            "compilation is available via compile_step()"
        )

    async def close(self) -> None:
        return None
