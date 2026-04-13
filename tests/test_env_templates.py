from __future__ import annotations

from pathlib import Path

from seektalent.resources import (
    env_template_file,
    read_env_template,
    source_artifacts_root,
    source_env_template_file,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_env_templates_stay_in_sync() -> None:
    assert not (_repo_root() / "src" / "seektalent" / "default.env").exists()


def test_bundled_env_template_matches_source_env_example() -> None:
    template_path = env_template_file()
    source_path = source_env_template_file()

    assert source_path == _repo_root() / ".env.example"
    assert read_env_template() == template_path.read_text(encoding="utf-8")
    assert template_path.read_text(encoding="utf-8") == source_path.read_text(encoding="utf-8")


def test_bundled_runtime_assets_match_source_artifacts() -> None:
    source_root = source_artifacts_root()
    bundled_root = _repo_root() / "src" / "seektalent" / "_bundled" / "artifacts"
    relative_paths = [
        Path("knowledge/packs/finance_risk_control_ai.json"),
        Path("knowledge/packs/llm_agent_rag_engineering.json"),
        Path("knowledge/packs/search_ranking_retrieval_engineering.json"),
        Path("runtime/active.json"),
        Path("runtime/calibrations/qwen3-reranker-8b-mxfp8-2026-04-07-v1.json"),
        Path("runtime/policies/business-default-2026-04-09-v1.json"),
        Path("runtime/registries/school_types.json"),
    ]

    for relative_path in relative_paths:
        assert (bundled_root / relative_path).read_bytes() == (source_root / relative_path).read_bytes()


def test_configuration_doc_mentions_active_env_fields() -> None:
    text = (_repo_root() / "docs" / "configuration.md").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in text
    assert "OPENAI_BASE_URL" in text
    assert "ANTHROPIC_API_KEY" in text
    assert "GOOGLE_API_KEY" in text
    assert "SEEKTALENT_REQUIREMENT_EXTRACTION_PROVIDER" in text
    assert "SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION_PROVIDER" in text
    assert "SEEKTALENT_SEARCH_CONTROLLER_DECISION_PROVIDER" in text
    assert "SEEKTALENT_BRANCH_OUTCOME_EVALUATION_PROVIDER" in text
    assert "SEEKTALENT_SEARCH_RUN_FINALIZATION_PROVIDER" in text
    assert "SEEKTALENT_RERANK_BASE_URL" in text
    assert "SEEKTALENT_RERANK_TIMEOUT_SECONDS" in text
    assert "SEEKTALENT_RERANK_HOST" in text
    assert "SEEKTALENT_RERANK_PORT" in text
    assert "SEEKTALENT_RERANK_MODEL_ID" in text
