"""Run ledger (``state.json``) and events (``events.jsonl``) formats (SPEC §9).

Two ledger levels — nodes and steps. Enums cover the step/node/run status
machines and the step outcome taxonomy. ``state.json`` is written only by the
engine, only atomically (I3); models here are the format contract.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    waiting_human = "waiting_human"  # phase 3
    cancelled = "cancelled"
    reused = "reused"


class StepOutcome(str, Enum):
    ok = "ok"
    failed_validation = "failed_validation"
    failed_agent = "failed_agent"
    failed_infra = "failed_infra"
    timeout = "timeout"


class NodeStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"
    reused = "reused"
    waiting_human = "waiting_human"  # phase 3


class RunStatus(str, Enum):
    created = "created"
    validating = "validating"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    waiting_human = "waiting_human"  # phase 3


class NodeState(BaseModel):
    """Ledger record for a node (SPEC §9). ``winner*`` set by select nodes."""

    model_config = ConfigDict(extra="forbid")

    status: NodeStatus
    error: str | None = None
    winner: str | None = None
    winner_model: str | None = None


class StepState(BaseModel):
    """Ledger record for a step (SPEC §9)."""

    model_config = ConfigDict(extra="forbid")

    node: str
    status: StepStatus
    outcome: StepOutcome | None = None
    tries: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class RunState(BaseModel):
    """``state.json`` (SPEC §9)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: RunStatus
    pipeline: str
    created_at: str
    finished_at: str | None = None
    reuse_from: str | None = None
    force_nodes: list[str] = Field(default_factory=list)
    nodes: dict[str, NodeState] = Field(default_factory=dict)
    steps: dict[str, StepState] = Field(default_factory=dict)


class EventType(str, Enum):
    run_state_changed = "run_state_changed"
    step_state_changed = "step_state_changed"
    node_state_changed = "node_state_changed"
    heartbeat = "heartbeat"
    tool_call = "tool_call"
    log = "log"
    question = "question"  # phase 3


class Event(BaseModel):
    """One ``events.jsonl`` record (SPEC §9). ``seq`` assigned by the writer."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    ts: str
    type: EventType
    step_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
