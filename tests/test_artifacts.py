"""Tests for input materialization, artifact naming, and the gate (SPEC §10.1, §10.2, §10.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refract.artifacts import (
    GatePort,
    GateReport,
    artifact_filename,
    artifact_path,
    check_port,
    link_or_copy,
    materialize_collection,
    materialize_dir_or_any,
    materialize_file,
    materialize_map_item,
    run_gate,
    write_gate_report,
)
from refract.models.types import ItemInfo
from refract.registry import ArtifactRegistry


def _write_registry(tmp_path: Path) -> ArtifactRegistry:
    types_dir = tmp_path / "types"
    types_dir.mkdir(parents=True, exist_ok=True)
    (types_dir / "artifact_types.yaml").write_text(
        """
version: "0.1"
types:
  source@v1:        { kind: any }
  extract@v1:       { kind: file, format: json, schema: extract.schema.json }
  requirements@v1:
    kind: file
    format: markdown
    rules:
      - { rule: regex, pattern: "^# Requirements:", flags: "m" }
      - { rule: min_length, value: 10 }
  bundle@v1:        { kind: dir }
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


# --- link_or_copy -----------------------------------------------------------


class TestLinkOrCopy:
    def test_links_or_copies_file_content(self, tmp_path: Path) -> None:
        # SPEC §10.1
        src = tmp_path / "src.txt"
        src.write_text("hello world", encoding="utf-8")
        dst = tmp_path / "nested" / "dst.txt"
        link_or_copy(src, dst)
        assert dst.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("x", encoding="utf-8")
        dst = tmp_path / "a" / "b" / "c" / "dst.txt"
        link_or_copy(src, dst)
        assert dst.exists()
        assert dst.read_text(encoding="utf-8") == "x"

    def test_links_or_copies_directory_recursively(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "srcdir"
        (src_dir / "sub").mkdir(parents=True)
        (src_dir / "a.txt").write_text("A", encoding="utf-8")
        (src_dir / "sub" / "b.txt").write_text("B", encoding="utf-8")

        dst_dir = tmp_path / "out" / "dstdir"
        link_or_copy(src_dir, dst_dir)

        assert (dst_dir / "a.txt").read_text(encoding="utf-8") == "A"
        assert (dst_dir / "sub" / "b.txt").read_text(encoding="utf-8") == "B"


# --- artifact naming (§10.4) -------------------------------------------------


class TestArtifactNaming:
    def test_file_json_extension(self, registry: ArtifactRegistry) -> None:
        rtype = registry.get("extract@v1")
        assert rtype is not None
        assert artifact_filename("extract", rtype) == "extract.json"

    def test_file_markdown_extension(self, registry: ArtifactRegistry) -> None:
        rtype = registry.get("requirements@v1")
        assert rtype is not None
        assert artifact_filename("doc", rtype) == "doc.md"

    def test_file_text_extension(self, tmp_path: Path) -> None:
        types_dir = tmp_path / "types"
        types_dir.mkdir(parents=True)
        (types_dir / "artifact_types.yaml").write_text(
            'version: "0.1"\ntypes:\n  plain@v1: { kind: file, format: text }\n',
            encoding="utf-8",
        )
        reg = ArtifactRegistry.load(tmp_path)
        rtype = reg.get("plain@v1")
        assert rtype is not None
        assert artifact_filename("notes", rtype) == "notes.txt"

    def test_dir_kind_no_extension(self, registry: ArtifactRegistry) -> None:
        rtype = registry.get("bundle@v1")
        assert rtype is not None
        assert artifact_filename("bundle", rtype) == "bundle"

    def test_any_kind_no_extension(self, registry: ArtifactRegistry) -> None:
        rtype = registry.get("source@v1")
        assert rtype is not None
        assert artifact_filename("source", rtype) == "source"

    def test_artifact_path_joins_base_dir(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        rtype = registry.get("extract@v1")
        assert rtype is not None
        p = artifact_path(tmp_path / "output", "extract", rtype)
        assert p == tmp_path / "output" / "extract.json"


# --- materialization (§10.1) -------------------------------------------------


class TestMaterializeFile:
    def test_single_file_lands_at_port_dot_ext(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        rtype = registry.get("extract@v1")
        assert rtype is not None
        src = tmp_path / "extract.json"
        src.write_text('{"value": 1}', encoding="utf-8")
        input_root = tmp_path / "input"
        dst = materialize_file(src, input_root, "extracts", rtype)
        assert dst == input_root / "extracts" / "extracts.json"
        assert dst.read_text(encoding="utf-8") == '{"value": 1}'


class TestMaterializeDirOrAny:
    def test_file_source_placed_under_own_name(self, tmp_path: Path) -> None:
        src = tmp_path / "rfp.pdf"
        src.write_text("pdf-bytes", encoding="utf-8")
        input_root = tmp_path / "input"
        port_dir = materialize_dir_or_any(src, input_root, "source")
        assert port_dir == input_root / "source"
        assert (port_dir / "rfp.pdf").read_text(encoding="utf-8") == "pdf-bytes"

    def test_dir_source_contents_placed_inside(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "bundle"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("A", encoding="utf-8")
        (src_dir / "b.txt").write_text("B", encoding="utf-8")
        input_root = tmp_path / "input"
        port_dir = materialize_dir_or_any(src_dir, input_root, "bundle")
        assert port_dir == input_root / "bundle"
        assert (port_dir / "a.txt").read_text(encoding="utf-8") == "A"
        assert (port_dir / "b.txt").read_text(encoding="utf-8") == "B"
        # no extra top-level "bundle" dir nested inside
        assert not (port_dir / "bundle").exists()


class TestMaterializeCollection:
    def test_copies_manifest_and_item_dirs(self, tmp_path: Path) -> None:
        src_coll = tmp_path / "src_collection"
        src_coll.mkdir()
        (src_coll / "_collection.json").write_text(
            json.dumps(
                {
                    "type": "collection<extract@v1>",
                    "items": [
                        {
                            "slug": "rfp-doc",
                            "source": "rfp.pdf",
                            "source_hash": "sha256:abc",
                            "status": "ok",
                            "path": "rfp-doc/",
                            "error": None,
                        }
                    ],
                    "stats": {"total": 1, "ok": 1, "failed": 0},
                }
            ),
            encoding="utf-8",
        )
        item_dir = src_coll / "rfp-doc"
        item_dir.mkdir()
        (item_dir / "extract.json").write_text('{"value": 1}', encoding="utf-8")

        input_root = tmp_path / "input"
        port_dir = materialize_collection(src_coll, input_root, "extracts")

        assert port_dir == input_root / "extracts"
        assert (port_dir / "_collection.json").exists()
        assert (port_dir / "rfp-doc" / "extract.json").read_text(
            encoding="utf-8"
        ) == '{"value": 1}'


class TestMaterializeMapItem:
    def test_file_payload_plus_item_json(self, tmp_path: Path) -> None:
        payload = tmp_path / "rfp.pdf"
        payload.write_text("pdf-bytes", encoding="utf-8")
        item = ItemInfo(slug="rfp-doc", source="rfp.pdf", source_hash="sha256:abc")
        input_root = tmp_path / "input"
        port_dir = materialize_map_item(payload, input_root, "source", item)

        assert (port_dir / "rfp.pdf").read_text(encoding="utf-8") == "pdf-bytes"
        item_json_path = port_dir / "_item.json"
        assert item_json_path.exists()
        round_tripped = ItemInfo.model_validate_json(
            item_json_path.read_text(encoding="utf-8")
        )
        assert round_tripped == item

    def test_dir_payload_plus_item_json(self, tmp_path: Path) -> None:
        payload_dir = tmp_path / "payload"
        payload_dir.mkdir()
        (payload_dir / "a.txt").write_text("A", encoding="utf-8")
        item = ItemInfo(slug="folder-item", source="folder/", source_hash="sha256:def")
        input_root = tmp_path / "input"
        port_dir = materialize_map_item(payload_dir, input_root, "source", item)

        assert (port_dir / "a.txt").read_text(encoding="utf-8") == "A"
        round_tripped = ItemInfo.model_validate_json(
            (port_dir / "_item.json").read_text(encoding="utf-8")
        )
        assert round_tripped == item


# --- the gate (§10.2) --------------------------------------------------------


class TestGate:
    def test_missing_output_not_ok(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        rtype = registry.get("extract@v1")
        assert rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        result = check_port(output_dir, GatePort(port="extract", rtype=rtype))
        assert result.ok is False
        assert any("missing" in p for p in result.problems)

    def test_invalid_json_not_ok(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        rtype = registry.get("extract@v1")
        assert rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "extract.json").write_text("{not valid json", encoding="utf-8")
        result = check_port(output_dir, GatePort(port="extract", rtype=rtype))
        assert result.ok is False

    def test_json_schema_failure_not_ok(self, tmp_path: Path) -> None:
        # verdict@v1 schema requires verdict in {approved, revise}
        registry = ArtifactRegistry.builtins_only()
        rtype = registry.get("verdict@v1")
        assert rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "verdict.json").write_text(
            json.dumps({"verdict": "bogus"}), encoding="utf-8"
        )
        result = check_port(output_dir, GatePort(port="verdict", rtype=rtype))
        assert result.ok is False

    def test_json_valid_ok(self, tmp_path: Path) -> None:
        registry = ArtifactRegistry.builtins_only()
        rtype = registry.get("verdict@v1")
        assert rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "verdict.json").write_text(
            json.dumps({"verdict": "approved"}), encoding="utf-8"
        )
        result = check_port(output_dir, GatePort(port="verdict", rtype=rtype))
        assert result.ok is True
        assert result.problems == []

    def test_rules_fail_when_unmet(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        rtype = registry.get("requirements@v1")
        assert rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "doc.md").write_text("no header, too short", encoding="utf-8")
        result = check_port(output_dir, GatePort(port="doc", rtype=rtype))
        assert result.ok is False
        assert len(result.problems) >= 1

    def test_rules_ok_when_met(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        rtype = registry.get("requirements@v1")
        assert rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "doc.md").write_text(
            "# Requirements:\nSome long enough content here.\n", encoding="utf-8"
        )
        result = check_port(output_dir, GatePort(port="doc", rtype=rtype))
        assert result.ok is True
        assert result.problems == []

    def test_run_gate_skips_optional_ports(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        extract_rtype = registry.get("extract@v1")
        question_registry = ArtifactRegistry.builtins_only()
        question_rtype = question_registry.get("question@v1")
        assert extract_rtype is not None and question_rtype is not None

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "extract.json").write_text(
            json.dumps({"value": 1}), encoding="utf-8"
        )
        # question.json is NOT written; optional port must not fail the gate.
        report = run_gate(
            output_dir,
            [
                GatePort(port="extract", rtype=extract_rtype),
                GatePort(port="question", rtype=question_rtype, optional=True),
            ],
        )
        assert report.ok is True
        assert [p.port for p in report.ports] == ["extract"]

    def test_run_gate_fails_overall_when_one_port_fails(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        extract_rtype = registry.get("extract@v1")
        assert extract_rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        # missing output/extract.json
        report = run_gate(output_dir, [GatePort(port="extract", rtype=extract_rtype)])
        assert report.ok is False
        assert report.ports[0].ok is False

    def test_write_gate_report_round_trips(
        self, tmp_path: Path, registry: ArtifactRegistry
    ) -> None:
        extract_rtype = registry.get("extract@v1")
        assert extract_rtype is not None
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "extract.json").write_text(
            json.dumps({"value": 1}), encoding="utf-8"
        )
        report = run_gate(output_dir, [GatePort(port="extract", rtype=extract_rtype)])
        workdir = tmp_path / "step"
        workdir.mkdir()
        path = write_gate_report(workdir, report)
        assert path == workdir / "gate_report.json"
        round_tripped = GateReport.model_validate_json(path.read_text(encoding="utf-8"))
        assert round_tripped == report
