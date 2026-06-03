from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from seektalent.config import AppSettings


def test_removed_pi_agent_mode_is_not_a_live_worker_mode() -> None:
    with pytest.raises(ValidationError, match="pi_agent"):
        AppSettings(_env_file=None, liepin_worker_mode="pi_agent")


def test_dokobot_action_is_not_a_live_worker_mode() -> None:
    with pytest.raises(ValidationError, match="dokobot_action"):
        AppSettings(_env_file=None, liepin_worker_mode="dokobot_action")


def test_liepin_opencli_backend_defaults_to_ready_opencli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEEKTALENT_LIEPIN_WORKER_MODE", raising=False)
    monkeypatch.delenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", raising=False)
    monkeypatch.delenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.liepin_worker_mode == "opencli"
    assert settings.liepin_browser_action_backend == "opencli"
    assert settings.liepin_opencli_command_argv[1:] == ("-m", "seektalent.opencli_launcher")
    assert Path(settings.liepin_opencli_command_argv[0]).exists()
    assert settings.liepin_opencli_session == "seektalent-liepin"
    assert settings.liepin_opencli_allowed_hosts == (
        "www.liepin.com",
        "h.liepin.com",
        "c.liepin.com",
        "lpt.liepin.com",
    )
    assert settings.liepin_opencli_allowed_start_urls == ("https://h.liepin.com/search/getConditionItem#session",)
    assert settings.liepin_opencli_max_actions_per_task == 80
    assert settings.liepin_opencli_max_pages_per_task == 1
    assert settings.liepin_opencli_max_cards_per_task == 20
    assert settings.liepin_opencli_timeout_seconds == 900
    assert settings.liepin_opencli_detail_open_timeout_seconds == 90
    assert settings.liepin_opencli_idle_close_seconds == 120
    assert settings.liepin_opencli_close_blank_window is False
    assert settings.liepin_opencli_pacing_enabled is True
    assert settings.liepin_opencli_pacing_min_ms == 700
    assert settings.liepin_opencli_pacing_max_ms == 1800

    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_PACING_MIN_MS", "2000")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_PACING_MAX_MS", "1000")
    with pytest.raises(ValueError, match="liepin_opencli_pacing"):
        AppSettings(_env_file=None)


def test_liepin_worker_mode_accepts_opencli() -> None:
    settings = AppSettings(_env_file=None, liepin_worker_mode="opencli", liepin_browser_action_backend="opencli")

    assert settings.liepin_worker_mode == "opencli"
    assert settings.liepin_browser_action_backend == "opencli"


def test_liepin_opencli_backend_validates_json_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "disabled")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_HOSTS_JSON", '["www.liepin.com"]')
    monkeypatch.setenv(
        "SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_START_URLS_JSON",
        '["https://h.liepin.com/search/getConditionItem#session"]',
    )
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_MAX_ACTIONS_PER_TASK", "12")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_MAX_PAGES_PER_TASK", "1")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_MAX_CARDS_PER_TASK", "10")

    settings = AppSettings(_env_file=None)

    assert settings.liepin_browser_action_backend == "opencli"
    assert settings.liepin_opencli_allowed_hosts == ("www.liepin.com",)
    assert settings.liepin_opencli_allowed_start_urls == ("https://h.liepin.com/search/getConditionItem#session",)
    assert settings.liepin_opencli_max_actions_per_task == 12
    assert settings.liepin_opencli_max_pages_per_task == 1
    assert settings.liepin_opencli_max_cards_per_task == 10


def test_liepin_opencli_backend_rejects_empty_start_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "disabled")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_START_URLS_JSON", "[]")

    with pytest.raises(ValueError, match="liepin_opencli_allowed_start_urls_json must not be empty"):
        AppSettings(_env_file=None)


def test_liepin_opencli_command_resolves_from_code_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    binary = workspace / "apps/web-svelte/node_modules/.bin/opencli"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("SEEKTALENT_CODE_ROOT", str(workspace))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "disabled")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", "apps/web-svelte/node_modules/.bin/opencli")

    settings = AppSettings(_env_file=None)

    assert settings.liepin_opencli_command_argv == (str(binary),)


def test_liepin_opencli_bare_command_uses_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", "opencli --profile default")

    settings = AppSettings(_env_file=None)

    assert settings.liepin_opencli_command_argv == ("opencli", "--profile", "default")


def test_liepin_opencli_empty_command_uses_default_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "disabled")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "disabled")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", "")

    settings = AppSettings(_env_file=None)

    assert settings.liepin_opencli_command_argv[1:] == ("-m", "seektalent.opencli_launcher")
    assert Path(settings.liepin_opencli_command_argv[0]).exists()
