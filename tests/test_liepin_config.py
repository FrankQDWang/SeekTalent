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


def test_removed_local_worker_mode_is_rejected() -> None:
    removed_mode = "managed" + "_local"
    with pytest.raises(ValidationError, match=removed_mode):
        AppSettings(_env_file=None, liepin_worker_mode=removed_mode)


def test_liepin_opencli_backend_defaults_to_ready_opencli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEEKTALENT_LIEPIN_WORKER_MODE", raising=False)
    monkeypatch.delenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", raising=False)
    monkeypatch.delenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.provider_name == "liepin"
    assert settings.liepin_worker_mode == "opencli"
    assert settings.liepin_browser_action_backend == "opencli"
    assert settings.liepin_opencli_command_argv[1:] == ("-m", "seektalent.opencli_launcher")
    assert Path(settings.liepin_opencli_command_argv[0]).exists()
    assert settings.liepin_opencli_session == "seektalent-liepin"
    assert settings.liepin_opencli_window_mode == "background"
    assert settings.liepin_opencli_allowed_hosts == (
        "www.liepin.com",
        "h.liepin.com",
        "c.liepin.com",
        "lpt.liepin.com",
    )
    assert settings.liepin_opencli_allowed_start_urls == (
        "https://h.liepin.com/search/getConditionItem#session",
        "https://h.liepin.com/resume/search",
    )
    assert settings.liepin_opencli_max_actions_per_task == 80
    assert settings.liepin_opencli_max_pages_per_task == 1
    assert settings.liepin_opencli_max_cards_per_task == 20
    assert settings.liepin_opencli_timeout_seconds == 900
    assert settings.liepin_opencli_detail_open_timeout_seconds == 90
    assert settings.liepin_opencli_pacing_enabled is True
    assert settings.liepin_opencli_pacing_min_ms == 700
    assert settings.liepin_opencli_pacing_max_ms == 1800
    assert settings.liepin_exploit_detail_target == 3
    assert settings.liepin_explore_detail_target == 2

    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_PACING_MIN_MS", "2000")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_PACING_MAX_MS", "1000")
    with pytest.raises(ValueError, match="liepin_opencli_pacing"):
        AppSettings(_env_file=None)


def test_default_liepin_detail_targets_are_three_and_two() -> None:
    settings = AppSettings(_env_file=None)
    assert settings.liepin_exploit_detail_target == 3
    assert settings.liepin_explore_detail_target == 2


@pytest.mark.parametrize(
    "env_key",
    (
        "SEEKTALENT_LIEPIN_OPENCLI_IDLE_" + "CLOSE_SECONDS",
        "SEEKTALENT_LIEPIN_OPENCLI_CLOSE_" + "BLANK_WINDOW",
    ),
)
def test_removed_liepin_opencli_cleanup_env_vars_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
) -> None:
    monkeypatch.setenv(env_key, "1")

    with pytest.raises(ValueError, match="removed Liepin OpenCLI cleanup config"):
        AppSettings(_env_file=None)


@pytest.mark.parametrize(
    "env_key",
    (
        "SEEKTALENT_LIEPIN_OPENCLI_IDLE_" + "CLOSE_SECONDS",
        "SEEKTALENT_LIEPIN_OPENCLI_CLOSE_" + "BLANK_WINDOW",
    ),
)
def test_removed_liepin_opencli_cleanup_env_file_values_are_rejected(tmp_path: Path, env_key: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{env_key}=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="removed Liepin OpenCLI cleanup config"):
        AppSettings(_env_file=env_file)


@pytest.mark.parametrize(
    "init_key",
    (
        "liepin_opencli_idle_" + "close_seconds",
        "liepin_opencli_close_" + "blank_window",
    ),
)
def test_removed_liepin_opencli_cleanup_init_kwargs_are_rejected(init_key: str) -> None:
    with pytest.raises(ValueError, match="removed Liepin OpenCLI cleanup config"):
        AppSettings(_env_file=None, **{init_key: 1})


@pytest.mark.parametrize(
    "init_key",
    (
        "SEEKTALENT_LIEPIN_OPENCLI_IDLE_" + "CLOSE_SECONDS",
        "SEEKTALENT_LIEPIN_OPENCLI_CLOSE_" + "BLANK_WINDOW",
    ),
)
def test_removed_liepin_opencli_cleanup_env_style_init_kwargs_are_rejected(init_key: str) -> None:
    with pytest.raises(ValueError, match="removed Liepin OpenCLI cleanup config"):
        AppSettings(_env_file=None, **{init_key: "1"})


def test_liepin_detail_targets_are_small_opencli_task_targets() -> None:
    settings = AppSettings(
        _env_file=None,
        liepin_exploit_detail_target=3,
        liepin_explore_detail_target=2,
    )

    assert settings.liepin_exploit_detail_target == 3
    assert settings.liepin_explore_detail_target == 2

    with pytest.raises(ValueError, match="liepin_exploit_detail_target"):
        AppSettings(_env_file=None, liepin_exploit_detail_target=11)

    with pytest.raises(ValueError, match="liepin_explore_detail_target"):
        AppSettings(_env_file=None, liepin_explore_detail_target=0)


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
    binary = workspace / "apps/web-react/node_modules/.bin/opencli"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("SEEKTALENT_CODE_ROOT", str(workspace))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "disabled")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", "apps/web-react/node_modules/.bin/opencli")

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
