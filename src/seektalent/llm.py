from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai import NativeOutput, PromptedOutput
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, Model, get_user_agent, infer_model
from pydantic_ai.providers import infer_provider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from seektalent.config import AppSettings, load_process_env


def model_provider(model_id: str) -> str:
    return model_id.split(":", 1)[0]


def _normalize_openai_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    normalized = base_url.rstrip("/")
    if normalized.endswith("/responses"):
        normalized = normalized[: -len("/responses")]
    return normalized


def _fresh_openai_provider(
    base_url: str | None = None,
    api_key: str | None = None,
) -> OpenAIProvider:
    return OpenAIProvider(
        base_url=_normalize_openai_base_url(base_url),
        api_key=api_key,
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=DEFAULT_HTTP_TIMEOUT, connect=5),
            headers={"User-Agent": get_user_agent()},
        )
    )


def build_model(
    model_id: str,
    *,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
) -> Model:
    load_process_env()

    def provider_factory(provider_name: str):
        if provider_name in {"openai", "openai-chat", "openai-responses"}:
            return _fresh_openai_provider(openai_base_url, openai_api_key)
        return infer_provider(provider_name)

    return infer_model(model_id, provider_factory=provider_factory)


def ensure_native_structured_output(model_id: str, model: Model) -> None:
    profile = model.profile
    if getattr(profile, "supports_json_schema_output", False):
        return
    raise ValueError(f"{model_id} does not support native structured output via JSON Schema.")


def build_output_spec(model_id: str, model: Model, output_type: Any) -> Any:
    if model_id.startswith("openai-chat:"):
        return PromptedOutput(output_type)
    ensure_native_structured_output(model_id, model)
    return NativeOutput(output_type, strict=True)


def build_model_settings(
    settings: AppSettings,
    model_id: str,
    *,
    reasoning_effort: str | None = None,
) -> ModelSettings:
    effective_effort = reasoning_effort or settings.reasoning_effort
    thinking = False if effective_effort == "off" else effective_effort
    model_settings: ModelSettings = {"thinking": thinking}
    if not model_id.startswith("openai-responses:"):
        return model_settings

    openai_settings: ModelSettings = {
        "thinking": thinking,
        "openai_text_verbosity": "low",
    }
    if thinking is not False:
        openai_settings["openai_reasoning_summary"] = "concise"
    return openai_settings


def preflight_models(settings: AppSettings) -> None:
    seen: set[tuple[str, str | None, str | None]] = set()
    model_specs = [
        (settings.requirements_model, None, None),
        (settings.controller_model, None, None),
        (settings.scoring_model, None, None),
        (settings.reflection_model, None, None),
        (settings.finalize_model, None, None),
    ]
    if settings.enable_eval:
        model_specs.append(
            (
                settings.effective_judge_model,
                settings.judge_openai_base_url,
                settings.judge_openai_api_key,
            )
        )
    for model_id, openai_base_url, openai_api_key in model_specs:
        key = (model_id, _normalize_openai_base_url(openai_base_url), openai_api_key)
        if key in seen:
            continue
        model = build_model(
            model_id,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
        )
        if not model_id.startswith("openai-chat:"):
            ensure_native_structured_output(model_id, model)
        seen.add(key)
