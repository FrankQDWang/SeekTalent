from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai import NativeOutput, PromptedOutput
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from seektalent.candidate_feedback.proposal_runtime import model_dependency_gate_allows_mainline
from seektalent.config import AppSettings, TextLLMConfigMigrationError, load_process_env
from seektalent.llm import (
    build_output_spec,
    build_model,
    build_model_settings,
    build_provider_request_policy,
    resolve_stage_model_config,
    resolve_structured_output_mode,
    resolve_text_llm_base_url,
)
from tests.settings_factory import make_settings


ROOT = Path(__file__).resolve().parents[1]
ENV_TEMPLATES = [
    ROOT / ".env.example",
    ROOT / "src" / "seektalent" / "default.env",
]
STRICT_NATIVE_OPENAI_STAGES = (
    "requirements",
    "controller",
    "reflection",
    "scoring",
    "finalize",
    "judge",
    "structured_repair",
)


def _json_schema_capable_model() -> object:
    return SimpleNamespace(profile=SimpleNamespace(supports_json_schema_output=True))


def test_canonical_text_llm_defaults_use_dual_protocol_surface() -> None:
    settings = make_settings()

    assert settings.text_llm_protocol_family == "openai_chat_completions_compatible"
    assert settings.text_llm_provider_label == "bailian"
    assert settings.text_llm_endpoint_kind == "bailian_openai_chat_completions"
    assert settings.text_llm_endpoint_region == "beijing"
    assert settings.requirements_model_id == "deepseek-v4-pro"
    assert settings.controller_model_id == "deepseek-v4-pro"
    assert settings.reflection_model_id == "deepseek-v4-pro"
    assert settings.judge_model_id == "deepseek-v4-pro"
    assert settings.scoring_model_id == "deepseek-v4-flash"
    assert settings.finalize_model_id == "deepseek-v4-flash"
    assert settings.structured_repair_model_id == "deepseek-v4-flash"
    assert settings.candidate_feedback_model_id == "deepseek-v4-flash"


def test_legacy_stage_key_in_dotenv_fails_with_migration_error(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:deepseek-v3.2\n",
        encoding="utf-8",
    )

    with pytest.raises(TextLLMConfigMigrationError, match="legacy text-llm config"):
        AppSettings(_env_file=env_file)  # ty: ignore[unknown-argument]


def test_prefixed_value_on_new_model_id_key_fails_with_migration_error(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SEEKTALENT_REQUIREMENTS_MODEL_ID=openai-responses:gpt-5.4-mini\n",
        encoding="utf-8",
    )

    with pytest.raises(TextLLMConfigMigrationError, match="provider-prefixed model string"):
        AppSettings(_env_file=env_file)  # ty: ignore[unknown-argument]


def test_candidate_feedback_legacy_model_key_is_hard_cut(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SEEKTALENT_CANDIDATE_FEEDBACK_MODEL=openai-chat:qwen3.5-flash\n",
        encoding="utf-8",
    )

    with pytest.raises(TextLLMConfigMigrationError, match="legacy text-llm config"):
        AppSettings(_env_file=env_file)  # ty: ignore[unknown-argument]


def test_stage_model_id_init_kwarg_with_prefixed_value_fails_fast() -> None:
    with pytest.raises(TextLLMConfigMigrationError, match="provider-prefixed model string"):
        AppSettings(requirements_model_id="openai-chat:deepseek-v3.2", _env_file=None)  # ty: ignore[unknown-argument]


def test_protocol_family_and_endpoint_kind_must_match() -> None:
    with pytest.raises(ValidationError, match="text_llm_endpoint_kind"):
        make_settings(
            text_llm_protocol_family="anthropic_messages_compatible",
            text_llm_endpoint_kind="bailian_openai_chat_completions",
        )


def test_settings_scan_default_dotenv_when_not_overridden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:deepseek-v3.2\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(TextLLMConfigMigrationError, match="legacy text-llm config"):
        AppSettings()


def test_explicit_env_file_none_skips_default_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:deepseek-v3.2\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = AppSettings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.requirements_model_id == "deepseek-v4-pro"


def test_checked_in_env_templates_use_new_text_llm_keys() -> None:
    for path in ENV_TEMPLATES:
        text = path.read_text(encoding="utf-8")
        assert "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=" in text
        assert "SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=" in text
        assert "SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=" in text
        assert "SEEKTALENT_REQUIREMENTS_MODEL_ID=deepseek-v4-pro" in text
        assert "SEEKTALENT_JUDGE_MODEL_ID=deepseek-v4-pro" in text
        assert "SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND=llm_deepseek_v4_flash" in text
        assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash" in text
        assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT=off" in text
        assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=3.0" in text
        assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS=2048" in text
        assert "SEEKTALENT_REQUIREMENTS_MODEL=" not in text
        assert "SEEKTALENT_JUDGE_OPENAI_BASE_URL=" not in text


