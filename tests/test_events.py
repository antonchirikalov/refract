"""Tests for events.jsonl writer (SPEC §9)."""

from __future__ import annotations

import json
from pathlib import Path

from refract.events import EventWriter
from refract.models.ledger import Event


def _clock_seq() -> "callable":
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"T{counter['n']}"

    return clock


class TestSeqAndTs:
    def test_seq_increments_and_ts_from_clock(self, tmp_path: Path) -> None:
        # SPEC §9
        writer = EventWriter(tmp_path, clock=_clock_seq())
        e1 = writer.emit({"type": "log"})
        e2 = writer.emit({"type": "log"})
        e3 = writer.emit({"type": "log"})

        assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)
        assert (e1.ts, e2.ts, e3.ts) == ("T1", "T2", "T3")


class TestAppendOnlyJsonl:
    def test_lines_are_newline_delimited_json_and_roundtrip(
        self, tmp_path: Path
    ) -> None:
        # SPEC §9: events.jsonl is append-only, one JSON record per line
        writer = EventWriter(tmp_path, clock=_clock_seq())
        writer.emit({"type": "run_state_changed", "payload": {"from": "created"}})
        writer.emit({"type": "node_state_changed", "step_id": "gen", "payload": {}})

        path = tmp_path / "events.jsonl"
        lines = path.read_text("utf-8").splitlines()
        assert len(lines) == 2

        records = [Event.model_validate(json.loads(line)) for line in lines]
        assert records[0].type.value == "run_state_changed"
        assert records[0].seq == 1
        assert records[1].step_id == "gen"
        assert records[1].seq == 2

    def test_append_only_new_writer_continues_seq_from_zero_but_keeps_old_lines(
        self, tmp_path: Path
    ) -> None:
        # Verifies the file is truly append-only across writer instances; the
        # engine only ever constructs one EventWriter per run, but the file's
        # append semantics (never truncated) is the property under test.
        writer1 = EventWriter(tmp_path, clock=_clock_seq())
        writer1.emit({"type": "log"})

        writer2 = EventWriter(tmp_path, clock=_clock_seq())
        writer2.emit({"type": "log"})

        path = tmp_path / "events.jsonl"
        lines = path.read_text("utf-8").splitlines()
        assert len(lines) == 2


class TestDefaults:
    def test_payload_defaults_to_empty_dict_and_step_id_to_none(
        self, tmp_path: Path
    ) -> None:
        # SPEC §9
        writer = EventWriter(tmp_path, clock=_clock_seq())
        record = writer.emit({"type": "heartbeat"})

        assert record.payload == {}
        assert record.step_id is None
