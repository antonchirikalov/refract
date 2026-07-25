"""Block catalog for pipeline authoring (SPEC §19.1).

One machine-readable answer to "what can a pipeline be built from": artifact
types, agent contracts, builtin nodes, meta-node shapes, templates, and the
graph constraints — each constraint named by the validator code it would trigger,
so a builder LLM learns from the same error vocabulary the engine speaks.

A pure projection of what already exists (registry §5, agent packages §6,
``BUILTINS`` §13, meta-node models §8.1). No new on-disk format, no secrets and
no filesystem paths (I8) — providers and models come from ``GET /api/models``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from refract.builtins import BUILTINS
from refract.graph import load_agents
from refract.models.agent import AgentSpec, Port, capability_tier
from refract.models.pipeline import (
    AgentParams,
    DiscoverParams,
    LoopParams,
    SelectParams,
)
from refract.registry import ArtifactRegistry

CATALOG_VERSION = "0.1"

# SPEC §16 as data: the rule a builder must respect, keyed by the code the
# validator answers with when it is broken.
CONSTRAINTS: list[dict[str, str]] = [
    {
        "code": "E_LOOP_SHAPE",
        "rule": "loop has exactly one body and one critic agent; the critic's primary port must be verdict@v1",
    },
    {
        "code": "E_NESTED_MAP",
        "rule": "map: cannot consume a collection produced by a map/map_over node; one map per node",
    },
    {
        "code": "E_MAP_CONFLICT",
        "rule": "a node declares either map: or map_over:, never both",
    },
    {
        "code": "E_MAP_PORT_AMBIGUOUS",
        "rule": "a mapped node must have exactly one non-optional input port to fan out over",
    },
    {
        "code": "E_BINDING_ILLEGAL",
        "rule": "the only scalar binding is @<select>.winner_model in a model: field",
    },
    {
        "code": "E_AGENT_PRODUCES_COLLECTION",
        "rule": "agents never produce collections — fan-out belongs to the engine (I6)",
    },
    {
        "code": "E_TYPE_MISMATCH",
        "rule": "an input's type must match the source port's type; a select's selector produces selection@v1",
    },
    {
        "code": "E_INPUT_MISSING",
        "rule": "every non-optional consumes port must be wired",
    },
    {
        "code": "E_UNKNOWN_NODE_REF",
        "rule": "input sources reference an existing node and one of its ports",
    },
    {
        "code": "E_UNKNOWN_AGENT",
        "rule": "agent refs are name@version present in the library",
    },
    {
        "code": "E_UNKNOWN_TYPE",
        "rule": "artifact types must be registered; control types are reserved (E_RESERVED_TYPE)",
    },
    {"code": "E_CYCLE", "rule": "the graph is acyclic"},
    {"code": "E_DUP_NODE_ID", "rule": "node ids are unique"},
    {"code": "E_HITL_SHAPE", "rule": "at most one question@v1 port per agent"},
    {
        "code": "E_DISCOVER_SHAPE",
        "rule": "a discover node's agent produces exactly one dir-kind artifact; the engine turns it into collection<source@v1>",
    },
    {
        "code": "E_MODEL_UNRESOLVED",
        "rule": "every executable node resolves to a provider/model",
    },
    {
        "code": "W_CACHE_UNSUPPORTED",
        "rule": "cache: true is accepted and ignored in v0.1 (warning)",
    },
    {
        "code": "W_SECURITY",
        "rule": "a node reachable from scanned input that needs bash/webfetch/mcp is flagged (warning)",
    },
]


def _port(port: Port) -> dict[str, Any]:
    return {"port": port.port, "type": port.type, "optional": port.optional}


def _params_schema(model: type) -> dict[str, Any]:
    """JSON Schema of a params model, with pydantic's $defs kept inline."""
    schema = model.model_json_schema()  # type: ignore[attr-defined]
    assert isinstance(schema, dict)
    return schema


def _agent_entry(ref: str, spec: AgentSpec) -> dict[str, Any]:
    tiers = [capability_tier(cap) for cap in spec.needs]
    order = ("safe", "moderate", "dangerous")
    max_tier = max(tiers, key=order.index) if tiers else "safe"
    return {
        "ref": ref,
        "name": spec.name,
        "version": spec.version,
        "description": " ".join(spec.description.split()),
        "consumes": [_port(p) for p in spec.consumes],
        "produces": [_port(p) for p in spec.produces],
        "needs": list(spec.needs),
        "max_tier": max_tier,
        "timeout_s": spec.defaults.timeout_s,
    }


def _type_entry(name: str, resolved: Any) -> dict[str, Any]:
    return {
        "id": name,
        "kind": resolved.kind.value
        if hasattr(resolved.kind, "value")
        else str(resolved.kind),
        "format": (
            resolved.format.value
            if resolved.format is not None and hasattr(resolved.format, "value")
            else resolved.format
        ),
        "inline": resolved.inline,
        "rules": len(resolved.rules),
        "has_schema": resolved.schema is not None,
        "builtin": resolved.is_builtin,
        "control": resolved.is_control_type,
    }


def build_catalog(library_path: Path | str) -> dict[str, Any]:
    """Assemble the catalog for the library at ``library_path`` (SPEC §19.1)."""
    library_path = Path(library_path)
    registry = ArtifactRegistry.load(library_path)
    agents, _errors = load_agents(library_path)

    templates_dir = library_path / "templates"
    templates = (
        sorted(p.stem for p in templates_dir.glob("*.yaml"))
        if templates_dir.is_dir()
        else []
    )

    return {
        "version": CATALOG_VERSION,
        "artifact_types": [
            _type_entry(name, registry.get(name)) for name in sorted(registry.names())
        ],
        "agents": [_agent_entry(ref, agents[ref]) for ref in sorted(agents)],
        "builtins": [
            {
                "type": f"builtin/{name}",
                "produces": [_port(p) for p in defn.produces],
                "params_schema": _params_schema(defn.params_model),
                "executable": defn.run is not None,
            }
            for name, defn in sorted(BUILTINS.items())
        ],
        "node_kinds": [
            {
                "kind": "agent",
                "params_schema": _params_schema(AgentParams),
                "fan_out": ["map", "map_over"],
                "required": ["agent"],
            },
            {
                "kind": "loop",
                "params_schema": _params_schema(LoopParams),
                "blocks": {"body": "agent", "critic": "agent"},
                "required": ["body", "critic", "outputs"],
            },
            {
                "kind": "discover",
                "params_schema": _params_schema(DiscoverParams),
                "required": ["agent"],
                "outputs": ["sources"],
                "note": (
                    "network source of collection<source@v1>; its agent produces one "
                    "dir artifact and the engine assembles the collection (SPEC §20)"
                ),
            },
            {
                "kind": "select",
                "params_schema": _params_schema(SelectParams),
                "blocks": {"selector": "agent"},
                "required": ["candidates", "selector"],
                "outputs": ["out"],
            },
        ],
        "templates": templates,
        "constraints": CONSTRAINTS,
    }
