"""Tests for builtin/scanner (SPEC §13) and its scheduler integration."""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from refract.builtins import scanner
from refract.events import EventWriter
from refract.models.ledger import NodeStatus, RunStatus
from refract.models.pipeline import Pipeline
from refract.models.types import CollectionManifest
from refract.runtime.mock import MockRuntime, ScriptedResponse
from refract.scheduler import run_pipeline
from refract.state import Ledger

from graph_fixtures import agent_spec, write_registry


def _clock_seq() -> "callable":
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"T{counter['n']}"

    return clock


async def _no_sleep(_seconds: float) -> None:
    return None


def _write_agent_pkg(run_dir: Path, ref: str) -> None:
    pkg_dir = run_dir / "snapshot" / "agents" / ref
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "prompt.md").write_text(f"You are {ref}.", encoding="utf-8")


# --- unit: scanner.run -------------------------------------------------------


class TestScannerRun:
    def test_mixed_file_and_folder_one_item_each(self, tmp_path: Path) -> None:
        # SPEC §13: a top-level file and a top-level subfolder each become ONE item.
        input_dir = tmp_path / "input"
        (input_dir).mkdir()
        (input_dir / "a.txt").write_text("hello", encoding="utf-8")
        docs = input_dir / "docs"
        docs.mkdir()
        (docs / "one.txt").write_text("1", encoding="utf-8")
        (docs / "two.txt").write_text("2", encoding="utf-8")

        output_dir = tmp_path / "output"
        manifest = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=input_dir,
            output_dir=output_dir,
            port="sources",
        )

        assert len(manifest.items) == 2
        assert manifest.stats.total == 2
        assert manifest.stats.ok == 2
        assert manifest.stats.failed == 0

        by_source = {item.source: item for item in manifest.items}
        assert set(by_source) == {"a.txt", "docs"}

        file_item = by_source["a.txt"]
        folder_item = by_source["docs"]

        file_payload = output_dir / "sources" / file_item.path.rstrip("/") / "a.txt"
        assert file_payload.is_file()
        assert file_payload.read_text("utf-8") == "hello"

        folder_payload_dir = output_dir / "sources" / folder_item.path.rstrip("/")
        assert folder_payload_dir.is_dir()
        assert (folder_payload_dir / "one.txt").read_text("utf-8") == "1"
        assert (folder_payload_dir / "two.txt").read_text("utf-8") == "2"

        manifest_path = output_dir / "sources" / "_collection.json"
        assert manifest_path.exists()
        loaded = CollectionManifest.model_validate(
            json.loads(manifest_path.read_text("utf-8"))
        )
        assert loaded == manifest
        assert loaded.type == "collection<source@v1>"

    def test_source_hash_deterministic_and_mtime_independent(
        self, tmp_path: Path
    ) -> None:
        # SPEC §13: source_hash is content-only; identical content -> identical
        # hash regardless of mtime, and re-running produces the same hash.
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.txt").write_text("same content", encoding="utf-8")

        m1 = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=input_dir,
            output_dir=tmp_path / "out1",
            port="sources",
        )
        m2 = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=input_dir,
            output_dir=tmp_path / "out2",
            port="sources",
        )
        assert m1.items[0].source_hash == m2.items[0].source_hash
        assert m1.items[0].source_hash.startswith("sha256:")

        # recreate the same content at a different path/time -> same file hash
        other_dir = tmp_path / "input_other"
        other_dir.mkdir()
        time.sleep(0.01)
        (other_dir / "renamed.txt").write_text("same content", encoding="utf-8")
        m3 = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=other_dir,
            output_dir=tmp_path / "out3",
            port="sources",
        )
        assert m3.items[0].source_hash == m1.items[0].source_hash

    def test_folder_hash_changes_with_content(self, tmp_path: Path) -> None:
        # SPEC §13: folder source_hash covers the whole tree's content.
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        docs = input_dir / "docs"
        docs.mkdir()
        (docs / "one.txt").write_text("v1", encoding="utf-8")

        m1 = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=input_dir,
            output_dir=tmp_path / "out1",
            port="sources",
        )

        (docs / "one.txt").write_text("v2", encoding="utf-8")
        m2 = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=input_dir,
            output_dir=tmp_path / "out2",
            port="sources",
        )

        assert m1.items[0].source_hash != m2.items[0].source_hash

    def test_exclude_skips_named_top_level_entry(self, tmp_path: Path) -> None:
        # SPEC §13: params.exclude matches exact top-level names only.
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "a.txt").write_text("keep", encoding="utf-8")
        git_dir = input_dir / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("ignored", encoding="utf-8")

        manifest = scanner.run(
            params=scanner.ScannerParams(exclude=[".git"]),
            input_dir=input_dir,
            output_dir=tmp_path / "out",
            port="sources",
        )

        assert manifest.stats.total == 1
        assert [item.source for item in manifest.items] == ["a.txt"]

    def test_empty_or_missing_input_dir(self, tmp_path: Path) -> None:
        # SPEC §13: missing input dir -> empty collection, not an error.
        output_dir = tmp_path / "out"
        manifest = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=tmp_path / "does_not_exist",
            output_dir=output_dir,
            port="sources",
        )
        assert manifest.items == []
        assert manifest.stats.total == 0
        assert manifest.stats.ok == 0
        assert manifest.stats.failed == 0
        assert (output_dir / "sources" / "_collection.json").exists()

        # an existing but empty directory behaves the same way.
        empty_dir = tmp_path / "empty_input"
        empty_dir.mkdir()
        manifest2 = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=empty_dir,
            output_dir=tmp_path / "out2",
            port="sources",
        )
        assert manifest2.stats.total == 0

    def test_slug_collision_gets_distinct_suffix(self, tmp_path: Path) -> None:
        # SPEC §5/§13: two entries slugifying to the same base get -2, -3, ...
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "A B.txt").write_text("one", encoding="utf-8")
        (input_dir / "A_B.txt").write_text("two", encoding="utf-8")

        manifest = scanner.run(
            params=scanner.ScannerParams(),
            input_dir=input_dir,
            output_dir=tmp_path / "out",
            port="sources",
        )

        slugs = sorted(item.slug for item in manifest.items)
        assert len(slugs) == 2
        assert len(set(slugs)) == 2
        base = slugs[0]
        assert slugs[1] == f"{base}-2"