def test_prf_probe_llm_backend_defaults_are_explicit() -> None:
    settings = make_settings()

    assert settings.prf_probe_proposal_backend == "llm_deepseek_v4_flash"
    assert settings.prf_probe_phrase_proposal_model_id == "deepseek-v4-flash"
    assert settings.prf_probe_phrase_proposal_reasoning_effort == "off"
    assert settings.prf_probe_phrase_proposal_timeout_seconds == 3.0
    assert settings.prf_probe_phrase_proposal_max_output_tokens == 2048


def test_prf_probe_phrase_proposal_stage_uses_prompted_json() -> None:
    settings = make_settings()

    stage = resolve_stage_model_config(settings, stage="prf_probe_phrase_proposal")

    assert stage.model_id == "deepseek-v4-flash"
    assert stage.reasoning_effort == "off"
    assert stage.thinking_mode is False
    assert resolve_structured_output_mode(stage) == "prompted_json"


def test_prf_defaults_preserve_shadow_mode_and_legacy_backend() -> None:
    settings = make_settings()

    assert settings.prf_v1_5_mode == "shadow"
    assert settings.prf_model_backend == "legacy"


def test_sidecar_default_settings_remain_exposed() -> None:
    settings = make_settings()

    assert settings.prf_sidecar_profile == "host-local"
    assert settings.prf_sidecar_bind_host == "127.0.0.1"
    assert settings.prf_sidecar_endpoint == "http://127.0.0.1:8741"
    assert settings.prf_sidecar_serve_mode == "dev-bootstrap"
    assert settings.prf_sidecar_bakeoff_promoted is False


def test_mainline_mode_requires_pinned_model_revisions() -> None:
    settings = make_settings(prf_v1_5_mode="mainline")

    assert model_dependency_gate_allows_mainline(settings) is False


def test_runtime_mode_defaults_to_dev_paths() -> None:
    settings = make_settings()

    assert settings.runtime_mode == "dev"
    assert settings.artifacts_dir == "artifacts"
    assert settings.runs_dir == "runs"
    assert settings.llm_cache_dir == ".seektalent/cache"


def test_with_overrides_preserves_runtime_default_resolution() -> None:
    settings = make_settings().with_overrides(runtime_mode="prod")

    assert settings.runtime_mode == "prod"
    assert settings.artifacts_dir == "~/.seektalent/artifacts"
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_load_process_env_only_imports_provider_boundary_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=openai-key",
                "ANTHROPIC_API_KEY=anthropic-key",
                "SEEKTALENT_REQUIREMENTS_MODEL_ID=deepseek-v4-pro",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_REQUIREMENTS_MODEL_ID", raising=False)

    load_process_env(env_file)

    assert os.environ["OPENAI_API_KEY"] == "openai-key"
    assert os.environ["ANTHROPIC_API_KEY"] == "anthropic-key"
    assert "SEEKTALENT_REQUIREMENTS_MODEL_ID" not in os.environ


def test_openai_protocol_family_means_chat_completions_not_responses() -> None:
    settings = make_settings(
        text_llm_protocol_family="openai_chat_completions_compatible",
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        text_llm_endpoint_region="beijing",
    )

    stage = resolve_stage_model_config(settings, stage="requirements")

    assert stage.protocol_family == "openai_chat_completions_compatible"
    assert stage.endpoint_kind == "bailian_openai_chat_completions"
    assert stage.model_id == "deepseek-v4-pro"


def test_bailian_anthropic_deepseek_v4_requires_beijing_region() -> None:
    settings = make_settings(
        text_llm_protocol_family="anthropic_messages_compatible",
        text_llm_endpoint_kind="bailian_anthropic_messages",
        text_llm_endpoint_region="singapore",
    )

    with pytest.raises(ValueError, match="Beijing"):
        resolve_stage_model_config(settings, stage="requirements")


def test_bailian_openai_chat_base_url_resolves_for_beijing() -> None:
    settings = make_settings(
        text_llm_protocol_family="openai_chat_completions_compatible",
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        text_llm_endpoint_region="beijing",
    )

    assert resolve_text_llm_base_url(settings) == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_bailian_anthropic_base_url_resolves_for_beijing() -> None:
    settings = make_settings(
        text_llm_protocol_family="anthropic_messages_compatible",
        text_llm_endpoint_kind="bailian_anthropic_messages",
        text_llm_endpoint_region="beijing",
    )

    assert resolve_text_llm_base_url(settings) == "https://dashscope.aliyuncs.com/apps/anthropic"


