from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, cast

import httpx
from pydantic_ai import NativeOutput, PromptedOutput
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, Model, get_user_agent, infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers import infer_provider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from seektalent.config import AppSettings, ReasoningEffort, load_process_env


StructuredOutputMode = Literal["native_json_schema", "prompted_json"]

NATIVE_OPENAI_CHAT_MODELS = {"openai-chat:deepseek-v3.2"}
BAILIAN_THINKING_MODELS = {
    "openai-chat:deepseek-v3.2",
    "openai-chat:kimi/kimi-k2.5",
}
STAGE_MODEL_ATTR = {
    "requirements": "requirements_model_id",
    "controller": "controller_model_id",
    "scoring": "scoring_model_id",
    "finalize": "finalize_model_id",
    "reflection": "reflection_model_id",
    "structured_repair": "structured_repair_model_id",
    "judge": "judge_model_id",
    "tui_summary": "tui_summary_model_id",
    "candidate_feedback": "candidate_feedback_model_id",
}
TEXT_LLM_BASE_URLS = {
    ("openai_chat_completions_compatible", "bailian_openai_chat_completions", "beijing"): "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ("anthropic_messages_compatible", "bailian_anthropic_messages", "beijing"): "https://dashscope.aliyuncs.com/apps/anthropic",
    ("anthropic_messages_compatible", "bailian_anthropic_messages", "singapore"): "https://dashscope-intl.aliyuncs.com/apps/anthropic",
}


@dataclass(frozen=True)
class ProviderRequestPolicy:
    extra_body: dict[str, object]


@dataclass(frozen=True)
class TextLLMCapability:
    structured_output_mode: StructuredOutputMode
    supports_thinking: bool
    supports_reasoning_effort: bool
    allowed_reasoning_efforts: frozenset[str]


@dataclass(frozen=True)
class ResolvedTextModelConfig:
    stage: str
    protocol_family: str
    provider_label: str
    endpoint_kind: str
    endpoint_region: str
    base_url: str
    api_key: str | None
    model_id: str
    structured_output_mode: StructuredOutputMode
    thinking_mode: bool
    reasoning_effort: ReasoningEffort
    openai_prompt_cache_enabled: bool
    openai_prompt_cache_retention: str | None


TEXT_LLM_CAPABILITIES = {
    (
        "bailian",
        "openai_chat_completions_compatible",
        "bailian_openai_chat_completions",
        "beijing",
        "deepseek-v4-pro",
    ): TextLLMCapability(
        structured_output_mode="prompted_json",
        supports_thinking=True,
        supports_reasoning_effort=True,
        allowed_reasoning_efforts=frozenset({"high", "max"}),
    ),
    (
        "bailian",
        "openai_chat_completions_compatible",
        "bailian_openai_chat_completions",
        "beijing",
        "deepseek-v4-flash",
    ): TextLLMCapability(
        structured_output_mode="prompted_json",
        supports_thinking=True,
        supports_reasoning_effort=True,
        allowed_reasoning_efforts=frozenset({"high", "max"}),
    ),
    (
        "bailian",
        "anthropic_messages_compatible",
        "bailian_anthropic_messages",
        "beijing",
        "deepseek-v4-pro",
    ): TextLLMCapability(
        structured_output_mode="prompted_json",
        supports_thinking=True,
        supports_reasoning_effort=True,
        allowed_reasoning_efforts=frozenset({"high", "max"}),
    ),
    (
        "bailian",
        "anthropic_messages_compatible",
        "bailian_anthropic_messages",
        "beijing",
        "deepseek-v4-flash",
    ): TextLLMCapability(
        structured_output_mode="prompted_json",
        supports_thinking=True,
        supports_reasoning_effort=True,
        allowed_reasoning_efforts=frozenset({"high", "max"}),
    ),
}


def model_provider(model_id: str) -> str:
    if ":" in model_id:
        return model_id.split(":", 1)[0]
    return "bailian"


