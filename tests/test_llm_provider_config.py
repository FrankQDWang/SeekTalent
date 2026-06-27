from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai import NativeOutput, PromptedOutput
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from seektalent.config import AppSettings, PRFConfigMigrationError, TextLLMConfigMigrationError, load_process_env
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
SOURCE_ENV_TEMPLATE = ROOT / ".env.example"
PACKAGED_ENV_TEMPLATE = ROOT / "src" / "seektalent" / "default.env"
STRICT_NATIVE_OPENAI_STAGES = (
    "requirements",
    "controller",
    "reflection",
    "scoring",
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
    assert settings.workbench_note_writer_model_id == "deepseek-v4-flash"
    assert settings.workbench_note_writer_reasoning_effort == "off"
    assert settings.workbench_conversation_model_id == "deepseek-v4-flash"
    assert settings.workbench_conversation_reasoning_effort == "max"


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


def test_source_env_template_uses_new_text_llm_keys() -> None:
    text = SOURCE_ENV_TEMPLATE.read_text(encoding="utf-8")

    assert "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=" in text
    assert "SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=" in text
    assert "SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=" in text
    assert "SEEKTALENT_REQUIREMENTS_MODEL_ID=deepseek-v4-pro" in text
    assert "SEEKTALENT_JUDGE_MODEL_ID=deepseek-v4-pro" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_WORKBENCH_NOTE_WRITER_MODEL_ID=deepseek-v4-flash" in text
    assert "SEEKTALENT_WORKBENCH_NOTE_WRITER_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_WORKBENCH_CONVERSATION_MODEL_ID=deepseek-v4-flash" in text
    assert "SEEKTALENT_WORKBENCH_CONVERSATION_REASONING_EFFORT=max" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=3.0" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_LIVE_HARNESS_TIMEOUT_SECONDS=30.0" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS=2048" in text
    assert "SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND=" not in text
    assert "SEEKTALENT_REQUIREMENTS_MODEL=" not in text
    assert "SEEKTALENT_JUDGE_OPENAI_BASE_URL=" not in text


def test_packaged_env_template_is_minimal_user_setup() -> None:
    lines = [
        line for line in PACKAGED_ENV_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert lines == [
        "SEEKTALENT_TEXT_LLM_API_KEY=",
        "SEEKTALENT_CTS_TENANT_KEY=",
        "SEEKTALENT_CTS_TENANT_SECRET=",
    ]


def test_llm_prf_runtime_and_live_harness_timeouts_are_separate() -> None:
    settings = AppSettings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.prf_probe_phrase_proposal_model_id == "deepseek-v4-flash"
    assert settings.prf_probe_phrase_proposal_reasoning_effort == "off"
    assert settings.prf_probe_phrase_proposal_timeout_seconds == 3.0
    assert settings.prf_probe_phrase_proposal_live_harness_timeout_seconds == 30.0
    assert settings.prf_probe_phrase_proposal_max_output_tokens == 2048


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND", "sidecar_span"),
        ("SEEKTALENT_PRF_V1_5_MODE", "shadow"),
        ("SEEKTALENT_PRF_MODEL_BACKEND", "http_sidecar"),
        ("SEEKTALENT_PRF_SIDECAR_ENDPOINT", "http://127.0.0.1:8741"),
        ("SEEKTALENT_PRF_SPAN_MODEL_NAME", "fastino/gliner2-multi-v1"),
        ("SEEKTALENT_PRF_EMBEDDING_MODEL_NAME", "Alibaba-NLP/gte-multilingual-base"),
    ],
)
def test_removed_prf_config_keys_fail_settings_validation(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    monkeypatch.setenv(key, value)

    with pytest.raises(PRFConfigMigrationError, match=key):
        AppSettings(_env_file=None)  # ty: ignore[unknown-argument]


def test_removed_prf_config_keys_in_env_file_fail_settings_validation(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_PRF_MODEL_BACKEND=http_sidecar\n", encoding="utf-8")

    with pytest.raises(PRFConfigMigrationError, match="SEEKTALENT_PRF_MODEL_BACKEND"):
        AppSettings(_env_file=env_file)  # ty: ignore[unknown-argument]


def test_env_file_none_does_not_scan_default_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("SEEKTALENT_PRF_MODEL_BACKEND=http_sidecar\n", encoding="utf-8")

    settings = AppSettings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.prf_probe_phrase_proposal_model_id == "deepseek-v4-flash"


def test_prf_probe_phrase_proposal_stage_uses_prompted_json() -> None:
    settings = make_settings()

    stage = resolve_stage_model_config(settings, stage="prf_probe_phrase_proposal")

    assert stage.model_id == "deepseek-v4-flash"
    assert stage.reasoning_effort == "off"
    assert stage.thinking_mode is False
    assert resolve_structured_output_mode(stage) == "prompted_json"


def test_workbench_note_writer_defaults_to_deepseek_v4_flash_non_reasoning() -> None:
    settings = make_settings()

    assert settings.workbench_note_writer_model_id == "deepseek-v4-flash"
    assert settings.workbench_note_writer_reasoning_effort == "off"
    stage = resolve_stage_model_config(settings, stage="workbench_note_writer")

    assert stage.model_id == "deepseek-v4-flash"
    assert stage.reasoning_effort == "off"
    assert stage.thinking_mode is False
    assert resolve_structured_output_mode(stage) == "plain_text"


def test_workbench_note_writer_output_spec_is_plain_text() -> None:
    stage = resolve_stage_model_config(make_settings(), stage="workbench_note_writer")

    output_spec = build_output_spec(stage, _json_schema_capable_model(), str)

    assert output_spec is str
    assert not isinstance(output_spec, PromptedOutput)


def test_workbench_conversation_stage_uses_bailian_native_strict_schema() -> None:
    stage = resolve_stage_model_config(make_settings(), stage="workbench_conversation")
    output_spec = build_output_spec(stage, _json_schema_capable_model(), dict)
    policy = build_provider_request_policy(stage)

    assert stage.provider_label == "bailian"
    assert stage.endpoint_kind == "bailian_openai_chat_completions"
    assert stage.model_id == "deepseek-v4-flash"
    assert stage.thinking_mode is True
    assert stage.reasoning_effort == "max"
    assert policy.extra_body == {"enable_thinking": True, "reasoning_effort": "max"}
    assert resolve_structured_output_mode(stage) == "native_json_schema"
    assert isinstance(output_spec, NativeOutput)


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
    controller_stage = resolve_stage_model_config(settings, stage="controller")
    reflection_stage = resolve_stage_model_config(settings, stage="reflection")
    scoring_stage = resolve_stage_model_config(settings, stage="scoring")
    judge_stage = resolve_stage_model_config(settings, stage="judge")

    assert requirements_stage.reasoning_effort == "off"
    assert requirements_stage.thinking_mode is False
    assert controller_stage.reasoning_effort == "off"
    assert controller_stage.thinking_mode is False
    assert reflection_stage.reasoning_effort == "off"
    assert reflection_stage.thinking_mode is False
    assert scoring_stage.reasoning_effort == "off"
    assert scoring_stage.thinking_mode is False
    assert judge_stage.reasoning_effort == "off"
    assert judge_stage.thinking_mode is False
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


def test_workbench_note_writer_off_omits_provider_reasoning_effort() -> None:
    settings = make_settings(
        workbench_note_writer_model_id="deepseek-v4-flash",
        workbench_note_writer_reasoning_effort="off",
    )

    config = resolve_stage_model_config(settings, stage="workbench_note_writer")
    policy = build_provider_request_policy(config)
    model_settings = build_model_settings(config)

    assert policy.extra_body.get("reasoning_effort") is None
    assert config.thinking_mode is False
    assert config.reasoning_effort == "off"
    assert model_settings["thinking"] is False


def test_workbench_note_writer_bailian_path_disables_thinking_without_reasoning_effort() -> None:
    settings = make_settings(
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        workbench_note_writer_reasoning_effort="off",
    )

    policy = build_provider_request_policy(resolve_stage_model_config(settings, stage="workbench_note_writer"))

    assert policy.extra_body == {"enable_thinking": False}


def test_workbench_note_writer_anthropic_path_disables_thinking_without_reasoning_effort() -> None:
    settings = make_settings(
        text_llm_protocol_family="anthropic_messages_compatible",
        text_llm_endpoint_kind="bailian_anthropic_messages",
        text_llm_endpoint_region="beijing",
        workbench_note_writer_reasoning_effort="off",
    )

    policy = build_provider_request_policy(resolve_stage_model_config(settings, stage="workbench_note_writer"))

    assert policy.extra_body == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in policy.extra_body


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
