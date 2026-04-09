from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.resources import artifacts_root


def _copy_artifacts(tmp_path: Path) -> Path:
    target = tmp_path / "artifacts"
    shutil.copytree(artifacts_root(), target)
    return target


def test_default_bootstrap_assets_loads_active_knowledge_packs() -> None:
    assets = default_bootstrap_assets()

    assert assets.policy_id == "business-default-2026-04-09-v1"
    assert assets.knowledge_pack_ids == (
        "llm_agent_rag_engineering",
        "search_ranking_retrieval_engineering",
        "finance_risk_control_ai",
    )
    assert assets.calibration_id == "qwen3-reranker-8b-mxfp8-2026-04-07-v1"
    assert [pack.knowledge_pack_id for pack in assets.knowledge_packs] == list(assets.knowledge_pack_ids)


def test_default_bootstrap_assets_fails_when_pack_file_is_missing(tmp_path: Path) -> None:
    copied = _copy_artifacts(tmp_path)
    (
        copied
        / "knowledge"
        / "packs"
        / "llm_agent_rag_engineering.json"
    ).unlink()

    with pytest.raises(FileNotFoundError):
        default_bootstrap_assets(artifacts_root=copied)


def test_default_bootstrap_assets_fails_when_pack_id_mismatches_filename(tmp_path: Path) -> None:
    copied = _copy_artifacts(tmp_path)
    duplicate_path = (
        copied
        / "knowledge"
        / "packs"
        / "search_ranking_retrieval_engineering.json"
    )
    payload = json.loads(duplicate_path.read_text(encoding="utf-8"))
    payload["knowledge_pack_id"] = "llm_agent_rag_engineering"
    duplicate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="knowledge_pack_id_mismatch"):
        default_bootstrap_assets(artifacts_root=copied)
