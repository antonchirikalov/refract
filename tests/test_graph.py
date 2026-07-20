"""Tests for pipeline parsing, ref grammar, and topological sort (SPEC §8)."""

from __future__ import annotations

from pathlib import Path

from refract.graph import (
    BindingRef,
    BodyRef,
    DataRef,
    load_agents,
    load_pipeline,
    parse_pipeline_file,
    parse_ref,
    validate_pipeline,
)
from refract.models.errors import Code
from refract.models.pipeline import Pipeline
from tests.graph_fixtures import (
    EXTRACT_PIPELINE_YAML,
    agent_spec,
    make_ctx,
    standard_agents,
)


# --- parse_ref grammar (SPEC §8.1) ------------------------------------------


class TestParseRef:
    def test_data_ref(self) -> None:
        assert parse_ref("scan.sources") == DataRef("scan", "sources")

    def test_data_ref_with_dotted_port(self) -> None:
        # only the first "." splits node id from port
        assert parse_ref("scan.sources.extra") == DataRef("scan", "sources.extra")

    def test_body_ref_bare(self) -> None:
        assert parse_ref("@body") == BodyRef(None)

    def test_body_ref_with_port(self) -> None:
        assert parse_ref("@body.doc") == BodyRef("doc")

    def test_binding_ref(self) -> None:
        assert parse_ref("@choose.winner_model") == BindingRef("choose")

    def test_malformed_at_ref_is_none(self) -> None:
        assert parse_ref("@bogus") is None

    def test_malformed_at_ref_with_wrong_attr_is_none(self) -> None:
        assert parse_ref("@choose.not_winner_model") is None

    def test_plain_string_without_dot_is_none(self) -> None:
        assert parse_ref("nodotshere") is None


# --- parse_pipeline_file -----------------------------------------------------


class TestParsePipelineFile:
    def test_malformed_yaml_yields_e_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "pipeline.yaml"
        p.write_text("nodes: [\n", encoding="utf-8")
        pipeline, errors = parse_pipeline_file(p)
        assert pipeline is None
        assert any(e.code == Code.E_YAML for e in errors)

    def test_schema_invalid_yields_e_schema(self, tmp_path: Path) -> None:
        p = tmp_path / "pipeline.yaml"
        # missing required fields (version/name/nodes)
        p.write_text("foo: bar\n", encoding="utf-8")
        pipeline, errors = parse_pipeline_file(p)
        assert pipeline is None
        assert any(e.code == Code.E_SCHEMA for e in errors)

    def test_valid_pipeline_parses(self, tmp_path: Path) -> None:
        p = tmp_path / "pipeline.yaml"
        p.write_text(EXTRACT_PIPELINE_YAML, encoding="utf-8")
        pipeline, errors = parse_pipeline_file(p)
        assert pipeline is not None
        assert errors == []
        assert pipeline.name == "extract"


# --- load_pipeline / load_agents / full valid pipeline ----------------------


class TestLoadAgents:
    def test_loads_valid_package(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agents" / "source_processor"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(
            """
name: source_processor
version: 1
consumes:
  - { port: source, type: source@v1 }
produces:
  - { port: extract, type: extract@v1 }
""",
            encoding="utf-8",
        )
        (agent_dir / "prompt.md").write_text("You are a source processor.", "utf-8")
        agents, errors = load_agents(tmp_path)
        assert errors == []
        assert "source_processor@1" in agents
        assert agents["source_processor@1"].name == "source_processor"

    def test_malformed_agent_yaml_yields_e_schema(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agents" / "broken"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(
            "name: broken\nversion: 1\n# missing produces\n", encoding="utf-8"
        )
        agents, errors = load_agents(tmp_path)
        assert "broken@1" not in agents
        assert any(e.code == Code.E_SCHEMA for e in errors)

    def test_missing_agents_dir_yields_empty(self, tmp_path: Path) -> None:
        agents, errors = load_agents(tmp_path)
        assert agents == {}
        assert errors == []


