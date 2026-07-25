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
