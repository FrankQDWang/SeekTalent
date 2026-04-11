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


def knowledge_pack_dir() -> Path:
    return artifacts_root() / KNOWLEDGE_ROOT / "packs"


def knowledge_pack_file(knowledge_pack_id: str) -> Path:
    return knowledge_pack_dir() / f"{knowledge_pack_id}.json"


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


def runtime_policy_dir() -> Path:
    return artifacts_root() / RUNTIME_ROOT / "policies"


def policy_file(policy_id: str) -> Path:
    return runtime_policy_dir() / f"{policy_id}.json"


def runtime_active_file() -> Path:
    return artifacts_root() / RUNTIME_ROOT / "active.json"


def runtime_case_dir(case_id: str) -> Path:
    return artifacts_root() / RUNTIME_ROOT / "cases" / case_id


def runtime_eval_dir() -> Path:
    return artifacts_root() / RUNTIME_ROOT / "evals"


def runtime_eval_matrix_file(experiment_id: str) -> Path:
    return runtime_eval_dir() / f"{experiment_id.lower()}-matrix.json"


def package_spec_file() -> Path:
    return PACKAGE_ROOT / DEFAULT_CTS_SPEC_NAME


def repo_env_template_file() -> Path:
    return repo_root() / ".env.example"


def read_repo_env_template() -> str:
    template_file = repo_env_template_file()
    if not template_file.exists():
        raise FileNotFoundError(
            f"repo_env_template_not_found: expected {template_file}"
        )
    return template_file.read_text(encoding="utf-8")


def resolve_user_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return Path.cwd() / path
