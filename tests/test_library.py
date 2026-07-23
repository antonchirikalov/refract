"""Guard tests for the shipped ``library/`` (SPEC §5/§6/§17 migration).

Loads the real registry + agent packages (no MockRuntime needed — pure loading
and graph validation) so a malformed shipped package or type schema fails CI.
"""

from __future__ import annotations

from pathlib import Path

from refract.graph import ValidationContext, load_agents, load_pipeline
from refract.registry import ArtifactRegistry

LIBRARY = Path(__file__).resolve().parents[1] / "library"


def test_library_agents_load_without_errors() -> None:
    agents, errors = load_agents(LIBRARY)
    assert errors == []
    # the Phase 0 migrated agents (SPEC §17) plus the demo agent are present
    assert {"source_processor@1", "requirements_writer@1", "demo_writer@1"} <= set(
        agents
    )


def test_registry_loads_extract_type_and_schema() -> None:
    reg = ArtifactRegistry.load(LIBRARY)
    # extract@v1 is a json file type whose schema loaded cleanly at registry load
    assert reg.has("extract@v1")
    assert reg.has("requirements@v1")
    assert reg.has("source@v1")


def test_extract_pipeline_validates(tmp_path: Path) -> None:
    # The canonical Extract shape: scan -> map(source_processor) -> writer.
    pipeline = tmp_path / "extract.yaml"
    pipeline.write_text(
        "\n".join(
            [
                'version: "0.1"',
                "name: extract",
                "nodes:",
                "  - id: scan",
                "    type: builtin/scanner",
                "  - id: extract",
                "    type: agent",
                "    agent: source_processor@1",
                "    map: scan.sources",
                "    params: { workers: 3, min_ok: 1, on_item_failure: skip }",
                "  - id: write",
                "    type: agent",
                "    agent: requirements_writer@1",
                "    inputs: { extracts: extract.extract }",
            ]
        ),
        encoding="utf-8",
    )
    agents, _ = load_agents(LIBRARY)
    ctx = ValidationContext(
        registry=ArtifactRegistry.load(LIBRARY),
        agents=agents,
        known_providers={"kimi"},
        available_providers={"kimi"},
        default_model="kimi/kimi-k3",
    )
    graph = load_pipeline(pipeline, ctx)
    assert graph.ok, [(e.code.value, e.node_id, e.message) for e in graph.errors]
    assert graph.order == ["scan", "extract", "write"]
