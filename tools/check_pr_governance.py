from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


RED_PREFIXES = (
    ".github/",
    "tools/",
    "src/seektalent/runtime/",
    "src/seektalent/prompts/",
    "src/seektalent/providers/",
    "src/seektalent_conversation_agent/",
    "src/seektalent/core/retrieval/",
    "apps/liepin-worker/",
)

RED_FILES = {
    ".env.example",
    "src/seektalent/default.env",
    "src/seektalent/models.py",
    "src/seektalent/config.py",
    "src/seektalent_ui/workbench_store.py",
    "src/seektalent_ui/runtime_bridge.py",
    "src/seektalent_ui/runtime_graph.py",
    "scripts/verify-dev-workbench.sh",
    "scripts/verify-red-zone.sh",
}

YELLOW_PREFIXES = (
    "src/seektalent_ui/",
    "apps/web-svelte/src/lib/api/",
)

GREEN_PREFIXES = (
    "docs/",
    "tests/",
    "apps/web-svelte/src/",
    "apps/web-svelte/tests/",
)

GENERATED_DIR_PREFIXES = (
    "artifacts/",
    "docs/superpowers/",
    "src/seektalent_ui/resources/workbench/",
)

GENERATED_FILES = {
    "apps/web-svelte/src/lib/api/schema.d.ts",
}

ARCHITECTURE_RADAR_FILES = {
    "tach.toml",
    "tools/tach_baseline.json",
}

DEPENDENCY_CONTROL_FILE_NAMES = {
    "bun.lock",
    "bun.lockb",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "uv.lock",
    "yarn.lock",
}

REQUIREMENTS_FILE_RE = re.compile(r"requirements(?:[-_][\w.-]+)?\.txt")

CONFIG_ENV_FILES = {
    ".env.example",
    "src/seektalent/config.py",
    "src/seektalent/default.env",
}

BEHAVIOR_PREFIXES = (
    "apps/liepin-worker/",
    "src/seektalent/core/retrieval/",
    "src/seektalent/prompts/",
    "src/seektalent/providers/",
    "src/seektalent/requirements/",
    "src/seektalent/retrieval/",
    "src/seektalent/runtime/",
    "src/seektalent/scoring/",
)

BACKEND_ARCHITECTURE_CLEANUP_LAYERS = {
    "governance",
    "other",
    "provider",
    "runtime",
}

SECURITY_REMEDIATION_PREFIX = "docs/security/remediations/"
SECURITY_REMEDIATION_SUFFIX = ".json"
SECURITY_REMEDIATION_SCHEMA_VERSION = "seektalent.security_remediation.v1"
SECURITY_REMEDIATION_ALLOWED_LAYERS = {
    "bff",
    "other",
    "provider",
    "runtime",
}
SECURITY_FINDING_ID_RE = re.compile(r"^[A-Z]+-SEC-\d{3}$")

RED_ZONE_REVIEW_PREFIX = "docs/governance/red-zone/"
RED_ZONE_REVIEW_SUFFIX = ".json"
RED_ZONE_REVIEW_SCHEMA_VERSION = "seektalent.red_zone_change.v1"
RED_ZONE_REVIEW_ALLOWED_TYPES = {
    "boundary_cleanup",
    "refactor",
}
RED_ZONE_REVIEW_ALLOWED_LAYERS = {
    "bff",
    "provider",
    "runtime",
}
RED_ZONE_REVIEW_REQUIRED_VERIFICATION = "scripts/verify-red-zone.sh"

