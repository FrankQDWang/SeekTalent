from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


ReasoningEffort = Literal["low", "medium", "high"]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CVMATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cts_base_url: str = "https://link.hewa.cn"
    cts_tenant_key: str | None = None
    cts_tenant_secret: str | None = None
    cts_timeout_seconds: float = 20.0
    cts_spec_path: str = "cts.validated.yaml"

    strategy_model: str = "gpt-5.4-mini"
    scoring_model: str = "gpt-5.4-mini"
    finalize_model: str = "gpt-5.4-mini"
    reflection_model: str = "gpt-5.4"
    reasoning_effort: ReasoningEffort = "medium"

    min_rounds: int = 3
    max_rounds: int = 5
    scoring_max_concurrency: int = 5
    search_max_pages_per_round: int = 3
    search_max_attempts_per_round: int = 3
    search_no_progress_limit: int = 2
    mock_cts: bool = True
    enable_reflection: bool = True

    runs_dir: str = "runs"

    @model_validator(mode="after")
    def validate_ranges(self) -> "AppSettings":
        if self.min_rounds < 1:
            raise ValueError("min_rounds must be >= 1")
        if self.max_rounds < self.min_rounds:
            raise ValueError("max_rounds must be >= min_rounds")
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
        return self.project_root / "src" / "cv_match" / "prompts"

    @property
    def spec_file(self) -> Path:
        return self.project_root / self.cts_spec_path

    @property
    def runs_path(self) -> Path:
        return self.project_root / self.runs_dir

    @property
    def openai_api_key_present(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    @property
    def llm_backend_mode(self) -> str:
        if self.openai_api_key_present:
            return "openai-responses"
        return "missing-openai-key"

    def require_openai_api_key(self) -> None:
        if self.openai_api_key_present:
            return
        raise ValueError("OPENAI_API_KEY is required to start a run.")

    def require_cts_credentials(self) -> None:
        if self.mock_cts:
            return
        if not self.cts_tenant_key or not self.cts_tenant_secret:
            raise ValueError(
                "Real CTS mode requires CVMATCH_CTS_TENANT_KEY and CVMATCH_CTS_TENANT_SECRET."
            )

    def with_overrides(self, **overrides: object) -> "AppSettings":
        filtered = {key: value for key, value in overrides.items() if value is not None}
        return self.model_copy(update=filtered)
