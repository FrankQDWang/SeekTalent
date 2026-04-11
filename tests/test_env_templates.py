from __future__ import annotations

from pathlib import Path

from seektalent.resources import read_repo_env_template, repo_env_template_file


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_env_templates_stay_in_sync() -> None:
    assert not (_repo_root() / "src" / "seektalent" / "default.env").exists()


def test_repo_env_template_points_to_env_example() -> None:
    template_path = repo_env_template_file()

    assert template_path == _repo_root() / ".env.example"
    assert read_repo_env_template() == template_path.read_text(encoding="utf-8")


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
