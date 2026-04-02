from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai import NativeOutput
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, Model, get_user_agent, infer_model
from pydantic_ai.providers import infer_provider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from cv_match.config import AppSettings, load_process_env


def model_provider(model_id: str) -> str:
    return model_id.split(":", 1)[0]


def _fresh_openai_provider() -> OpenAIProvider:
    return OpenAIProvider(
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=DEFAULT_HTTP_TIMEOUT, connect=5),
            headers={"User-Agent": get_user_agent()},
        )
    )


def build_model(model_id: str) -> Model:
    load_process_env()

    def provider_factory(provider_name: str):
        if provider_name in {"openai", "openai-chat", "openai-responses"}:
            return _fresh_openai_provider()
        return infer_provider(provider_name)

    return infer_model(model_id, provider_factory=provider_factory)


def ensure_native_structured_output(model_id: str, model: Model) -> None:
    profile = model.profile
    if getattr(profile, "supports_json_schema_output", False):
        return
    raise ValueError(f"{model_id} does not support native structured output via JSON Schema.")


def build_output_spec(model_id: str, model: Model, output_type: Any) -> Any:
    ensure_native_structured_output(model_id, model)
    return NativeOutput(output_type, strict=True)


def build_model_settings(settings: AppSettings, model_id: str) -> ModelSettings:
    model_settings: ModelSettings = {"thinking": settings.reasoning_effort}
    if not model_id.startswith("openai-responses:"):
        return model_settings

    return {
        "thinking": settings.reasoning_effort,
        "openai_reasoning_summary": "concise",
        "openai_text_verbosity": "low",
    }


def preflight_models(settings: AppSettings) -> None:
    seen: set[str] = set()
    for model_id in (
        settings.requirements_model,
        settings.controller_model,
        settings.scoring_model,
        settings.reflection_model,
        settings.finalize_model,
    ):
        if model_id in seen:
            continue
        model = build_model(model_id)
        ensure_native_structured_output(model_id, model)
        seen.add(model_id)