# --- integration: run_pipeline with builtin/scanner + downstream agent ------


class TestScannerSchedulerIntegration:
    async def test_scan_then_summarize_collection_edge(self, tmp_path: Path) -> None:
        # SPEC §13/§10.5: scanner writes a collection; a downstream agent
        # consuming collection<source@v1> gets it materialized whole.
        run_dir = tmp_path / "run"
        project_input_dir = tmp_path / "project_input"
        project_input_dir.mkdir()
        (project_input_dir / "rfp.pdf").write_bytes(b"pdf-bytes")
        (project_input_dir / "notes.txt").write_text("notes", encoding="utf-8")

        registry = write_registry(tmp_path)

        summarizer = agent_spec(
            "summarizer",
            consumes=[{"port": "srcs", "type": "collection<source@v1>"}],
            produces=[{"port": "doc", "type": "requirements@v1"}],
        )
        agents = {summarizer.ref: summarizer}
        _write_agent_pkg(run_dir, summarizer.ref)

        pipeline = Pipeline.model_validate(
            yaml.safe_load(
                f"""
version: "0.1"
name: scan_pipeline
nodes:
  - id: scan
    type: builtin/scanner
  - id: summarize
    type: agent
    agent: {summarizer.ref}
    inputs: {{ srcs: "scan.sources" }}
    params: {{ model: "mock/mock-1", gate_retries: 0 }}
"""
            )
        )

        ledger = Ledger.create(
            run_dir,
            run_id="run_test",
            pipeline="scan_pipeline",
            node_ids=["scan", "summarize"],
            created_at="T0",
        )
        events = EventWriter(run_dir, clock=_clock_seq())
        runtime = MockRuntime(
            {
                "summarize": [
                    ScriptedResponse(files={"doc.md": "# Requirements:\nsummary body."})
                ]
            }
        )

        status = await run_pipeline(
            run_dir,
            pipeline=pipeline,
            agents=agents,
            registry=registry,
            runtime=runtime,
            ledger=ledger,
            events=events,
            project_input_dir=project_input_dir,
            clock=_clock_seq(),
            sleeper=_no_sleep,
        )

        assert status is RunStatus.completed
        assert ledger.get_node("scan").status is NodeStatus.done
        assert ledger.get_node("summarize").status is NodeStatus.done

        scan_manifest_path = (
            run_dir
            / "steps"
            / "scan"
            / "main"
            / "output"
            / "sources"
            / "_collection.json"
        )
        assert scan_manifest_path.exists()
        scan_manifest = CollectionManifest.model_validate(
            json.loads(scan_manifest_path.read_text("utf-8"))
        )
        assert len(scan_manifest.items) == 2
        assert scan_manifest.stats.total == 2

        consumer_manifest_path = (
            run_dir
            / "steps"
            / "summarize"
            / "main"
            / "input"
            / "srcs"
            / "_collection.json"
        )
        assert consumer_manifest_path.exists()
        consumer_manifest = CollectionManifest.model_validate(
            json.loads(consumer_manifest_path.read_text("utf-8"))
        )
        assert len(consumer_manifest.items) == 2

    async def test_scanner_reexec_is_idempotent(self, tmp_path: Path) -> None:
        # Regression: re-executing a builtin (crash recovery flips running→pending
        # and re-runs) must rebuild output from scratch, not merge into the prior
        # run. A subfolder source would raise FileExistsError from copytree onto a
        # leftover slug dir, silently corrupting the collection, without the guard.
        run_dir = tmp_path / "run"
        project_input_dir = tmp_path / "project_input"
        (project_input_dir / "docs").mkdir(parents=True)
        (project_input_dir / "docs" / "a.txt").write_text("aaa", encoding="utf-8")
        (project_input_dir / "top.txt").write_text("top", encoding="utf-8")

        registry = write_registry(tmp_path)
        pipeline = Pipeline.model_validate(
            yaml.safe_load(
                """
version: "0.1"
name: scan_only
nodes:
  - id: scan
    type: builtin/scanner
"""
            )
        )
        runtime = MockRuntime({"scan": [ScriptedResponse()]})  # never used

        manifest_path = (
            run_dir
            / "steps"
            / "scan"
            / "main"
            / "output"
            / "sources"
            / "_collection.json"
        )

        for _ in range(2):  # second pass simulates resume/re-exec on same run_dir
            ledger = Ledger.create(
                run_dir,
                run_id="run_test",
                pipeline="scan_only",
                node_ids=["scan"],
                created_at="T0",
            )
            events = EventWriter(run_dir, clock=_clock_seq())
            status = await run_pipeline(
                run_dir,
                pipeline=pipeline,
                agents={},
                registry=registry,
                runtime=runtime,
                ledger=ledger,
                events=events,
                project_input_dir=project_input_dir,
                clock=_clock_seq(),
                sleeper=_no_sleep,
            )
            assert status is RunStatus.completed
            assert ledger.get_node("scan").status is NodeStatus.done
            manifest = CollectionManifest.model_validate(
                json.loads(manifest_path.read_text("utf-8"))
            )
            assert manifest.stats.total == 2
            assert manifest.stats.failed == 0  # no copytree collision → no false-failed
