from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from seektalent.resources import DEFAULT_CTS_SPEC_NAME, package_prompt_dir, package_spec_file, resolve_user_path


ReasoningEffort = Literal["off", "low", "medium", "high"]
RuntimeMode = Literal["dev", "prod"]
DEV_RUNS_DIR = "runs"
DEV_LLM_CACHE_DIR = ".seektalent/cache"
PROD_RUNS_DIR = "~/.seektalent/runs"
PROD_LLM_CACHE_DIR = "~/.seektalent/cache"
MODEL_FIELDS = (
    "requirements_model",
    "controller_model",
    "scoring_model",
    "finalize_model",
    "reflection_model",
    "structured_repair_model",
    "judge_model",
    "tui_summary_model",
    "candidate_feedback_model",
    "company_discovery_model",
)
PROVIDER_ENV_VARS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
}


def load_process_env(env_file: str | Path = ".env") -> None:
    path = Path(env_file)
    if not path.exists():
        return
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
        if not key or key not in PROVIDER_ENV_VARS or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _is_qualified_model_id(model_id: str) -> bool:
    if ":" not in model_id:
        return False
    provider, name = model_id.split(":", 1)
    return bool(provider and name)


def _packaged_runtime_forces_prod() -> bool:
    return os.environ.get("SEEKTALENT_PACKAGED") == "1" or bool(getattr(sys, "frozen", False))


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEEKTALENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cts_base_url: str = "https://link.hewa.cn"
    cts_tenant_key: str | None = None
    cts_tenant_secret: str | None = None
    cts_timeout_seconds: float = 20.0
    cts_spec_path: str = DEFAULT_CTS_SPEC_NAME

    requirements_model: str = "openai-responses:gpt-5.4-mini"
    controller_model: str = "openai-responses:gpt-5.4-mini"
    scoring_model: str = "openai-responses:gpt-5.4-mini"
    finalize_model: str = "openai-responses:gpt-5.4-mini"
    reflection_model: str = "openai-responses:gpt-5.4"
    requirements_enable_thinking: bool = True
    structured_repair_model: str = "openai-chat:qwen3.5-flash"
    structured_repair_reasoning_effort: ReasoningEffort = "off"
    judge_model: str | None = None
    tui_summary_model: str | None = None
    judge_openai_base_url: str | None = None
    judge_openai_api_key: str | None = None
    reasoning_effort: ReasoningEffort = "medium"
    judge_reasoning_effort: ReasoningEffort | None = None
    controller_enable_thinking: bool = True
    reflection_enable_thinking: bool = True
    candidate_feedback_enabled: bool = True
    candidate_feedback_model: str = "openai-chat:qwen3.5-flash"
    candidate_feedback_reasoning_effort: ReasoningEffort = "off"
    target_company_enabled: bool = False
    company_discovery_enabled: bool = True
    company_discovery_provider: str = "bocha"
    bocha_api_key: str | None = None
    company_discovery_model: str = "openai-chat:qwen3.5-flash"
    company_discovery_reasoning_effort: ReasoningEffort = "off"
    company_discovery_max_search_calls: int = 4
    company_discovery_max_results_per_query: int = 30
    company_discovery_max_open_pages: int = 8
    company_discovery_timeout_seconds: int = 25
    company_discovery_accepted_company_limit: int = 8
    company_discovery_min_confidence: float = 0.65

    min_rounds: int = 3
    max_rounds: int = 10
    scoring_max_concurrency: int = 10
    judge_max_concurrency: int = 5
    search_max_pages_per_round: int = 3
    search_max_attempts_per_round: int = 3
    search_no_progress_limit: int = 2
    runtime_mode: RuntimeMode = "dev"
    llm_cache_dir: str | None = None
    openai_prompt_cache_enabled: bool = False
    openai_prompt_cache_retention: str | None = None
    mock_cts: bool = False
    enable_eval: bool = False
    enable_reflection: bool = True
    wandb_entity: str | None = None
    wandb_project: str | None = None
    weave_entity: str | None = None
    weave_project: str | None = None

    runs_dir: str | None = None

    @field_validator(*MODEL_FIELDS)
    @classmethod
    def validate_model_id(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            if info.field_name in {"judge_model", "tui_summary_model"}:
                return value
            raise ValueError(f"{info.field_name} must use the provider:model format, got {value!r}.")
        if _is_qualified_model_id(value):
            return value
        raise ValueError(
            f"{info.field_name} must use the provider:model format, got {value!r}."
        )

    @model_validator(mode="after")
    def resolve_runtime_defaults(self) -> "AppSettings":
        if _packaged_runtime_forces_prod():
            self.runtime_mode = "prod"
        if self.runs_dir is None:
            self.runs_dir = PROD_RUNS_DIR if self.runtime_mode == "prod" else DEV_RUNS_DIR
        if self.llm_cache_dir is None:
            self.llm_cache_dir = PROD_LLM_CACHE_DIR if self.runtime_mode == "prod" else DEV_LLM_CACHE_DIR
        return self

    @model_validator(mode="after")
    def validate_ranges(self) -> "AppSettings":
        if self.min_rounds < 1:
            raise ValueError("min_rounds must be >= 1")
        if self.max_rounds < self.min_rounds:
            raise ValueError("max_rounds must be >= min_rounds")
        if self.max_rounds > 10:
            raise ValueError("max_rounds must be <= 10")
        if self.scoring_max_concurrency < 1:
            raise ValueError("scoring_max_concurrency must be >= 1")
        if self.judge_max_concurrency < 1:
            raise ValueError("judge_max_concurrency must be >= 1")
        if self.search_max_pages_per_round < 1:
            raise ValueError("search_max_pages_per_round must be >= 1")
        if self.search_max_attempts_per_round < 1:
            raise ValueError("search_max_attempts_per_round must be >= 1")
        if self.search_no_progress_limit < 1:
            raise ValueError("search_no_progress_limit must be >= 1")
        if self.company_discovery_provider != "bocha":
            raise ValueError("company_discovery_provider must be 'bocha'")
        if self.company_discovery_max_search_calls < 1:
            raise ValueError("company_discovery_max_search_calls must be >= 1")
        if self.company_discovery_max_results_per_query < 1:
            raise ValueError("company_discovery_max_results_per_query must be >= 1")
        if self.company_discovery_max_open_pages < 0:
            raise ValueError("company_discovery_max_open_pages must be >= 0")
        if self.company_discovery_timeout_seconds < 1:
            raise ValueError("company_discovery_timeout_seconds must be >= 1")
        if self.company_discovery_accepted_company_limit < 1:
            raise ValueError("company_discovery_accepted_company_limit must be >= 1")
        if not 0 <= self.company_discovery_min_confidence <= 1:
            raise ValueError("company_discovery_min_confidence must be between 0 and 1")
        return self

    @property
    def project_root(self) -> Path:
        return Path.cwd()

    @property
    def prompt_dir(self) -> Path:
        return package_prompt_dir()

    @property
    def spec_file(self) -> Path:
        if self.cts_spec_path == DEFAULT_CTS_SPEC_NAME:
            return package_spec_file()
        return resolve_user_path(self.cts_spec_path)

    @property
    def runs_path(self) -> Path:
        if self.runs_dir is None:
            raise ValueError("runs_dir was not resolved")
        return resolve_user_path(self.runs_dir)

    @property
    def effective_judge_model(self) -> str:
        return self.judge_model or self.scoring_model

    @property
    def effective_tui_summary_model(self) -> str:
        return self.tui_summary_model or self.scoring_model

    @property
    def effective_judge_reasoning_effort(self) -> ReasoningEffort:
        return self.judge_reasoning_effort or self.reasoning_effort

    @property
    def effective_weave_entity(self) -> str | None:
        return self.weave_entity or self.wandb_entity

    def require_cts_credentials(self) -> None:
        if not self.cts_tenant_key or not self.cts_tenant_secret:
            raise ValueError(
                "Real CTS mode requires SEEKTALENT_CTS_TENANT_KEY and SEEKTALENT_CTS_TENANT_SECRET."
            )

    def with_overrides(self, **overrides: object) -> "AppSettings":
        filtered = {key: value for key, value in overrides.items() if value is not None}
        data = self.model_dump()
        if "runtime_mode" in filtered:
            if "runs_dir" not in filtered:
                data.pop("runs_dir", None)
            if "llm_cache_dir" not in filtered:
                data.pop("llm_cache_dir", None)
        return type(self).model_validate({**data, **filtered})
