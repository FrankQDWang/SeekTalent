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


def test_repo_env_example_matches_packaged_mirror() -> None:
    assert repo_env_example_file().read_bytes() == package_env_example_file().read_bytes()
    assert Path(".env.example").read_bytes() == package_env_example_file().read_bytes()
