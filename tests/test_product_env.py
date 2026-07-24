from __future__ import annotations

import sys
from pathlib import Path

from seektalent.config import DEFAULT_LIEPIN_OPENCLI_COMMAND
from seektalent.product_env import MANAGED_OPENCLI_COMMAND_MARKER, build_workbench_command_env, load_product_user_env


def test_load_product_user_env_reads_only_product_keys(tmp_path: Path) -> None:
    env_file = tmp_path / ".seektalent" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(
        "\n".join(
            [
                "SEEKTALENT_TEXT_LLM_API_KEY=file-text-key",
                "export SEEKTALENT_CTS_TENANT_KEY='file-cts-key'",
                'SEEKTALENT_CTS_TENANT_SECRET="file-cts-secret"',
                "SEEKTALENT_LIEPIN_OPENCLI_SESSION=must-not-load",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env = {"SEEKTALENT_TEXT_LLM_API_KEY": "shell-text-key"}

    load_product_user_env(env, env_file=env_file)

    assert env["SEEKTALENT_TEXT_LLM_API_KEY"] == "shell-text-key"
    assert "SEEKTALENT_CTS_TENANT_KEY" not in env
    assert "SEEKTALENT_CTS_TENANT_SECRET" not in env
    assert "SEEKTALENT_LIEPIN_OPENCLI_SESSION" not in env


def test_build_workbench_command_env_adds_product_keys_and_internal_liepin_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    env_file = home / ".seektalent" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "\n".join(
            [
                "SEEKTALENT_TEXT_LLM_API_KEY=user-text-key",
                "SEEKTALENT_CTS_TENANT_KEY=user-cts-key",
                "SEEKTALENT_CTS_TENANT_SECRET=user-cts-secret",
                "SEEKTALENT_LIEPIN_OPENCLI_SESSION=must-not-load",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env({}, env_file=env_file)

    assert env["SEEKTALENT_WORKSPACE_ROOT"] == str(home)
    assert env["SEEKTALENT_RUNTIME_MODE"] == "prod"
    assert env["SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE"] == "prod"
    assert env["SEEKTALENT_LIEPIN_OPENCLI_PACING_ENABLED"] == "false"
    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in env
    assert "SEEKTALENT_CTS_TENANT_KEY" not in env
    assert "SEEKTALENT_CTS_TENANT_SECRET" not in env
    assert "SEEKTALENT_LIEPIN_OPENCLI_SESSION" not in env
    for name in (
        "SEEKTALENT_LIEPIN_API_TOKEN",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET",
        "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET",
    ):
        assert env[name]
        assert env[name] not in {"local-development", "local-development-liepin-api-token"}
    assert (home / ".seektalent" / "workbench-secrets.env").exists()


def test_build_workbench_command_env_uses_home_workspace_root_even_when_cwd_is_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir("/")

    env = build_workbench_command_env({"SEEKTALENT_WORKSPACE_ROOT": "/must-not-use"})

    assert env["SEEKTALENT_WORKSPACE_ROOT"] == str(home)


def test_build_workbench_command_env_ignores_unmarked_opencli_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env({"SEEKTALENT_LIEPIN_OPENCLI_COMMAND": "opencli browser host-global"})

    assert env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND"] == DEFAULT_LIEPIN_OPENCLI_COMMAND


def test_build_workbench_command_env_preserves_marked_managed_opencli_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    command = "/domi/node /home/user/.seektalent/opencli/main.js"
    env = build_workbench_command_env(
        {
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": command,
            MANAGED_OPENCLI_COMMAND_MARKER: "1",
        }
    )

    assert env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND"] == command
    assert env[MANAGED_OPENCLI_COMMAND_MARKER] == "1"


def test_build_workbench_command_env_sets_helper_python_to_current_interpreter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_API_KEY": "test-key",
        }
    )

    assert env["SEEKTALENT_PYTHON"] == sys.executable


def test_build_workbench_command_env_preserves_pythonpath_for_prefix_installs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "PYTHONPATH": "/home/user/.seektalent/python-prefix/0.7.17/Lib/site-packages",
            "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL": "domi",
            "SEEKTALENT_DOMI_JWT": "domi-test-jwt",
        }
    )

    assert env["PYTHONPATH"] == "/home/user/.seektalent/python-prefix/0.7.17/Lib/site-packages"


def test_build_workbench_command_env_ignores_stale_seektalent_runtime_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_API_KEY": "shell-key",
            "SEEKTALENT_PROVIDER_NAME": "cts",
            "SEEKTALENT_RUNTIME_MODE": "dev",
            "SEEKTALENT_LIEPIN_WORKER_MODE": "disabled",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "disabled",
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": "legacy-global-opencli",
            "SEEKTALENT_DOMI_JWT": "domi-test-jwt",
            "SEEKTALENT_DOMI_LLM_BASE_URL": "https://domi.example/v1",
            "SEEKTALENT_DOMI_LLM_CHANNEL": "seek_talent",
            "SEEKTALENT_CTS_TENANT_KEY": "must-not-leak",
        }
    )

    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in env
    assert env["SEEKTALENT_PROVIDER_NAME"] == "liepin"
    assert env["SEEKTALENT_RUNTIME_MODE"] == "prod"
    assert env["SEEKTALENT_LIEPIN_WORKER_MODE"] == "opencli"
    assert env["SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND"] == "opencli"
    assert env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND"] == DEFAULT_LIEPIN_OPENCLI_COMMAND
    assert env["SEEKTALENT_DOMI_JWT"] == "domi-test-jwt"
    assert env["SEEKTALENT_DOMI_LLM_BASE_URL"] == "https://domi.example/v1"
    assert env["SEEKTALENT_DOMI_LLM_CHANNEL"] == "seek_talent"
    assert "SEEKTALENT_CTS_TENANT_KEY" not in env


