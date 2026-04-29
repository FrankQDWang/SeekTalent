from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.artifacts.registry import resolve_descriptor
from seektalent.tracing import RunTracer


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_SOURCE_ROOT = ROOT / "src" / "seektalent"
ACTIVE_SOURCE_TOLERANCE_FILES = {
    "src/seektalent/artifacts/registry.py",
    "src/seektalent/evaluation.py",
    "src/seektalent/runtime/runtime_diagnostics.py",
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


def _active_source_files() -> list[Path]:
    return sorted(ACTIVE_SOURCE_ROOT.rglob("*.py"))


def scan_for_disallowed_path_literals(*, disallowed: list[str], allowed_files: set[str]) -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in _active_source_files():
        repo_relative = str(path.relative_to(ROOT))
        if repo_relative in allowed_files:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in disallowed:
            if needle in text:
                offenders.append((repo_relative, needle))
    return offenders


def test_core_modules_do_not_stitch_legacy_round_paths() -> None:
    disallowed = ['"rounds/"', '"evaluation/"', '"trace.log"', '"events.jsonl"', '"run_manifest.json"', '"benchmark_manifest.json"']
    allowed_files = {
        "src/seektalent/artifacts/legacy.py",
        "src/seektalent/artifacts/store.py",
        "src/seektalent/artifacts/registry.py",
        *ACTIVE_SOURCE_TOLERANCE_FILES,
    }
    offenders = scan_for_disallowed_path_literals(disallowed=disallowed, allowed_files=allowed_files)
    assert offenders == []


def test_core_modules_do_not_stitch_prf_sidecar_artifact_paths() -> None:
    disallowed = [
        "prf_sidecar_dependency_manifest.json",
        "prf_span_candidates.json",
        "prf_expression_families.json",
        "prf_policy_decision.json",
    ]
    allowed_files = {
        "src/seektalent/artifacts/legacy.py",
        "src/seektalent/artifacts/store.py",
        "src/seektalent/artifacts/registry.py",
        *ACTIVE_SOURCE_TOLERANCE_FILES,
    }
    offenders = scan_for_disallowed_path_literals(disallowed=disallowed, allowed_files=allowed_files)
    assert offenders == []


def test_active_source_tree_has_no_removed_company_discovery_literals_outside_legacy_tolerance_paths() -> None:
    disallowed = [
        "company_discovery",
        "company-discovery",
        "target_company",
        "target-company",
        "web_company_discovery",
        "company_rescue",
    ]
    offenders = scan_for_disallowed_path_literals(
        disallowed=disallowed,
        allowed_files=ACTIVE_SOURCE_TOLERANCE_FILES,
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
