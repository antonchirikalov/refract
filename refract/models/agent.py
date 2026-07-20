"""Agent package format — ``agent.yaml`` (SPEC §6).

Structural model only: port types are strings (may be ``collection<X>``);
semantic rules that need registry context (single primary output, no
collection produces, HITL shape) are enforced by the graph validator (§8.3).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_PORT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_BASE_CAPABILITIES = frozenset({"read", "edit", "vision", "bash", "webfetch"})


class Port(BaseModel):
    """A consumes/produces port (SPEC §6)."""

    model_config = ConfigDict(extra="forbid")

    port: str
    type: str
    optional: bool = False

    @field_validator("port")
    @classmethod
    def _port_name(cls, v: str) -> str:
        if not _PORT_RE.match(v):
            raise ValueError(f"invalid port name: {v!r}")
        return v


class AgentDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_s: int = 3600


class AgentSpec(BaseModel):
    """``agent.yaml`` (SPEC §6). Referenced from the graph as ``name@version``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int
    description: str = ""
    consumes: list[Port] = Field(default_factory=list)
    produces: list[Port]
    needs: list[str] = Field(default_factory=list)
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"invalid agent name: {v!r}")
        return v

    @field_validator("needs")
    @classmethod
    def _capabilities(cls, v: list[str]) -> list[str]:
        for cap in v:
            if cap in _BASE_CAPABILITIES:
                continue
            if cap.startswith("mcp:") and len(cap) > len("mcp:"):
                continue
            raise ValueError(f"unknown capability: {cap!r}")
        return v

    @property
    def ref(self) -> str:
        """Library reference string, e.g. ``source_processor@1``."""
        return f"{self.name}@{self.version}"
