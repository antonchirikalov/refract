"""builtin/brief (SPEC §20.4).

Reads the research brief from the project's input folder and hands it to the graph
as a ``brief@v1`` artifact — the deterministic, no-runner counterpart of the UI's
"type a topic instead of picking documents" flow (``input_mode: brief``, §8).

Kept separate from ``builtin/scanner`` on purpose: scanner turns a folder into a
collection to fan out over, while a brief is exactly one artifact that the
``discover`` node consumes (§20.1).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

DEFAULT_BRIEF_FILE = "brief.md"


class BriefParams(BaseModel):
    """Params for ``builtin/brief`` (SPEC §20.4)."""

    model_config = ConfigDict(extra="forbid")

    file: str = DEFAULT_BRIEF_FILE  # name inside the input folder
    input: str | None = None  # override for the input path


def run(
    *, params: BriefParams, input_dir: Path, output_dir: Path, port: str
) -> dict[str, object]:
    """Copy the brief into ``output_dir/<port>.md``; empty or missing → error.

    Returns a small summary for the ledger. Raising here means the node fails,
    which is what a missing brief must do: every downstream node depends on it.
    """
    source = Path(input_dir) / params.file
    if not source.is_file():
        raise FileNotFoundError(
            f"no brief at {params.file!r} in the input folder — "
            "a brief pipeline needs one (SPEC §20.4)"
        )
    text = source.read_text("utf-8", errors="replace").strip()
    if not text:
        raise ValueError(f"brief {params.file!r} is empty")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{port}.md"
    shutil.copyfile(source, target)
    return {"file": params.file, "chars": len(text)}
