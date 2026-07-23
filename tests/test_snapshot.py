"""Tests for the run snapshot builder (refract/snapshot.py) — SPEC §9."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from refract.models.pipeline import Pipeline
from refract.snapshot import (
    build_resolved,
    build_snapshot,
    package_hash,
    resolve_model,
    used_agent_refs,
)

PIPELINE_YAML = """
version: "0.1"
name: extract
nodes:
  - id: extract
    type: agent
    agent: source_processor@1
    inputs: { source: scan.sources }
"""

LOOP_SELECT_YAML = """
version: "0.1"
name: mixed
nodes:
  - id: extract
    type: agent
    agent: source_processor@1
    inputs: { source: scan.sources }

  - id: refine
    type: loop
    params: { max_rounds: 3, on_max_rounds: pass }
    body:   { agent: requirements_writer@1, inputs: { extracts: extract.extract } }
    critic: { agent: requirements_critic@1, inputs: { doc: "@body", extracts: extract.extract } }
    outputs: { doc: "@body" }

  - id: choose
    type: select
    candidates: refine.doc
    selector: { agent: requirements_critic@1 }
"""


def _write_agent_package(
    root: Path, name: str, *, prompt: str = "Do the thing."
) -> Path:
    """Create a minimal on-disk agent package under <root>/agents/<name>/."""
    pkg = root / "agents" / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "agent.yaml").write_text(
        f"name: {name}\nversion: 1\nproduces: [{{port: out, type: extract@v1}}]\n",
        encoding="utf-8",
    )
    (pkg / "prompt.md").write_text(prompt, encoding="utf-8")
    return pkg


def load_pipeline(text: str) -> Pipeline:
    return Pipeline.model_validate(yaml.safe_load(text))


# --- package_hash ------------------------------------------------------------


def test_package_hash_deterministic_and_order_independent(tmp_path: Path) -> None:
    """SPEC §9: package_hash is stable across filesystem iteration order."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "b.txt").write_text("bbb", encoding="utf-8")
    (pkg / "a.txt").write_text("aaa", encoding="utf-8")
    sub = pkg / "nested"
    sub.mkdir()
    (sub / "c.txt").write_text("ccc", encoding="utf-8")

    h1 = package_hash(pkg)
    h2 = package_hash(pkg)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_package_hash_changes_with_content(tmp_path: Path) -> None:
    """SPEC §9: package_hash changes when any file's content changes."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.txt").write_text("aaa", encoding="utf-8")

    before = package_hash(pkg)
    (pkg / "a.txt").write_text("zzz", encoding="utf-8")
    after = package_hash(pkg)

    assert before != after


def test_package_hash_uses_posix_relative_paths(tmp_path: Path) -> None:
    """A nested file's relpath is included POSIX-style, not absolute or OS-specific."""
    pkg = tmp_path / "pkg"
    (pkg / "nested").mkdir(parents=True)
    (pkg / "nested" / "d.txt").write_text("ddd", encoding="utf-8")

    # Recreate an identical package elsewhere and confirm identical hash
    # (i.e. hash depends only on relative structure + content, not absolute path).
    pkg2 = tmp_path / "elsewhere" / "pkg2"
    (pkg2 / "nested").mkdir(parents=True)
    (pkg2 / "nested" / "d.txt").write_text("ddd", encoding="utf-8")

    assert package_hash(pkg) == package_hash(pkg2)


def test_package_hash_case_differing_names_is_platform_stable(tmp_path: Path) -> None:
    """Lines are sorted by ASCII bytes, not case-insensitive Path order (SPEC §9).

    'B' (66) sorts before 'a' (97), so the digest is fixed regardless of the
    host filesystem's Path comparison rules (Windows case-folds, POSIX does not).
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "B.txt").write_text("b", encoding="utf-8")
    (pkg / "a.txt").write_text("a", encoding="utf-8")

    assert package_hash(pkg) == (
        "sha256:9ba2530e8ceb2c25ca9c9d5f2063808c8ce3abfe39d4bbc5f9ea60f06416303f"
    )


# --- resolve_model -------------------------------------------------------------


def test_resolve_model_override_wins() -> None:
    """SPEC §7 priority: override > node model > default."""
    result = resolve_model(
        "extract",
        "node/model",
        overrides={"extract": "override/model"},
        default_model="default/model",
    )
    assert result == "override/model"


def test_resolve_model_node_wins_over_default() -> None:
    result = resolve_model(
        "extract",
        "node/model",
        overrides={},
        default_model="default/model",
    )
    assert result == "node/model"


def test_resolve_model_falls_back_to_default() -> None:
    result = resolve_model(
        "extract",
        None,
        overrides={},
        default_model="default/model",
    )
    assert result == "default/model"


def test_resolve_model_binding_passed_through_verbatim() -> None:
    """A ``@sel.winner_model`` scalar binding ignores overrides and default."""
    result = resolve_model(
        "extract",
        "@choose.winner_model",
        overrides={"extract": "override/model"},
        default_model="default/model",
    )
    assert result == "@choose.winner_model"


# --- used_agent_refs -----------------------------------------------------------


def test_used_agent_refs_dedup_and_order() -> None:
    """SPEC §9: distinct name@version refs, first-seen order, across node kinds."""
    pipeline = load_pipeline(LOOP_SELECT_YAML)
    refs = used_agent_refs(pipeline)

    assert refs == [
        "source_processor@1",
        "requirements_writer@1",
        "requirements_critic@1",
    ]


# --- build_resolved -------------------------------------------------------------


def test_build_resolved_fills_timeout_from_agent_default() -> None:
    """SPEC §9: params.timeout_s filled from agent defaults.timeout_s when None."""
    pipeline = load_pipeline(PIPELINE_YAML)
    from refract.models.agent import AgentSpec

    agents = {
        "source_processor@1": AgentSpec.model_validate(
            {
                "name": "source_processor",
                "version": 1,
                "produces": [{"port": "out", "type": "extract@v1"}],
                "defaults": {"timeout_s": 120},
            }
        )
    }

    resolved = build_resolved(pipeline, agents=agents, overrides={}, default_model=None)
    node = next(n for n in resolved["nodes"] if n["id"] == "extract")
    assert node["params"]["timeout_s"] == 120


def test_build_resolved_leaves_explicit_timeout_untouched() -> None:
    """Explicit node params.timeout_s is not overwritten by the agent default."""
    text = """
