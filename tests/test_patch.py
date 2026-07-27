"""Scoped node edits from the UI inspector (SPEC §19.2.1).

The general patch vocabulary was rejected (§19.2); these two setters exist because an
inspector that cannot change a model or a loop's rounds is not an inspector.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from refract.patch import apply_node_patch

LIBRARY = Path(__file__).resolve().parents[1] / "library"
TEMPLATE = (LIBRARY / "templates" / "requirements_to_design.yaml").read_text("utf-8")


def _nodes(text: str) -> dict[str, dict]:
    return {n["id"]: n for n in yaml.safe_load(text)["nodes"]}


# a loop whose body is a chain, for the body1..bodyN addressing (SPEC §10.3)
CHAIN = """version: "0.1"
name: chain
nodes:
  - id: refine
    type: loop
    body:
      - agent: requirements_writer@1
      - agent: requirements_polisher@1
        inputs: { draft: "@prev" }
    critic:
      agent: requirements_critic@1
      inputs: { draft: "@body" }
    outputs: { doc: "@body" }
"""


class TestChainBlocks:
    def test_addresses_a_chain_element_by_number(self) -> None:
        out = apply_node_patch(
            CHAIN, node_id="refine", block="body2", model="openai/gpt-5.6"
        )
        body = _nodes(out)["refine"]["body"]
        assert body[1]["model"] == "openai/gpt-5.6"
        assert "model" not in body[0]  # only the element asked for

    def test_plain_body_on_a_longer_chain_is_ambiguous(self) -> None:
        """Editing "the body" of a two-element chain must not silently pick one."""
        with pytest.raises(ValueError, match="body1..body2"):
            apply_node_patch(CHAIN, node_id="refine", block="body", model="kimi/k3")

    def test_plain_body_still_works_for_a_chain_of_one(self) -> None:
        single = CHAIN.replace(
            """      - agent: requirements_polisher@1
        inputs: { draft: "@prev" }
""",
            "",
        )
        out = apply_node_patch(single, node_id="refine", block="body", model="kimi/k3")
        assert _nodes(out)["refine"]["body"][0]["model"] == "kimi/k3"

    def test_out_of_range_element_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            apply_node_patch(CHAIN, node_id="refine", block="body9", model="kimi/k3")

    def test_unset_model_on_a_chain_element(self) -> None:
        with_model = apply_node_patch(
            CHAIN, node_id="refine", block="body2", model="kimi/k3"
        )
        out = apply_node_patch(
            with_model, node_id="refine", block="body2", unset_model=True
        )
        assert "model" not in _nodes(out)["refine"]["body"][1]


class TestModel:
    def test_sets_a_plain_nodes_model(self) -> None:
        out = apply_node_patch(TEMPLATE, node_id="extract", model="openai/gpt-5.6")
        assert _nodes(out)["extract"]["params"]["model"] == "openai/gpt-5.6"

    def test_sets_a_blocks_model_inside_a_meta_node(self) -> None:
        out = apply_node_patch(
            TEMPLATE, node_id="refine", block="critic", model="openai/gpt-5.6"
        )
        refine = _nodes(out)["refine"]
        assert refine["critic"]["model"] == "openai/gpt-5.6"
        assert "model" not in refine.get("body", {})  # only the block asked for

    def test_unset_model_returns_the_node_to_the_project_default(self) -> None:
        with_model = apply_node_patch(TEMPLATE, node_id="extract", model="kimi/k3")
        out = apply_node_patch(with_model, node_id="extract", unset_model=True)
        # the field is REMOVED, not nulled: null would fail model resolution (§7)
        assert "model" not in _nodes(out)["extract"].get("params", {})

    def test_unknown_node_or_block_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            apply_node_patch(TEMPLATE, node_id="nope", model="kimi/k3")
        with pytest.raises(KeyError):
            apply_node_patch(
                TEMPLATE, node_id="extract", block="critic", model="kimi/k3"
            )


class TestParams:
    def test_sets_a_loops_round_count(self) -> None:
        out = apply_node_patch(TEMPLATE, node_id="refine", params={"max_rounds": 5})
        assert _nodes(out)["refine"]["params"]["max_rounds"] == 5

    def test_rejects_a_param_the_node_does_not_have(self) -> None:
        with pytest.raises(ValueError, match="unknown param"):
            apply_node_patch(TEMPLATE, node_id="refine", params={"workers": 4})

    def test_rejects_a_wrong_type(self) -> None:
        with pytest.raises(Exception):
            apply_node_patch(TEMPLATE, node_id="refine", params={"max_rounds": "many"})

    def test_rejects_a_non_scalar(self) -> None:
        with pytest.raises(ValueError, match="scalar"):
            apply_node_patch(TEMPLATE, node_id="extract", params={"workers": {"a": 1}})


class TestRoundTrip:
    def test_comments_and_untouched_nodes_survive(self) -> None:
        # A project's pipeline comes from a template with explanations in it; losing
        # them because someone switched a model would be a poor trade.
        out = apply_node_patch(TEMPLATE, node_id="refine", params={"max_rounds": 4})

        assert "# Source documents to a solution design" in out
        assert "# Why one pipeline instead of the separate" in out
        assert _nodes(out)["design"] == _nodes(TEMPLATE)["design"]

    def test_the_result_is_still_a_valid_pipeline_document(self) -> None:
        from refract.models.pipeline import Pipeline

        out = apply_node_patch(
            TEMPLATE, node_id="sd_refine", block="critic", model="kimi/k3"
        )
        pipeline = Pipeline.model_validate(yaml.safe_load(out))

        assert pipeline.checkpoints == ["refine"]
