from pathlib import Path

from seektalent.resources import (
    env_example_template_file,
    package_env_example_file,
    read_env_example_template,
    repo_env_example_file,
)


def test_read_env_example_template_prefers_repo_copy() -> None:
    assert env_example_template_file() == repo_env_example_file()
    assert read_env_example_template() == repo_env_example_file().read_text(encoding="utf-8")


def test_source_env_example_is_minimal_release_template() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]

    assert lines == ["SEEKTALENT_TEXT_LLM_API_KEY="]


def test_packaged_default_env_is_minimal_user_template() -> None:
    text = package_env_example_file().read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]

    assert lines == [
        "SEEKTALENT_TEXT_LLM_API_KEY=",
    ]
