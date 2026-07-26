"""API + built UI for the end-to-end tests, with a scripted runtime.

Playwright boots this (see playwright.config.ts). It is the real engine and the real
API — only the AgentRuntime is scripted, the same way the python suite injects
MockRuntime (SPEC §18): no network, no provider quota, no opencode.

Each start gets a fresh workspace under a temp dir, so a spec's assertions never
depend on what a previous run left behind.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from refract.api import create_app  # noqa: E402
from refract.cli import AppConfig  # noqa: E402
from refract.models.config import McpFile, ProvidersFile  # noqa: E402
from refract.runtime.base import EventCallback, StepResult, StepSpec  # noqa: E402

# The prompt carries each output's port, type and path (generated from the contract,
# I5), which is all a scripted runtime needs to produce believable artifacts.
OUTPUT_RE = re.compile(
    r"### `(?P<port>[^`]+)` \((?P<type>[^)]+)\)(?P<opt> — optional)?\s*\n+"
    r"Write to: `(?P<path>[^`]+)`",
    re.MULTILINE,
)

REQUIREMENTS = """# Requirements: Warehouse Goods Receiving

## Functional

- FR-1: The receiver scans a pallet barcode on an Android handheld.
- FR-2: The system validates the scan against the ERP's expected delivery.

## Non-functional

- NFR-1: Receiving stays usable for 4 hours without network.
"""

DESIGN = (
    "# Solution Design\n\nAn offline-first client over an ERP integration layer.\n"
    + ("\nBody paragraph with enough substance to clear the length rule.\n" * 30)
    # design_doc@v1 requires this section: the gate checks that it exists, the critic
    # judges whether it is honest
    + "\n## Assumptions to confirm\n\n- Versions named above are proposals.\n"
)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _first_ok_slug(workdir: Path) -> str:
    for manifest in workdir.glob("input/*/_collection.json"):
        for item in json.loads(manifest.read_text("utf-8")).get("items", []):
            if item.get("status") == "ok":
                return str(item["slug"])
    return "unknown"


class ScriptedRuntime:
    """Writes contract-shaped artifacts instead of calling a model."""

    def __init__(self, step_delay_s: float = 0.35) -> None:
        self._delay = step_delay_s

    async def run_step(self, spec: StepSpec, on_event: EventCallback) -> StepResult:
        on_event(
            {"type": "heartbeat", "step_id": spec.step_id, "payload": {"elapsed_s": 1}}
        )
        await asyncio.sleep(self._delay)  # so the UI has something to show
        for match in OUTPUT_RE.finditer(spec.prompt):
            if match.group("opt"):
                continue  # optional ports (question@v1) stay unwritten
            atype = match.group("type")
            target = spec.workdir / match.group("path").rstrip("/")
            if atype == "verdict@v1":
                _write_json(target, {"verdict": "approved", "issues": []})
            elif atype == "selection@v1":
                _write_json(
                    target,
                    {"winner": _first_ok_slug(spec.workdir), "reason": "scripted"},
                )
            elif atype == "extract@v1":
                _write_json(
                    target,
                    {
                        "source": spec.step_id.split(":")[-1],
                        "requirements": [
                            {"text": "Barcode receiving.", "category": "functional"}
                        ],
                        "decisions": [],
                        "constraints": [],
                        "open_questions": [],
                        "trust_level": "medium",
                    },
                )
            elif atype == "requirements@v1":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(REQUIREMENTS, encoding="utf-8")
            elif atype == "design_doc@v1":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(DESIGN, encoding="utf-8")
            elif atype == "found_sources@v1":
                target.mkdir(parents=True, exist_ok=True)
                (target / "source-one.md").write_text("# One\n", encoding="utf-8")
                (target / "source-two.md").write_text("# Two\n", encoding="utf-8")
                (target / "source-three.md").write_text("# Three\n", encoding="utf-8")
            elif atype in ("arch_report@v1", "discovery_report@v1", "brief@v1"):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# Questions\n\n1. Which ERP?\n", encoding="utf-8")
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target / "note.md").write_text("# note\n", encoding="utf-8")
        (spec.workdir / "raw.txt").write_text("[scripted runtime]", encoding="utf-8")
        (spec.workdir / "agent.events.jsonl").write_text(
            json.dumps({"type": "log", "payload": {"message": "scripted"}}) + "\n",
            encoding="utf-8",
        )
        return StepResult(completed=True, usage={"cost": 0})

    async def close(self) -> None:
        return None


def build() -> tuple[object, Path]:
    home = Path(tempfile.mkdtemp(prefix="refract-e2e-"))
    workspace = home / "projects"
    workspace.mkdir(parents=True)
    os.environ["REFRACT_HOME"] = str(home)
    os.environ.setdefault("MOONSHOT_API_KEY", "e2e")
    os.environ.setdefault("OPENAI_API_KEY", "e2e")
    # a project with documents already in place, for specs that only need to run one
    shutil.copytree(
        REPO / "examples" / "extract-project",
        workspace / "extract-project",
        ignore=shutil.ignore_patterns("runs", ".opencode", "node_modules"),
    )
    app_config = AppConfig(
        library_path=REPO / "library",
        providers=ProvidersFile.model_validate(
            {
                "providers": {
                    "kimi": {"api_key_env": "MOONSHOT_API_KEY", "models": ["k3"]},
                    "openai": {"api_key_env": "OPENAI_API_KEY", "models": ["gpt-5.6"]},
                }
            }
        ),
        mcp=McpFile(),
    )
    api = create_app(
        projects_root=workspace,
        app_config=app_config,
        runtime_factory=lambda app, pipeline: ScriptedRuntime(),
        static_dir=REPO / "web" / "dist",
    )
    return api, workspace


if __name__ == "__main__":
    import uvicorn

    api, workspace = build()
    print(f"e2e workspace: {workspace}", flush=True)
    uvicorn.run(api, host="127.0.0.1", port=8799, log_level="warning")
