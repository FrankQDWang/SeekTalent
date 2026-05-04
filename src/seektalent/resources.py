from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CTS_SPEC_NAME = "cts.validated.yaml"
REQUIRED_PROMPTS = (
    "requirements",
    "controller",
    "scoring",
    "reflection",
    "finalize",
    "judge",
    "tui_summary",
    "candidate_feedback",
    "prf_probe_phrase_proposal",
    "repair_requirements",
    "repair_controller",
    "repair_reflection",
)


def package_prompt_dir() -> Path:
    return PACKAGE_ROOT / "prompts"


def package_spec_file() -> Path:
    return PACKAGE_ROOT / DEFAULT_CTS_SPEC_NAME


def repo_env_example_file() -> Path:
    return PACKAGE_ROOT.parents[1] / ".env.example"


def package_env_example_file() -> Path:
    return PACKAGE_ROOT / "default.env"


def env_example_template_file() -> Path:
    repo_file = repo_env_example_file()
    if repo_file.exists():
        return repo_file
    return package_env_example_file()


def read_env_example_template() -> str:
    return env_example_template_file().read_text(encoding="utf-8")


def resolve_user_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def resolve_path_from_root(path_value: str | Path, *, root: Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return root / path
