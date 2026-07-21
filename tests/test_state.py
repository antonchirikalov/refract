"""Tests for the run ledger (refract/state.py) — SPEC §9."""

from __future__ import annotations

import json
from pathlib import Path

from refract.models.ledger import NodeStatus, RunStatus, StepOutcome, StepStatus
from refract.state import STATE_FILENAME, Ledger


def _make_ledger(run_dir: Path) -> Ledger:
    return Ledger.create(
        run_dir,
        run_id="run-1",
        pipeline="demo.yaml",
        node_ids=["a", "b"],
        created_at="2026-07-20T00:00:00Z",
    )


def test_round_trip_persistence(tmp_path: Path) -> None:
    """SPEC §9: create -> mutate -> load returns an equal state."""
    ledger = _make_ledger(tmp_path)
    ledger.set_node_status("a", NodeStatus.running)
    ledger.set_step(
        "a#1",
        node="a",
        status=StepStatus.done,
        outcome=StepOutcome.ok,
        tries=1,
        started_at="2026-07-20T00:00:01Z",
        finished_at="2026-07-20T00:00:02Z",
    )
    ledger.set_node_status("a", NodeStatus.done)

    reloaded = Ledger.load(tmp_path)
    assert reloaded.state.model_dump() == ledger.state.model_dump()


def test_crash_recovery_running_to_pending(tmp_path: Path) -> None:
    """SPEC §9: running steps/nodes become pending on load; other statuses untouched."""
    ledger = _make_ledger(tmp_path)
    ledger.set_step("a#1", node="a", status=StepStatus.running, tries=1)
    ledger.set_step(
        "b#1", node="b", status=StepStatus.done, outcome=StepOutcome.ok, tries=1
    )
    ledger.set_node_status("a", NodeStatus.running)
    ledger.set_node_status("b", NodeStatus.failed, error="boom")

    reloaded = Ledger.load(tmp_path)

    assert reloaded.get_step("a#1").status is StepStatus.pending
    assert reloaded.get_step("b#1").status is StepStatus.done
    assert reloaded.get_node("a").status is NodeStatus.pending
    assert reloaded.get_node("b").status is NodeStatus.failed
    assert reloaded.get_node("b").error == "boom"

    # The recovery must have been persisted back to disk.
    on_disk = json.loads((tmp_path / STATE_FILENAME).read_text("utf-8"))
    assert on_disk["steps"]["a#1"]["status"] == "pending"
    assert on_disk["nodes"]["a"]["status"] == "pending"
    assert on_disk["steps"]["b#1"]["status"] == "done"
    assert on_disk["nodes"]["b"]["status"] == "failed"


def test_atomicity_no_tmp_left_and_valid_json(tmp_path: Path) -> None:
    """I3: save() is atomic (tmp + os.replace); no stray tmp; content always valid."""
    ledger = _make_ledger(tmp_path)
    ledger.set_node_status("a", NodeStatus.running)

    tmp_path_file = tmp_path / (STATE_FILENAME + ".tmp")
    assert not tmp_path_file.exists()

    raw = json.loads((tmp_path / STATE_FILENAME).read_text("utf-8"))
    from refract.models.ledger import RunState

    RunState.model_validate(raw)  # must round-trip without error


def test_stale_tmp_file_does_not_corrupt_load(tmp_path: Path) -> None:
    """A stray leftover .tmp file from an interrupted write must not affect load()."""
    ledger = _make_ledger(tmp_path)
    ledger.set_node_status("a", NodeStatus.done)

    stale_tmp = tmp_path / (STATE_FILENAME + ".tmp")
    stale_tmp.write_text("{not valid json", encoding="utf-8")

    reloaded = Ledger.load(tmp_path)
    assert reloaded.get_node("a").status is NodeStatus.done

    # save() again should still succeed and clean up (replace) the tmp file.
    reloaded.set_node_status("b", NodeStatus.done)
    assert not stale_tmp.exists() or json.loads(stale_tmp.read_text("utf-8"))


