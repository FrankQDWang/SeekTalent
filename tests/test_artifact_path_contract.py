from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKED_FILES = [
    ROOT / "src/seektalent/candidate_feedback/proposal_runtime.py",
    ROOT / "src/seektalent/runtime/orchestrator.py",
    ROOT / "src/seektalent/runtime/second_lane_runtime.py",
    ROOT / "src/seektalent/runtime/controller_runtime.py",
    ROOT / "src/seektalent/runtime/retrieval_runtime.py",
    ROOT / "src/seektalent/runtime/reflection_runtime.py",
    ROOT / "src/seektalent/runtime/finalize_runtime.py",
    ROOT / "src/seektalent/runtime/post_finalize_runtime.py",
    ROOT / "src/seektalent/runtime/rescue_execution_runtime.py",
    ROOT / "src/seektalent/runtime/company_discovery_runtime.py",
    ROOT / "src/seektalent/runtime/requirements_runtime.py",
    ROOT / "src/seektalent/runtime/runtime_diagnostics.py",
    ROOT / "src/seektalent/scoring/scorer.py",
    ROOT / "src/seektalent/evaluation.py",
    ROOT / "src/seektalent/cli.py",
    ROOT / "experiments/claude_code_baseline/harness.py",
    ROOT / "experiments/jd_text_baseline/harness.py",
    ROOT / "experiments/openclaw_baseline/harness.py",
]


def scan_for_disallowed_path_literals(*, disallowed: list[str], allowed_files: set[str]) -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in CHECKED_FILES:
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
    }
    offenders = scan_for_disallowed_path_literals(disallowed=disallowed, allowed_files=allowed_files)
    assert offenders == []