def _normalize_openai_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    normalized = base_url.rstrip("/")
    if normalized.endswith("/responses"):
        normalized = normalized[: -len("/responses")]
    return normalized


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=DEFAULT_HTTP_TIMEOUT, connect=5),
        headers={"User-Agent": get_user_agent()},
    )


def _fresh_openai_provider(
    base_url: str | None = None,
    api_key: str | None = None,
) -> OpenAIProvider:
    return OpenAIProvider(
        base_url=_normalize_openai_base_url(base_url),
        api_key=api_key,
        http_client=_http_client(),
    )


def resolve_text_llm_base_url(settings: AppSettings) -> str:
    if settings.text_llm_base_url_override:
        if settings.text_llm_protocol_family == "openai_chat_completions_compatible":
            return _normalize_openai_base_url(settings.text_llm_base_url_override) or ""
        return settings.text_llm_base_url_override.rstrip("/")
    key = (
        settings.text_llm_protocol_family,
        settings.text_llm_endpoint_kind,
        settings.text_llm_endpoint_region,
    )
    try:
        return TEXT_LLM_BASE_URLS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported text LLM endpoint mapping: {key!r}") from exc


def resolve_text_llm_api_key(settings: AppSettings) -> str | None:
    return settings.text_llm_api_key


def _resolve_stage_reasoning_policy(
    settings: AppSettings,
    *,
    stage: str,
) -> tuple[bool, ReasoningEffort]:
    if stage == "requirements":
        return settings.requirements_enable_thinking, "high" if settings.requirements_enable_thinking else "off"
    if stage == "controller":
        return settings.controller_enable_thinking, "high" if settings.controller_enable_thinking else "off"
    if stage == "reflection":
        return settings.reflection_enable_thinking, "high" if settings.reflection_enable_thinking else "off"
    if stage == "judge":
        effort = settings.effective_judge_reasoning_effort
        return effort != "off", effort
    if stage == "structured_repair":
        effort = settings.structured_repair_reasoning_effort
        return effort != "off", effort
    if stage == "candidate_feedback":
        effort = settings.candidate_feedback_reasoning_effort
        return effort != "off", effort
    if stage in {"scoring", "finalize", "tui_summary"}:
        return False, "off"
    raise ValueError(f"Unsupported text-llm stage: {stage}")


def resolve_structured_output_mode(config: ResolvedTextModelConfig) -> StructuredOutputMode:
    capability = _resolve_text_llm_capability(config)
    if capability is not None:
        return capability.structured_output_mode
    if config.provider_label == "bailian":
        return "prompted_json"
    return "native_json_schema"


def _resolve_text_llm_capability(config: ResolvedTextModelConfig) -> TextLLMCapability | None:
    return TEXT_LLM_CAPABILITIES.get(
        (
            config.provider_label,
            config.protocol_family,
            config.endpoint_kind,
            config.endpoint_region,
            config.model_id,
        )
    )


def validate_protocol_endpoint_region_model_matrix(config: ResolvedTextModelConfig) -> None:
    if (
        config.protocol_family == "anthropic_messages_compatible"
        and config.provider_label == "bailian"
        and config.model_id.startswith("deepseek-v4")
        and config.endpoint_region != "beijing"
    ):
        raise ValueError(
            "Bailian Anthropic-compatible DeepSeek V4 is region-gated to the Beijing endpoint."
        )
    capability = _resolve_text_llm_capability(config)
    if capability is None:
        return
    if config.thinking_mode and not capability.supports_thinking:
        raise ValueError(f"{config.stage} does not support provider-side thinking for {config.model_id}.")
    if config.reasoning_effort == "off":
        return
    if not capability.supports_reasoning_effort:
        raise ValueError(f"{config.stage} does not support reasoning_effort for {config.model_id}.")
    if config.reasoning_effort not in capability.allowed_reasoning_efforts:
        raise ValueError(
            f"{config.stage} reasoning_effort {config.reasoning_effort!r} is unsupported for {config.model_id}."
        )


