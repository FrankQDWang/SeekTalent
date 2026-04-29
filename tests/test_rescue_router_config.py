from __future__ import annotations

import pytest
from pydantic import ValidationError

from seektalent.config import AppSettings
from seektalent.models import QueryTermCandidate, RetrievalState
from tests.settings_factory import make_settings


def test_rescue_feature_defaults() -> None:
    settings = make_settings()

    assert settings.candidate_feedback_enabled is True
    assert settings.candidate_feedback_model == "openai-chat:qwen3.5-flash"
    assert settings.candidate_feedback_reasoning_effort == "off"
    assert not hasattr(settings, "target_company_enabled")
    assert not hasattr(settings, "company_discovery_enabled")
    assert not hasattr(settings, "bocha_api_key")


def test_stale_company_values_are_ignored_from_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SEEKTALENT_CTS_TIMEOUT_SECONDS=42",
                "SEEKTALENT_TARGET_COMPANY_ENABLED=true",
                "SEEKTALENT_COMPANY_DISCOVERY_ENABLED=true",
                "SEEKTALENT_BOCHA_API_KEY=stale-secret",
                "SEEKTALENT_COMPANY_DISCOVERY_PROVIDER=bocha",
            ]
        ),
        encoding="utf-8",
    )

    settings = AppSettings(_env_file=env_file)

    assert settings.cts_timeout_seconds == 42
    assert not hasattr(settings, "target_company_enabled")
    assert not hasattr(settings, "company_discovery_enabled")
    assert not hasattr(settings, "bocha_api_key")


def test_candidate_feedback_query_term_source_is_valid() -> None:
    term = QueryTermCandidate(
        term="LangGraph",
        source="candidate_feedback",
        category="expansion",
        priority=30,
        evidence="Supported by two fit seed resumes.",
        first_added_round=4,
        retrieval_role="core_skill",
        queryability="admitted",
        family="feedback.langgraph",
    )

    assert term.source == "candidate_feedback"
    assert term.family == "feedback.langgraph"


def test_retrieval_state_tracks_rescue_attempts() -> None:
    state = RetrievalState(
        candidate_feedback_attempted=True,
        anchor_only_broaden_attempted=True,
        rescue_lane_history=[
            {
                "round_no": 4,
                "selected_lane": "candidate_feedback",
                "forced_query_terms": ["AI Agent", "LangGraph"],
            }
        ],
    )

    assert state.candidate_feedback_attempted is True
    assert state.anchor_only_broaden_attempted is True
    assert state.rescue_lane_history[0]["selected_lane"] == "candidate_feedback"


def test_retrieval_state_rejects_removed_company_discovery_attempted_field() -> None:
    with pytest.raises(ValidationError):
        RetrievalState(company_discovery_attempted=True)