MAJOR_REFACTOR_GOAL_PREFIX = "docs/governance/agent-goals/"
MAJOR_REFACTOR_GOAL_SUFFIX = ".json"
MAJOR_REFACTOR_GOAL_SCHEMA_VERSION = "seektalent.major_refactor_goal.v1"
MAJOR_REFACTOR_ALLOWED_LAYERS = {
    "bff",
    "dependencies",
    "docs",
    "frontend",
    "governance",
    "other",
    "provider",
    "runtime",
    "sources",
    "tests",
}
MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID = {
    "source-decoupling-2026-06": (
        "uv run python tools/check_source_boundaries.py",
        "scripts/verify-source-decoupling.sh",
        "scripts/verify-red-zone.sh",
        "scripts/verify-dev-workbench.sh",
        "uv run pytest",
        "cd apps/web-svelte && bun run test",
        "cd apps/liepin-worker && bun test",
    ),
    "governance-bootstrap-2026-06": (
        "uv run pytest tests/test_pr_governance.py -q",
        "uv run ruff check tools/check_pr_governance.py tests/test_pr_governance.py",
        "uv run ty check tools/check_pr_governance.py tests/test_pr_governance.py",
    ),
    "goal-2-agent-safety-gate-2026-06": (
        "uv run pytest tests/test_pr_governance.py -q",
        "uv run ruff check tools/check_pr_governance.py tests/test_pr_governance.py",
        "uv run ty check tools/check_pr_governance.py tests/test_pr_governance.py",
        "uv run pytest tests/test_agent_safety_gate.py tests/test_source_boundaries.py -q",
        "uv run python tools/check_agent_safety_gate.py --base origin/main",
        "uv run python tools/check_source_boundaries.py",
    ),
    "runtime-control-plane-2026-06": (
        "uv run pytest tests/test_runtime_control_*.py -q",
        "uv run python tools/check_source_boundaries.py",
        "uv run pytest",
    ),
}

CODE_EXTENSIONS = {
    ".cjs",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".svelte",
    ".ts",
    ".tsx",
}

TEST_PATH_PARTS = (
    "/tests/",
    "tests/",
)

DEFAULT_MAX_PROD_FILE_LINES = 600
DEFAULT_MAX_TEST_FILE_LINES = 900


@dataclass(frozen=True)
class GovernanceResult:
    ok: bool
    messages: list[str]
    red_files: list[str]


@dataclass(frozen=True)
class LineCountChange:
    path: str
    base_lines: int | None
    head_lines: int | None


def classify_path(path: str) -> str:
    if path in RED_FILES or path.startswith(RED_PREFIXES):
        return "red"
    if path.startswith(YELLOW_PREFIXES):
        return "yellow"
    if path.startswith(GREEN_PREFIXES):
        return "green"
    return "neutral"


def layer_for_path(path: str) -> str:
    if is_dependency_control_file(path):
        return "dependencies"
    if path.startswith("src/seektalent/runtime/"):
        return "runtime"
    if path.startswith("src/seektalent/sources/"):
        return "sources"
    if path.startswith("src/seektalent/prompts/"):
        return "prompts"
    if path.startswith("src/seektalent/providers/"):
        return "provider"
    if path.startswith("src/seektalent_ui/"):
        return "bff"
    if path.startswith("apps/web-svelte/"):
        return "frontend"
    if path == ".gitignore" or path.startswith(".github/") or path.startswith("tools/") or path.startswith("scripts/"):
        return "governance"
    if path.startswith("docs/"):
        return "docs"
    if path.startswith("tests/"):
        return "tests"
    return "other"


def is_generated(path: str) -> bool:
    return path in GENERATED_FILES or path.startswith(GENERATED_DIR_PREFIXES)


def is_dependency_control_file(path: str) -> bool:
    name = Path(path.replace("\\", "/")).name
    return name in DEPENDENCY_CONTROL_FILE_NAMES or REQUIREMENTS_FILE_RE.fullmatch(name) is not None


def is_prompt_file(path: str) -> bool:
    return path.startswith("src/seektalent/prompts/")


def is_runtime_file(path: str) -> bool:
    return path.startswith("src/seektalent/runtime/")


def is_config_env_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized in CONFIG_ENV_FILES or normalized.endswith(".env") or normalized.endswith(".env.example")


