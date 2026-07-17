from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_seektalent_staging


def _staging_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, str], Path, Path]:
    root = tmp_path / "staging"
    home = root / "home"
    home.mkdir(parents=True)
    node = tmp_path / "standalone" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")
    node.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(run_seektalent_staging, "_require_staging_port_ownership", lambda _root: None)
    env = {
        "HOME": str(home),
        "PATH": str(node.parent),
        "SEEKTALENT_STAGING_ROOT": str(root),
        "SEEKTALENT_OPENCLI_NODE": str(node),
        "SEEKTALENT_TEXT_LLM_API_KEY": "staging-key",
        "SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE": "https://llm.example/v1",
        "SEEKTALENT_CONTROLLER_MODEL_ID": "staging-controller",
        "SEEKTALENT_DOMI_JWT": "must-not-leak",
        "SEEKTALENT_DOMI_NODE": "/opt/domi/node",
    }
    return env, root, node


def test_build_staging_env_uses_prod_policy_with_non_domi_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_env, root, node = _staging_env(tmp_path, monkeypatch)

    env, resolved_root = run_seektalent_staging.build_staging_env(base_env)

    assert resolved_root == root.resolve()
    assert env["HOME"] == str((root / "home").resolve())
    assert env["SEEKTALENT_WORKSPACE_ROOT"] == str(root / "home")
    assert env["SEEKTALENT_PACKAGED"] == "1"
    assert env["SEEKTALENT_RUNTIME_MODE"] == "prod"
    assert env["SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE"] == "prod"
    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "bailian"
    assert env["SEEKTALENT_TEXT_LLM_API_KEY"] == "staging-key"
    assert env["SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE"] == "https://llm.example/v1"
    assert env["SEEKTALENT_CONTROLLER_MODEL_ID"] == "staging-controller"
    assert env["SEEKTALENT_OPENCLI_NODE"] == str(node.resolve())
    assert "SEEKTALENT_DOMI_JWT" not in env
    assert "SEEKTALENT_DOMI_NODE" not in env
    assert "DOMI_NODE" not in env


def test_build_staging_env_rejects_non_isolated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_env, _, _ = _staging_env(tmp_path, monkeypatch)
    base_env["HOME"] = str(tmp_path / "real-home")

    with pytest.raises(run_seektalent_staging.StagingConfigurationError, match="isolated staging home"):
        run_seektalent_staging.build_staging_env(base_env)


