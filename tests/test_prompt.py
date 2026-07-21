"""Tests for prompt assembly (SPEC §11)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refract.models.agent import AgentSpec
from refract.models.types import ItemInfo
from refract.prompt import RevisionContext, build_task_prompt
from refract.registry import ArtifactRegistry, INLINE_MAX_BYTES


def _write_registry(tmp_path: Path) -> ArtifactRegistry:
    types_dir = tmp_path / "types"
    types_dir.mkdir(parents=True, exist_ok=True)
    (types_dir / "artifact_types.yaml").write_text(
        """
version: "0.1"
types:
  source@v1:        { kind: any }
  extract@v1:       { kind: file, format: json, schema: extract.schema.json }
  big_note@v1:       { kind: file, format: text, inline: true }
  requirements@v1:
    kind: file
    format: markdown
    rules:
      - { rule: regex, pattern: "^# Requirements:", flags: "m" }
      - { rule: min_length, value: 2000 }
""",
        encoding="utf-8",
    )
    schema_dir = types_dir / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "extract.schema.json").write_text(
        json.dumps({"type": "object", "required": ["value"]}), encoding="utf-8"
    )
    return ArtifactRegistry.load(tmp_path)


@pytest.fixture
def registry(tmp_path: Path) -> ArtifactRegistry:
    return _write_registry(tmp_path)


def _agent(**kwargs: object) -> AgentSpec:
    base = {
        "name": "writer",
        "version": 1,
        "consumes": [],
        "produces": [{"port": "doc", "type": "requirements@v1"}],
    }
    base.update(kwargs)
    return AgentSpec.model_validate(base)


def _collection_manifest(n_items: int) -> dict:
    items = [
        {
            "slug": f"item-{i}",
            "source": f"item-{i}.txt",
            "source_hash": f"sha256:{i}",
            "status": "ok",
            "path": f"item-{i}/",
            "error": None,
        }
        for i in range(n_items)
    ]
    return {
        "type": "collection<extract@v1>",
        "items": items,
        "stats": {"total": n_items, "ok": n_items, "failed": 0},
    }


# --- outputs section (§11 item 3, I5) ---------------------------------------


class TestOutputsSection:
    def test_output_section_names_port_and_path(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(produces=[{"port": "doc", "type": "requirements@v1"}])
        workdir = tmp_path / "step"
        workdir.mkdir()
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert "doc" in prompt
        assert "output/doc.md" in prompt

    def test_output_section_json_required_fields(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(produces=[{"port": "extract", "type": "extract@v1"}])
        workdir = tmp_path / "step"
        workdir.mkdir()
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert "output/extract.json" in prompt
        assert "value" in prompt  # required field summary

    def test_output_section_min_length_summary(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(produces=[{"port": "doc", "type": "requirements@v1"}])
        workdir = tmp_path / "step"
        workdir.mkdir()
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert "At least 2000 characters" in prompt

    def test_output_section_regex_mentions_pattern(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(produces=[{"port": "doc", "type": "requirements@v1"}])
        workdir = tmp_path / "step"
        workdir.mkdir()
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert "# Requirements:" in prompt


# --- inline limits (§5 / §11) -----------------------------------------------


class TestInputInlining:
    def test_control_type_input_is_inlined(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "verdict", "type": "verdict@v1"}])
        workdir = tmp_path / "step"
        input_dir = workdir / "input" / "verdict"
        input_dir.mkdir(parents=True)
        (input_dir / "verdict.json").write_text(
            json.dumps({"verdict": "approved"}), encoding="utf-8"
        )
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert '"verdict": "approved"' in prompt

    def test_inline_true_type_under_4kb_is_inlined(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "note", "type": "big_note@v1"}])
        workdir = tmp_path / "step"
        input_dir = workdir / "input" / "note"
        input_dir.mkdir(parents=True)
        content = "x" * (INLINE_MAX_BYTES - 100)
        (input_dir / "note.txt").write_text(content, encoding="utf-8")
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert content in prompt

    def test_inline_true_type_at_or_over_4kb_is_not_inlined(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "note", "type": "big_note@v1"}])
        workdir = tmp_path / "step"
        input_dir = workdir / "input" / "note"
        input_dir.mkdir(parents=True)
        content = "y" * INLINE_MAX_BYTES  # at limit, not under
        (input_dir / "note.txt").write_text(content, encoding="utf-8")
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert content not in prompt
        assert "input/note/note.txt" in prompt

    def test_non_inline_type_never_inlined_even_when_small(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "extracted", "type": "extract@v1"}])
        workdir = tmp_path / "step"
        input_dir = workdir / "input" / "extracted"
        input_dir.mkdir(parents=True)
        (input_dir / "extracted.json").write_text(
            json.dumps({"value": "small"}), encoding="utf-8"
        )
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert '"value": "small"' not in prompt
        assert "input/extracted/extracted.json" in prompt


# --- collection input (§11 item 2) ------------------------------------------


class TestCollectionInput:
    def test_small_collection_fully_inlined(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "extracts", "type": "collection<extract@v1>"}])
        workdir = tmp_path / "step"
        input_dir = workdir / "input" / "extracts"
        input_dir.mkdir(parents=True)
        manifest = _collection_manifest(5)
        (input_dir / "_collection.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert "item-4" in prompt  # last item present
        assert '"total": 5' in prompt

    def test_large_collection_not_fully_inlined(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "extracts", "type": "collection<extract@v1>"}])
        workdir = tmp_path / "step"
        input_dir = workdir / "input" / "extracts"
        input_dir.mkdir(parents=True)
        manifest = _collection_manifest(60)
        (input_dir / "_collection.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        # 51st item (index 50, 0-based) must not appear; stats/path note must.
        assert "item-50" not in prompt
        assert "item-49" in prompt  # 50th item (index 49) is within first 50
        assert "input/extracts/_collection.json" in prompt
        assert "60" in prompt  # stats total mentioned


# --- map element input --------------------------------------------------


class TestMapElementInput:
    def test_map_element_described_as_single_element(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "source", "type": "source@v1"}])
        workdir = tmp_path / "step"
        input_dir = workdir / "input" / "source"
        input_dir.mkdir(parents=True)
        (input_dir / "rfp.pdf").write_text("payload", encoding="utf-8")
        item = ItemInfo(slug="rfp-doc", source="rfp.pdf", source_hash="sha256:abc")
        (input_dir / "_item.json").write_text(
            json.dumps(item.model_dump(mode="json")), encoding="utf-8"
        )
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert "_item.json" in prompt
        assert "map element" in prompt.lower()


# --- revision + gate_feedback additions -------------------------------------


class TestRevisionAndGateFeedback:
    def test_revision_context_adds_section(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent()
        workdir = tmp_path / "step"
        workdir.mkdir(parents=True)
        rev = RevisionContext(
            previous_path="input/_previous/doc.md",
            verdict_json='{"verdict": "revise", "issues": [{"note": "fix X"}]}',
            hint="Focus on section 3.",
        )
        prompt = build_task_prompt(
            agent=agent, registry=registry, workdir=workdir, revision=rev
        )
        assert "input/_previous/doc.md" in prompt
        assert '"verdict": "revise"' in prompt
        assert "Focus on section 3." in prompt

    def test_gate_feedback_adds_section(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent()
        workdir = tmp_path / "step"
        workdir.mkdir(parents=True)
        feedback = json.dumps({"ok": False, "ports": [{"port": "doc", "ok": False}]})
        prompt = build_task_prompt(
            agent=agent, registry=registry, workdir=workdir, gate_feedback=feedback
        )
        assert feedback in prompt

    def test_no_revision_no_gate_feedback_by_default(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent()
        workdir = tmp_path / "step"
        workdir.mkdir(parents=True)
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)
        assert "Revision" not in prompt
        assert "Validation feedback" not in prompt


# --- only relative paths (I1) ------------------------------------------------


class TestOnlyRelativePaths:
    def test_workdir_absolute_path_not_in_prompt(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        agent = _agent(consumes=[{"port": "source", "type": "source@v1"}])
        workdir = tmp_path / "some_step_workdir"
        input_dir = workdir / "input" / "source"
        input_dir.mkdir(parents=True)
        (input_dir / "rfp.pdf").write_text("payload", encoding="utf-8")
        prompt = build_task_prompt(agent=agent, registry=registry, workdir=workdir)

        # No absolute workdir string, no drive letter, no backslashes.
        assert str(workdir) not in prompt
        assert str(tmp_path) not in prompt
        assert "\\" not in prompt
        # forward-slash relative path is present
        assert "input/source" in prompt
