from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydantic import ValidationError
from seektalent.config import AppSettings
from seektalent.dev_mode import build_dev_mode_env_diagnostics, build_dev_mode_status
from seektalent_ui.server import _can_recover_with_dev_mode_env_diagnostics, create_app
from tests.settings_factory import make_settings


def test_raw_env_diagnostics_do_not_expose_secret_values(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin.md"
    env = {
        "SEEKTALENT_TEXT_LLM_API_KEY": "sk-secret-value",
        "SEEKTALENT_CTS_TENANT_KEY": "tenant-key-secret",
        "SEEKTALENT_CTS_TENANT_SECRET": "tenant-secret-value",
        "SEEKTALENT_LIEPIN_WORKER_MODE": "opencli",
        "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
    }

    payload = build_dev_mode_env_diagnostics(env, workspace_root=tmp_path).model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True)
    components = {component["name"]: component for component in payload["components"]}

    assert "sk-secret-value" not in raw
    assert "tenant-key-secret" not in raw
    assert "tenant-secret-value" not in raw
    assert "account-binding-secret" not in raw
    assert str(skill_path) not in raw
    assert payload["overallStatus"] == "ready"
    assert "cts" not in components


def test_raw_env_diagnostics_reports_local_data_root_posture(tmp_path: Path) -> None:
    env = {
        "SEEKTALENT_ARTIFACTS_DIR": str(tmp_path / ".seektalent" / "artifacts"),
        "SEEKTALENT_LLM_CACHE_DIR": str(tmp_path / "repo-cache"),
    }
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(env, workspace_root=tmp_path)
    roots = {root.name: root for root in payload.dataRoots}

    assert roots["artifacts"].status in {"safe", "unknown"}
    assert roots["llm_cache"].status == "warning"
    assert roots["llm_cache"].reasonCode == "inside_repo"


def test_raw_env_diagnostics_reports_opencli_missing_setup_without_appsettings(tmp_path: Path) -> None:
    payload = build_dev_mode_env_diagnostics(
        {"SEEKTALENT_LIEPIN_WORKER_MODE": "opencli"},
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}

    assert payload.mode == "raw_env_diagnostics"
    assert payload.overallStatus == "needs_setup"
    assert components["liepin_worker_mode"].status == "configured"
    assert components["liepin_opencli_browser"].status == "missing"
    assert components["liepin_opencli_browser"].reasonCode == "liepin_opencli_backend_disabled"
    assert components["liepin_account_binding_secret"].status == "needs_setup"
    assert not any(name.startswith("liepin_pi") for name in components)


def test_raw_env_diagnostics_reports_configured_opencli_browser(tmp_path: Path) -> None:
    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "opencli",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True)

    assert components["liepin_worker_mode"].status == "configured"
    assert components["liepin_opencli_browser"].status == "configured"
    assert components["liepin_account_binding_secret"].status == "configured"
    assert "liepin_pi" not in raw
    assert "DokoBot" not in raw
    assert str(tmp_path) not in raw


def test_server_startup_can_fallback_to_readiness_for_invalid_opencli_config(tmp_path: Path) -> None:
    env = {
        "SEEKTALENT_LIEPIN_WORKER_MODE": "opencli",
        "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
        "SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_HOSTS_JSON": "not-json",
    }

    with pytest.raises(ValidationError) as exc_info:
        AppSettings(
            _env_file=None,
            workspace_root=str(tmp_path),
            **{key.removeprefix("SEEKTALENT_").lower(): value for key, value in env.items()},
        )

    assert _can_recover_with_dev_mode_env_diagnostics(exc_info.value, env)


def test_server_startup_does_not_recover_legacy_pi_agent_config(tmp_path: Path) -> None:
    env = {"SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent"}

    with pytest.raises(ValidationError) as exc_info:
        AppSettings(
            _env_file=None,
            workspace_root=str(tmp_path),
            liepin_worker_mode="pi_agent",
        )

    assert not _can_recover_with_dev_mode_env_diagnostics(exc_info.value, env)


def test_valid_settings_status_reports_configured_components(tmp_path: Path) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        provider_name="cts",
        text_llm_api_key="sk-live",
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="opencli",
        liepin_account_binding_secret="non-placeholder-secret",
    )

    payload = build_dev_mode_status(settings)
    components = {component.name: component for component in payload.components}

    assert payload.mode == "settings"
    assert payload.overallStatus in {"ready", "warning"}
    assert components["text_llm"].status == "configured"
    assert components["cts"].status == "configured"
    assert components["liepin_account_binding_secret"].status == "configured"
    assert components["liepin_opencli_browser"].status == "configured"
    assert not any(name.startswith("liepin_pi") for name in components)


def test_dev_mode_status_uses_configured_opencli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEEKTALENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND", "opencli")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET", "secret")

    status = build_dev_mode_status(AppSettings(_env_file=None))

    components = {item.name: item for item in status.components}
    assert "cts" not in components
    assert components["liepin_opencli_browser"].status == "configured"
    assert components["liepin_opencli_browser"].reasonCode == "liepin_opencli_preflight_required"
    assert not any(name.startswith("liepin_pi") for name in components)


def test_dev_server_startup_does_not_bootstrap_project_browser_config(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True, provider_name="cts")

    create_app(settings=settings)

    assert not (tmp_path / "apps" / "web-react" / "node_modules" / ".bin" / "opencli").exists()


def test_dev_server_startup_keeps_disabled_liepin_mode_explicit(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True, provider_name="cts", liepin_worker_mode="disabled")

    app = create_app(settings=settings)

    assert app.state.settings.liepin_worker_mode == "disabled"
    assert not (tmp_path / ".seektalent" / "liepin_account_binding_secret").exists()
