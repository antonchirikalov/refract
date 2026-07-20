"""Model round-trip tests for pydantic formats (SPEC §5-9, §18)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from refract.models.agent import AgentSpec
from refract.models.config import (
    McpFile,
    McpHttpServer,
    McpStdioServer,
    ProjectConfig,
    ProvidersFile,
)
from refract.models.ledger import (
    Event,
    EventType,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
    StepOutcome,
    StepState,
    StepStatus,
)
from refract.models.pipeline import (
    AgentNode,
    BuiltinNode,
    LoopNode,
    Pipeline,
    SelectNode,
)
from refract.models.types import ArtifactTypeDef


# --- Pipeline / node discriminated union -------------------------------------


class TestPipelineNodes:
    def _pipeline_dict(self) -> dict:
        # SPEC §8 example (adapted)
        return {
            "version": "0.1",
            "name": "extract",
            "nodes": [
                {
                    "id": "scan",
                    "type": "builtin/scanner",
                    "params": {"exclude": ["plan", ".git", "outputs", "__pycache__"]},
                },
                {
                    "id": "extract",
                    "type": "agent",
                    "agent": "source_processor@1",
                    "map": "scan.sources",
                    "params": {
                        "workers": 3,
                        "gate_retries": 2,
                        "on_item_failure": "skip",
                        "min_ok": 1,
                    },
                },
                {
                    "id": "refine",
                    "type": "loop",
                    "params": {"max_rounds": 5, "on_max_rounds": "pass"},
                    "body": {
                        "agent": "requirements_writer@1",
                        "inputs": {"extracts": "extract.extract"},
                    },
                    "critic": {
                        "agent": "requirements_critic@1",
                        "inputs": {"doc": "@body", "extracts": "extract.extract"},
                    },
                    "outputs": {"doc": "@body"},
                },
                {
                    "id": "choose",
                    "type": "select",
                    "candidates": "design.design_doc",
                    "selector": {"agent": "solution_design_selector@1"},
                    "params": {"fallback": "first_ok"},
                },
            ],
        }

    def test_discriminated_union_picks_right_classes(self) -> None:
        pipeline = Pipeline.model_validate(self._pipeline_dict())
        kinds = [type(n) for n in pipeline.nodes]
        assert kinds == [BuiltinNode, AgentNode, LoopNode, SelectNode]

    def test_builtin_node_builtin_name(self) -> None:
        pipeline = Pipeline.model_validate(self._pipeline_dict())
        scan = pipeline.nodes[0]
        assert isinstance(scan, BuiltinNode)
        assert scan.builtin_name == "scanner"
        assert scan.type == "builtin/scanner"

    def test_agent_node_map_and_params(self) -> None:
        pipeline = Pipeline.model_validate(self._pipeline_dict())
        extract = pipeline.nodes[1]
        assert isinstance(extract, AgentNode)
        assert extract.map == "scan.sources"
        assert extract.map_over is None
        assert extract.params.workers == 3
        assert extract.params.gate_retries == 2
        assert extract.params.on_item_failure == "skip"
        assert extract.params.min_ok == 1
        # defaults (SPEC §8.2)
        assert extract.params.infra_retries == 2
        assert extract.params.cache is False

    def test_loop_node_body_critic_outputs(self) -> None:
        pipeline = Pipeline.model_validate(self._pipeline_dict())
        refine = pipeline.nodes[2]
        assert isinstance(refine, LoopNode)
        assert refine.body.agent == "requirements_writer@1"
        assert refine.body.inputs == {"extracts": "extract.extract"}
        assert refine.critic.agent == "requirements_critic@1"
        assert refine.critic.inputs == {"doc": "@body", "extracts": "extract.extract"}
        assert refine.outputs == {"doc": "@body"}
        assert refine.params.max_rounds == 5
        assert refine.params.on_max_rounds == "pass"

    def test_select_node_candidates_selector_fallback(self) -> None:
        pipeline = Pipeline.model_validate(self._pipeline_dict())
        choose = pipeline.nodes[3]
        assert isinstance(choose, SelectNode)
        assert choose.candidates == "design.design_doc"
        assert choose.selector.agent == "solution_design_selector@1"
        assert choose.params.fallback == "first_ok"

    def test_param_defaults_loop_select(self) -> None:
        # SPEC §8.2 defaults not overridden in the fixture
        pipeline = Pipeline.model_validate(self._pipeline_dict())
        refine = pipeline.nodes[2]
        choose = pipeline.nodes[3]
        assert isinstance(refine, LoopNode)
        assert isinstance(choose, SelectNode)
        assert refine.params.gate_retries == 2
        assert refine.params.infra_retries == 2
        assert choose.params.gate_retries == 2

    def test_map_over_node(self) -> None:
        # SPEC §8 solution-design pattern
        data = {
            "version": "0.1",
            "name": "solution_design",
            "nodes": [
                {
                    "id": "design",
                    "type": "agent",
                    "agent": "solution_designer@1",
                    "inputs": {"requirements": "refine.doc"},
                    "map_over": {"models": ["kimi/kimi-k3", "openai/gpt-5.6"]},
                },
            ],
        }
        pipeline = Pipeline.model_validate(data)
        design = pipeline.nodes[0]
        assert isinstance(design, AgentNode)
        assert design.map is None
        assert design.map_over is not None
        assert design.map_over.models == ["kimi/kimi-k3", "openai/gpt-5.6"]

    def test_round_trip(self) -> None:
        original = self._pipeline_dict()
        pipeline = Pipeline.model_validate(original)
        dumped = pipeline.model_dump(by_alias=True)
        pipeline2 = Pipeline.model_validate(dumped)
        assert pipeline2.model_dump(by_alias=True) == dumped

    def test_scalar_binding_model_field_kept_verbatim(self) -> None:
        data = {
            "version": "0.1",
            "name": "sd",
            "nodes": [
                {
                    "id": "sd_refine",
                    "type": "loop",
                    "params": {"max_rounds": 3},
                    "body": {
                        "agent": "solution_designer@1",
                        "model": "@choose.winner_model",
                        "inputs": {"requirements": "refine.doc", "draft": "choose.out"},
                    },
                    "critic": {
                        "agent": "solution_design_critic@1",
                        "inputs": {"doc": "@body"},
                    },
                    "outputs": {"doc": "@body"},
                },
            ],
        }
        pipeline = Pipeline.model_validate(data)
        node = pipeline.nodes[0]
        assert isinstance(node, LoopNode)
        assert node.body.model == "@choose.winner_model"


# --- AgentSpec ----------------------------------------------------------------


class TestAgentSpec:
    def _valid(self) -> dict:
        # SPEC §6 example
        return {
            "name": "source_processor",
            "version": 1,
            "description": "Extracts requirements JSON from one source document",
            "consumes": [{"port": "source", "type": "source@v1"}],
            "produces": [
                {"port": "extract", "type": "extract@v1"},
                {"port": "clarification", "type": "question@v1", "optional": True},
            ],
            "needs": ["read", "vision", "mcp:pdf-reader"],
            "defaults": {"timeout_s": 3600},
        }

    def test_valid_package_ref(self) -> None:
        spec = AgentSpec.model_validate(self._valid())
        assert spec.ref == "source_processor@1"

    def test_round_trip(self) -> None:
        original = self._valid()
        spec = AgentSpec.model_validate(original)
        dumped = spec.model_dump(by_alias=True)
        spec2 = AgentSpec.model_validate(dumped)
        assert spec2.model_dump(by_alias=True) == dumped

    def test_invalid_capability_rejected(self) -> None:
        data = self._valid()
        data["needs"] = ["read", "fly"]
        with pytest.raises(ValidationError):
            AgentSpec.model_validate(data)

    def test_mcp_capability_without_name_rejected(self) -> None:
        data = self._valid()
        data["needs"] = ["mcp:"]
        with pytest.raises(ValidationError):
            AgentSpec.model_validate(data)

    def test_bad_name_rejected(self) -> None:
        data = self._valid()
        data["name"] = "Source-Processor"
        with pytest.raises(ValidationError):
            AgentSpec.model_validate(data)

    def test_defaults_timeout(self) -> None:
        data = self._valid()
        del data["defaults"]
        spec = AgentSpec.model_validate(data)
        assert spec.defaults.timeout_s == 3600


# --- ArtifactTypeDef consistency validators -----------------------------------


class TestArtifactTypeDefConsistency:
    def test_valid_file_json_with_schema(self) -> None:
        ArtifactTypeDef.model_validate(
            {"kind": "file", "format": "json", "schema": "x.schema.json"}
        )

    def test_format_only_for_kind_file(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactTypeDef.model_validate({"kind": "any", "format": "json"})

    def test_schema_only_for_kind_file(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactTypeDef.model_validate({"kind": "dir", "schema": "x.schema.json"})

    def test_rules_only_for_kind_file(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactTypeDef.model_validate(
                {"kind": "any", "rules": [{"rule": "min_length", "value": 10}]}
            )

    def test_schema_only_for_format_json(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactTypeDef.model_validate(
                {"kind": "file", "format": "markdown", "schema": "x.schema.json"}
            )

    def test_kind_any_valid(self) -> None:
        ArtifactTypeDef.model_validate({"kind": "any"})

    def test_kind_file_with_rules_valid(self) -> None:
        ArtifactTypeDef.model_validate(
            {
                "kind": "file",
                "format": "markdown",
                "rules": [{"rule": "min_length", "value": 2000}],
            }
        )

    def test_regex_rule_valid_flags_and_pattern(self) -> None:
        ArtifactTypeDef.model_validate(
            {
                "kind": "file",
                "format": "markdown",
                "rules": [{"rule": "regex", "pattern": "^# Req", "flags": "m"}],
            }
        )

    def test_regex_rule_unknown_flag_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactTypeDef.model_validate(
                {
                    "kind": "file",
                    "format": "markdown",
                    "rules": [{"rule": "regex", "pattern": "x", "flags": "z"}],
                }
            )

    def test_regex_rule_invalid_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactTypeDef.model_validate(
                {
                    "kind": "file",
                    "format": "markdown",
                    "rules": [{"rule": "regex", "pattern": "([unclosed"}],
                }
            )


# --- Config models -------------------------------------------------------------


class TestProjectConfig:
    def test_round_trip(self) -> None:
        data = {
            "version": "0.1",
            "name": "Atlas RFP",
            "input": "./input",
            "defaults": {"model": "kimi/kimi-k3"},
        }
        cfg = ProjectConfig.model_validate(data)
        dumped = cfg.model_dump(by_alias=True)
        cfg2 = ProjectConfig.model_validate(dumped)
        assert cfg2.model_dump(by_alias=True) == dumped
        assert cfg.defaults.model == "kimi/kimi-k3"

    def test_defaults_missing_input(self) -> None:
        cfg = ProjectConfig.model_validate({"version": "0.1", "name": "P"})
        assert cfg.input == "./input"


class TestProvidersFile:
    def test_round_trip(self) -> None:
        data = {
            "providers": {
                "kimi": {"api_key_env": "MOONSHOT_API_KEY", "max_concurrent": 4},
                "openai": {"api_key_env": "OPENAI_API_KEY", "max_concurrent": 4},
            },
            "library_path": "/path/to/refract/library",
        }
        pf = ProvidersFile.model_validate(data)
        dumped = pf.model_dump(by_alias=True)
        pf2 = ProvidersFile.model_validate(dumped)
        assert pf2.model_dump(by_alias=True) == dumped
        assert pf.providers["kimi"].api_key_env == "MOONSHOT_API_KEY"


class TestMcpFile:
    def _data(self) -> dict:
        return {
            "servers": {
                "pdf-reader": {"command": ["npx", "-y", "@mcp/pdf-reader"], "env": {}},
                "tavily": {"url": "https://example.com", "token_env": "TAVILY_API_KEY"},
            }
        }

    def test_stdio_variant_resolves(self) -> None:
        mcp = McpFile.model_validate(self._data())
        assert isinstance(mcp.servers["pdf-reader"], McpStdioServer)
        assert mcp.servers["pdf-reader"].command == ["npx", "-y", "@mcp/pdf-reader"]

    def test_http_variant_resolves(self) -> None:
        mcp = McpFile.model_validate(self._data())
        assert isinstance(mcp.servers["tavily"], McpHttpServer)
        assert mcp.servers["tavily"].url == "https://example.com"
        assert mcp.servers["tavily"].token_env == "TAVILY_API_KEY"

    def test_round_trip(self) -> None:
        original = self._data()
        mcp = McpFile.model_validate(original)
        dumped = mcp.model_dump(by_alias=True)
        mcp2 = McpFile.model_validate(dumped)
        assert mcp2.model_dump(by_alias=True) == dumped


# --- Ledger (SPEC §9) -----------------------------------------------------------


class TestLedger:
    def test_run_state_round_trip_with_nodes_and_steps(self) -> None:
        data = {
            "run_id": "run_20260719_101500",
            "status": "running",
            "pipeline": "extract",
            "created_at": "2026-07-19T10:15:00Z",
            "finished_at": None,
            "reuse_from": None,
            "force_nodes": [],
            "nodes": {
                "scan": {"status": "done", "error": None},
                "extract": {"status": "running", "error": None},
                "refine": {"status": "pending", "error": None},
                "choose": {
                    "status": "pending",
                    "error": None,
                    "winner": None,
                    "winner_model": None,
                },
            },
            "steps": {
                "extract:rfp-doc": {
                    "node": "extract",
                    "status": "done",
                    "outcome": "ok",
                    "tries": 1,
                    "started_at": "2026-07-19T10:15:10Z",
                    "finished_at": "2026-07-19T10:15:20Z",
                    "error": None,
                }
            },
        }
        state = RunState.model_validate(data)
        dumped = state.model_dump(by_alias=True)
        state2 = RunState.model_validate(dumped)
        assert state2.model_dump(by_alias=True) == dumped

        assert state.nodes["scan"].status == NodeStatus.done
        assert state.steps["extract:rfp-doc"].outcome == StepOutcome.ok

    def test_enums_serialize_to_string_values(self) -> None:
        assert dumped_value(StepStatus.running) == "running"
        assert dumped_value(StepOutcome.failed_validation) == "failed_validation"
        assert dumped_value(NodeStatus.skipped) == "skipped"
        assert dumped_value(RunStatus.completed) == "completed"

    def test_node_state_winner_fields(self) -> None:
        ns = NodeState.model_validate(
            {"status": "done", "winner": "kimi_kimi-k3", "winner_model": "kimi/kimi-k3"}
        )
        dumped = ns.model_dump(by_alias=True)
        assert dumped["winner"] == "kimi_kimi-k3"
        assert dumped["winner_model"] == "kimi/kimi-k3"

    def test_event_round_trip(self) -> None:
        data = {
            "seq": 41,
            "ts": "2026-07-19T10:15:22Z",
            "type": "step_state_changed",
            "step_id": "extract:rfp-doc",
            "payload": {"from": "running", "to": "done", "outcome": "ok"},
        }
        event = Event.model_validate(data)
        assert event.type == EventType.step_state_changed
        dumped = event.model_dump(by_alias=True)
        event2 = Event.model_validate(dumped)
        assert event2.model_dump(by_alias=True) == dumped

    def test_step_state_defaults(self) -> None:
        step = StepState.model_validate({"node": "extract", "status": "pending"})
        assert step.tries == 0
        assert step.outcome is None


def dumped_value(enum_member: object) -> str:
    """Helper: the JSON-serialized string value of an enum member."""
    assert isinstance(enum_member, str)
    return str(enum_member.value)  # type: ignore[attr-defined]
