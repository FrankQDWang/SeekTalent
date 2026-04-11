from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic_ai import NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


LLMProvider = Literal["openai", "dashscope", "moonshot", "glm"]
RequestedOutputMode = Literal["auto", "native", "tool", "prompted"]
ResolvedOutputMode = Literal["native", "tool", "prompted"]
LLMCallpoint = Literal[
    "requirement_extraction",
    "bootstrap_keyword_generation",
    "search_controller_decision",
    "branch_outcome_evaluation",
    "search_run_finalization",
]

DEFAULT_PROVIDER: LLMProvider = "openai"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_OUTPUT_MODE: RequestedOutputMode = "auto"
SUPPORTED_PROVIDERS: tuple[LLMProvider, ...] = ("openai", "dashscope", "moonshot", "glm")
SUPPORTED_REQUESTED_OUTPUT_MODES: tuple[RequestedOutputMode, ...] = (
    "auto",
    "native",
    "tool",
    "prompted",
)
CALLPOINT_ENV_PREFIXES: dict[LLMCallpoint, str] = {
    "requirement_extraction": "SEEKTALENT_REQUIREMENT_EXTRACTION",
    "bootstrap_keyword_generation": "SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION",
    "search_controller_decision": "SEEKTALENT_SEARCH_CONTROLLER_DECISION",
    "branch_outcome_evaluation": "SEEKTALENT_BRANCH_OUTCOME_EVALUATION",
    "search_run_finalization": "SEEKTALENT_SEARCH_RUN_FINALIZATION",
}
CALLPOINT_ALLOWED_OUTPUT_MODES: dict[LLMCallpoint, tuple[ResolvedOutputMode, ...]] = {
    "requirement_extraction": ("native", "tool", "prompted"),
    "bootstrap_keyword_generation": ("native", "tool", "prompted"),
    "search_controller_decision": ("native", "tool"),
    "branch_outcome_evaluation": ("native", "tool", "prompted"),
    "search_run_finalization": ("native", "tool", "prompted"),
}
PROVIDER_OUTPUT_ORDER: dict[LLMProvider, tuple[ResolvedOutputMode, ...]] = {
    "openai": ("native", "tool", "prompted"),
    "dashscope": ("native", "tool", "prompted"),
    "moonshot": ("native", "tool", "prompted"),
    "glm": ("native", "tool", "prompted"),
}


@dataclass(frozen=True)
class ResolvedLLMConfig:
    provider: LLMProvider
    model: str
    base_url: str | None
    api_key: str | None
    requested_output_mode: RequestedOutputMode
    resolved_output_mode: ResolvedOutputMode


@dataclass(frozen=True)
class LLMCallpointStatus:
    provider: str | None
    model: str | None
    base_url_configured: bool
    requested_output_mode: str | None
    resolved_output_mode: str | None


@dataclass(frozen=True)
class LLMBinding:
    model: Any
    output_type: Any
    audit_output_mode: str
    audit_model_name: str


def build_llm_binding(
    output_type: Any,
    *,
    callpoint: LLMCallpoint,
    model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> LLMBinding:
    if model is not None:
        return LLMBinding(
            model=model,
            output_type=NativeOutput(output_type, strict=True),
            audit_output_mode="NativeOutput(strict=True)",
            audit_model_name=_model_name(model),
        )
    config = resolve_llm_config(callpoint, env_file=env_file)
    return LLMBinding(
        model=_build_openai_compatible_model(config),
        output_type=_build_output_type(output_type, config.resolved_output_mode),
        audit_output_mode=_output_mode_label(config.resolved_output_mode),
        audit_model_name=f"{config.provider}:{config.model}",
    )


def inspect_llm_callpoints(
    env_file: str | Path | None = ".env",
) -> dict[LLMCallpoint, LLMCallpointStatus]:
    values = _read_env_values(env_file)
    return {
        callpoint: _inspect_callpoint(callpoint, values)
        for callpoint in CALLPOINT_ENV_PREFIXES
    }


def resolve_llm_config(
    callpoint: LLMCallpoint,
    *,
    env_file: str | Path | None = ".env",
) -> ResolvedLLMConfig:
    return _resolve_llm_config(callpoint, _read_env_values(env_file))


def _resolve_llm_config(
    callpoint: LLMCallpoint,
    values: dict[str, str],
) -> ResolvedLLMConfig:
    prefix = CALLPOINT_ENV_PREFIXES[callpoint]
    raw_provider = _value(values, f"{prefix}_PROVIDER") or DEFAULT_PROVIDER
    if raw_provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"{prefix}_PROVIDER must be one of {', '.join(SUPPORTED_PROVIDERS)}."
        )
    provider: LLMProvider = raw_provider
    model = _value(values, f"{prefix}_MODEL") or DEFAULT_MODEL
    requested_mode = _value(values, f"{prefix}_OUTPUT_MODE") or DEFAULT_OUTPUT_MODE
    if requested_mode not in SUPPORTED_REQUESTED_OUTPUT_MODES:
        raise ValueError(
            f"{prefix}_OUTPUT_MODE must be one of {', '.join(SUPPORTED_REQUESTED_OUTPUT_MODES)}."
        )
    allowed_modes = CALLPOINT_ALLOWED_OUTPUT_MODES[callpoint]
    if requested_mode != "auto" and requested_mode not in allowed_modes:
        raise ValueError(
            f"{prefix}_OUTPUT_MODE={requested_mode} is not allowed for {callpoint}."
        )
    resolved_mode = _resolve_output_mode(
        callpoint,
        provider,
        requested_mode,  # type: ignore[arg-type]
    )
    base_url = _value(values, f"{prefix}_BASE_URL")
    api_key = _value(values, f"{prefix}_API_KEY")
    if provider == "openai":
        base_url = base_url or _value(values, "OPENAI_BASE_URL")
        api_key = api_key or _value(values, "OPENAI_API_KEY")
    if provider != "openai" and base_url is None:
        raise ValueError(f"{prefix}_BASE_URL is required for provider={provider}.")
    if api_key is None:
        key_field = (
            f"{prefix}_API_KEY or OPENAI_API_KEY"
            if provider == "openai"
            else f"{prefix}_API_KEY"
        )
        raise ValueError(f"{key_field} is required for provider={provider}.")
    return ResolvedLLMConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        requested_output_mode=requested_mode,  # type: ignore[arg-type]
        resolved_output_mode=resolved_mode,
    )


