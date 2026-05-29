from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REGISTRY_PATH = Path(__file__).with_name("browser_boundary_registry.json")


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, Any]:
    with _REGISTRY_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != "liepin-browser-boundary-registry-v1":
        raise ValueError("Unsupported Liepin browser boundary registry schema_version")
    return data


def _tuple_field(name: str) -> tuple[str, ...]:
    value = _load_registry().get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Liepin browser boundary registry field {name!r} must be a list of strings")
    return tuple(value)


FORBIDDEN_PROVIDER_OPERATIONS = _tuple_field("skill_forbidden_operations")
PYTHON_FORBIDDEN_IMPORTS = _tuple_field("python_forbidden_imports")
PYTHON_FORBIDDEN_OPERATION_MARKERS = _tuple_field("python_forbidden_operation_markers")
TYPESCRIPT_FORBIDDEN_OPERATION_MARKERS = _tuple_field("typescript_forbidden_operation_markers")
TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS = _tuple_field(
    "typescript_provider_action_forbidden_operation_markers"
)
TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS = _tuple_field(
    "typescript_session_lifecycle_allowed_operation_markers"
)
BOUNDARY_PATTERN_DECLARATION_PATHS = _tuple_field("allowlist_paths")