def is_behavior_file(path: str) -> bool:
    return path.startswith(BEHAVIOR_PREFIXES)


def line_limit_for_path(
    path: str,
    *,
    max_prod_file_lines: int = DEFAULT_MAX_PROD_FILE_LINES,
    max_test_file_lines: int = DEFAULT_MAX_TEST_FILE_LINES,
) -> int | None:
    if is_generated(path) or Path(path).suffix not in CODE_EXTENSIONS:
        return None
    if any(part in path for part in TEST_PATH_PARTS):
        return max_test_file_lines
    return max_prod_file_lines


def merge_changed_file_sets(*file_sets: Sequence[str]) -> list[str]:
    return sorted({path.strip() for file_set in file_sets for path in file_set if path.strip()})


def is_backend_architecture_cleanup(paths: Sequence[str], layers: Sequence[str]) -> bool:
    return any(path in ARCHITECTURE_RADAR_FILES for path in paths) and set(layers) <= BACKEND_ARCHITECTURE_CLEANUP_LAYERS


def security_remediation_manifest_paths(paths: Sequence[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path.startswith(SECURITY_REMEDIATION_PREFIX) and path.endswith(SECURITY_REMEDIATION_SUFFIX)
    )


def red_zone_review_manifest_paths(paths: Sequence[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path.startswith(RED_ZONE_REVIEW_PREFIX) and path.endswith(RED_ZONE_REVIEW_SUFFIX)
    )


def major_refactor_goal_manifest_paths(paths: Sequence[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path.startswith(MAJOR_REFACTOR_GOAL_PREFIX) and path.endswith(MAJOR_REFACTOR_GOAL_SUFFIX)
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _required_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapping_value(value: object, key: str) -> object:
    if not isinstance(value, Mapping):
        return None
    for candidate_key, candidate_value in value.items():
        if candidate_key == key:
            return candidate_value
    return None


def validate_security_remediation_manifests(
    paths: Sequence[str],
    *,
    red_files: Sequence[str],
    layers: Sequence[str],
    project_root: Path,
) -> tuple[bool, list[str]]:
    manifest_paths = security_remediation_manifest_paths(paths)
    if not manifest_paths:
        return False, []

    messages: list[str] = []
    if set(layers) - SECURITY_REMEDIATION_ALLOWED_LAYERS:
        messages.append(
            "security remediation manifest cannot cover layers: "
            + ", ".join(sorted(set(layers) - SECURITY_REMEDIATION_ALLOWED_LAYERS))
        )

    covered_files: set[str] = set()
    changed_files = set(paths)
    for manifest_path in manifest_paths:
        file_path = project_root / manifest_path
        if not file_path.is_file():
            messages.append(f"security remediation manifest missing: {manifest_path}")
            continue
        try:
            raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            messages.append(f"security remediation manifest is invalid JSON: {manifest_path}: {exc.msg}")
            continue
        if not isinstance(raw_payload, Mapping):
            messages.append(f"security remediation manifest must be a JSON object: {manifest_path}")
            continue
        if _mapping_value(raw_payload, "schema_version") != SECURITY_REMEDIATION_SCHEMA_VERSION:
            messages.append(f"security remediation manifest has unsupported schema_version: {manifest_path}")
        findings = _mapping_value(raw_payload, "findings")
        if not isinstance(findings, list) or not findings:
            messages.append(f"security remediation manifest must list findings: {manifest_path}")
        else:
            for index, finding in enumerate(findings):
                if not isinstance(finding, Mapping):
                    messages.append(f"security remediation finding must be an object: {manifest_path}#{index}")
                    continue
                finding_id = _mapping_value(finding, "id")
                title = _mapping_value(finding, "title")
                if not isinstance(finding_id, str) or not SECURITY_FINDING_ID_RE.fullmatch(finding_id):
                    messages.append(f"security remediation finding id is invalid: {manifest_path}#{index}")
                if not isinstance(title, str) or not title.strip():
                    messages.append(f"security remediation finding title is missing: {manifest_path}#{index}")
        remediated_files = _string_list(_mapping_value(raw_payload, "remediated_files"))
        if not remediated_files:
            messages.append(f"security remediation manifest must list remediated_files: {manifest_path}")
        covered_files.update(remediated_files)
        stale_files = sorted(set(remediated_files) - changed_files)
        if stale_files:
            messages.append(
                "security remediation manifest references unchanged files: " + ", ".join(stale_files)
            )

    missing_red_files = sorted(set(red_files) - covered_files)
    if missing_red_files:
        messages.append(
            "security remediation manifest does not cover red-zone files: " + ", ".join(missing_red_files)
        )
    return not messages, messages


def validate_red_zone_review_manifests(
    paths: Sequence[str],
    *,
    red_files: Sequence[str],
    layers: Sequence[str],
    project_root: Path,
) -> tuple[bool, list[str]]:
    manifest_paths = red_zone_review_manifest_paths(paths)
    if not manifest_paths:
        return False, []

    messages: list[str] = []
    if set(layers) - RED_ZONE_REVIEW_ALLOWED_LAYERS:
        messages.append(
            "red-zone review manifest cannot cover layers: "
            + ", ".join(sorted(set(layers) - RED_ZONE_REVIEW_ALLOWED_LAYERS))
        )

    covered_files: set[str] = set()
    changed_files = set(paths)
    for manifest_path in manifest_paths:
        file_path = project_root / manifest_path
        if not file_path.is_file():
            messages.append(f"red-zone review manifest missing: {manifest_path}")
            continue
        try:
            raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            messages.append(f"red-zone review manifest is invalid JSON: {manifest_path}: {exc.msg}")
            continue
        if not isinstance(raw_payload, Mapping):
            messages.append(f"red-zone review manifest must be a JSON object: {manifest_path}")
            continue
        if _mapping_value(raw_payload, "schema_version") != RED_ZONE_REVIEW_SCHEMA_VERSION:
            messages.append(f"red-zone review manifest has unsupported schema_version: {manifest_path}")
        if _mapping_value(raw_payload, "change_type") not in RED_ZONE_REVIEW_ALLOWED_TYPES:
            messages.append(f"red-zone review manifest has unsupported change_type: {manifest_path}")
        for key in ("summary", "rationale"):
            if not _required_string(_mapping_value(raw_payload, key)):
                messages.append(f"red-zone review manifest must include {key}: {manifest_path}")

        manifest_red_files = _string_list(_mapping_value(raw_payload, "red_files"))
        if not manifest_red_files:
            messages.append(f"red-zone review manifest must list red_files: {manifest_path}")
        covered_files.update(manifest_red_files)
        stale_files = sorted(set(manifest_red_files) - changed_files)
        if stale_files:
            messages.append("red-zone review manifest references unchanged files: " + ", ".join(stale_files))

        verification = _string_list(_mapping_value(raw_payload, "verification"))
        if not verification:
            messages.append(f"red-zone review manifest must list verification: {manifest_path}")
        elif RED_ZONE_REVIEW_REQUIRED_VERIFICATION not in verification:
            messages.append(
                f"red-zone review manifest must include {RED_ZONE_REVIEW_REQUIRED_VERIFICATION}: {manifest_path}"
            )

    missing_red_files = sorted(set(red_files) - covered_files)
    if missing_red_files:
        messages.append("red-zone review manifest does not cover red-zone files: " + ", ".join(missing_red_files))
    return not messages, messages


def validate_major_refactor_goal_manifests(
    paths: Sequence[str],
    *,
    red_files: Sequence[str],
    layers: Sequence[str],
    dependency_files: Sequence[str],
    project_root: Path,
) -> tuple[bool, list[str]]:
    manifest_paths = major_refactor_goal_manifest_paths(paths)
    if not manifest_paths:
        return False, []

    messages: list[str] = []
    if len(manifest_paths) > 1:
        messages.append("only one major refactor goal manifest is allowed")

    changed_files = set(paths)
    changed_layers = set(layers)
    changed_dependency_files = set(dependency_files)
    covered_red_files: set[str] = set()
    covered_dependency_files: set[str] = set()

    for manifest_path in manifest_paths:
        file_path = project_root / manifest_path
        if not file_path.is_file():
            messages.append(f"major refactor goal manifest missing: {manifest_path}")
            continue
        try:
            raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            messages.append(f"major refactor goal manifest is invalid JSON: {manifest_path}: {exc.msg}")
            continue
        if not isinstance(raw_payload, Mapping):
            messages.append(f"major refactor goal manifest must be a JSON object: {manifest_path}")
            continue

        goal_id = _mapping_value(raw_payload, "goal_id")
        if _mapping_value(raw_payload, "schema_version") != MAJOR_REFACTOR_GOAL_SCHEMA_VERSION:
            messages.append(f"major refactor goal manifest has unsupported schema_version: {manifest_path}")
        if goal_id not in MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID:
            messages.append(f"major refactor goal manifest has unsupported goal_id: {manifest_path}")
        if _mapping_value(raw_payload, "change_type") != "major_refactor":
            messages.append(f"major refactor goal manifest must use change_type=major_refactor: {manifest_path}")
        for key in ("summary", "rationale"):
            if not _required_string(_mapping_value(raw_payload, key)):
                messages.append(f"major refactor goal manifest must include {key}: {manifest_path}")

        touched_layers = set(_string_list(_mapping_value(raw_payload, "touched_layers")))
        if not touched_layers:
            messages.append(f"major refactor goal manifest must list touched_layers: {manifest_path}")
        else:
            uncovered_layers = sorted(changed_layers - touched_layers)
            unsupported_layers = sorted(touched_layers - MAJOR_REFACTOR_ALLOWED_LAYERS)
            if uncovered_layers:
                messages.append(
                    "major refactor goal manifest does not cover layers: " + ", ".join(uncovered_layers)
                )
            if unsupported_layers:
                messages.append(
                    "major refactor goal manifest cannot cover layers: " + ", ".join(unsupported_layers)
                )

        manifest_red_files = _string_list(_mapping_value(raw_payload, "red_files"))
        if not manifest_red_files:
            messages.append(f"major refactor goal manifest must list red_files: {manifest_path}")
        covered_red_files.update(manifest_red_files)
        stale_red_files = sorted(set(manifest_red_files) - changed_files)
        if stale_red_files:
            messages.append(
                "major refactor goal manifest references unchanged red-zone files: "
                + ", ".join(stale_red_files)
            )

        verification = _string_list(_mapping_value(raw_payload, "verification"))
        if not verification:
            messages.append(f"major refactor goal manifest must list verification: {manifest_path}")
        elif isinstance(goal_id, str):
            for command in MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID.get(goal_id, ()):
                if command not in verification:
                    messages.append(
                        f"major refactor goal manifest must include verification `{command}`: {manifest_path}"
                    )

        if not _string_list(_mapping_value(raw_payload, "deletion_targets")):
            messages.append(f"major refactor goal manifest must list deletion_targets: {manifest_path}")
        if not _string_list(_mapping_value(raw_payload, "risks")):
            messages.append(f"major refactor goal manifest must list risks: {manifest_path}")

        manifest_dependency_files = _string_list(_mapping_value(raw_payload, "dependency_files"))
        covered_dependency_files.update(manifest_dependency_files)
        stale_dependency_files = sorted(set(manifest_dependency_files) - changed_files)
        if stale_dependency_files:
            messages.append(
                "major refactor goal manifest references unchanged dependency files: "
                + ", ".join(stale_dependency_files)
            )
        if manifest_dependency_files and not _required_string(_mapping_value(raw_payload, "dependency_rationale")):
            messages.append(f"major refactor goal manifest must explain dependency files: {manifest_path}")

    missing_red_files = sorted(set(red_files) - covered_red_files)
    if missing_red_files:
        messages.append("major refactor goal manifest does not cover red-zone files: " + ", ".join(missing_red_files))

    missing_dependency_files = sorted(changed_dependency_files - covered_dependency_files)
    if missing_dependency_files:
        messages.append(
            "major refactor goal manifest does not cover dependency files: "
            + ", ".join(missing_dependency_files)
        )

    return not messages, messages


def validate_major_refactor_config_behavior_reviews(
    paths: Sequence[str],
    *,
    config_env_files: Sequence[str],
    behavior_files: Sequence[str],
    project_root: Path,
) -> tuple[bool, list[str]]:
    manifest_paths = major_refactor_goal_manifest_paths(paths)
    if not manifest_paths or not config_env_files or not behavior_files:
        return False, []

    messages: list[str] = []
    changed_files = set(paths)
    covered_config_env_files: set[str] = set()
    for manifest_path in manifest_paths:
        file_path = project_root / manifest_path
        if not file_path.is_file():
            continue
        try:
            raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            messages.append(
                f"major refactor goal manifest is invalid JSON for config/env review: {manifest_path}: {exc.msg}"
            )
            continue
        if not isinstance(raw_payload, Mapping):
            continue

        manifest_config_env_files = _string_list(_mapping_value(raw_payload, "config_env_files"))
        covered_config_env_files.update(manifest_config_env_files)
        stale_config_env_files = sorted(set(manifest_config_env_files) - changed_files)
        if stale_config_env_files:
            messages.append(
                "major refactor goal manifest references unchanged config/env files: "
                + ", ".join(stale_config_env_files)
            )
        if manifest_config_env_files and not _required_string(
            _mapping_value(raw_payload, "config_behavior_rationale")
        ):
            messages.append(
                f"major refactor goal manifest must explain config/env plus behavior changes: {manifest_path}"
            )

    missing_config_env_files = sorted(set(config_env_files) - covered_config_env_files)
    if missing_config_env_files:
        messages.append(
            "major refactor goal manifest does not cover config/env files: "
            + ", ".join(missing_config_env_files)
        )
    return not messages, messages


def major_refactor_line_count_exemptions(
    paths: Sequence[str],
    *,
    project_root: Path,
) -> tuple[set[str], list[str]]:
    manifest_paths = major_refactor_goal_manifest_paths(paths)
    if not manifest_paths:
        return set(), []

    changed_files = set(paths)
    exemptions: set[str] = set()
    messages: list[str] = []
    for manifest_path in manifest_paths:
        file_path = project_root / manifest_path
        if not file_path.is_file():
            continue
        try:
            raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            messages.append(
                f"major refactor goal manifest is invalid JSON for line count exemptions: {manifest_path}: {exc.msg}"
            )
            continue
        if not isinstance(raw_payload, Mapping):
            continue
        manifest_exemptions = _string_list(_mapping_value(raw_payload, "line_count_exemptions"))
        exemptions.update(manifest_exemptions)
        if manifest_exemptions and not _required_string(_mapping_value(raw_payload, "line_count_rationale")):
            messages.append(f"major refactor goal manifest must explain line count exemptions: {manifest_path}")
        stale_exemptions = sorted(set(manifest_exemptions) - changed_files)
        if stale_exemptions:
            messages.append(
                "major refactor goal manifest references unchanged line count exemptions: "
                + ", ".join(stale_exemptions)
            )
    return exemptions, messages


def evaluate_line_counts(
    line_changes: Sequence[LineCountChange],
    *,
    max_prod_file_lines: int = DEFAULT_MAX_PROD_FILE_LINES,
    max_test_file_lines: int = DEFAULT_MAX_TEST_FILE_LINES,
) -> list[str]:
    messages: list[str] = []
    for change in line_changes:
        if change.head_lines is None:
            continue
        limit = line_limit_for_path(
            change.path,
            max_prod_file_lines=max_prod_file_lines,
            max_test_file_lines=max_test_file_lines,
        )
        if limit is None:
            continue
        if change.base_lines is None and change.head_lines > limit:
            messages.append(f"new file too long: {change.path} has {change.head_lines} lines > {limit}")
        elif change.base_lines is not None and change.base_lines > limit and change.head_lines > change.base_lines:
            messages.append(
                f"oversized file grew: {change.path} grew from {change.base_lines} to {change.head_lines} lines "
                f"(limit {limit})"
            )
        elif change.base_lines is not None and change.base_lines <= limit and change.head_lines > limit:
            messages.append(f"file too long: {change.path} has {change.head_lines} lines > {limit}")
    return messages


def evaluate_changed_files(
    paths: Sequence[str],
    *,
    max_files: int = 15,
    max_layers: int = 1,
    line_changes: Sequence[LineCountChange] = (),
    max_prod_file_lines: int = DEFAULT_MAX_PROD_FILE_LINES,
    max_test_file_lines: int = DEFAULT_MAX_TEST_FILE_LINES,
    project_root: Path | None = None,
) -> GovernanceResult:
    non_generated = sorted(path for path in paths if path and not is_generated(path))
    layers = sorted(
        {
            layer_for_path(path)
            for path in non_generated
            if layer_for_path(path) not in {"docs", "tests"}
        }
    )
    red_files = sorted(path for path in non_generated if classify_path(path) == "red")
    dependency_files = sorted(path for path in non_generated if is_dependency_control_file(path))
    prompt_runtime_files = sorted(
        path for path in non_generated if is_prompt_file(path) or is_runtime_file(path)
    )
    config_env_files = sorted(path for path in non_generated if is_config_env_file(path))
    behavior_files = sorted(path for path in non_generated if is_behavior_file(path))
    manifest_paths = security_remediation_manifest_paths(non_generated)
    red_zone_manifest_paths = red_zone_review_manifest_paths(non_generated)
    major_refactor_manifest_paths = major_refactor_goal_manifest_paths(non_generated)
    security_remediation, security_remediation_messages = validate_security_remediation_manifests(
        non_generated,
        red_files=red_files,
        layers=layers,
        project_root=project_root or Path.cwd(),
    )
    red_zone_review, red_zone_review_messages = validate_red_zone_review_manifests(
        non_generated,
        red_files=red_files,
        layers=layers,
        project_root=project_root or Path.cwd(),
    )
    major_refactor_goal, major_refactor_messages = validate_major_refactor_goal_manifests(
        non_generated,
        red_files=red_files,
        layers=layers,
        dependency_files=dependency_files,
        project_root=project_root or Path.cwd(),
    )
    major_refactor_config_behavior_review, major_refactor_config_behavior_messages = (
        validate_major_refactor_config_behavior_reviews(
            non_generated,
            config_env_files=config_env_files,
            behavior_files=behavior_files,
            project_root=project_root or Path.cwd(),
        )
    )
    line_count_exemptions, line_count_exemption_messages = major_refactor_line_count_exemptions(
        non_generated,
        project_root=project_root or Path.cwd(),
    )
    messages: list[str] = []

    file_budget_paths = [
        path
        for path in non_generated
        if path not in manifest_paths
        and path not in red_zone_manifest_paths
        and path not in major_refactor_manifest_paths
    ]
    if len(file_budget_paths) > max_files:
        messages.append(f"too many non-generated files changed: {len(file_budget_paths)} > {max_files}")
    if (
        len(layers) > max_layers
        and not is_backend_architecture_cleanup(non_generated, layers)
        and not security_remediation
        and not major_refactor_goal
    ):
        messages.append(f"cross-layer change touches {len(layers)} layers: {', '.join(layers)}")
    if red_files:
        messages.append("red-zone files touched: " + ", ".join(red_files))
    if dependency_files:
        messages.append("dependency control files touched: " + ", ".join(dependency_files))
    if any(is_prompt_file(path) for path in non_generated) and any(is_runtime_file(path) for path in non_generated):
        messages.append("prompt and runtime files touched together: " + ", ".join(prompt_runtime_files))
    if config_env_files and behavior_files:
        messages.append(
            "config/env and behavior files touched together: " + ", ".join([*config_env_files, *behavior_files])
        )
    messages.extend(security_remediation_messages)
    messages.extend(red_zone_review_messages)
    messages.extend(major_refactor_messages)
    messages.extend(major_refactor_config_behavior_messages)
    messages.extend(line_count_exemption_messages)
    messages.extend(
        evaluate_line_counts(
            [
                change
                for change in line_changes
                if not (major_refactor_goal and change.path in line_count_exemptions)
            ],
            max_prod_file_lines=max_prod_file_lines,
            max_test_file_lines=max_test_file_lines,
        )
    )

    blocking = list(messages)
    if red_files and is_backend_architecture_cleanup(non_generated, layers):
        blocking = [message for message in blocking if not message.startswith("red-zone files touched:")]
    if security_remediation:
        blocking = [
            message
            for message in blocking
            if not (
                message.startswith("red-zone files touched:")
                or message.startswith("cross-layer change touches")
            )
        ]
    if red_zone_review:
        blocking = [message for message in blocking if not message.startswith("red-zone files touched:")]
    if major_refactor_goal:
        blocking = [
            message
            for message in blocking
            if not (
                message.startswith("too many non-generated files changed")
                or message.startswith("cross-layer change touches")
                or message.startswith("red-zone files touched:")
                or message.startswith("dependency control files touched:")
                or (
                    major_refactor_config_behavior_review
                    and message.startswith("config/env and behavior files touched together:")
                )
            )
        ]
    return GovernanceResult(ok=not blocking, messages=messages, red_files=red_files)


def changed_files(base: str) -> list[str]:
    merge_base = subprocess.check_output(["git", "merge-base", base, "HEAD"], text=True).strip()
    committed = subprocess.check_output(["git", "diff", "--name-only", f"{merge_base}...HEAD"], text=True)
    unstaged = subprocess.check_output(["git", "diff", "--name-only"], text=True)
    staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True)
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True)
    return merge_changed_file_sets(
        committed.splitlines(),
        unstaged.splitlines(),
        staged.splitlines(),
        untracked.splitlines(),
    )


def count_lines_in_bytes(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)


def count_file_lines(path: str) -> int | None:
    file_path = Path(path)
    if not file_path.is_file():
        return None
    return count_lines_in_bytes(file_path.read_bytes())


def count_git_file_lines(revision: str, path: str) -> int | None:
    completed = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return count_lines_in_bytes(completed.stdout)


def changed_file_line_counts(base: str, paths: Sequence[str]) -> list[LineCountChange]:
    merge_base = subprocess.check_output(["git", "merge-base", base, "HEAD"], text=True).strip()
    return [
        LineCountChange(
            path,
            base_lines=count_git_file_lines(merge_base, path),
            head_lines=count_file_lines(path),
        )
        for path in paths
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check PR size and path governance.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--max-files", type=int, default=15)
    parser.add_argument("--max-layers", type=int, default=1)
    parser.add_argument("--max-prod-file-lines", type=int, default=DEFAULT_MAX_PROD_FILE_LINES)
    parser.add_argument("--max-test-file-lines", type=int, default=DEFAULT_MAX_TEST_FILE_LINES)
    args = parser.parse_args()

    paths = changed_files(args.base)
    result = evaluate_changed_files(
        paths,
        max_files=args.max_files,
        max_layers=args.max_layers,
        line_changes=changed_file_line_counts(args.base, paths),
        max_prod_file_lines=args.max_prod_file_lines,
        max_test_file_lines=args.max_test_file_lines,
    )
    for message in result.messages:
        print(message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
