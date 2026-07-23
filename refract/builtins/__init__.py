"""Builtin-node registry (SPEC §13).

``BUILTINS: dict[str, BuiltinDef]``; ``BuiltinDef = {params_model, produces,
run}``. The graph validator reads node ports and the params model from here.
The ``run`` callables are attached with the step lifecycle in a later Phase 0
task; ``None`` means "not yet executable".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from refract.builtins.scanner import ScannerParams
from refract.builtins.scanner import run as scanner_run
from refract.models.agent import Port


@dataclass(frozen=True)
class BuiltinDef:
    """A builtin node definition (SPEC §13).

    ``run`` is a deterministic, synchronous callable invoked by the scheduler as
    ``run(params=<params_model instance>, input_dir=..., output_dir=..., port=...)``
    and returns the produced artifact/manifest. ``None`` means "not yet executable".
    """

    params_model: type[BaseModel]
    produces: list[Port]
    run: Callable[..., object] | None = None


BUILTINS: dict[str, BuiltinDef] = {
    "scanner": BuiltinDef(
        params_model=ScannerParams,
        produces=[Port(port="sources", type="collection<source@v1>")],
        run=scanner_run,
    ),
}

__all__ = ["BUILTINS", "BuiltinDef", "ScannerParams"]
