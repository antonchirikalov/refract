"""events.jsonl append-only writer (SPEC §9).

A single writer assigns the monotonic ``seq`` and timestamp, then appends one
JSON record per event. The scheduler drives an asyncio loop but emits events
synchronously from step callbacks, so a synchronous append writer (one owner,
never shared) already satisfies the "single writer" rule. The clock is
injectable so tests stay deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from refract.models.ledger import Event, EventType

EVENTS_FILENAME = "events.jsonl"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventWriter:
    """Owns a run's ``events.jsonl``; the only writer of it (SPEC §9)."""

    def __init__(
        self, run_dir: Path | str, *, clock: Callable[[], str] = utcnow_iso
    ) -> None:
        self.path = Path(run_dir) / EVENTS_FILENAME
        self._clock = clock
        self._seq = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Mapping[str, object]) -> Event:
        """Assign ``seq``/``ts`` and append the record (append-only, UTF-8)."""
        self._seq += 1
        raw_payload = event.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
        raw_step = event.get("step_id")
        record = Event(
            seq=self._seq,
            ts=self._clock(),
            type=EventType(str(event["type"])),
            step_id=str(raw_step) if raw_step is not None else None,
            payload=payload,
        )
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        return record
