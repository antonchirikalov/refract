"""Tests for the pure turn-tracking helpers of the opencode adapter (SPEC §12).

``OpencodeRuntime`` itself is not in the automated suite (it needs a real
opencode binary — SPEC §18, docs/opencode-smoke.md). The turn snapshot and the
part→event mapping are pure, and they carry the I9 trace, so they are tested here.
"""

from __future__ import annotations

from refract.runtime.opencode import _events_from_parts, _TurnSnapshot


class TestTurnSnapshot:
    def test_starts_empty_and_not_completed(self) -> None:
        snap = _TurnSnapshot()
        assert snap.parts == []
        assert snap.text == ""
        assert snap.completed is False

    def test_absorbs_info_and_parts_and_joins_text(self) -> None:
        snap = _TurnSnapshot()
        snap.absorb(
            {"role": "assistant", "cost": 0.01},
            [{"type": "text", "text": "wrote "}, {"type": "text", "text": "the doc"}],
        )
        assert snap.text == "wrote the doc"
        assert snap.info["cost"] == 0.01

    def test_completed_follows_time_completed(self) -> None:
        snap = _TurnSnapshot()
        snap.absorb({"time": {"created": 1}}, [])
        assert snap.completed is False
        snap.absorb({"time": {"created": 1, "completed": 2}}, [])
        assert snap.completed is True

    def test_a_partless_post_body_cannot_erase_polled_parts(self) -> None:
        # The live failure mode this guards: the POST body arrived with no parts
        # while polling had already collected the whole turn, and the trace
        # (raw.txt / agent.events.jsonl, I9) came out empty.
        snap = _TurnSnapshot()
        snap.absorb({}, [{"type": "text", "text": "draft written"}])
        snap.absorb({"cost": 0.02}, [])

        assert snap.text == "draft written"
        assert snap.info["cost"] == 0.02

    def test_ignores_malformed_payloads(self) -> None:
        snap = _TurnSnapshot()
        snap.absorb("nope", "also nope")
        snap.absorb(None, [{"type": "text", "text": "kept"}, "junk"])

        assert snap.parts == [{"type": "text", "text": "kept"}]
        assert snap.text == "kept"


class TestEventsFromParts:
    def test_tool_parts_become_tool_call_events(self) -> None:
        events = _events_from_parts(
            "write",
            [
                {"type": "tool", "tool": "edit", "state": {"status": "completed"}},
                {"type": "text", "text": "done"},
            ],
        )
        kinds = [e["type"] for e in events]
        assert kinds == ["tool_call", "log"]
        assert events[0]["payload"]["tool"] == "edit"  # type: ignore[index]

    def test_empty_parts_still_yield_one_record(self) -> None:
        # agent.events.jsonl must never be an empty file (I9).
        assert len(_events_from_parts("write", [])) == 1

    def test_fallback_message_is_caller_supplied(self) -> None:
        # A timed-out step must not be described as "message complete" — that
        # claim sent a live investigation down the wrong path.
        events = _events_from_parts("write", [], fallback="timed out, no reply")
        assert events[0]["payload"]["message"] == "timed out, no reply"  # type: ignore[index]
