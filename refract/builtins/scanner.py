"""builtin/scanner (SPEC §13).

Produces collection<source@v1> from the input folder: each top-level file and
each subfolder becomes one source@v1 element (a subfolder is one element, whole).
Top-level entries whose name starts with ``.`` are skipped — they are tooling
artifacts (``.gitkeep``, ``.DS_Store``), not sources.
Deterministic, no runner — runs in ``steps/<node>/main/`` and writes the output
collection under ``output/<port>/`` (``_collection.json`` + per-slug payload dirs).

``source_hash``: a file → ``sha256`` of its bytes; a folder → ``sha256`` of the
sorted ``"<relpath>:<sha256(file)>"`` lines (mtime never participates, so the
hash is stable across copies — matches ``snapshot.package_hash``).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from refract.registry import slugify, unique_slug
from refract.models.types import (
    CollectionItem,
    CollectionManifest,
    CollectionStats,
    CollectionStatus,
)

_SOURCE_TYPE = "source@v1"
_COLLECTION_TYPE = "collection<source@v1>"
_MANIFEST_FILENAME = "_collection.json"


class ScannerParams(BaseModel):
    """Params for ``builtin/scanner`` (SPEC §13)."""

    model_config = ConfigDict(extra="forbid")

    exclude: list[str] = Field(default_factory=list)  # exact top-level names
    input: str | None = None  # override for the input path


# --- hashing ----------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def source_hash(path: Path) -> str:
    """Content hash of one source element (file or folder), SPEC §13.

    Public because ``discover`` (SPEC §20.2) assembles its collection with exactly
    these rules — one hashing scheme for both collection producers.
    """
    if path.is_dir():
        lines = sorted(
            f"{p.relative_to(path).as_posix()}:{_file_sha256(p)}"
            for p in path.rglob("*")
            if p.is_file()
        )
        digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    else:
        digest = _file_sha256(path)
    return f"sha256:{digest}"


# --- run --------------------------------------------------------------------


def run(
    *, params: ScannerParams, input_dir: Path, output_dir: Path, port: str
) -> CollectionManifest:
    """Scan ``input_dir`` into a ``collection<source@v1>`` under ``output_dir/<port>/``.

    Each top-level file and subfolder becomes one element; a subfolder is copied
    whole into its slug dir. Deterministic: entries are processed in sorted name
    order, so slug collision suffixes are stable.
    """
    input_dir = Path(input_dir)
    collection_dir = Path(output_dir) / port
    collection_dir.mkdir(parents=True, exist_ok=True)

    exclude = set(params.exclude)
    items: list[CollectionItem] = []
    taken: set[str] = set()
    ok = failed = 0

    entries = [] if not input_dir.is_dir() else sorted(input_dir.iterdir())
    for entry in entries:
        if entry.name in exclude or entry.name.startswith("."):
            continue  # dot-entries are tooling artifacts, never sources (SPEC §13)
        slug = unique_slug(slugify(entry.name), taken)
        taken.add(slug)
        slug_dir = collection_dir / slug
        try:
            item_hash = source_hash(entry)
            if entry.is_dir():
                shutil.copytree(entry, slug_dir)
            else:
                slug_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry, slug_dir / entry.name)
            status, error = CollectionStatus.ok, None
            ok += 1
        except OSError as exc:  # unreadable source: recorded, no payload
            item_hash = "sha256:"
            status, error = CollectionStatus.failed, str(exc)
            failed += 1
        items.append(
            CollectionItem(
                slug=slug,
                source=entry.name,
                source_hash=item_hash,
                status=status,
                path=f"{slug}/",
                error=error,
            )
        )

    manifest = CollectionManifest(
        type=_COLLECTION_TYPE,
        items=items,
        stats=CollectionStats(total=len(items), ok=ok, failed=failed),
    )
    (collection_dir / _MANIFEST_FILENAME).write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest
