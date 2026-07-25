"""Tests for the Phase 2 REST/WS API (SPEC §15).

All tests use ``fastapi.testclient.TestClient`` + a MockRuntime factory — no
network, no real opencode. A temp ``projects_root`` holds a copy of
``examples/demo-project``; ``AppConfig`` points at the repo ``library`` and a
single ``kimi`` provider (matching the demo project's default model
``kimi/kimi-k3`` and how ``test_cli`` builds AppConfig). The MockRuntime writes
a valid ``requirements.md`` for ``write:*`` so the run completes fast and
deterministically.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("fastapi")  # API tests need the optional `api` extra installed
from fastapi.testclient import TestClient  # noqa: E402

from refract.api import create_app  # noqa: E402
from refract.cli import AppConfig  # noqa: E402
from refract.models.config import ProvidersFile  # noqa: E402
from refract.models.pipeline import Pipeline  # noqa: E402
from refract.runtime.mock import MockRuntime, ScriptedResponse  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PATH = REPO_ROOT / "library"
DEMO_PROJECT = REPO_ROOT / "examples" / "demo-project"

REQ = "# Requirements: Demo\n\n- FR-1: the system shall do a thing.\n"


def _clock_seq() -> Callable[[], str]:
    counter = {"n": 0}

    def clock() -> str:
        counter["n"] += 1
        return f"T{counter['n']}"

    return clock


def _app_config() -> AppConfig:
    providers = ProvidersFile.model_validate(
        {
            "providers": {
                "kimi": {"api_key_env": "MOONSHOT_API_KEY", "max_concurrent": 4}
            }
        }
    )
    return AppConfig(library_path=LIBRARY_PATH, providers=providers)


def _mock_factory(app: AppConfig, pipeline: Pipeline) -> MockRuntime:
    return MockRuntime({"write:*": [ScriptedResponse(files={"requirements.md": REQ})]})


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    shutil.copytree(
        DEMO_PROJECT,
        projects_root / "demo-project",
        ignore=shutil.ignore_patterns("runs"),
    )
    api = create_app(
        projects_root=projects_root,
        app_config=_app_config(),
        runtime_factory=_mock_factory,
        clock=_clock_seq(),
    )
    return TestClient(api)


def _wait_completed(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        if resp.status_code == 200 and resp.json()["status"] in {
            "completed",
            "failed",
            "cancelled",
        }:
            return resp.json()
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


# --- 1. list projects ---------------------------------------------------------


def test_list_projects(client: TestClient) -> None:
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert "demo-project" in resp.json()


# --- 2. pipelines + validate --------------------------------------------------


def test_pipelines_and_validate(client: TestClient) -> None:
    resp = client.get("/api/projects/demo-project/pipelines")
    assert resp.status_code == 200
    assert resp.json() == ["demo"]

    resp = client.get("/api/projects/demo-project/pipelines/demo")
    assert resp.status_code == 200
    assert "nodes:" in resp.json()["yaml"]

    resp = client.post("/api/projects/demo-project/pipelines/demo/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == []


# --- 3. start a run in the background -> 202 {run_id}, poll to completed -------


def test_start_run_and_poll(client: TestClient) -> None:
    resp = client.post("/api/projects/demo-project/runs", json={"pipeline": "demo"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert run_id

    state = _wait_completed(client, run_id)
    assert state["status"] == "completed"
    assert state["nodes"]["scan"]["status"] == "done"
    assert state["nodes"]["write"]["status"] == "done"


# --- 4. artifacts list + fetch ------------------------------------------------


def test_artifacts_list_and_fetch(client: TestClient) -> None:
    resp = client.post("/api/projects/demo-project/runs", json={"pipeline": "demo"})
    run_id = resp.json()["run_id"]
    _wait_completed(client, run_id)

    resp = client.get(f"/api/runs/{run_id}/steps/write/artifacts")
    assert resp.status_code == 200
    files = resp.json()
    reqs = [f for f in files if f.endswith("requirements.md")]
    assert reqs, files

    resp = client.get(f"/api/runs/{run_id}/steps/write/artifacts/{reqs[0]}")
    assert resp.status_code == 200
    assert resp.text.replace("\r\n", "\n") == REQ


# --- 5. WS events replay ------------------------------------------------------


def test_ws_events_replay(client: TestClient) -> None:
    resp = client.post("/api/projects/demo-project/runs", json={"pipeline": "demo"})
    run_id = resp.json()["run_id"]
    _wait_completed(client, run_id)

    events: list[dict] = []
    with client.websocket_connect(f"/api/runs/{run_id}/events?from_seq=0") as ws:
        try:
            while True:
                events.append(ws.receive_json())
        except Exception:
            pass

    assert events
    types = {e["type"] for e in events}
    assert "run_state_changed" in types
    completed = [
        e
        for e in events
        if e["type"] == "run_state_changed"
        and e.get("payload", {}).get("to") == "completed"
    ]
    assert completed, events


# --- 6. PUT pipeline (no active run) + models --------------------------------


def test_put_pipeline_and_models(client: TestClient) -> None:
    original = client.get("/api/projects/demo-project/pipelines/demo").json()["yaml"]
    resp = client.put(
        "/api/projects/demo-project/pipelines/demo",
        content=original,
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200

    resp = client.get("/api/models")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert "kimi" in names
    kimi = next(p for p in resp.json() if p["name"] == "kimi")
    assert kimi["available"] is True
    # never echo secret values, only the env var name
    assert kimi["api_key_env"] == "MOONSHOT_API_KEY"


# --- pipeline write: verify, then commit (SPEC §19.2/§19.3) -------------------

_INVALID = """version: "0.1"
name: demo
nodes:
  - id: write
    type: agent
    agent: no_such_agent@1
    inputs: { sources: nowhere.sources }