version: "0.1"
name: extract
nodes:
  - id: extract
    type: agent
    agent: source_processor@1
    inputs: { source: scan.sources }
    params: { timeout_s: 999 }
"""
    pipeline = load_pipeline(text)
    from refract.models.agent import AgentSpec

    agents = {
        "source_processor@1": AgentSpec.model_validate(
            {
                "name": "source_processor",
                "version": 1,
                "produces": [{"port": "out", "type": "extract@v1"}],
                "defaults": {"timeout_s": 120},
            }
        )
    }

    resolved = build_resolved(pipeline, agents=agents, overrides={}, default_model=None)
    node = next(n for n in resolved["nodes"] if n["id"] == "extract")
    assert node["params"]["timeout_s"] == 999


def test_build_resolved_fills_effective_model() -> None:
    from refract.models.agent import AgentSpec

    pipeline = load_pipeline(PIPELINE_YAML)
    agents = {
        "source_processor@1": AgentSpec.model_validate(
            {
                "name": "source_processor",
                "version": 1,
                "produces": [{"port": "out", "type": "extract@v1"}],
            }
        )
    }
    resolved = build_resolved(
        pipeline,
        agents=agents,
        overrides={"extract": "override/model"},
        default_model="default/model",
    )
    node = next(n for n in resolved["nodes"] if n["id"] == "extract")
    assert node["params"]["model"] == "override/model"


# --- build_snapshot end-to-end --------------------------------------------------


def _make_library(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    _write_agent_package(library, "source_processor")
    return library


def test_build_snapshot_end_to_end(tmp_path: Path) -> None:
    """SPEC §9: snapshot dir contains verbatim pipeline, resolved.yaml, agent copies, lock."""
    pipeline_path = tmp_path / "project" / "pipeline.yaml"
    pipeline_path.parent.mkdir(parents=True)
    pipeline_path.write_text(PIPELINE_YAML, encoding="utf-8")

    pipeline = load_pipeline(PIPELINE_YAML)
    library = _make_library(tmp_path)

    from refract.models.agent import AgentSpec

    agents = {
        "source_processor@1": AgentSpec.model_validate(
            {
                "name": "source_processor",
                "version": 1,
                "produces": [{"port": "out", "type": "extract@v1"}],
                "defaults": {"timeout_s": 42},
            }
        )
    }

    run_dir = tmp_path / "run-1"
    run_dir.mkdir()

    info = build_snapshot(
        run_dir,
        pipeline_path=pipeline_path,
        pipeline=pipeline,
        library_path=library,
        agents=agents,
        overrides={},
        default_model="kimi/kimi-k3",
    )

    assert info.snapshot_dir == run_dir / "snapshot"

    # pipeline.yaml verbatim copy
    copied_pipeline = info.snapshot_dir / "pipeline.yaml"
    assert copied_pipeline.read_bytes() == pipeline_path.read_bytes()

    # resolved.yaml is parseable and has effective model + timeout filled
    resolved = yaml.safe_load((info.snapshot_dir / "resolved.yaml").read_text("utf-8"))
    node = next(n for n in resolved["nodes"] if n["id"] == "extract")
    assert node["params"]["model"] == "kimi/kimi-k3"
    assert node["params"]["timeout_s"] == 42

    # agent package fully copied
    agent_dir = info.snapshot_dir / "agents" / "source_processor@1"
    assert (agent_dir / "prompt.md").is_file()
    assert (agent_dir / "agent.yaml").is_file()

    # lock file matches package_hash of the copied dir
    lock = json.loads((info.snapshot_dir / "agents.lock.json").read_text("utf-8"))
    assert lock == info.agents_lock
    assert lock["source_processor@1"] == package_hash(agent_dir)


def test_build_snapshot_missing_agent_raises(tmp_path: Path) -> None:
    """SPEC §9: build_snapshot raises FileNotFoundError for a missing agent package."""
    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(PIPELINE_YAML, encoding="utf-8")
    pipeline = load_pipeline(PIPELINE_YAML)

    library = tmp_path / "empty-library"
    library.mkdir()

    from refract.models.agent import AgentSpec

    agents = {
        "source_processor@1": AgentSpec.model_validate(
            {
                "name": "source_processor",
                "version": 1,
                "produces": [{"port": "out", "type": "extract@v1"}],
            }
        )
    }

    run_dir = tmp_path / "run-1"
    run_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        build_snapshot(
            run_dir,
            pipeline_path=pipeline_path,
            pipeline=pipeline,
            library_path=library,
            agents=agents,
        )