def _inspect_callpoint(
    callpoint: LLMCallpoint,
    values: dict[str, str],
) -> LLMCallpointStatus:
    prefix = CALLPOINT_ENV_PREFIXES[callpoint]
    raw_provider = _value(values, f"{prefix}_PROVIDER") or DEFAULT_PROVIDER
    raw_model = _value(values, f"{prefix}_MODEL") or DEFAULT_MODEL
    requested_mode = _value(values, f"{prefix}_OUTPUT_MODE") or DEFAULT_OUTPUT_MODE
    base_url = _value(values, f"{prefix}_BASE_URL")
    if raw_provider == "openai":
        base_url = base_url or _value(values, "OPENAI_BASE_URL")
    resolved_mode: str | None = None
    if raw_provider in SUPPORTED_PROVIDERS and requested_mode in SUPPORTED_REQUESTED_OUTPUT_MODES:
        allowed_modes = CALLPOINT_ALLOWED_OUTPUT_MODES[callpoint]
        if requested_mode == "auto":
            resolved_mode = _resolve_output_mode(
                callpoint,
                raw_provider,  # type: ignore[arg-type]
                "auto",
            )
        elif requested_mode in allowed_modes:
            resolved_mode = requested_mode
    return LLMCallpointStatus(
        provider=raw_provider,
        model=raw_model,
        base_url_configured=base_url is not None,
        requested_output_mode=requested_mode,
        resolved_output_mode=resolved_mode,
    )


def _resolve_output_mode(
    callpoint: LLMCallpoint,
    provider: LLMProvider,
    requested_mode: RequestedOutputMode,
) -> ResolvedOutputMode:
    if requested_mode != "auto":
        return requested_mode
    allowed_modes = set(CALLPOINT_ALLOWED_OUTPUT_MODES[callpoint])
    for mode in PROVIDER_OUTPUT_ORDER[provider]:
        if mode in allowed_modes:
            return mode
    raise ValueError(f"no_supported_output_mode_for_{callpoint}")


def _build_openai_compatible_model(config: ResolvedLLMConfig) -> OpenAIChatModel:
    provider = OpenAIProvider(
        base_url=config.base_url,
        api_key=config.api_key,
    )
    return OpenAIChatModel(config.model, provider=provider)


def _build_output_type(output_type: Any, mode: ResolvedOutputMode) -> Any:
    if mode == "native":
        return NativeOutput(output_type, strict=True)
    if mode == "tool":
        return ToolOutput(output_type, strict=True)
    return PromptedOutput(output_type)


def _output_mode_label(mode: ResolvedOutputMode) -> str:
    if mode == "native":
        return "NativeOutput(strict=True)"
    if mode == "tool":
        return "ToolOutput(strict=True)"
    return "PromptedOutput"


def _read_env_values(env_file: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file is not None:
        path = Path(env_file)
        if path.exists():
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                values[key] = _strip_env_value(value)
    for key, value in os.environ.items():
        values[key] = value
    return values


def _strip_env_value(value: str) -> str:
    clean = value.strip()
    if len(clean) >= 2 and clean[0] == clean[-1] and clean[0] in {"'", '"'}:
        return clean[1:-1]
    return clean


def _value(values: dict[str, str], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    clean = value.strip()
    return clean or None


def _model_name(model: Any | None) -> str:
    if model is None:
        return "default"
    for attr in ("model_name", "name"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return type(model).__name__


__all__ = [
    "CALLPOINT_ENV_PREFIXES",
    "LLMCallpoint",
    "LLMCallpointStatus",
    "ResolvedLLMConfig",
    "build_llm_binding",
    "inspect_llm_callpoints",
    "resolve_llm_config",
]
