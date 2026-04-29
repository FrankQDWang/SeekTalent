from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from seektalent.core.runtime_context import RuntimeContext
from seektalent.resources import (
    DEFAULT_CTS_SPEC_NAME,
    package_prompt_dir,
    package_spec_file,
    resolve_path_from_root,
    resolve_user_path,
)


ReasoningEffort = Literal["off", "low", "medium", "high"]
RuntimeMode = Literal["dev", "prod"]
DEV_ARTIFACTS_DIR = "artifacts"
DEV_RUNS_DIR = "runs"
DEV_LLM_CACHE_DIR = ".seektalent/cache"
PROD_ARTIFACTS_DIR = "~/.seektalent/artifacts"
PROD_RUNS_DIR = "~/.seektalent/runs"
PROD_LLM_CACHE_DIR = "~/.seektalent/cache"
TEXT_LLM_MODEL_ID_FIELDS = (
    "requirements_model_id",
    "controller_model_id",
    "scoring_model_id",
    "finalize_model_id",
    "reflection_model_id",
    "structured_repair_model_id",
    "judge_model_id",
    "tui_summary_model_id",
    "candidate_feedback_model_id",
)
OPTIONAL_TEXT_LLM_MODEL_ID_FIELDS = {"tui_summary_model_id"}
LEGACY_TEXT_LLM_ENV_VARS = {
    "SEEKTALENT_REQUIREMENTS_MODEL",
    "SEEKTALENT_CONTROLLER_MODEL",
    "SEEKTALENT_SCORING_MODEL",
    "SEEKTALENT_FINALIZE_MODEL",
    "SEEKTALENT_REFLECTION_MODEL",
    "SEEKTALENT_STRUCTURED_REPAIR_MODEL",
    "SEEKTALENT_JUDGE_MODEL",
    "SEEKTALENT_TUI_SUMMARY_MODEL",
    "SEEKTALENT_CANDIDATE_FEEDBACK_MODEL",
    "SEEKTALENT_JUDGE_OPENAI_BASE_URL",
    "SEEKTALENT_JUDGE_OPENAI_API_KEY",
}
TEXT_LLM_MODEL_PREFIXES = ("openai-chat:", "openai-responses:", "anthropic:")
PROVIDER_ENV_VARS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
}


class TextLLMConfigMigrationError(ValueError):
    pass


def _strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_env_file_entries(env_file: str | Path) -> dict[str, str]:
    path = Path(env_file)
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
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
        entries[key] = _strip_env_value(value)
    return entries


def _legacy_text_llm_env_findings(source: str, entries: dict[str, str]) -> list[str]:
    findings: list[str] = []
    for key in sorted(LEGACY_TEXT_LLM_ENV_VARS):
        if key in entries:
            findings.append(f"{source}:{key}")
    for field_name in TEXT_LLM_MODEL_ID_FIELDS:
        env_key = f"SEEKTALENT_{field_name.upper()}"
        value = entries.get(env_key)
        if value is None:
            continue
        if any(value.startswith(prefix) for prefix in TEXT_LLM_MODEL_PREFIXES):
            findings.append(f"{source}:{env_key}={value}")
    return findings


def _legacy_text_llm_kwargs_findings(kwargs: dict[str, object]) -> list[str]:
    findings: list[str] = []
    for field_name in TEXT_LLM_MODEL_ID_FIELDS:
        value = kwargs.get(field_name)
        if isinstance(value, str) and any(value.startswith(prefix) for prefix in TEXT_LLM_MODEL_PREFIXES):
            findings.append(f"kwargs:{field_name}={value}")
    return findings


def _scan_text_llm_migration_sources(*, kwargs: dict[str, object], env_file: object | None) -> None:
    findings: list[str] = []
    findings.extend(_legacy_text_llm_env_findings("process_env", os.environ))
    if env_file is None:
        env_files: list[Path] = [Path(".env")]
    elif isinstance(env_file, (str, Path)):
        env_files = [Path(env_file), Path(".env")]
    elif isinstance(env_file, tuple):
        env_files = [Path(item) for item in env_file if item is not None]
        if Path(".env") not in env_files:
            env_files.append(Path(".env"))
    elif isinstance(env_file, list):
        env_files = [Path(item) for item in env_file if item is not None]
        if Path(".env") not in env_files:
            env_files.append(Path(".env"))
    else:
        env_files = [Path(str(env_file)), Path(".env")]
    seen_env_files: set[Path] = set()
    for source_file in env_files:
        if source_file in seen_env_files:
            continue
        seen_env_files.add(source_file)
        findings.extend(_legacy_text_llm_env_findings(str(source_file), _read_env_file_entries(source_file)))
    findings.extend(_legacy_text_llm_kwargs_findings(kwargs))
    if findings:
        raise TextLLMConfigMigrationError(
            "Legacy text LLM config detected: " + ", ".join(findings) + "."
        )


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


