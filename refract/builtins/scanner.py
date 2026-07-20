"""builtin/scanner (SPEC §13).

Produces collection<source@v1> from the input folder: each top-level file and
each subfolder becomes one source@v1 element. Deterministic, no runner.

Phase 0: the parameter model and produced ports (read by the graph validator)
live here now; the execution ``run`` is wired up with the step lifecycle in a
later Phase 0 task.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ScannerParams(BaseModel):
    """Params for ``builtin/scanner`` (SPEC §13)."""

    model_config = ConfigDict(extra="forbid")

    exclude: list[str] = Field(default_factory=list)  # exact top-level names
    input: str | None = None  # override for the input path
