from seektalent.models import QueryTermCandidate, RetrievalState
from tests.settings_factory import make_settings


def test_rescue_feature_defaults() -> None:
    settings = make_settings()

    assert settings.candidate_feedback_enabled is True
    assert settings.candidate_feedback_model == "openai-chat:qwen3.5-flash"
    assert settings.candidate_feedback_reasoning_effort == "off"
    assert settings.target_company_enabled is False
    assert settings.company_discovery_enabled is True
    assert settings.company_discovery_provider == "bocha"
    assert settings.company_discovery_model == "openai-chat:qwen3.5-flash"


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
        company_discovery_attempted=True,
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
    assert state.company_discovery_attempted is True
    assert state.anchor_only_broaden_attempted is True
    assert state.rescue_lane_history[0]["selected_lane"] == "candidate_feedback"
