"""Project and application configuration formats (SPEC §7).

``project.yaml`` (``ProjectConfig``), ``~/.refract/providers.yaml``
(``ProvidersFile``), ``~/.refract/mcp.yaml`` (``McpFile``).
"""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag


class ProjectDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None


class ProjectConfig(BaseModel):
    """``project.yaml`` (SPEC §7)."""

    model_config = ConfigDict(extra="forbid")

    version: str
    name: str
    input: str = "./input"
    defaults: ProjectDefaults = Field(default_factory=ProjectDefaults)


class ProviderConfig(BaseModel):
    """One provider entry (SPEC §7). Key = model prefix up to the first ``/``."""

    model_config = ConfigDict(extra="forbid")

    api_key_env: str
    max_concurrent: int = 4


class ProvidersFile(BaseModel):
    """``~/.refract/providers.yaml`` (SPEC §7)."""

    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    library_path: str | None = None


class McpStdioServer(BaseModel):
    """Stdio MCP server launched via a command (SPEC §7)."""

    model_config = ConfigDict(extra="forbid")

    command: list[str]
    env: dict[str, str] = Field(default_factory=dict)


class McpHttpServer(BaseModel):
    """Remote MCP server reached over HTTP (SPEC §7)."""

    model_config = ConfigDict(extra="forbid")

    url: str
    token_env: str | None = None


def _mcp_discriminator(v: object) -> str:
    if isinstance(v, dict):
        return "http" if "url" in v else "stdio"
    return "http" if getattr(v, "url", None) is not None else "stdio"


McpServer = Annotated[
    Union[
        Annotated[McpStdioServer, Tag("stdio")],
        Annotated[McpHttpServer, Tag("http")],
    ],
    Discriminator(_mcp_discriminator),
]


class McpFile(BaseModel):
    """``~/.refract/mcp.yaml`` (SPEC §7)."""

    model_config = ConfigDict(extra="forbid")

    servers: dict[str, McpServer] = Field(default_factory=dict)
