"""`refract init` / `refract templates` scaffolding (SPEC §14).

Uses the real shipped library (it carries pipeline templates); no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from refract.cli import (
    AppConfig,
    UsageError,
    init_impl,
    templates_impl,
    validate_impl,
)
from refract.models.config import ProvidersFile

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PATH = REPO_ROOT / "library"


def _app(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
    providers = ProvidersFile.model_validate(
        {"providers": {"kimi": {"api_key_env": "MOONSHOT_API_KEY"}}}
    )
    return AppConfig(library_path=LIBRARY_PATH, providers=providers)


def test_templates_impl_lists_shipped_templates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert templates_impl(_app(monkeypatch)) == 0
    listed = set(capsys.readouterr().out.split())
    assert {"extract", "discovery", "solution_design"} <= listed


def test_init_scaffolds_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(monkeypatch)
    proj = tmp_path / "atlas"
    # default model in the template's loop is kimi/kimi-k3; align the project
    # default so the single kimi provider satisfies validation.
    code = init_impl(
        proj, template="extract", app=app, name="Atlas", model="kimi/kimi-k3"
    )
    assert code == 0
    assert (proj / "pipelines" / "extract.yaml").exists()
    assert (proj / "input" / ".gitkeep").exists()
    config = yaml.safe_load((proj / "project.yaml").read_text("utf-8"))
    assert config["name"] == "Atlas"
    assert config["defaults"]["model"] == "kimi/kimi-k3"
    assert config["input"] == "./input"
    # the scaffolded project validates against the real library
    assert validate_impl(proj, app=app) == 0


def test_init_defaults_name_to_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(monkeypatch)
    proj = tmp_path / "my-proj"
    init_impl(proj, template="discovery", app=app)
    config = yaml.safe_load((proj / "project.yaml").read_text("utf-8"))
    assert config["name"] == "my-proj"
    assert config["defaults"]["model"] == "openai/gpt-5.6"  # documented default


def test_init_unknown_template_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(UsageError, match="unknown template"):
        init_impl(tmp_path / "p", template="nope", app=_app(monkeypatch))


def test_init_refuses_to_clobber_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(monkeypatch)
    proj = tmp_path / "p"
    init_impl(proj, template="extract", app=app)
    with pytest.raises(UsageError, match="already exists"):
        init_impl(proj, template="discovery", app=app)
    # --force overwrites
    assert init_impl(proj, template="discovery", app=app, force=True) == 0
    assert (proj / "pipelines" / "discovery.yaml").exists()
