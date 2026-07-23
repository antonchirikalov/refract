"""Shared builders for graph-validation tests (SPEC §8).

Not a test module itself (no ``test_`` prefix) — imported by test_graph.py and
test_graph_validation.py to keep individual tests concise.
"""

from __future__ import annotations

import json
from pathlib import Path

from refract.graph import ValidationContext
from refract.models.agent import AgentSpec
from refract.registry import ArtifactRegistry

STANDARD_TYPES_YAML = """
version: "0.1"
types:
  source@v1:        { kind: any }
  extract@v1:       { kind: file, format: json, schema: extract.schema.json }
  requirements@v1:
    kind: file
    format: markdown
    rules:
      - { rule: regex, pattern: "^# Requirements:", flags: "m" }
  design_doc@v1:    { kind: file, format: markdown }
"""


def write_registry(tmp_path: Path) -> ArtifactRegistry:
    """Build a standard registry (source/extract/requirements/design_doc) under tmp_path."""
    types_dir = tmp_path / "types"
    types_dir.mkdir(parents=True, exist_ok=True)
    (types_dir / "artifact_types.yaml").write_text(
        STANDARD_TYPES_YAML, encoding="utf-8"
    )
    schema_dir = types_dir / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "extract.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    return ArtifactRegistry.load(tmp_path)


def agent_spec(
    name: str,
    version: int = 1,
    *,
    consumes: list[dict] | None = None,
    produces: list[dict] | None = None,
    needs: list[str] | None = None,
) -> AgentSpec:
    """Build an AgentSpec from plain dicts (no on-disk package needed)."""
    return AgentSpec.model_validate(
        {
            "name": name,
            "version": version,
            "consumes": consumes or [],
            "produces": produces or [{"port": "out", "type": "extract@v1"}],
            "needs": needs or [],
        }
    )


def standard_agents() -> dict[str, AgentSpec]:
    """A standard agent set matching the Extract-shape pipeline used across tests."""
    source_processor = agent_spec(
        "source_processor",
        consumes=[{"port": "source", "type": "source@v1"}],
        produces=[{"port": "extract", "type": "extract@v1"}],
    )
    requirements_writer = agent_spec(
        "requirements_writer",
        consumes=[{"port": "extracts", "type": "collection<extract@v1>"}],
        produces=[{"port": "doc", "type": "requirements@v1"}],
    )
    requirements_critic = agent_spec(
        "requirements_critic",
        consumes=[
            {"port": "doc", "type": "requirements@v1"},
            {"port": "extracts", "type": "collection<extract@v1>"},
        ],
        produces=[{"port": "verdict", "type": "verdict@v1"}],
    )
    return {
        s.ref: s for s in (source_processor, requirements_writer, requirements_critic)
    }


def make_ctx(
    tmp_path: Path,
    *,
    agents: dict[str, AgentSpec] | None = None,
    known_providers: set[str] | None = None,
    available_providers: set[str] | None = None,
    default_model: str | None = "kimi/kimi-k3",
    model_overrides: dict[str, str] | None = None,
) -> ValidationContext:
    """A ValidationContext with the standard registry + standard agents by default.

    Providers default to {'kimi'} known+available so unrelated tests don't get
    spurious E_MODEL_UNRESOLVED / E_PROVIDER_UNAVAILABLE noise.
    """
    return ValidationContext(
        registry=write_registry(tmp_path),
        agents=agents if agents is not None else standard_agents(),
        known_providers=known_providers if known_providers is not None else {"kimi"},
        available_providers=(
            available_providers if available_providers is not None else {"kimi"}
        ),
        default_model=default_model,
        model_overrides=model_overrides or {},
    )


EXTRACT_PIPELINE_YAML = """
version: "0.1"
name: extract
nodes:
  - id: scan
    type: builtin/scanner
    params: { exclude: [".git"] }

  - id: extract
    type: agent
    agent: source_processor@1
    map: scan.sources

  - id: refine
    type: loop
    params: { max_rounds: 3, on_max_rounds: pass }
    body:   { agent: requirements_writer@1, inputs: { extracts: extract.extract } }
    critic: { agent: requirements_critic@1, inputs: { doc: "@body", extracts: extract.extract } }
    outputs: { doc: "@body" }
"""
