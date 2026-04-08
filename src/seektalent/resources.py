from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CTS_SPEC_NAME = "cts.validated.yaml"


def package_root() -> Path:
    return PACKAGE_ROOT


def package_spec_file() -> Path:
    return PACKAGE_ROOT / DEFAULT_CTS_SPEC_NAME


def default_env_template_file() -> Path:
    return PACKAGE_ROOT / "default.env"


def read_default_env_template() -> str:
    return default_env_template_file().read_text(encoding="utf-8")


def resolve_user_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return Path.cwd() / path
