from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.canonical_cases import canonical_case_specs
from seektalent.resources import artifacts_root


def _copy_artifacts(tmp_path: Path) -> Path:
    target = tmp_path / "artifacts"
    shutil.copytree(artifacts_root(), target)
    return target


def test_default_bootstrap_assets_reads_active_manifest(tmp_path: Path) -> None:
    copied = _copy_artifacts(tmp_path)
    active_path = copied / "runtime" / "active.json"
    policy_path = copied / "runtime" / "policies" / "policy-test.json"
    policy_path.write_text(
        json.dumps(
            {
                "knowledge_pack_id_override": "llm_agent_rag_engineering",
                "fusion_weight_preferences": {
                    "rerank": 0.55,
                    "must_have": 0.25,
                    "preferred": 0.1,
                    "risk_penalty": 0.1,
                },
                "fit_gate_overrides": {
                    "locations": [],
                    "min_years": None,
                    "max_years": None,
                    "company_names": [],
                    "school_names": [],
                    "degree_requirement": None,
                    "gender_requirement": None,
                    "min_age": None,
                    "max_age": None,
                },
                "stability_policy": {
                    "mode": "soft_penalty",
                    "penalty_weight": 1.0,
                    "confidence_floor": 0.6,
                    "allow_hard_gate": False,
                },
                "explanation_preferences": {
                    "top_n_for_explanation": 5,
                    "emphasize_business_delivery": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    active_payload = json.loads(active_path.read_text(encoding="utf-8"))
    active_payload["policy_id"] = "policy-test"
    active_path.write_text(json.dumps(active_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assets = default_bootstrap_assets(artifacts_root=copied)

    assert assets.policy_id == "policy-test"
    assert assets.business_policy_pack.knowledge_pack_id_override == "llm_agent_rag_engineering"


def test_runtime_artifact_builder_is_stable() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tracked_paths = [
        repo_root / "artifacts" / "runtime" / "cases",
        repo_root / "artifacts" / "runtime" / "evals",
        repo_root / "docs" / "v-0.3.2" / "traces",
        repo_root / "docs" / "v-0.3.2" / "trace-index.md",
    ]
    before = _snapshot_files(tracked_paths)
    subprocess.run(
        ["./.venv/bin/python", "scripts/build_runtime_artifacts.py"],
        cwd=repo_root,
        check=True,
    )
    after = _snapshot_files(tracked_paths)
    assert after == before


def test_runtime_artifact_builder_emits_nine_canonical_cases() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    case_root = repo_root / "artifacts" / "runtime" / "cases"
    case_ids = sorted(path.name for path in case_root.iterdir() if path.is_dir())

    assert case_ids == sorted(spec.case_id for spec in canonical_case_specs())
    assert "case-bootstrap-close-high-score-multi-pack" in case_ids
    assert "case-bootstrap-out-of-domain-generic" in case_ids


def test_canonical_bundle_eval_contains_phased_diagnostics() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    bundle_path = (
        repo_root
        / "artifacts"
        / "runtime"
        / "cases"
        / "case-crossover-legal"
        / "bundle.json"
    )
    bundle_payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    metrics = {metric["name"]: metric["value"] for metric in bundle_payload["eval"]["metrics"]}

    assert "search_round_indexes" in metrics
    assert "must_have_query_coverage_by_search_round" in metrics
    assert "net_new_shortlist_gain_by_search_round" in metrics
    assert "operator_distribution_explore" in metrics
    assert isinstance(metrics["search_round_indexes"], list)
    assert isinstance(metrics["must_have_query_coverage_by_search_round"], list)
    assert isinstance(metrics["operator_distribution_explore"], dict)


def _snapshot_files(paths: list[Path]) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in paths:
        if path.is_file():
            snapshot[str(path)] = path.read_bytes()
            continue
        for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
            snapshot[str(file_path)] = file_path.read_bytes()
    return snapshot