def resolve_stage_model_config(settings: AppSettings, *, stage: str) -> ResolvedTextModelConfig:
    model_id = getattr(settings, STAGE_MODEL_ATTR[stage])
    if model_id is None:
        if stage == "tui_summary":
            model_id = settings.scoring_model_id
        else:
            raise ValueError(f"Stage {stage} has no configured model id.")
    thinking_mode, reasoning_effort = _resolve_stage_reasoning_policy(settings, stage=stage)
    config = ResolvedTextModelConfig(
        stage=stage,
        protocol_family=settings.text_llm_protocol_family,
        provider_label=settings.text_llm_provider_label,
        endpoint_kind=settings.text_llm_endpoint_kind,
        endpoint_region=settings.text_llm_endpoint_region,
        base_url=resolve_text_llm_base_url(settings),
        api_key=resolve_text_llm_api_key(settings),
        model_id=model_id,
        structured_output_mode="prompted_json",
        thinking_mode=thinking_mode,
        reasoning_effort=reasoning_effort,
        openai_prompt_cache_enabled=settings.openai_prompt_cache_enabled,
        openai_prompt_cache_retention=settings.openai_prompt_cache_retention,
    )
    validate_protocol_endpoint_region_model_matrix(config)
    return replace(config, structured_output_mode=resolve_structured_output_mode(config))


def build_provider_request_policy(config: ResolvedTextModelConfig) -> ProviderRequestPolicy:
    if config.protocol_family == "openai_chat_completions_compatible":
        extra_body: dict[str, object] = {"enable_thinking": config.thinking_mode}
        if config.reasoning_effort != "off":
            extra_body["reasoning_effort"] = config.reasoning_effort
        return ProviderRequestPolicy(extra_body=extra_body)
    thinking_type = "enabled" if config.thinking_mode else "disabled"
    extra_body = {"thinking": {"type": thinking_type}}
    if config.reasoning_effort != "off":
        extra_body["reasoning_effort"] = config.reasoning_effort
    return ProviderRequestPolicy(extra_body=extra_body)


def _build_resolved_model(config: ResolvedTextModelConfig) -> Model:
    if config.protocol_family == "openai_chat_completions_compatible":
        return OpenAIChatModel(
            config.model_id,
            provider=OpenAIProvider(
                base_url=config.base_url,
                api_key=config.api_key,
                http_client=_http_client(),
            ),
        )
    return AnthropicModel(
        config.model_id,
        provider=AnthropicProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            http_client=_http_client(),
        ),
    )


def build_model(
    model_or_config: str | ResolvedTextModelConfig,
    *,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
) -> Model:
    load_process_env()
    if isinstance(model_or_config, ResolvedTextModelConfig):
        return _build_resolved_model(model_or_config)

    def provider_factory(provider_name: str):
        if provider_name in {"openai", "openai-chat", "openai-responses"}:
            return _fresh_openai_provider(openai_base_url, openai_api_key)
        return infer_provider(provider_name)

    return infer_model(model_or_config, provider_factory=provider_factory)


def ensure_native_structured_output(model_id: str, model: Model) -> None:
    profile = model.profile
    if getattr(profile, "supports_json_schema_output", False):
        return
    raise ValueError(f"{model_id} does not support native structured output via JSON Schema.")


def build_output_spec(model_or_config: str | ResolvedTextModelConfig, model: Model, output_type: Any) -> Any:
    if isinstance(model_or_config, ResolvedTextModelConfig):
        if resolve_structured_output_mode(model_or_config) == "native_json_schema":
            ensure_native_structured_output(model_or_config.model_id, model)
            return NativeOutput(output_type, strict=True)
        return PromptedOutput(output_type)
    model_id = model_or_config
    if model_id in NATIVE_OPENAI_CHAT_MODELS:
        ensure_native_structured_output(model_id, model)
        return NativeOutput(output_type, strict=True)
    if model_id.startswith("openai-chat:"):
        return PromptedOutput(output_type)
    ensure_native_structured_output(model_id, model)
    return NativeOutput(output_type, strict=True)


