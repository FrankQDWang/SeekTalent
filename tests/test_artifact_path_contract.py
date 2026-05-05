from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.artifacts.registry import resolve_descriptor
from seektalent.tracing import RunTracer


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_SCAN_ROOTS = [
    ROOT / "src" / "seektalent",
    ROOT / "tools",
    ROOT / "experiments",
]
ACTIVE_TEXT_PATHS = [
    ROOT / "src/seektalent/prompts/requirements.md",
]
ALLOWED_COMPANY_SNIPPETS_BY_FILE = {
    "src/seektalent/artifacts/registry.py": [
        "company_discovery_plan_call",
        "company_discovery_extract_call",
        "company_discovery_reduce_call",
    ],
    "src/seektalent/evaluation.py": [
        "company_rescue_policy_version",
        '"company_rescue_policy_version" in payload',
        'payload.get("lane_type") == "company_rescue"',
        'if any("company_discovery" in logical_name for logical_name in resolver.manifest.logical_artifacts):',
        'if any("company_discovery" in key for key in prompt_hashes):',
        'return settings.get("company_discovery_enabled") is True or settings.get("target_company_enabled") is True',
    ],
    "src/seektalent/runtime/runtime_diagnostics.py": [
        "round.*.retrieval.company_discovery_plan_call",
        "round.*.retrieval.company_discovery_extract_call",
        "round.*.retrieval.company_discovery_reduce_call",
        "_LEGACY_COMPANY_DISCOVERY_SCHEMA_PRESSURE_PATTERNS",
        "_historical_company_discovery_call_paths",
        "_historical_company_discovery_call_paths",
    ],
    "src/seektalent/prompts/requirements.md": [
        "target-company lists",
    ],
}
REMOVED_COMPANY_DISCOVERY_LOGICAL_NAMES = [
    "round.01.retrieval.company_discovery_input",
    "round.01.retrieval.company_discovery_result",
    "round.01.retrieval.company_discovery_plan",
    "round.01.retrieval.company_search_queries",
    "round.01.retrieval.company_search_results",
    "round.01.retrieval.company_search_rerank",
    "round.01.retrieval.company_page_reads",
    "round.01.retrieval.company_evidence_cards",
    "round.01.retrieval.query_term_pool_after_company_discovery",
    "round.01.retrieval.company_discovery_decision",
]


def _active_python_files() -> list[Path]:
    files: list[Path] = []
    for root in ACTIVE_SCAN_ROOTS:
        files.extend(sorted(root.rglob("*.py")))
    return files


def _scan_text(
    *,
    text: str,
    repo_relative: str,
    disallowed: list[str],
) -> list[tuple[str, str]]:
    cleaned = text
    for snippet in ALLOWED_COMPANY_SNIPPETS_BY_FILE.get(repo_relative, []):
        cleaned = cleaned.replace(snippet, "", 1)
    return [(repo_relative, needle) for needle in disallowed if needle in cleaned]


def scan_for_disallowed_path_literals(*, disallowed: list[str], allowed_files: set[str]) -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in _active_python_files():
        repo_relative = str(path.relative_to(ROOT))
        if repo_relative in allowed_files:
            continue
        text = path.read_text(encoding="utf-8")
        offenders.extend(_scan_text(text=text, repo_relative=repo_relative, disallowed=disallowed))
    return offenders


def test_core_modules_do_not_stitch_legacy_round_paths() -> None:
    disallowed = ['"rounds/"', '"evaluation/"', '"trace.log"', '"events.jsonl"', '"run_manifest.json"', '"benchmark_manifest.json"']
    allowed_files = {
        "src/seektalent/artifacts/legacy.py",
        "src/seektalent/artifacts/store.py",
        "src/seektalent/artifacts/registry.py",
        "tools/audit_run_latency.py",
    }
    offenders = scan_for_disallowed_path_literals(disallowed=disallowed, allowed_files=allowed_files)
    assert offenders == []


def test_core_modules_do_not_stitch_removed_prf_artifact_paths() -> None:
    disallowed = [
        "_".join(["prf", "sidecar", "dependency", "manifest"]) + ".json",
        "prf_span_candidates.json",
        "prf_expression_families.json",
    ]
    allowed_files = {
        "src/seektalent/artifacts/legacy.py",
        "src/seektalent/artifacts/store.py",
        "src/seektalent/artifacts/registry.py",
    }
    offenders = scan_for_disallowed_path_literals(disallowed=disallowed, allowed_files=allowed_files)
    assert offenders == []


def test_active_source_tree_has_no_removed_company_discovery_literals_outside_explicit_legacy_tolerance() -> None:
    disallowed = [
        "company_discovery",
        "company-discovery",
        "target_company",
        "target-company",
        "web_company_discovery",
        "company_rescue",
    ]
    offenders = scan_for_disallowed_path_literals(disallowed=disallowed, allowed_files=set())
    for path in ACTIVE_TEXT_PATHS:
        repo_relative = str(path.relative_to(ROOT))
        offenders.extend(
            _scan_text(
                text=path.read_text(encoding="utf-8"),
                repo_relative=repo_relative,
                disallowed=disallowed,
            )
        )
    assert offenders == []


def test_active_source_tree_has_no_bocha_references() -> None:
    offenders = scan_for_disallowed_path_literals(disallowed=["bocha"], allowed_files=set())
    assert offenders == []


def test_removed_company_discovery_round_artifacts_no_longer_resolve_as_active_descriptors() -> None:
    for logical_name in REMOVED_COMPANY_DISCOVERY_LOGICAL_NAMES:
        with pytest.raises(KeyError):
            resolve_descriptor(logical_name)


def test_fresh_run_manifest_excludes_company_discovery_logical_artifacts(tmp_path: Path) -> None:
    tracer = RunTracer(tmp_path / "artifacts")
    try:
        tracer.write_json("runtime.run_config", {"settings": {}, "prompt_hashes": {}})
        tracer.write_json("input.input_snapshot", {"job_title": "Python Engineer"})
        tracer.write_json("input.input_truth", {"job_title": "Python Engineer"})
        tracer.write_text("output.run_summary", "summary")
        manifest = tracer.session.load_manifest()
    finally:
        tracer.close(status="failed", failure_summary="test cleanup")

    forbidden = ("company_discovery", "target_company", "company_rescue", "web_company_discovery")
    assert all(not any(token in logical_name for token in forbidden) for logical_name in manifest.logical_artifacts)
    assert all(not any(token in entry.path for token in forbidden) for entry in manifest.logical_artifacts.values())


def test_company_discovery_runtime_module_was_removed() -> None:
    assert not (ROOT / "src/seektalent/runtime/company_discovery_runtime.py").exists()


def test_company_discovery_package_was_removed() -> None:
    assert not (ROOT / "src/seektalent/company_discovery").exists()
    assert not (ROOT / "src/seektalent/prompts/company_discovery_plan.md").exists()
    assert not (ROOT / "src/seektalent/prompts/company_discovery_extract.md").exists()
    assert not (ROOT / "src/seektalent/prompts/company_discovery_reduce.md").exists()
