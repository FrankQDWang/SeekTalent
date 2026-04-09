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


def test_default_bootstrap_assets_loads_shared_artifacts() -> None:
    assets = default_bootstrap_assets()

    assert assets.knowledge_base_snapshot.snapshot_id == "kb-2026-04-07-v1"
    assert len(assets.knowledge_cards) == 5
    finance_card = next(card for card in assets.knowledge_cards if card.domain_id == "finance_risk_control_ai")
    assert finance_card.source_report_ids == [
        "report.business_vertical.finance_risk_control_ai.codex_synthesis_2026_04_07"
    ]
    assert finance_card.source_report_ids[0] in assets.knowledge_base_snapshot.compiled_report_ids


def test_default_bootstrap_assets_fails_when_reviewed_report_is_missing(tmp_path: Path) -> None:
    copied = _copy_artifacts(tmp_path)
    (
        copied
        / "knowledge"
        / "reviewed_reports"
        / "BusinessVertical_LLM与Agent企业交付_整合版.md"
    ).unlink()

    with pytest.raises(ValueError, match="missing_reviewed_reports"):
        default_bootstrap_assets(artifacts_root=copied)


def test_default_bootstrap_assets_fails_when_snapshot_card_ids_drift(tmp_path: Path) -> None:
    copied = _copy_artifacts(tmp_path)
    snapshot_path = copied / "knowledge" / "compiled" / "snapshots" / "kb-2026-04-07-v1.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["card_ids"] = payload["card_ids"][:-1]
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="knowledge_card_id_mismatch"):
        default_bootstrap_assets(artifacts_root=copied)
