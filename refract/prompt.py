"""Prompt assembly (SPEC §11).

Builds the TASK prompt (items 2–4 of §11): the inputs section, the outputs
section, and the optional revision / gate_feedback additions — all GENERATED
from the agent contract and the registry (I5), never hand-written. The agent's
``prompt.md`` (item 1) is the system prompt and is passed separately.

Input inlining reads the already-materialized ``input/<port>/`` tree, so this
runs after materialization (§10.1). Only relative paths appear (I1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from refract.artifacts import artifact_filename
from refract.models.agent import AgentSpec, Port
from refract.models.types import MinLengthRule, RegexRule, TypeKind
from refract.registry import ArtifactRegistry, parse_type_ref

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_COLLECTION_INLINE_MAX_ITEMS = 50  # SPEC §11

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


@dataclass
class RevisionContext:
    """Data for the loop revision addition (SPEC §11 item 4)."""

    previous_path: str
    verdict_json: str
    hint: str | None = None


def build_task_prompt(
    *,
    agent: AgentSpec,
    registry: ArtifactRegistry,
    workdir: Path | str,
    revision: RevisionContext | None = None,
    gate_feedback: str | None = None,
) -> str:
    """Assemble the task prompt (SPEC §11 items 2–4) for one step."""
    workdir = Path(workdir)
    sections = [
        _render_inputs(agent, registry, workdir),
        _render_outputs(agent, registry),
    ]
    if revision is not None:
        sections.append(
            _env.get_template("revision.md.j2").render(
                previous_path=revision.previous_path,
                verdict=revision.verdict_json,
                hint=revision.hint,
            )
        )
    if gate_feedback is not None:
        sections.append(
            _env.get_template("gate_feedback.md.j2").render(report=gate_feedback)
        )
    return "\n".join(s.strip("\n") for s in sections if s.strip()) + "\n"


# --- inputs (SPEC §11 item 2) ----------------------------------------------


def _render_inputs(agent: AgentSpec, registry: ArtifactRegistry, workdir: Path) -> str:
    inputs = [_describe_input(port, registry, workdir) for port in agent.consumes]
    return _env.get_template("inputs.md.j2").render(inputs=inputs)


def _describe_input(
    port: Port, registry: ArtifactRegistry, workdir: Path
) -> dict[str, object]:
    port_dir = workdir / "input" / port.port
    inner_name, is_collection = parse_type_ref(port.type)
    rtype = registry.get(inner_name)

    collection_manifest = port_dir / "_collection.json"
    item_marker = port_dir / "_item.json"

    if is_collection or collection_manifest.exists():
        return _describe_collection(port, port_dir, workdir, collection_manifest)
    if item_marker.exists():
        return {
            "port": port.port,
            "type": port.type,
            "path": _rel(port_dir, workdir),
            "content": None,
            "note": "A single map element; see `_item.json` for its provenance.",
        }

    # single file or dir/any
    content: str | None = None
    note: str | None = None
    file_path = port_dir / artifact_filename(port.port, rtype) if rtype else None
    if rtype is not None and rtype.kind is TypeKind.file and file_path is not None:
        display_path = _rel(file_path, workdir)
        if file_path.exists():
            size = file_path.stat().st_size
            if rtype.should_inline(size):
                content = file_path.read_text("utf-8")
    else:
        display_path = _rel(port_dir, workdir)
        note = "Directory input; read its contents."
    return {
        "port": port.port,
        "type": port.type,
        "path": display_path,
        "content": content,
        "note": note,
    }


def _describe_collection(
    port: Port, port_dir: Path, workdir: Path, manifest: Path
) -> dict[str, object]:
    content: str | None = None
    note: str | None = f"Collection at `{_rel(port_dir, workdir)}`."
    if manifest.exists():
        data = json.loads(manifest.read_text("utf-8"))
        items = data.get("items", [])
        if len(items) <= _COLLECTION_INLINE_MAX_ITEMS:
            content = json.dumps(data, ensure_ascii=False, indent=2)
            note = None
        else:
            trimmed = dict(data)
            trimmed["items"] = items[:_COLLECTION_INLINE_MAX_ITEMS]
            note = (
                f"Collection at `{_rel(port_dir, workdir)}` — "
                f"{data.get('stats')}; first {_COLLECTION_INLINE_MAX_ITEMS} items:\n"
                f"```\n{json.dumps(trimmed, ensure_ascii=False, indent=2)}\n```"
            )
    return {
        "port": port.port,
        "type": port.type,
        "path": _rel(manifest if manifest.exists() else port_dir, workdir),
        "content": content,
        "note": note,
    }


# --- outputs (SPEC §11 item 3) ---------------------------------------------


def _render_outputs(agent: AgentSpec, registry: ArtifactRegistry) -> str:
    outputs = []
    for port in agent.produces:
        rtype = registry.get(port.type)
        rel = (
            f"output/{artifact_filename(port.port, rtype)}"
            if rtype
            else f"output/{port.port}"
        )
        outputs.append(
            {
                "port": port.port,
                "type": port.type,
                "optional": port.optional,
                "path": rel,
                "summary": _schema_summary(registry, port.type),
            }
        )
    return _env.get_template("outputs.md.j2").render(outputs=outputs)


def _schema_summary(registry: ArtifactRegistry, type_name: str) -> str:
    rtype = registry.get(type_name)
    if rtype is None:
        return ""
    lines: list[str] = []
    fmt = rtype.format.value if rtype.format is not None else rtype.kind.value
    lines.append(f"Format: {fmt}.")
    if rtype.schema is not None:
        required = rtype.schema.get("required")
        props = rtype.schema.get("properties")
        if isinstance(required, list) and required:
            lines.append(f"Required fields: {', '.join(str(r) for r in required)}.")
        if isinstance(props, dict) and props:
            lines.append(f"Fields: {', '.join(props.keys())}.")
    for rule in rtype.rules:
        if isinstance(rule, RegexRule):
            lines.append(f"Must match regex `{rule.pattern}`.")
        elif isinstance(rule, MinLengthRule):
            lines.append(f"At least {rule.value} characters.")
    return " ".join(lines)


def _rel(path: Path, workdir: Path) -> str:
    """Path relative to the workdir, forward slashes (I1: only relative paths)."""
    return path.relative_to(workdir).as_posix()