class TestValidPipelineAndToposort:
    def test_valid_pipeline_has_no_blocking_errors(self, tmp_path: Path) -> None:
        # SPEC §8.3 — a fully valid pipeline returns [] blocking errors
        ctx = make_ctx(tmp_path)
        p = tmp_path / "pipeline.yaml"
        p.write_text(EXTRACT_PIPELINE_YAML, encoding="utf-8")
        loaded = load_pipeline(p, ctx)
        assert loaded.ok is True
        blocking = [e for e in loaded.errors if not e.code.is_warning]
        assert blocking == []
        assert loaded.order == ["scan", "extract", "refine"]

    def test_toposort_scan_map_loop_order(self, tmp_path: Path) -> None:
        pipeline = Pipeline.model_validate(
            {
                "version": "0.1",
                "name": "extract",
                "nodes": [
                    {"id": "scan", "type": "builtin/scanner", "params": {}},
                    {
                        "id": "extract",
                        "type": "agent",
                        "agent": "source_processor@1",
                        "map": "scan.sources",
                    },
                    {
                        "id": "refine",
                        "type": "loop",
                        "body": {
                            "agent": "requirements_writer@1",
                            "inputs": {"extracts": "extract.extract"},
                        },
                        "critic": {
                            "agent": "requirements_critic@1",
                            "inputs": {
                                "doc": "@body",
                                "extracts": "extract.extract",
                            },
                        },
                        "outputs": {"doc": "@body"},
                    },
                ],
            }
        )
        ctx = make_ctx(tmp_path)
        order, errors = validate_pipeline(pipeline, ctx)
        assert order.index("scan") < order.index("extract")
        assert order.index("extract") < order.index("refine")
        assert [e for e in errors if not e.code.is_warning] == []

    def test_toposort_select_before_loop_scalar_binding(self, tmp_path: Path) -> None:
        # SPEC §8.1 — model: "@choose.winner_model" creates a scheduling dependency:
        # a loop whose body model is "@choose.winner_model" must be ordered AFTER
        # the select node it binds to.
        agents = standard_agents()
        agents["solution_designer@1"] = agent_spec(
            "solution_designer",
            consumes=[{"port": "requirements", "type": "requirements@v1"}],
            produces=[{"port": "design_doc", "type": "design_doc@v1"}],
        )
        agents["solution_design_selector@1"] = agent_spec(
            "solution_design_selector",
            consumes=[{"port": "candidates", "type": "collection<design_doc@v1>"}],
            produces=[{"port": "winner", "type": "selection@v1"}],
        )
        agents["solution_design_critic@1"] = agent_spec(
            "solution_design_critic",
            consumes=[{"port": "doc", "type": "design_doc@v1"}],
            produces=[{"port": "verdict", "type": "verdict@v1"}],
        )
        yaml_text = (
            EXTRACT_PIPELINE_YAML
            + """
  - id: design
    type: agent
    agent: solution_designer@1
    inputs: { requirements: refine.doc }
    map_over: { models: ["kimi/kimi-k3"] }

  - id: choose
    type: select
    candidates: design.design_doc
    selector: { agent: solution_design_selector@1 }

  - id: sd_refine
    type: loop
    params: { max_rounds: 3 }
    body:
      agent: solution_designer@1
      model: "@choose.winner_model"
      inputs: { requirements: refine.doc }
    critic: { agent: solution_design_critic@1, inputs: { doc: "@body" } }
    outputs: { doc: "@body" }
"""
        )
        pipeline = Pipeline.model_validate(_yaml_load(yaml_text))
        ctx = make_ctx(tmp_path, agents=agents)
        order, errors = validate_pipeline(pipeline, ctx)
        assert order.index("choose") < order.index("sd_refine")
        assert order.index("design") < order.index("choose")
        assert [e for e in errors if not e.code.is_warning] == []


def _yaml_load(text: str) -> dict:
    import yaml

    return yaml.safe_load(text)
