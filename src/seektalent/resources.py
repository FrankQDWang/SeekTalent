from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CTS_SPEC_NAME = "cts.validated.yaml"
ARTIFACTS_ROOT_NAME = "artifacts"
KNOWLEDGE_ROOT = "knowledge"
RUNTIME_ROOT = "runtime"


def package_root() -> Path:
    return PACKAGE_ROOT


def repo_root() -> Path:
    return PACKAGE_ROOT.parents[1]


def artifacts_root() -> Path:
    return repo_root() / ARTIFACTS_ROOT_NAME


def reviewed_reports_dir() -> Path:
    return artifacts_root() / KNOWLEDGE_ROOT / "reviewed_reports"


def compiled_knowledge_dir() -> Path:
    return artifacts_root() / KNOWLEDGE_ROOT / "compiled"


def compiled_cards_file() -> Path:
    return compiled_knowledge_dir() / "cards.json"


def compiled_snapshot_file(snapshot_id: str) -> Path:
    return compiled_knowledge_dir() / "snapshots" / f"{snapshot_id}.json"


def runtime_registry_dir() -> Path:
    return artifacts_root() / RUNTIME_ROOT / "registries"


def school_types_registry_file() -> Path:
    return runtime_registry_dir() / "school_types.json"


@lru_cache(maxsize=1)
def load_school_type_registry() -> dict[str, tuple[str, ...]]:
    payload = json.loads(school_types_registry_file().read_text(encoding="utf-8"))
    return {
        str(school_name): tuple(str(item) for item in school_types)
        for school_name, school_types in payload.items()
        if isinstance(school_name, str) and isinstance(school_types, Sequence) and not isinstance(school_types, str)
    }


def runtime_calibration_dir() -> Path:
    return artifacts_root() / RUNTIME_ROOT / "calibrations"


def calibration_file(calibration_id: str) -> Path:
    return runtime_calibration_dir() / f"{calibration_id}.json"


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
