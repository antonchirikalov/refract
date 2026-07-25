"""Scoped edits to a ``pipeline.yaml`` from the UI inspector (SPEC §19.2.1).

Two things and no more: which model runs a node (or one of a meta-node's blocks) and
that node's scalar params. The general patch vocabulary was considered and rejected
(§19.2) — this exists because an inspector that cannot change a model or a loop's round
count is not an inspector.

Edits go through ruamel's round-trip loader, so a pipeline started from a template
keeps its comments and key order: losing the explanation because someone switched a
model would be a poor trade.
"""

from __future__ import annotations

import io
from typing import Any

from pydantic import BaseModel
from ruamel.yaml import YAML

from refract.models.pipeline import (
    AgentParams,
    DiscoverParams,
    LoopParams,
    SelectParams,
)

BLOCKS = ("body", "critic", "selector")

_PARAMS_MODEL: dict[str, type[BaseModel]] = {
    "agent": AgentParams,
    "loop": LoopParams,
    "select": SelectParams,
    "discover": DiscoverParams,
}


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # never re-wrap a line the author wrote
    return y


def _validate_params(node_type: str, params: dict[str, Any]) -> None:
    """Check keys/types against the node's params model (SPEC §8.2)."""
    model = _PARAMS_MODEL.get(node_type)
    if model is None:  # builtin params are the builtin's own concern (§13)
        return
    for key, value in params.items():
        if key not in model.model_fields:
            raise ValueError(f"unknown param {key!r} for a {node_type} node")
        if isinstance(value, dict | list):
            raise ValueError(f"param {key!r} must be a scalar")
    model.model_validate({**params})


def apply_node_patch(
    text: str,
    *,
    node_id: str,
    model: str | None = None,
    unset_model: bool = False,
    block: str | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """Return ``text`` with the node's model/params changed (SPEC §19.2.1).

    Raises ``KeyError`` for an unknown node or block and ``ValueError`` for a param
    the node's model does not define. Never validates the GRAPH — the caller does
    that on the result, because only it knows the project's registry and providers.
    """
    yaml = _yaml()
    doc = yaml.load(text)
    nodes = doc.get("nodes") if isinstance(doc, dict) else None
    if not isinstance(nodes, list):
        raise ValueError("pipeline has no nodes list")

    target = next(
        (n for n in nodes if isinstance(n, dict) and n.get("id") == node_id), None
    )
    if target is None:
        raise KeyError(f"no node {node_id!r} in this pipeline")

    node_type = str(target.get("type", ""))
    if block is not None:
        if block not in BLOCKS:
            raise ValueError(f"unknown block {block!r}")
        if block not in target:
            raise KeyError(f"node {node_id!r} has no {block!r} block")

    if unset_model:
        # back to the project default (§7): drop the field rather than write null
        holder = target[block] if block else target.get("params")
        if isinstance(holder, dict):
            holder.pop("model", None)
    elif model is not None:
        if block:
            target[block]["model"] = model
        else:
            if not isinstance(target.get("params"), dict):
                target["params"] = {}
            target["params"]["model"] = model

    if params:
        _validate_params(node_type, params)
        if not isinstance(target.get("params"), dict):
            target["params"] = {}
        for key, value in params.items():
            target["params"][key] = value

    out = io.StringIO()
    yaml.dump(doc, out)
    return out.getvalue()
