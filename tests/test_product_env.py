from __future__ import annotations

from pathlib import Path

from seektalent.config import DEFAULT_LIEPIN_OPENCLI_COMMAND
from seektalent.product_env import build_workbench_command_env, load_product_user_env


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
    assert env["SEEKTALENT_TEXT_LLM_API_KEY"] == "user-text-key"
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


def test_build_workbench_command_env_uses_managed_opencli_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env({"SEEKTALENT_LIEPIN_OPENCLI_COMMAND": "opencli browser host-global"})

    assert env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND"] == DEFAULT_LIEPIN_OPENCLI_COMMAND