def test_set_step_insert_then_update(tmp_path: Path) -> None:
    """set_step inserts on first call, updates in place on subsequent calls."""
    ledger = _make_ledger(tmp_path)

    ledger.set_step("a#1", node="a", status=StepStatus.running, tries=1)
    step = ledger.get_step("a#1")
    assert step is not None
    assert step.status is StepStatus.running
    assert step.tries == 1
    assert step.outcome is None

    ledger.set_step(
        "a#1",
        node="a",
        status=StepStatus.done,
        outcome=StepOutcome.ok,
        tries=2,
        finished_at="2026-07-20T00:01:00Z",
    )
    step = ledger.get_step("a#1")
    assert step is not None
    assert step.status is StepStatus.done
    assert step.outcome is StepOutcome.ok
    assert step.tries == 2
    assert step.finished_at == "2026-07-20T00:01:00Z"

    # No duplicate record was created.
    assert len(ledger.state.steps) == 1


def test_steps_for_node_filters(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    ledger.set_step("a#1", node="a", status=StepStatus.done, tries=1)
    ledger.set_step("a#2", node="a", status=StepStatus.failed, tries=2)
    ledger.set_step("b#1", node="b", status=StepStatus.done, tries=1)

    a_steps = ledger.steps_for_node("a")
    assert set(a_steps) == {"a#1", "a#2"}
    assert all(s.node == "a" for s in a_steps.values())

    b_steps = ledger.steps_for_node("b")
    assert set(b_steps) == {"b#1"}


def test_set_node_selection_records_winner(tmp_path: Path) -> None:
    """SPEC §10.3: select node exports winner/winner_model onto the node record."""
    ledger = _make_ledger(tmp_path)
    ledger.set_node_selection("a", winner="draft-2", winner_model="gpt-x")

    node = ledger.get_node("a")
    assert node is not None
    assert node.winner == "draft-2"
    assert node.winner_model == "gpt-x"

    reloaded = Ledger.load(tmp_path)
    assert reloaded.get_node("a").winner == "draft-2"
    assert reloaded.get_node("a").winner_model == "gpt-x"


def test_reset_failed_steps_only_failed(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    ledger.set_step(
        "a#1",
        node="a",
        status=StepStatus.failed,
        outcome=StepOutcome.failed_agent,
        tries=1,
        error="agent crashed",
    )
    ledger.set_step("a#2", node="a", status=StepStatus.done, tries=1)
    ledger.set_step("b#1", node="b", status=StepStatus.running, tries=1)

    reset_ids = ledger.reset_failed_steps()

    assert reset_ids == ["a#1"]
    step = ledger.get_step("a#1")
    assert step is not None
    assert step.status is StepStatus.pending
    assert step.outcome is None
    assert step.error is None

    # Untouched steps stay as they were.
    assert ledger.get_step("a#2").status is StepStatus.done
    assert ledger.get_step("b#1").status is StepStatus.running


def test_reset_failed_steps_empty_when_none_failed(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    ledger.set_step("a#1", node="a", status=StepStatus.done, tries=1)

    assert ledger.reset_failed_steps() == []


def test_has_failed_nodes(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    assert ledger.has_failed_nodes() is False

    ledger.set_node_status("a", NodeStatus.failed, error="oops")
    assert ledger.has_failed_nodes() is True


def test_enum_values_serialize_as_strings(tmp_path: Path) -> None:
    """SPEC §9: enums must serialize to their plain string form on disk."""
    ledger = _make_ledger(tmp_path)
    ledger.set_run_status(RunStatus.running)
    ledger.set_node_status("a", NodeStatus.running)
    ledger.set_step("a#1", node="a", status=StepStatus.running, tries=1)

    raw = json.loads((tmp_path / STATE_FILENAME).read_text("utf-8"))
    assert raw["status"] == "running"
    assert raw["nodes"]["a"]["status"] == "running"
    assert raw["steps"]["a#1"]["status"] == "running"


def test_node_ids_lists_all_nodes(tmp_path: Path) -> None:
    ledger = _make_ledger(tmp_path)
    assert set(ledger.node_ids()) == {"a", "b"}
