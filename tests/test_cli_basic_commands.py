from __future__ import annotations

import argparse

import pytest

from seektalent import __version__
from seektalent.cli_basic_commands import init_command, update_command, version_command
from seektalent.resources import read_env_example_template


def test_cli_basic_version_command_prints_version(capsys) -> None:
    assert version_command(argparse.Namespace()) == 0

    assert capsys.readouterr().out.strip() == __version__


def test_cli_basic_update_command_prints_upgrade_instructions(capsys) -> None:
    assert update_command(argparse.Namespace()) == 0

    output = capsys.readouterr().out
    assert f"Current version: {__version__}" in output
    assert "pip install -U seektalent" in output
    assert "pipx upgrade seektalent" in output


def test_cli_basic_init_command_writes_template_and_creates_directories(tmp_path, capsys) -> None:
    env_path = tmp_path / "nested" / ".env"

    assert init_command(argparse.Namespace(env_file=str(env_path), force=False)) == 0

    assert env_path.read_text(encoding="utf-8") == read_env_example_template()
    assert f"Wrote env template to {env_path}" in capsys.readouterr().out


def test_cli_basic_init_command_refuses_to_overwrite_without_force(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("existing configuration", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        init_command(argparse.Namespace(env_file=str(env_path), force=False))

    assert env_path.read_text(encoding="utf-8") == "existing configuration"