def _packaged_runtime_forces_prod() -> bool:
    return os.environ.get("SEEKTALENT_PACKAGED") == "1" or bool(getattr(sys, "frozen", False))


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEEKTALENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __init__(self, **data: object) -> None:
        env_file = data.get("_env_file")
        _scan_text_llm_migration_sources(kwargs=data, env_file=env_file)
        super().__init__(**data)

    cts_base_url: str = "https://link.hewa.cn"
    cts_tenant_key: str | None = None
    cts_tenant_secret: str | None = None
    cts_timeout_seconds: float = 20.0
    cts_spec_path: str = DEFAULT_CTS_SPEC_NAME

    text_llm_protocol_family: Literal[
        "openai_chat_completions_compatible",
        "anthropic_messages_compatible",
    ] = "anthropic_messages_compatible"
    text_llm_provider_label: str = "bailian"
    text_llm_endpoint_kind: Literal["bailian_openai_chat_completions", "bailian_anthropic_messages"] = (
        "bailian_anthropic_messages"
    )
    text_llm_endpoint_region: Literal["beijing", "singapore"] = "beijing"
    text_llm_base_url_override: str | None = None
    text_llm_api_key: str | None = None

    requirements_model_id: str = "deepseek-v4-pro"
    controller_model_id: str = "deepseek-v4-pro"
    scoring_model_id: str = "deepseek-v4-flash"
    finalize_model_id: str = "deepseek-v4-flash"
    reflection_model_id: str = "deepseek-v4-pro"
    requirements_enable_thinking: bool = True
    structured_repair_model_id: str = "deepseek-v4-flash"
    structured_repair_reasoning_effort: ReasoningEffort = "off"
    judge_model_id: str = "deepseek-v4-pro"
    tui_summary_model_id: str | None = None
    reasoning_effort: ReasoningEffort = "medium"
    judge_reasoning_effort: ReasoningEffort | None = None
    controller_enable_thinking: bool = True
    reflection_enable_thinking: bool = True
    candidate_feedback_enabled: bool = True
    candidate_feedback_model_id: str = "qwen3.5-flash"
    candidate_feedback_reasoning_effort: ReasoningEffort = "off"
    prf_v1_5_mode: Literal["disabled", "shadow", "mainline"] = "shadow"
    prf_span_model_name: str = "fastino/gliner2-multi-v1"
    prf_span_model_revision: str = ""
    prf_span_tokenizer_revision: str = ""
    prf_span_schema_version: str = "gliner2-schema-v1"
    prf_embedding_model_name: str = "Alibaba-NLP/gte-multilingual-base"
    prf_embedding_model_revision: str = ""
    prf_allow_remote_code: bool = False
    prf_require_pinned_models_for_mainline: bool = True
    prf_remote_code_audit_revision: str | None = None
    prf_familying_embedding_threshold: float = 0.92
    prf_model_backend: Literal["legacy", "http_sidecar"] = "legacy"
    prf_sidecar_profile: Literal["host-local", "docker-internal", "linux-host-network"] = "host-local"
    prf_sidecar_bind_host: str = "127.0.0.1"
    prf_sidecar_endpoint: str = "http://127.0.0.1:8741"
    prf_sidecar_endpoint_contract_version: str = "prf-sidecar-http-v1"
    prf_sidecar_serve_mode: Literal["dev-bootstrap", "prod-serve"] = "dev-bootstrap"
    prf_sidecar_timeout_seconds_shadow: float = 0.35
    prf_sidecar_timeout_seconds_mainline: float = 1.5
    prf_sidecar_max_batch_size: int = 32
    prf_sidecar_max_payload_bytes: int = 262_144
    prf_sidecar_bakeoff_promoted: bool = False
    min_rounds: int = 3
    max_rounds: int = 10
    scoring_max_concurrency: int = 10
    judge_max_concurrency: int = 5
    search_max_pages_per_round: int = 3
    search_max_attempts_per_round: int = 3
    search_no_progress_limit: int = 2
    runtime_mode: RuntimeMode = "dev"
    workspace_root: str | None = None
    artifacts_dir: str | None = None
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

    @field_validator(*TEXT_LLM_MODEL_ID_FIELDS, mode="before")
    @classmethod
    def validate_model_id(cls, value: object, info: ValidationInfo) -> str | None:
        if value == "" and info.field_name in OPTIONAL_TEXT_LLM_MODEL_ID_FIELDS:
            return None
        if value is None:
            if info.field_name in OPTIONAL_TEXT_LLM_MODEL_ID_FIELDS:
                return value
            raise ValueError(f"{info.field_name} must use an unprefixed model id, got {value!r}.")
        if not isinstance(value, str):
            raise ValueError(f"{info.field_name} must be a string, got {type(value).__name__}.")
        if ":" not in value:
            return value
        raise ValueError(f"{info.field_name} must use an unprefixed model id, got {value!r}.")

    @field_validator("openai_prompt_cache_retention", mode="before")
    @classmethod
    def normalize_empty_prompt_cache_retention(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def resolve_runtime_defaults(self) -> "AppSettings":
        provided_fields = set(self.model_fields_set)
        if _packaged_runtime_forces_prod():
            self.runtime_mode = "prod"
        if self.artifacts_dir is None:
            self.artifacts_dir = PROD_ARTIFACTS_DIR if self.runtime_mode == "prod" else DEV_ARTIFACTS_DIR
            if "artifacts_dir" not in provided_fields:
                self.model_fields_set.discard("artifacts_dir")
        if self.runs_dir is None:
            self.runs_dir = PROD_RUNS_DIR if self.runtime_mode == "prod" else DEV_RUNS_DIR
            if "runs_dir" not in provided_fields:
                self.model_fields_set.discard("runs_dir")
        if self.llm_cache_dir is None:
            self.llm_cache_dir = PROD_LLM_CACHE_DIR if self.runtime_mode == "prod" else DEV_LLM_CACHE_DIR
            if "llm_cache_dir" not in provided_fields:
                self.model_fields_set.discard("llm_cache_dir")
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
        if not 0 <= self.prf_familying_embedding_threshold <= 1:
            raise ValueError("prf_familying_embedding_threshold must be between 0 and 1")
        if self.prf_sidecar_timeout_seconds_shadow <= 0:
            raise ValueError("prf_sidecar_timeout_seconds_shadow must be > 0")
        if self.prf_sidecar_timeout_seconds_mainline <= 0:
            raise ValueError("prf_sidecar_timeout_seconds_mainline must be > 0")
        if self.prf_sidecar_max_batch_size < 1:
            raise ValueError("prf_sidecar_max_batch_size must be >= 1")
        if self.prf_sidecar_max_payload_bytes < 1:
            raise ValueError("prf_sidecar_max_payload_bytes must be >= 1")
        return self

    @property
    def project_root(self) -> Path:
        return self.runtime_context.workspace_root

    @property
    def runtime_context(self) -> RuntimeContext:
        return RuntimeContext.from_value(self.workspace_root)

    @property
    def prompt_dir(self) -> Path:
        return package_prompt_dir()

    @property
    def spec_file(self) -> Path:
        if self.cts_spec_path == DEFAULT_CTS_SPEC_NAME:
            return package_spec_file()
        return resolve_user_path(self.cts_spec_path)

    @property
    def llm_cache_path(self) -> Path:
        if self.llm_cache_dir is None:
            raise ValueError("llm_cache_dir was not resolved")
        return resolve_path_from_root(self.llm_cache_dir, root=self.project_root)

    @property
    def artifacts_path(self) -> Path:
        if self.artifacts_dir is None:
            raise ValueError("artifacts_dir was not resolved")
        path = resolve_path_from_root(self.artifacts_dir, root=self.project_root)
        legacy_runs_root = (self.project_root / "runs").resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        if (
            resolved_path == legacy_runs_root
            or legacy_runs_root in resolved_path.parents
            or resolved_path.name == "runs"
            or any(parent.name == "runs" for parent in resolved_path.parents)
        ):
            raise ValueError("The legacy runs/ root is decommissioned as an active output target. Use artifacts/ instead.")
        return path

    @property
    def runs_path(self) -> Path:
        if self.runs_dir is None:
            raise ValueError("runs_dir was not resolved")
        return resolve_path_from_root(self.runs_dir, root=self.project_root)

    @property
    def requirements_model(self) -> str:
        return f"openai-chat:{self.requirements_model_id}"

    @property
    def controller_model(self) -> str:
        return f"openai-chat:{self.controller_model_id}"

    @property
    def scoring_model(self) -> str:
        return f"openai-chat:{self.scoring_model_id}"

    @property
    def finalize_model(self) -> str:
        return f"openai-chat:{self.finalize_model_id}"

    @property
    def reflection_model(self) -> str:
        return f"openai-chat:{self.reflection_model_id}"

    @property
    def structured_repair_model(self) -> str:
        return f"openai-chat:{self.structured_repair_model_id}"

    @property
    def judge_model(self) -> str | None:
        return f"openai-responses:{self.judge_model_id}" if self.judge_model_id is not None else None

    @property
    def tui_summary_model(self) -> str | None:
        return f"openai-responses:{self.tui_summary_model_id}" if self.tui_summary_model_id is not None else None

    @property
    def judge_openai_base_url(self) -> str | None:
        return self.text_llm_base_url_override

    @property
    def judge_openai_api_key(self) -> str | None:
        return self.text_llm_api_key

    @property
    def candidate_feedback_model(self) -> str:
        return f"openai-chat:{self.candidate_feedback_model_id}"

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
        reset_artifacts_dir = "artifacts_dir" not in filtered and "artifacts_dir" not in self.model_fields_set
        reset_runs_dir = "runs_dir" not in filtered and "runs_dir" not in self.model_fields_set
        reset_llm_cache_dir = "llm_cache_dir" not in filtered and "llm_cache_dir" not in self.model_fields_set
        if reset_artifacts_dir:
            data["artifacts_dir"] = None
        if reset_runs_dir:
            data["runs_dir"] = None
        if reset_llm_cache_dir:
            data["llm_cache_dir"] = None
        settings = type(self).model_validate({**data, **filtered})
        if reset_artifacts_dir:
            settings.model_fields_set.discard("artifacts_dir")
        if reset_runs_dir:
            settings.model_fields_set.discard("runs_dir")
        if reset_llm_cache_dir:
            settings.model_fields_set.discard("llm_cache_dir")
        return settings