def _build_resolved_model_settings(
    config: ResolvedTextModelConfig,
    *,
    prompt_cache_key: str | None = None,
) -> ModelSettings:
    policy = build_provider_request_policy(config)
    model_settings: dict[str, object] = {
        "thinking": False if config.reasoning_effort == "off" else config.reasoning_effort,
        "extra_body": policy.extra_body,
    }
    if (
        prompt_cache_key is not None
        and config.protocol_family == "openai_chat_completions_compatible"
        and config.openai_prompt_cache_enabled
    ):
        model_settings["openai_prompt_cache_key"] = prompt_cache_key
        if config.openai_prompt_cache_retention is not None:
            model_settings["openai_prompt_cache_retention"] = config.openai_prompt_cache_retention
    return cast(ModelSettings, model_settings)


def build_model_settings(
    settings_or_config: AppSettings | ResolvedTextModelConfig,
    model_id: str | None = None,
    *,
    reasoning_effort: ReasoningEffort | None = None,
    enable_thinking: bool | None = None,
    prompt_cache_key: str | None = None,
) -> ModelSettings:
    if isinstance(settings_or_config, ResolvedTextModelConfig):
        return _build_resolved_model_settings(settings_or_config, prompt_cache_key=prompt_cache_key)

    settings = settings_or_config
    assert model_id is not None
    is_openai_model = model_id.startswith(("openai:", "openai-chat:", "openai-responses:"))
    effective_effort = reasoning_effort or settings.reasoning_effort
    if effective_effort == "off":
        thinking = False
    else:
        thinking = effective_effort
    model_settings: dict[str, object] = {"thinking": thinking}
    if model_id in BAILIAN_THINKING_MODELS and enable_thinking is not None:
        model_settings["extra_body"] = {"enable_thinking": enable_thinking}
    if is_openai_model and settings.openai_prompt_cache_enabled and prompt_cache_key is not None:
        model_settings["openai_prompt_cache_key"] = prompt_cache_key
        if settings.openai_prompt_cache_retention is not None:
            model_settings["openai_prompt_cache_retention"] = settings.openai_prompt_cache_retention
    if not model_id.startswith("openai-responses:"):
        return cast(ModelSettings, model_settings)

    openai_settings: dict[str, object] = {
        "thinking": thinking,
        "openai_text_verbosity": "low",
    }
    if thinking is not False:
        openai_settings["openai_reasoning_summary"] = "concise"
    if is_openai_model and settings.openai_prompt_cache_enabled and prompt_cache_key is not None:
        openai_settings["openai_prompt_cache_key"] = prompt_cache_key
        if settings.openai_prompt_cache_retention is not None:
            openai_settings["openai_prompt_cache_retention"] = settings.openai_prompt_cache_retention
    return cast(ModelSettings, openai_settings)


def preflight_models(
    settings: AppSettings,
    *,
    extra_model_specs: list[tuple[str, str | None, str | None]] | None = None,
) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    stage_names = ["requirements", "controller", "scoring", "reflection", "finalize"]
    if settings.enable_eval:
        stage_names.append("judge")
    for stage_name in stage_names:
        config = resolve_stage_model_config(settings, stage=stage_name)
        key = (
            config.protocol_family,
            config.endpoint_kind,
            config.endpoint_region,
            config.model_id,
        )
        if key in seen:
            continue
        model = build_model(config)
        if resolve_structured_output_mode(config) == "native_json_schema":
            ensure_native_structured_output(config.model_id, model)
        seen.add(key)
    if extra_model_specs:
        for model_id, openai_base_url, openai_api_key in extra_model_specs:
            model = build_model(
                model_id,
                openai_base_url=openai_base_url,
                openai_api_key=openai_api_key,
            )
            if model_id in NATIVE_OPENAI_CHAT_MODELS or not model_id.startswith("openai-chat:"):
                ensure_native_structured_output(model_id, model)