"""


def test_put_rejects_invalid_pipeline_without_writing(client: TestClient) -> None:
    before = client.get("/api/projects/demo-project/pipelines/demo").json()["yaml"]

    resp = client.put(
        "/api/projects/demo-project/pipelines/demo",
        content=_INVALID,
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    codes = {e["code"] for e in detail["errors"]}
    assert "E_UNKNOWN_AGENT" in codes
    # nothing was written — the editor still sees the old pipeline
    after = client.get("/api/projects/demo-project/pipelines/demo").json()["yaml"]
    assert after == before


def test_put_allow_invalid_saves_the_draft_and_still_reports(
    client: TestClient,
) -> None:
    resp = client.put(
        "/api/projects/demo-project/pipelines/demo?allow_invalid=true",
        content=_INVALID,
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["committed"] is True
    assert {e["code"] for e in body["errors"]}  # the report comes back anyway
    assert client.get("/api/projects/demo-project/pipelines/demo").json()["yaml"] == (
        _INVALID
    )


def test_put_returns_fresh_hash_matching_get(client: TestClient) -> None:
    original = client.get("/api/projects/demo-project/pipelines/demo").json()
    resp = client.put(
        "/api/projects/demo-project/pipelines/demo",
        content=original["yaml"],
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 200
    assert resp.json()["hash"] == original["hash"]
    assert (
        client.get("/api/projects/demo-project/pipelines/demo").json()["hash"]
        == (resp.json()["hash"])
    )


def test_put_with_stale_base_hash_is_refused(client: TestClient) -> None:
    original = client.get("/api/projects/demo-project/pipelines/demo").json()["yaml"]

    resp = client.put(
        "/api/projects/demo-project/pipelines/demo?base_hash=" + "0" * 64,
        content=original,
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 409
    assert "stale" in str(resp.json()["detail"])


def test_put_with_current_base_hash_succeeds(client: TestClient) -> None:
    current = client.get("/api/projects/demo-project/pipelines/demo").json()

    resp = client.put(
        f"/api/projects/demo-project/pipelines/demo?base_hash={current['hash']}",
        content=current["yaml"] + "\n# edited\n",
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 200
    assert resp.json()["committed"] is True


def test_put_leaves_no_tmp_file_behind(client: TestClient, tmp_path: Path) -> None:
    current = client.get("/api/projects/demo-project/pipelines/demo").json()
    client.put(
        "/api/projects/demo-project/pipelines/demo",
        content=current["yaml"],
        headers={"Content-Type": "text/plain"},
    )
    # the atomic write goes through <name>.yaml.tmp + os.replace
    pipelines = list((tmp_path / "projects").glob("demo-project/pipelines/*"))
    assert [p.name for p in pipelines] == ["demo.yaml"]  # the real file is there
    assert not [p for p in pipelines if p.suffix == ".tmp"]


def test_catalog_endpoint_serves_the_authoring_catalog(client: TestClient) -> None:
    resp = client.get("/api/catalog")

    assert resp.status_code == 200
    catalog = resp.json()
    assert {a["ref"] for a in catalog["agents"]} >= {"source_processor@1"}
    assert catalog["builtins"][0]["type"] == "builtin/scanner"
    assert any(c["code"] == "E_NESTED_MAP" for c in catalog["constraints"])