def test_build_workbench_command_env_passes_minimal_domi_llm_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL": "domi",
            "SEEKTALENT_DOMI_JWT": "domi-test-jwt",
            "SEEKTALENT_DOMI_LLM_BASE_URL": "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1",
            "SEEKTALENT_DOMI_LLM_CHANNEL": "seek_talent",
            "SEEKTALENT_TEXT_LLM_API_KEY": "must-not-be-required",
            "SEEKTALENT_CTS_TENANT_KEY": "must-not-leak",
        }
    )

    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert env["SEEKTALENT_DOMI_JWT"] == "domi-test-jwt"
    assert env["SEEKTALENT_DOMI_LLM_BASE_URL"] == "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1"
    assert env["SEEKTALENT_DOMI_LLM_CHANNEL"] == "seek_talent"
    assert env["SEEKTALENT_RUNTIME_MODE"] == "prod"
    assert env["SEEKTALENT_PROVIDER_NAME"] == "liepin"
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in env
    assert "SEEKTALENT_CTS_TENANT_KEY" not in env


def test_build_workbench_command_env_preserves_domi_opencli_node_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL": "domi",
            "SEEKTALENT_DOMI_JWT": "domi-test-jwt",
            "SEEKTALENT_WTSCLI_NODE_POLICY": "legacy-domi-policy",
            "SEEKTALENT_WTSCLI_NODE": "/opt/domi/bin/node",
            "SEEKTALENT_DOMI_NODE": "/opt/domi/current/node",
            "DOMI_NODE": "/opt/domi/fallback/node",
        }
    )

    assert "SEEKTALENT_WTSCLI_NODE_POLICY" not in env
    assert env["SEEKTALENT_WTSCLI_NODE"] == "/opt/domi/bin/node"
    assert env["SEEKTALENT_DOMI_NODE"] == "/opt/domi/current/node"
    assert env["DOMI_NODE"] == "/opt/domi/fallback/node"


def test_build_workbench_command_env_preserves_domi_node_for_opencli_with_default_llm(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_API_KEY": "bailian-key",
            "DOMI_NODE": "/opt/domi/fallback/node",
        }
    )

    assert env["DOMI_NODE"] == "/opt/domi/fallback/node"


def test_build_workbench_command_env_defaults_to_domi_provider_when_domi_jwt_is_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_DOMI_JWT": "domi-test-jwt",
            "SEEKTALENT_DOMI_NODE": "/opt/domi/current/node",
        }
    )

    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert env["SEEKTALENT_DOMI_JWT"] == "domi-test-jwt"
    assert env["SEEKTALENT_DOMI_NODE"] == "/opt/domi/current/node"
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in env


def test_build_workbench_command_env_forces_domi_provider_for_prod_workbench(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL": "bailian",
            "SEEKTALENT_TEXT_LLM_API_KEY": "stale-text-key",
            "SEEKTALENT_DOMI_JWT": "domi-test-jwt",
        }
    )

    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert env["SEEKTALENT_DOMI_JWT"] == "domi-test-jwt"
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in env


def test_build_workbench_command_env_preserves_windows_process_context_for_opencli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": r"C:\Windows\system32",
            "SEEKTALENT_TEXT_LLM_API_KEY": "test-key",
            "USERPROFILE": r"C:\Users\ci39059",
            "APPDATA": r"C:\Users\ci39059\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\ci39059\AppData\Local",
            "TEMP": r"C:\Users\ci39059\AppData\Local\Temp",
            "TMP": r"C:\Users\ci39059\AppData\Local\Temp",
            "SystemRoot": r"C:\Windows",
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
        }
    )

    assert env["USERPROFILE"] == r"C:\Users\ci39059"
    assert env["APPDATA"] == r"C:\Users\ci39059\AppData\Roaming"
    assert env["LOCALAPPDATA"] == r"C:\Users\ci39059\AppData\Local"
    assert env["TEMP"] == r"C:\Users\ci39059\AppData\Local\Temp"
    assert env["TMP"] == r"C:\Users\ci39059\AppData\Local\Temp"
    assert env["SystemRoot"] == r"C:\Windows"
    assert env["COMSPEC"] == r"C:\Windows\System32\cmd.exe"


def test_build_workbench_command_env_preserves_platform_context_case_insensitively(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "Path": r"C:\Windows\system32",
            "SEEKTALENT_TEXT_LLM_API_KEY": "test-key",
            "SYSTEMROOT": r"C:\Windows",
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
            "PROGRAMFILES": r"C:\Program Files",
            "OPENCLI_PROFILE": "work-profile",
        }
    )

    assert env["Path"] == r"C:\Windows\system32"
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert env["PROGRAMFILES"] == r"C:\Program Files"
    assert "OPENCLI_PROFILE" not in env
