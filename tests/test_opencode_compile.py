"""Tests for opencode COMPILE step file generation.

SPEC §12, §18 test_opencode_compile.

These tests exercise only ``compile_step`` / ``render_agent_md`` /
``build_opencode_config`` / ``tools_for_needs`` — pure file generation. No
subprocess, no network, no real opencode binary is ever invoked.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from refract.models.config import McpFile, ProvidersFile
from refract.runtime.base import StepSpec
from refract.runtime.opencode import (
    AGENTS_SUBDIR,
    build_opencode_config,
    compile_step,
    render_agent_md,
    tools_for_needs,
)


def make_providers() -> ProvidersFile:
    return ProvidersFile.model_validate(
        {
            "providers": {
                "kimi": {"api_key_env": "MOONSHOT_API_KEY", "max_concurrent": 4}
            }
        }
    )


def make_mcp() -> McpFile:
    return McpFile.model_validate(
        {
            "servers": {
                "pdf-reader": {
                    "command": ["npx", "-y", "@mcp/pdf-reader"],
                    "env": {},
                },
                "tavily": {"url": "https://x", "token_env": "TAVILY_API_KEY"},
            }
        }
    )


def make_spec(
    tmp_path: Path,
    *,
    model: str = "kimi/k2",
    needs: list[str] | None = None,
    system_prompt: str = "You are a helpful writer agent.\n",
) -> StepSpec:
    workdir = tmp_path / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    agent_dir = tmp_path / "snapshot" / "agents" / "demo_writer@1"
    return StepSpec(
        step_id="step-1",
        agent_dir=agent_dir,
        model=model,
        workdir=workdir,
        prompt="do the task",
        system_prompt=system_prompt,
        needs=needs or [],
        timeout_s=60,
    )


# ---------------------------------------------------------------------------
# tools_for_needs
# ---------------------------------------------------------------------------


def test_tools_for_needs_read_edit() -> None:
    # SPEC §12
    tools = tools_for_needs(["read", "edit"])
    for name in ("read", "grep", "glob", "list", "write", "edit"):
        assert tools[name] is True


def test_tools_for_needs_bash() -> None:
    # SPEC §12
    assert tools_for_needs(["bash"]) == {"bash": True}


def test_tools_for_needs_vision_contributes_nothing() -> None:
    # SPEC §12
    assert tools_for_needs(["vision"]) == {}


def test_tools_for_needs_mcp_contributes_no_tool_flag() -> None:
    # SPEC §12 — mcp needs are wired via opencode.json, not the tools block.
    assert tools_for_needs(["mcp:pdf-reader"]) == {}
    assert tools_for_needs(["read", "mcp:pdf-reader"]) == {
        "read": True,
        "grep": True,
        "glob": True,
        "list": True,
    }


# ---------------------------------------------------------------------------
# compile_step: file layout
# ---------------------------------------------------------------------------


def test_compile_step_writes_both_files(tmp_path: Path) -> None:
    # SPEC §12
    spec = make_spec(tmp_path, needs=["read", "edit"])
    compiled = compile_step(spec, providers=make_providers(), mcp=make_mcp())

    expected_md = spec.workdir / AGENTS_SUBDIR / "demo_writer.md"
    expected_json = spec.workdir / "opencode.json"

    assert compiled.agent_md == expected_md
    assert compiled.opencode_json == expected_json
    assert expected_md.exists()
    assert expected_json.exists()


def test_agent_md_filename_uses_bare_agent_name(tmp_path: Path) -> None:
    # SPEC §12 — agent_dir.name is "demo_writer@1"; compiled filename is bare.
    spec = make_spec(tmp_path)
    compiled = compile_step(spec, providers=make_providers(), mcp=make_mcp())
    assert compiled.agent_md.name == "demo_writer.md"
    assert "@" not in compiled.agent_md.name


# ---------------------------------------------------------------------------
# agent md: frontmatter + body
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    assert text.startswith("---\n")
    _, _, rest = text.partition("---\n")
    fm_text, _, body = rest.partition("\n---\n")
    fm = yaml.safe_load(fm_text)
    return fm, body


def test_render_agent_md_frontmatter_and_body() -> None:
    # SPEC §12
    md = render_agent_md(
        name="demo_writer",
        model="kimi/k2",
        needs=["read", "edit"],
        system_prompt="You are a helpful writer agent.\n",
    )
    fm, body = _split_frontmatter(md)
    assert fm["model"] == "kimi/k2"
    assert fm["tools"] == tools_for_needs(["read", "edit"])
    assert body.strip("\n") == "You are a helpful writer agent."


def test_compile_step_agent_md_matches_render(tmp_path: Path) -> None:
    # SPEC §12
    spec = make_spec(tmp_path, model="kimi/k2", needs=["bash"])
    compiled = compile_step(spec, providers=make_providers(), mcp=make_mcp())
    text = compiled.agent_md.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    assert fm["model"] == "kimi/k2"
    assert fm["tools"] == {"bash": True}
    assert body.strip("\n") == spec.system_prompt.strip("\n")


# ---------------------------------------------------------------------------
# opencode.json: provider / api key placeholder
# ---------------------------------------------------------------------------


def test_build_opencode_config_provider_placeholder() -> None:
    # SPEC §12, I8 — secrets referenced by env placeholder, never inlined.
    config = build_opencode_config(
        model="kimi/k2", needs=[], providers=make_providers(), mcp=make_mcp()
    )
    assert config["model"] == "kimi/k2"
    provider_cfg = config["provider"]["kimi"]  # type: ignore[index]
    api_key = provider_cfg["options"]["apiKey"]  # type: ignore[index]
    assert "MOONSHOT_API_KEY" in api_key
    # Must be an env placeholder, never a literal secret value.
    assert api_key == "{env:MOONSHOT_API_KEY}"


def test_build_opencode_config_auto_approves_permissions() -> None:
    # SPEC §12 — headless runs auto-approve; every gated permission is "allow".
    config = build_opencode_config(
        model="kimi/k2", needs=[], providers=make_providers(), mcp=make_mcp()
    )
    perm = config["permission"]
    assert perm == {
        "bash": "allow",
        "edit": "allow",
        "read": "allow",
        "webfetch": "allow",
        "websearch": "allow",
    }


def test_build_opencode_config_no_provider_entry_when_unknown() -> None:
    # SPEC §12 — provider not present in providers.yaml -> no provider section key.
    config = build_opencode_config(
        model="unknownprov/foo", needs=[], providers=make_providers(), mcp=make_mcp()
    )
    assert "provider" not in config


# ---------------------------------------------------------------------------
# opencode.json: mcp servers
# ---------------------------------------------------------------------------


def test_build_opencode_config_mcp_servers() -> None:
    # SPEC §12, I8 — mcp servers wired in, no raw token inlined.
    config = build_opencode_config(
        model="kimi/k2",
        needs=["mcp:pdf-reader", "mcp:tavily"],
        providers=make_providers(),
        mcp=make_mcp(),
    )
    mcp_out = config["mcp"]  # type: ignore[index]
    assert set(mcp_out.keys()) == {"pdf-reader", "tavily"}  # type: ignore[union-attr]

    pdf = mcp_out["pdf-reader"]  # type: ignore[index]
    assert pdf["type"] == "local"
    assert pdf["command"] == ["npx", "-y", "@mcp/pdf-reader"]

    tavily = mcp_out["tavily"]  # type: ignore[index]
    assert tavily["type"] == "remote"
    assert tavily["url"] == "https://x"
    # Token referenced via env placeholder, never inlined.
    header = tavily["headers"]["Authorization"]
    assert "TAVILY_API_KEY" in header
    assert header == "Bearer {env:TAVILY_API_KEY}"

    dumped = json.dumps(config)
    assert "TAVILY_API_KEY" not in dumped or "{env:TAVILY_API_KEY}" in dumped
    # No literal secret-looking values beyond the env placeholder strings.


def test_build_opencode_config_no_mcp_key_when_no_mcp_needs() -> None:
    # SPEC §12
    config = build_opencode_config(
        model="kimi/k2", needs=["read"], providers=make_providers(), mcp=make_mcp()
    )
    assert "mcp" not in config or config.get("mcp") == {}


# ---------------------------------------------------------------------------
# opencode.json: on-disk JSON validity + encoding
# ---------------------------------------------------------------------------


def test_compile_step_opencode_json_is_valid_utf8_json(tmp_path: Path) -> None:
    # SPEC §12
    spec = make_spec(
        tmp_path,
        model="kimi/k2",
        needs=["mcp:pdf-reader", "mcp:tavily", "read"],
        system_prompt="Écrivez en français, s'il vous plaît.\n",
    )
    compiled = compile_step(spec, providers=make_providers(), mcp=make_mcp())

    raw_bytes = compiled.opencode_json.read_bytes()
    # Must decode as UTF-8 without error.
    text = raw_bytes.decode("utf-8")
    data = json.loads(text)

    assert data["model"] == "kimi/k2"
    assert "pdf-reader" in data["mcp"]
    assert "tavily" in data["mcp"]

    # The agent md must also be readable as UTF-8 and preserve the prompt content.
    md_text = compiled.agent_md.read_text(encoding="utf-8")
    assert "s'il vous plaît" in md_text


def test_compile_step_round_trips_full_config(tmp_path: Path) -> None:
    # SPEC §12 — compile_step's written config matches build_opencode_config's output.
    spec = make_spec(tmp_path, model="kimi/k2", needs=["mcp:pdf-reader"])
    providers = make_providers()
    mcp = make_mcp()
    compiled = compile_step(spec, providers=providers, mcp=mcp)

    on_disk = json.loads(compiled.opencode_json.read_text(encoding="utf-8"))
    expected = build_opencode_config(
        model=spec.model, needs=spec.needs, providers=providers, mcp=mcp
    )
    assert on_disk == expected