def test_build_staging_env_loads_non_secret_llm_config_with_shell_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_env, root, _ = _staging_env(tmp_path, monkeypatch)
    base_env.pop("SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE")
    (root / "config.env").write_text(
        "\n".join(
            [
                'SEEKTALENT_TEXT_LLM_API_KEY="must-not-load"',
                'SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE="https://config.example/v1"',
                'SEEKTALENT_CONTROLLER_MODEL_ID="config-controller"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env, _ = run_seektalent_staging.build_staging_env(base_env)

    assert env["SEEKTALENT_TEXT_LLM_API_KEY"] == "staging-key"
    assert env["SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE"] == "https://config.example/v1"
    assert env["SEEKTALENT_CONTROLLER_MODEL_ID"] == "staging-controller"


def test_write_staging_llm_config_excludes_credentials_and_unrelated_settings(tmp_path: Path) -> None:
    source = tmp_path / ".env"
    target = tmp_path / "staging" / "config.env"
    source.write_text(
        "\n".join(
            [
                "SEEKTALENT_TEXT_LLM_API_KEY=secret",
                "SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE=https://llm.example/v1",
                "SEEKTALENT_CONTROLLER_MODEL_ID=controller",
                "SEEKTALENT_CTS_TENANT_SECRET=must-not-copy",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    run_seektalent_staging.write_staging_llm_config(source, target)

    output = target.read_text(encoding="utf-8")
    assert "SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE" in output
    assert "SEEKTALENT_CONTROLLER_MODEL_ID" in output
    assert "secret" not in output
    assert "CTS" not in output


def test_build_staging_env_rejects_domi_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_env, _, _ = _staging_env(tmp_path, monkeypatch)
    domi_node = tmp_path / "Application Support" / "Domi" / "runtime" / "node" / "bin" / "node"
    domi_node.parent.mkdir(parents=True)
    domi_node.write_text("", encoding="utf-8")
    domi_node.chmod(0o755)
    base_env["SEEKTALENT_OPENCLI_NODE"] = str(domi_node)

    with pytest.raises(run_seektalent_staging.StagingConfigurationError, match="refuses the Domi Node"):
        run_seektalent_staging.build_staging_env(base_env)


def test_staging_port_guard_rejects_foreign_browser_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "staging"
    manifest = root / "home" / ".seektalent" / "browser-bridge" / "bridge-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "implementation": "seektalent-opencli",
                "bridgeBuildId": "seektalent-opencli-0.1.0+expected",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_seektalent_staging,
        "_running_bridge_status",
        lambda: {"implementation": "opencli", "bridgeBuildId": "foreign"},
    )

    with pytest.raises(run_seektalent_staging.StagingConfigurationError, match="owned by Domi"):
        run_seektalent_staging._require_staging_port_ownership(root)


def test_staging_port_guard_accepts_the_paired_staging_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "staging"
    manifest = root / "home" / ".seektalent" / "browser-bridge" / "bridge-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "implementation": "seektalent-opencli",
                "bridgeBuildId": "seektalent-opencli-0.1.0+expected",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_seektalent_staging,
        "_running_bridge_status",
        lambda: {
            "implementation": "seektalent-opencli",
            "bridgeBuildId": "seektalent-opencli-0.1.0+expected",
            "pid": 42,
        },
    )
    monkeypatch.setattr(
        run_seektalent_staging,
        "_running_bridge_process_command",
        lambda _status: str(root / "home" / ".seektalent" / "opencli-runtime" / "daemon.js"),
    )

    run_seektalent_staging._require_staging_port_ownership(root)


def test_staging_port_guard_rejects_same_build_from_another_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "staging"
    manifest = root / "home" / ".seektalent" / "browser-bridge" / "bridge-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "implementation": "seektalent-opencli",
                "bridgeBuildId": "seektalent-opencli-0.1.0+expected",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_seektalent_staging,
        "_running_bridge_status",
        lambda: {
            "implementation": "seektalent-opencli",
            "bridgeBuildId": "seektalent-opencli-0.1.0+expected",
            "pid": 42,
        },
    )
    monkeypatch.setattr(
        run_seektalent_staging,
        "_running_bridge_process_command",
        lambda _status: str(tmp_path / "other-home" / ".seektalent" / "opencli-runtime" / "daemon.js"),
    )

    with pytest.raises(run_seektalent_staging.StagingConfigurationError, match="owned by Domi"):
        run_seektalent_staging._require_staging_port_ownership(root)


def test_main_launches_downloaded_package_server_with_prod_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_env, _, node = _staging_env(tmp_path, monkeypatch)
    for key, value in base_env.items():
        monkeypatch.setenv(key, value)
    launch_calls: list[tuple[list[str], dict[str, str]]] = []

    class Runtime:
        opencli_main = tmp_path / "runtime" / "main.js"

        def __init__(self) -> None:
            self.node = node

    class Completed:
        returncode = 0

    monkeypatch.setattr(
        run_seektalent_staging,
        "_ensure_browser_runtime",
        lambda *_args, **_kwargs: Runtime(),
    )
    monkeypatch.setattr(
        run_seektalent_staging.subprocess,
        "run",
        lambda command, **kwargs: launch_calls.append((list(command), kwargs["env"])) or Completed(),
    )

    assert run_seektalent_staging.main(["--port", "8123"]) == 0

    command, env = launch_calls[0]
    assert command[:3] == [run_seektalent_staging.sys.executable, "-m", "seektalent_ui.server"]
    assert command[command.index("--runtime-mode") + 1] == "prod"
    assert command[command.index("--port") + 1] == "8123"
    assert "--serve-frontend" in command
    assert command[command.index("--liepin-worker-mode") + 1] == "opencli"
    assert command[command.index("--liepin-browser-action-backend") + 1] == "opencli"
    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "bailian"
    assert env["SEEKTALENT_TEXT_LLM_API_KEY"] == "staging-key"
    assert str(Runtime.opencli_main) in env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND"]


def test_check_reports_paths_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_env, root, node = _staging_env(tmp_path, monkeypatch)
    for key, value in base_env.items():
        monkeypatch.setenv(key, value)

    class Runtime:
        opencli_main = tmp_path / "runtime" / "main.js"

        def __init__(self) -> None:
            self.node = node

    monkeypatch.setattr(
        run_seektalent_staging,
        "_ensure_browser_runtime",
        lambda *_args, **_kwargs: Runtime(),
    )
    monkeypatch.setattr(run_seektalent_staging, "_verify_browser_bridge", lambda _runtime: None)

    assert run_seektalent_staging.main(["--check"]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["mode"] == "staging"
    assert payload["runtimeMode"] == "prod"
    assert payload["provider"] == "bailian"
    assert payload["browserBridge"] == "connected"
    assert payload["stagingRoot"] == str(root.resolve())
    assert "staging-key" not in output


def test_check_fails_when_paired_browser_extension_is_not_connected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_env, _, node = _staging_env(tmp_path, monkeypatch)
    for key, value in base_env.items():
        monkeypatch.setenv(key, value)

    class Runtime:
        opencli_main = tmp_path / "runtime" / "main.js"

        def __init__(self) -> None:
            self.node = node

    class BrowserBridgeError(RuntimeError):
        safe_reason_code = "opencli_extension_disconnected"

    monkeypatch.setattr(
        run_seektalent_staging,
        "_ensure_browser_runtime",
        lambda *_args, **_kwargs: Runtime(),
    )
    monkeypatch.setattr(
        run_seektalent_staging,
        "_verify_browser_bridge",
        lambda _runtime: (_ for _ in ()).throw(BrowserBridgeError()),
    )

    assert run_seektalent_staging.main(["--check"]) == 1

    captured = capsys.readouterr()
    assert "reason_code=liepin_opencli_extension_disconnected" in captured.err
    assert captured.out == ""


def test_main_handles_operator_interrupt_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_env, _, node = _staging_env(tmp_path, monkeypatch)
    for key, value in base_env.items():
        monkeypatch.setenv(key, value)

    class Runtime:
        opencli_main = tmp_path / "runtime" / "main.js"

        def __init__(self) -> None:
            self.node = node

    monkeypatch.setattr(
        run_seektalent_staging,
        "_ensure_browser_runtime",
        lambda *_args, **_kwargs: Runtime(),
    )
    monkeypatch.setattr(
        run_seektalent_staging.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert run_seektalent_staging.main([]) == 130
    assert "Traceback" not in capsys.readouterr().err
