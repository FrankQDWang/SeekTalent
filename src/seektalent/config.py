from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from seektalent.resources import DEFAULT_CTS_SPEC_NAME, package_prompt_dir, package_spec_file, resolve_user_path


ReasoningEffort = Literal["off", "low", "medium", "high"]
MODEL_FIELDS = (
    "requirements_model",
    "controller_model",
    "scoring_model",
    "finalize_model",
    "reflection_model",
    "judge_model",
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
    judge_model: str | None = None
    judge_openai_base_url: str | None = None
    judge_openai_api_key: str | None = None
    reasoning_effort: ReasoningEffort = "medium"
    judge_reasoning_effort: ReasoningEffort | None = None

    min_rounds: int = 3
    max_rounds: int = 10
    scoring_max_concurrency: int = 5
    search_max_pages_per_round: int = 3
    search_max_attempts_per_round: int = 3
    search_no_progress_limit: int = 2
    mock_cts: bool = False
    enable_eval: bool = False
    enable_reflection: bool = True
    wandb_entity: str | None = None
    wandb_project: str | None = None
    weave_entity: str | None = None
    weave_project: str | None = None

    runs_dir: str = "runs"

    @field_validator(*MODEL_FIELDS)
    @classmethod
    def validate_model_id(cls, value: str, info: ValidationInfo) -> str:
        if value is None and info.field_name == "judge_model":
            return value
        if _is_qualified_model_id(value):
            return value
        raise ValueError(
            f"{info.field_name} must use the provider:model format, got {value!r}."
        )

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
        if self.search_max_pages_per_round < 1:
            raise ValueError("search_max_pages_per_round must be >= 1")
        if self.search_max_attempts_per_round < 1:
            raise ValueError("search_max_attempts_per_round must be >= 1")
        if self.search_no_progress_limit < 1:
            raise ValueError("search_no_progress_limit must be >= 1")
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
        return resolve_user_path(self.runs_dir)

    @property
    def effective_judge_model(self) -> str:
        return self.judge_model or self.scoring_model

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
        return type(self).model_validate({**self.model_dump(), **filtered})
