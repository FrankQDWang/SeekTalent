from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tests.settings_factory import make_settings


def test_settings_exposes_typed_runtime_limits_section() -> None:
    settings = make_settings(
        min_rounds=2,
        max_rounds=7,
        search_max_pages_per_round=4,
        search_max_attempts_per_round=5,
        search_no_progress_limit=3,
        scoring_timeout_seconds=45.0,
    )

    limits = settings.runtime_limits

    assert limits.min_rounds == 2
    assert limits.max_rounds == 7
    assert limits.search_max_pages_per_round == 4
    assert limits.search_max_attempts_per_round == 5
    assert limits.search_no_progress_limit == 3
    assert limits.scoring_timeout_seconds == 45.0
    with pytest.raises(FrozenInstanceError):
        limits.max_rounds = 1  # ty:ignore[invalid-assignment]


def test_settings_exposes_typed_source_provider_section() -> None:
    settings = make_settings(
        provider_name="liepin",
        liepin_worker_mode="opencli",
        liepin_opencli_max_pages_per_task=2,
        liepin_default_daily_detail_budget=9,
    )

    source = settings.source_providers

    assert source.provider_name == "liepin"
    assert source.liepin_worker_mode == "opencli"
    assert source.liepin_opencli_max_pages_per_task == 2
    assert source.liepin_default_daily_detail_budget == 9


def test_settings_exposes_typed_text_llm_section() -> None:
    settings = make_settings(
        text_llm_protocol_family="anthropic_messages_compatible",
        text_llm_endpoint_kind="bailian_anthropic_messages",
        requirements_model_id="deepseek-v4-pro",
        scoring_model_id="deepseek-v4-flash",
        judge_model_id="deepseek-v4-pro",
    )

    text_llm = settings.text_llm

    assert text_llm.protocol_family == "anthropic_messages_compatible"
    assert text_llm.endpoint_kind == "bailian_anthropic_messages"
    assert text_llm.requirements_model_id == "deepseek-v4-pro"
    assert text_llm.scoring_model_id == "deepseek-v4-flash"
    assert text_llm.judge_model_id == "deepseek-v4-pro"
