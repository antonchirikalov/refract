"""Tests for the authoring catalog (SPEC §19.1, §19.5 test_catalog)."""

from __future__ import annotations

import json
from pathlib import Path

from refract.builtins import BUILTINS
from refract.catalog import build_catalog
from refract.graph import load_agents
from refract.registry import ArtifactRegistry

LIBRARY = Path(__file__).resolve().parents[1] / "library"


def _catalog() -> dict[str, object]:
    return build_catalog(LIBRARY)


class TestCompleteness:
    def test_every_library_agent_is_listed_with_its_contract(self) -> None:
        agents, errors = load_agents(LIBRARY)
        assert errors == []
        catalog = _catalog()
        entries = {e["ref"]: e for e in catalog["agents"]}  # type: ignore[union-attr,index]
        assert set(entries) == set(agents)
        for ref, spec in agents.items():
            entry = entries[ref]
            assert [p["port"] for p in entry["consumes"]] == [
                p.port for p in spec.consumes
            ]
            assert [p["type"] for p in entry["produces"]] == [
                p.type for p in spec.produces
            ]
            assert entry["needs"] == spec.needs

    def test_every_artifact_type_is_listed(self) -> None:
        registry = ArtifactRegistry.load(LIBRARY)
        catalog = _catalog()
        ids = {t["id"] for t in catalog["artifact_types"]}  # type: ignore[union-attr,index]
        assert ids == set(registry.names())

    def test_every_builtin_is_listed_with_a_params_schema(self) -> None:
        catalog = _catalog()
        listed = {b["type"]: b for b in catalog["builtins"]}  # type: ignore[union-attr,index]
        assert set(listed) == {f"builtin/{n}" for n in BUILTINS}
        scanner = listed["builtin/scanner"]
        assert scanner["params_schema"]["type"] == "object"
        assert "exclude" in scanner["params_schema"]["properties"]
        assert scanner["produces"][0]["type"] == "collection<source@v1>"

    def test_meta_node_kinds_carry_their_blocks_and_params(self) -> None:
        kinds = {k["kind"]: k for k in _catalog()["node_kinds"]}  # type: ignore[union-attr,index]
        assert set(kinds) == {"agent", "loop", "select", "discover"}
        assert kinds["loop"]["blocks"] == {"body": "agent", "critic": "agent"}
        assert "max_rounds" in kinds["loop"]["params_schema"]["properties"]
        assert kinds["select"]["required"] == ["candidates", "selector"]
        assert kinds["agent"]["fan_out"] == ["map", "map_over"]
        # discover is the other legal collection producer (SPEC §20)
        assert kinds["discover"]["outputs"] == ["sources"]

    def test_templates_are_listed(self) -> None:
        assert set(_catalog()["templates"]) >= {  # type: ignore[arg-type]
            "extract",
            "discovery",
            "solution_design",
        }


class TestConstraints:
    def test_constraints_are_keyed_by_validator_codes(self) -> None:
        # The point of §19.1: a builder LLM gets the rule AND the code the
        # validator will answer with, so its next patch can react to the error.
        from refract.models.pipeline import Pipeline  # noqa: F401  (import guard)

        from refract.graph import Code

        known = {c.value for c in Code}
        for entry in _catalog()["constraints"]:  # type: ignore[union-attr]
            assert entry["code"] in known, entry
            assert entry["rule"].strip()

    def test_the_invariant_rules_a_builder_breaks_first_are_present(self) -> None:
        codes = {c["code"] for c in _catalog()["constraints"]}  # type: ignore[union-attr,index]
        assert {
            "E_NESTED_MAP",
            "E_LOOP_SHAPE",
            "E_AGENT_PRODUCES_COLLECTION",
            "E_BINDING_ILLEGAL",
        } <= codes


class TestNoLeaks:
    def test_catalog_carries_no_paths_or_secrets(self, tmp_path: Path) -> None:
        # I8: the catalog is shipped to clients; it must not carry the library
        # location, the user's home, or anything key-shaped.
        blob = json.dumps(_catalog(), ensure_ascii=False)
        assert str(LIBRARY) not in blob
        assert "C:\\" not in blob and "/Users/" not in blob
        assert "api_key" not in blob.lower()
        assert "sk-" not in blob

    def test_catalog_is_json_serializable(self) -> None:
        json.dumps(_catalog())  # no pydantic objects, no Paths, no enums leaking
