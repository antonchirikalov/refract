"""Pipeline template library: shipped + user templates (SPEC-UI §5.1).

Two sources, same format — ``<library>/templates/*.yaml`` ships with the engine
(read-only) and ``<refract_home>/templates/*.yaml`` holds what a user saved from a
project. A user template shadowing a shipped name is refused at write time, so
resolution stays unambiguous.

Metadata for the template gallery is DERIVED from the pipeline file itself (nodes,
agents, the union of their capabilities, whether it scans an input folder, and the
file's leading comment block as the description). No sidecar format.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from refract.models.agent import AgentSpec
from refract.models.pipeline import (
    AgentNode,
    BuiltinNode,
    LoopNode,
    Pipeline,
    SelectNode,
)

TEMPLATES_SUBDIR = "templates"
SOURCE_LIBRARY = "library"
SOURCE_USER = "user"


@dataclass(frozen=True)
class TemplateRef:
    """A template file and where it came from."""

    name: str
    path: Path
    source: str  # SOURCE_LIBRARY | SOURCE_USER


def template_dirs(library_path: Path, home: Path) -> list[tuple[str, Path]]:
    """Search order for templates: shipped first, then the user's."""
    return [
        (SOURCE_LIBRARY, Path(library_path) / TEMPLATES_SUBDIR),
        (SOURCE_USER, Path(home) / TEMPLATES_SUBDIR),
    ]


def list_templates(library_path: Path, home: Path) -> list[TemplateRef]:
    """All templates from both sources, shipped first, each source sorted."""
    out: list[TemplateRef] = []
    seen: set[str] = set()
    for source, directory in template_dirs(library_path, home):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            if path.stem in seen:  # a shadowing user file never wins silently
                continue
            seen.add(path.stem)
            out.append(TemplateRef(name=path.stem, path=path, source=source))
    return out


def find_template(name: str, library_path: Path, home: Path) -> TemplateRef | None:
    for ref in list_templates(library_path, home):
        if ref.name == name:
            return ref
    return None


def header_comment(text: str) -> str:
    """Comments in the file header — everything before ``nodes:`` — joined.

    The shipped templates put their description after ``version``/``name`` rather
    than on line 1, so "leading block" would miss it; stopping at ``nodes:`` keeps
    per-node comments out.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("nodes:"):
            break
        if stripped.startswith("#"):
            lines.append(stripped.lstrip("#").strip())
    return " ".join(part for part in lines if part)


def _agent_refs(pipeline: Pipeline) -> list[str]:
    refs: list[str] = []
    for node in pipeline.nodes:
        if isinstance(node, AgentNode):
            candidates = [node.agent]
        elif isinstance(node, LoopNode):
            candidates = [node.body.agent, node.critic.agent]
        elif isinstance(node, SelectNode):
            candidates = [node.selector.agent]
        else:
            candidates = []
        for ref in candidates:
            if ref not in refs:
                refs.append(ref)
    return refs


def template_metadata(
    ref: TemplateRef, *, agents: dict[str, AgentSpec]
) -> dict[str, Any]:
    """Gallery metadata derived from the template file (SPEC-UI §5.1)."""
    text = ref.path.read_text("utf-8")
    pipeline = Pipeline.model_validate(yaml.safe_load(text))
    agent_refs = _agent_refs(pipeline)
    needs: list[str] = []
    for agent_ref in agent_refs:
        spec = agents.get(agent_ref)
        for cap in spec.needs if spec else []:
            if cap not in needs:
                needs.append(cap)
    return {
        "name": ref.name,
        "source": ref.source,
        "title": pipeline.name or ref.name,
        "description": header_comment(text),
        "input_mode": pipeline.input_mode,
        "nodes": [{"id": n.id, "type": n.type} for n in pipeline.nodes],
        "agents": agent_refs,
        "needs": needs,
        "reads_input_folder": any(
            isinstance(n, BuiltinNode) and n.builtin_name == "scanner"
            for n in pipeline.nodes
        ),
    }