def test_default_openai_structured_stages_use_native_strict_output() -> None:
    settings = make_settings(
        text_llm_protocol_family="openai_chat_completions_compatible",
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        text_llm_endpoint_region="beijing",
    )
    model = _json_schema_capable_model()

    for stage_name in STRICT_NATIVE_OPENAI_STAGES:
        stage = resolve_stage_model_config(settings, stage=stage_name)
        output_spec = build_output_spec(stage, model, dict)

        assert resolve_structured_output_mode(stage) == "native_json_schema"
        assert isinstance(output_spec, NativeOutput)
        assert output_spec.strict is True


def test_anthropic_structured_stages_remain_prompted_output() -> None:
    settings = make_settings(
        text_llm_protocol_family="anthropic_messages_compatible",
        text_llm_endpoint_kind="bailian_anthropic_messages",
        text_llm_endpoint_region="beijing",
    )
    model = _json_schema_capable_model()

    for stage_name in STRICT_NATIVE_OPENAI_STAGES:
        stage = resolve_stage_model_config(settings, stage=stage_name)
        output_spec = build_output_spec(stage, model, dict)

        assert resolve_structured_output_mode(stage) == "prompted_json"
        assert isinstance(output_spec, PromptedOutput)


def test_openai_tui_summary_and_candidate_feedback_remain_prompted_output() -> None:
    settings = make_settings(
        text_llm_protocol_family="openai_chat_completions_compatible",
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        text_llm_endpoint_region="beijing",
    )
    model = _json_schema_capable_model()

    for stage_name in ("tui_summary", "candidate_feedback"):
        stage = resolve_stage_model_config(settings, stage=stage_name)
        output_spec = build_output_spec(stage, model, dict)

        assert resolve_structured_output_mode(stage) == "prompted_json"
        assert isinstance(output_spec, PromptedOutput)


def test_openai_stage_policy_can_prompt_one_strict_capable_stage_while_another_stays_native() -> None:
    settings = make_settings(
        text_llm_protocol_family="openai_chat_completions_compatible",
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        text_llm_endpoint_region="beijing",
        scoring_model_id="deepseek-v4-flash",
        candidate_feedback_model_id="deepseek-v4-flash",
    )
    model = _json_schema_capable_model()

    scoring_stage = resolve_stage_model_config(settings, stage="scoring")
    candidate_feedback_stage = resolve_stage_model_config(settings, stage="candidate_feedback")
    scoring_output_spec = build_output_spec(scoring_stage, model, dict)
    candidate_feedback_output_spec = build_output_spec(candidate_feedback_stage, model, dict)

    assert scoring_stage.model_id == candidate_feedback_stage.model_id == "deepseek-v4-flash"
    assert resolve_structured_output_mode(scoring_stage) == "native_json_schema"
    assert isinstance(scoring_output_spec, NativeOutput)
    assert scoring_output_spec.strict is True
    assert resolve_structured_output_mode(candidate_feedback_stage) == "prompted_json"
    assert isinstance(candidate_feedback_output_spec, PromptedOutput)


def test_bailian_deepseek_v4_defaults_to_native_json_schema_mode() -> None:
    settings = make_settings()
    stage = resolve_stage_model_config(settings, stage="controller")
    output_spec = build_output_spec(stage, _json_schema_capable_model(), dict)

    assert resolve_structured_output_mode(stage) == "native_json_schema"
    assert isinstance(output_spec, NativeOutput)
    assert output_spec.strict is True


def test_stage_reasoning_policy_defaults_are_explicit() -> None:
    settings = make_settings()

    requirements_stage = resolve_stage_model_config(settings, stage="requirements")
    scoring_stage = resolve_stage_model_config(settings, stage="scoring")
    judge_stage = resolve_stage_model_config(settings, stage="judge")

    assert requirements_stage.reasoning_effort == "high"
    assert requirements_stage.thinking_mode is True
    assert scoring_stage.reasoning_effort == "off"
    assert scoring_stage.thinking_mode is False
    assert judge_stage.reasoning_effort == "high"
    assert judge_stage.model_id == "deepseek-v4-pro"


def test_structured_repair_and_candidate_feedback_respect_configured_effort() -> None:
    settings = make_settings(
        structured_repair_reasoning_effort="high",
        candidate_feedback_reasoning_effort="high",
    )

    structured_repair_stage = resolve_stage_model_config(settings, stage="structured_repair")
    candidate_feedback_stage = resolve_stage_model_config(settings, stage="candidate_feedback")

    assert structured_repair_stage.thinking_mode is True
    assert structured_repair_stage.reasoning_effort == "high"
    assert candidate_feedback_stage.thinking_mode is True
    assert candidate_feedback_stage.reasoning_effort == "high"


def test_judge_reasoning_off_disables_provider_side_thinking() -> None:
    stage = resolve_stage_model_config(
        make_settings(judge_reasoning_effort="off"),
        stage="judge",
    )

    policy = build_provider_request_policy(stage)

    assert stage.thinking_mode is False
    assert stage.reasoning_effort == "off"
    assert policy.extra_body == {"enable_thinking": False}


def test_openai_path_builds_chat_model_not_responses_model() -> None:
    stage = resolve_stage_model_config(
        make_settings(
            text_llm_api_key="test-key",
            text_llm_protocol_family="openai_chat_completions_compatible",
            text_llm_endpoint_kind="bailian_openai_chat_completions",
            text_llm_endpoint_region="beijing",
        ),
        stage="requirements",
    )

    model = build_model(stage)

    assert isinstance(model, OpenAIChatModel)
    assert not isinstance(model, OpenAIResponsesModel)


def test_openai_resolved_model_default_provider_retry_behavior_is_unchanged() -> None:
    stage = resolve_stage_model_config(
        make_settings(
            text_llm_api_key="test-key",
            text_llm_protocol_family="openai_chat_completions_compatible",
            text_llm_endpoint_kind="bailian_openai_chat_completions",
            text_llm_endpoint_region="beijing",
        ),
        stage="requirements",
    )

    model = build_model(stage)

    assert model.client.max_retries == 2


def test_anthropic_path_preserves_bare_model_id() -> None:
    stage = resolve_stage_model_config(
        make_settings(
            text_llm_api_key="test-key",
            text_llm_protocol_family="anthropic_messages_compatible",
            text_llm_endpoint_kind="bailian_anthropic_messages",
            text_llm_endpoint_region="beijing",
        ),
        stage="requirements",
    )
    model = build_model(stage)

    assert isinstance(model, AnthropicModel)
    assert getattr(model, "model_name", None) == "deepseek-v4-pro"


def test_anthropic_resolved_model_default_provider_retry_behavior_is_unchanged() -> None:
    stage = resolve_stage_model_config(
        make_settings(
            text_llm_api_key="test-key",
            text_llm_protocol_family="anthropic_messages_compatible",
            text_llm_endpoint_kind="bailian_anthropic_messages",
            text_llm_endpoint_region="beijing",
        ),
        stage="requirements",
    )

    model = build_model(stage)

    assert model.client.max_retries == 2


def test_openai_scoring_policy_disables_thinking_in_provider_request_controls() -> None:
    stage = resolve_stage_model_config(
        make_settings(
            text_llm_protocol_family="openai_chat_completions_compatible",
            text_llm_endpoint_kind="bailian_openai_chat_completions",
            text_llm_endpoint_region="beijing",
        ),
        stage="scoring",
    )

    policy = build_provider_request_policy(stage)

    assert policy.extra_body == {"enable_thinking": False}


def test_openai_resolved_model_settings_preserve_prompt_cache_controls() -> None:
    stage = resolve_stage_model_config(
        make_settings(
            text_llm_protocol_family="openai_chat_completions_compatible",
            text_llm_endpoint_kind="bailian_openai_chat_completions",
            text_llm_endpoint_region="beijing",
            openai_prompt_cache_enabled=True,
            openai_prompt_cache_retention="1h",
        ),
        stage="requirements",
    )

    model_settings = build_model_settings(stage, prompt_cache_key="prompt-key")

    assert model_settings["openai_prompt_cache_key"] == "prompt-key"
    assert model_settings["openai_prompt_cache_retention"] == "1h"


def test_openai_base_url_override_is_normalized_on_resolved_path() -> None:
    settings = make_settings(
        text_llm_protocol_family="openai_chat_completions_compatible",
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        text_llm_endpoint_region="beijing",
        text_llm_base_url_override="https://example.com/v1/responses/",
    )

    stage = resolve_stage_model_config(settings, stage="requirements")

    assert stage.base_url == "https://example.com/v1"


def test_capability_matrix_rejects_unsupported_judge_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="judge"):
        resolve_stage_model_config(
            make_settings(judge_reasoning_effort="medium"),
            stage="judge",
        )
