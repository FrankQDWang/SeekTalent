from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

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
ReasoningEffortName = ReasoningEffort
RuntimeMode = Literal["dev", "prod"]
TextLLMProtocolFamily = Literal[
    "openai_chat_completions_compatible",
    "anthropic_messages_compatible",
]
TextLLMProviderLabel = Literal["bailian"]
TextLLMEndpointKind = Literal[
    "bailian_openai_chat_completions",
    "bailian_anthropic_messages",
]
TextLLMEndpointRegion = Literal["beijing", "singapore"]
ProviderName = Literal["cts", "liepin"]
LiepinWorkerMode = Literal["disabled", "fake_fixture", "managed_local", "external_http", "pi_agent"]
DEV_ARTIFACTS_DIR = "artifacts"
DEV_RUNS_DIR = "runs"
DEV_LLM_CACHE_DIR = ".seektalent/cache"
PROD_ARTIFACTS_DIR = "~/.seektalent/artifacts"
PROD_RUNS_DIR = "~/.seektalent/runs"
PROD_LLM_CACHE_DIR = "~/.seektalent/cache"
PROVIDER_ENV_VARS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
}
LEGACY_TEXT_LLM_ENV_KEYS = {
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
LEGACY_TEXT_LLM_INIT_KEYS = {
    "requirements_model",
    "controller_model",
    "scoring_model",
    "finalize_model",
    "reflection_model",
    "structured_repair_model",
    "judge_model",
    "tui_summary_model",
    "candidate_feedback_model",
    "judge_openai_base_url",
    "judge_openai_api_key",
}
TEXT_LLM_MODEL_ID_FIELDS = {
    "requirements_model_id",
    "controller_model_id",
    "scoring_model_id",
    "finalize_model_id",
    "reflection_model_id",
    "structured_repair_model_id",
    "judge_model_id",
    "tui_summary_model_id",
    "candidate_feedback_model_id",
    "prf_probe_phrase_proposal_model_id",
    "workbench_note_writer_model_id",
}
LEGACY_TEXT_LLM_PREFIXES = ("openai-chat:", "openai-responses:", "anthropic:")
TEXT_LLM_ENDPOINT_KIND_BY_PROTOCOL_FAMILY = {
    "openai_chat_completions_compatible": "bailian_openai_chat_completions",
    "anthropic_messages_compatible": "bailian_anthropic_messages",
}
ENV_FILE_SENTINEL = object()
REMOVED_PRF_ENV_KEYS = {
    "SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND",
    "SEEKTALENT_PRF_V1_5_MODE",
    "SEEKTALENT_PRF_MODEL_BACKEND",
    "SEEKTALENT_PRF_SPAN_MODEL_NAME",
    "SEEKTALENT_PRF_SPAN_MODEL_REVISION",
    "SEEKTALENT_PRF_SPAN_TOKENIZER_REVISION",
    "SEEKTALENT_PRF_SPAN_SCHEMA_VERSION",
    "SEEKTALENT_PRF_EMBEDDING_MODEL_NAME",
    "SEEKTALENT_PRF_EMBEDDING_MODEL_REVISION",
    "SEEKTALENT_PRF_ALLOW_REMOTE_CODE",
    "SEEKTALENT_PRF_REQUIRE_PINNED_MODELS_FOR_MAINLINE",
    "SEEKTALENT_PRF_REMOTE_CODE_AUDIT_REVISION",
    "SEEKTALENT_PRF_FAMILYING_EMBEDDING_THRESHOLD",
    "SEEKTALENT_PRF_SIDECAR_PROFILE",
    "SEEKTALENT_PRF_SIDECAR_BIND_HOST",
    "SEEKTALENT_PRF_SIDECAR_ENDPOINT",
    "SEEKTALENT_PRF_SIDECAR_ENDPOINT_CONTRACT_VERSION",
    "SEEKTALENT_PRF_SIDECAR_SERVE_MODE",
    "SEEKTALENT_PRF_SIDECAR_TIMEOUT_SECONDS_SHADOW",
    "SEEKTALENT_PRF_SIDECAR_TIMEOUT_SECONDS_MAINLINE",
    "SEEKTALENT_PRF_SIDECAR_MAX_BATCH_SIZE",
    "SEEKTALENT_PRF_SIDECAR_MAX_PAYLOAD_BYTES",
    "SEEKTALENT_PRF_SIDECAR_BAKEOFF_PROMOTED",
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


class TextLLMConfigMigrationError(ValueError):
    """Raised when removed text-llm config surfaces are still present."""


class PRFConfigMigrationError(ValueError):
    """Raised when removed PRF config surfaces are still present."""


@dataclass(frozen=True)
class LocalDataRootPosture:
    status: Literal["safe", "risky", "unknown"]
    reason_code: str
    path: Path


@dataclass(frozen=True)
class LocalDataRootPolicy:
    status: Literal["safe", "warning", "error", "unknown"]
    reason_code: str
    posture: LocalDataRootPosture


def classify_local_data_root(path: Path) -> LocalDataRootPosture:
    resolved = path.expanduser().resolve(strict=False)
    if (resolved / "pyproject.toml").exists() or (resolved / ".git").exists():
        return LocalDataRootPosture(status="risky", reason_code="repo_root", path=resolved)
    for parent in resolved.parents:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return LocalDataRootPosture(status="risky", reason_code="inside_repo", path=resolved)
    normalized_parts = tuple(part.lower() for part in resolved.parts)
    if any(_is_known_sync_folder_part(part) for part in normalized_parts):
        return LocalDataRootPosture(status="risky", reason_code="sync_folder", path=resolved)
    user_data_root = (Path.home() / ".seektalent").resolve(strict=False)
    if resolved == user_data_root or user_data_root in resolved.parents:
        return LocalDataRootPosture(status="safe", reason_code="user_data_root", path=resolved)
    return LocalDataRootPosture(status="unknown", reason_code="custom_path", path=resolved)


def _is_known_sync_folder_part(part: str) -> bool:
    exact_markers = {
        "icloud drive",
        "mobile documents",
        "dropbox",
        "google drive",
        "googledrive",
        "my drive",
        "box",
        "sharepoint",
        "synology drive",
        "jianguoyun",
        "nutstore",
    }
    return part in exact_markers or part.startswith("onedrive") or part.startswith("one drive")


def evaluate_local_data_root_policy(
    path: Path,
    *,
    runtime_mode: RuntimeMode,
    packaged: bool = False,
) -> LocalDataRootPolicy:
    posture = classify_local_data_root(path)
    if posture.status == "safe":
        return LocalDataRootPolicy(status="safe", reason_code=posture.reason_code, posture=posture)
    if posture.status == "risky":
        status: Literal["warning", "error"] = "error" if runtime_mode == "prod" or packaged else "warning"
        return LocalDataRootPolicy(status=status, reason_code=posture.reason_code, posture=posture)
    return LocalDataRootPolicy(status="unknown", reason_code=posture.reason_code, posture=posture)


def _read_env_kv_pairs(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    data: dict[str, str] = {}
    if not env_path.exists():
        return data
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip("'").strip('"')
    return data


def _is_provider_prefixed_model_id(model_id: str) -> bool:
    return model_id.startswith(LEGACY_TEXT_LLM_PREFIXES)


def _legacy_text_llm_error(reasons: list[str]) -> TextLLMConfigMigrationError:
    detail = "; ".join(dict.fromkeys(reasons))
    return TextLLMConfigMigrationError(
        "legacy text-llm config detected: "
        f"{detail}. Replace removed provider-prefixed stage settings with "
        "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY, SEEKTALENT_TEXT_LLM_ENDPOINT_KIND, "
        "SEEKTALENT_TEXT_LLM_ENDPOINT_REGION, and bare *_MODEL_ID values."
    )


def _scan_legacy_text_llm_inputs(
    *,
    env_file: str | Path | None,
    init_data: Mapping[str, object],
    include_default_env_file: bool,
) -> None:
    reasons: list[str] = []
    sources: list[Mapping[str, str]] = [dict(os.environ)]
    if include_default_env_file:
        sources.append(_read_env_kv_pairs(".env"))
    if env_file is not None:
        sources.append(_read_env_kv_pairs(env_file))
    init_values = {
        str(key): str(value)
        for key, value in init_data.items()
        if value is not None and not str(key).startswith("_")
    }
    sources.append(init_values)
    for source in sources:
        for key in sorted(LEGACY_TEXT_LLM_ENV_KEYS):
            if key in source:
                reasons.append(f"deprecated key {key}")
        for key in sorted(LEGACY_TEXT_LLM_INIT_KEYS):
            if key in source:
                reasons.append(f"deprecated init key {key}")
        for key, value in source.items():
            if key in TEXT_LLM_MODEL_ID_FIELDS or key.endswith("_MODEL_ID"):
                if _is_provider_prefixed_model_id(value):
                    reasons.append(f"provider-prefixed model string {value!r} on {key}")
    if reasons:
        raise _legacy_text_llm_error(reasons)


def _env_key_for_init_key(key: str) -> str:
    if key.startswith("SEEKTALENT_"):
        return key
    return f"SEEKTALENT_{key.upper()}"


def _scan_removed_prf_inputs(
    *,
    env_file: str | Path | None,
    init_data: Mapping[str, object],
    include_default_env_file: bool,
) -> None:
    sources: list[Mapping[str, str]] = [dict(os.environ)]
    if include_default_env_file:
        sources.append(_read_env_kv_pairs(".env"))
    if env_file is not None:
        sources.append(_read_env_kv_pairs(env_file))
    sources.append(
        {
            _env_key_for_init_key(str(key)): str(value)
            for key, value in init_data.items()
            if value is not None and not str(key).startswith("_")
        }
    )

    removed_keys = [
        key
        for source in sources
        for key in sorted(REMOVED_PRF_ENV_KEYS)
        if key in source
    ]
    if removed_keys:
        detail = ", ".join(dict.fromkeys(removed_keys))
        raise PRFConfigMigrationError(
            "removed PRF config detected: "
            f"{detail}. Remove stale sidecar/span PRF settings; active PRF proposal "
            "configuration is SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_*."
        )


def _packaged_runtime_forces_prod() -> bool:
    return os.environ.get("SEEKTALENT_PACKAGED") == "1" or bool(getattr(sys, "frozen", False))


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEEKTALENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __init__(self, **data: Any) -> None:
        env_file = data.get("_env_file", ENV_FILE_SENTINEL)
        scan_env_file = None if env_file is ENV_FILE_SENTINEL else env_file
        _scan_legacy_text_llm_inputs(
            env_file=scan_env_file,
            init_data=data,
            include_default_env_file=env_file is ENV_FILE_SENTINEL,
        )
        _scan_removed_prf_inputs(
            env_file=scan_env_file,
            init_data=data,
            include_default_env_file=env_file is ENV_FILE_SENTINEL,
        )
        super().__init__(**data)

    cts_base_url: str = "https://link.hewa.cn"
    cts_tenant_key: str | None = None
    cts_tenant_secret: str | None = None
    cts_timeout_seconds: float = 20.0
    cts_spec_path: str = DEFAULT_CTS_SPEC_NAME

    provider_name: ProviderName = "cts"
    liepin_worker_mode: LiepinWorkerMode = "disabled"
    liepin_allow_fake_fixture_worker: bool = False
    liepin_worker_base_url: str | None = None
    liepin_pi_command: str = "pi --mode rpc --no-session"
    liepin_pi_timeout_seconds: int = 120
    liepin_pi_skill_path: str = "src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md"
    liepin_pi_dokobot_tool_name: str = "dokobot"
    liepin_worker_host: str = "127.0.0.1"
    liepin_worker_port: int = 0
    liepin_worker_startup_timeout_seconds: float = 15.0
    liepin_worker_timeout_seconds: float = 30.0
    liepin_connector_db_path: str = ".seektalent/liepin_connector.sqlite3"
    liepin_session_store_dir: str = ".seektalent/liepin_sessions"
    liepin_session_store_key_id: str = "local-development"
    liepin_api_token: str = "local-development-liepin-api-token"
    liepin_account_binding_secret: str | None = "local-development"
    liepin_stream_token_secret: str | None = "local-development"
    liepin_detail_open_approval_secret: str | None = None
    liepin_default_daily_detail_budget: int = 20
    liepin_live_enabled: bool = False

    text_llm_protocol_family: TextLLMProtocolFamily = "openai_chat_completions_compatible"
    text_llm_provider_label: TextLLMProviderLabel = "bailian"
    text_llm_endpoint_kind: TextLLMEndpointKind = "bailian_openai_chat_completions"
    text_llm_endpoint_region: TextLLMEndpointRegion = "beijing"
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
    judge_reasoning_effort: ReasoningEffort | None = "high"
    controller_enable_thinking: bool = True
    reflection_enable_thinking: bool = True
    candidate_feedback_enabled: bool = True
    candidate_feedback_model_id: str = "deepseek-v4-flash"
    candidate_feedback_reasoning_effort: ReasoningEffort = "off"
    prf_probe_phrase_proposal_model_id: str = "deepseek-v4-flash"
    prf_probe_phrase_proposal_reasoning_effort: ReasoningEffortName = "off"
    workbench_note_writer_model_id: str = "deepseek-v4-flash"
    workbench_note_writer_reasoning_effort: ReasoningEffort = "off"
    prf_probe_phrase_proposal_timeout_seconds: float = 3.0
    prf_probe_phrase_proposal_live_harness_timeout_seconds: float = 30.0
    prf_probe_phrase_proposal_max_output_tokens: int = 2048
    min_rounds: int = 3
    max_rounds: int = 10
    scoring_max_concurrency: int = 10
    judge_max_concurrency: int = 5
    search_max_pages_per_round: int = 3
    search_max_attempts_per_round: int = 3
    search_no_progress_limit: int = 2
    runtime_mode: RuntimeMode = "dev"
    workspace_root: str | None = None
    workbench_enabled: bool = True
    workbench_legacy_liepin_login_relay_enabled: bool = False
    artifacts_dir: str | None = None
    llm_cache_dir: str | None = None
    flywheel_db_path: str = ".seektalent/flywheel.sqlite3"
    corpus_db_path: str = ".seektalent/corpus.sqlite3"
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

    @field_validator(*TEXT_LLM_MODEL_ID_FIELDS)
    @classmethod
    def validate_model_id(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value == "":
            if info.field_name == "tui_summary_model_id":
                return None
            raise ValueError(f"{info.field_name} must not be empty")
        if value is None:
            return value
        if _is_provider_prefixed_model_id(value):
            raise ValueError(f"provider-prefixed model string {value!r} is decommissioned")
        return value

    @field_validator("openai_prompt_cache_retention", mode="before")
    @classmethod
    def normalize_empty_prompt_cache_retention(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value

    @field_validator("workspace_root", "artifacts_dir", "runs_dir", "llm_cache_dir", mode="before")
    @classmethod
    def normalize_empty_local_path_string(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value

    @field_validator(
        "liepin_worker_base_url",
        "liepin_pi_command",
        "liepin_pi_skill_path",
        "liepin_pi_dokobot_tool_name",
        "liepin_account_binding_secret",
        "liepin_stream_token_secret",
        mode="before",
    )
    @classmethod
    def normalize_empty_liepin_optional_string(cls, value: str | None) -> str | None:
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
        if self.prf_probe_phrase_proposal_timeout_seconds <= 0:
            raise ValueError("prf_probe_phrase_proposal_timeout_seconds must be > 0")
        if self.prf_probe_phrase_proposal_live_harness_timeout_seconds <= 0:
            raise ValueError("prf_probe_phrase_proposal_live_harness_timeout_seconds must be > 0")
        if self.prf_probe_phrase_proposal_max_output_tokens < 256:
            raise ValueError("prf_probe_phrase_proposal_max_output_tokens must be >= 256")
        if self.liepin_worker_startup_timeout_seconds <= 0:
            raise ValueError("liepin_worker_startup_timeout_seconds must be > 0")
        if self.liepin_worker_timeout_seconds <= 0:
            raise ValueError("liepin_worker_timeout_seconds must be > 0")
        if self.liepin_pi_timeout_seconds <= 0:
            raise ValueError("liepin_pi_timeout_seconds must be > 0")
        if self.liepin_default_daily_detail_budget < 0:
            raise ValueError("liepin_default_daily_detail_budget must be >= 0")
        return self

    @model_validator(mode="after")
    def validate_liepin_worker_config(self) -> "AppSettings":
        if self.liepin_worker_mode == "fake_fixture" and not self.liepin_allow_fake_fixture_worker:
            raise ValueError("liepin_worker_mode=fake_fixture requires liepin_allow_fake_fixture_worker=True")
        if self.liepin_worker_mode == "external_http" and self.liepin_worker_base_url is None:
            raise ValueError("liepin_worker_base_url is required when liepin_worker_mode=external_http")
        if self.liepin_worker_mode == "pi_agent":
            if not self.liepin_account_binding_secret or self.liepin_account_binding_secret == "local-development":
                raise ValueError("liepin_account_binding_secret must be set to a non-placeholder value for pi_agent")
            self.liepin_pi_command_argv
        return self

    @model_validator(mode="after")
    def validate_text_llm_surface(self) -> "AppSettings":
        expected_endpoint_kind = TEXT_LLM_ENDPOINT_KIND_BY_PROTOCOL_FAMILY[self.text_llm_protocol_family]
        if self.text_llm_endpoint_kind != expected_endpoint_kind:
            raise ValueError(
                "text_llm_endpoint_kind must match text_llm_protocol_family "
                f"({self.text_llm_protocol_family} -> {expected_endpoint_kind})"
            )
        return self

    @property
    def project_root(self) -> Path:
        return self.runtime_context.workspace_root

    @property
    def runtime_context(self) -> RuntimeContext:
        return RuntimeContext.from_value(self.workspace_root)

    def resolve_workspace_path(self, value: str) -> Path:
        return resolve_path_from_root(value, root=self.project_root)

    @property
    def liepin_pi_skill_file_path(self) -> Path:
        return self.resolve_workspace_path(self.liepin_pi_skill_path)

    @property
    def liepin_pi_command_argv(self) -> tuple[str, ...]:
        from seektalent.providers.pi_agent.pi_external import build_pi_rpc_argv

        return build_pi_rpc_argv(self.liepin_pi_command, skill_path=self.liepin_pi_skill_file_path)

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
    def flywheel_path(self) -> Path:
        return resolve_path_from_root(self.flywheel_db_path, root=self.project_root)

    @property
    def corpus_path(self) -> Path:
        return self.resolve_workspace_path(self.corpus_db_path)

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
    def effective_judge_model(self) -> str:
        return self.judge_model_id

    @property
    def effective_tui_summary_model(self) -> str:
        return self.tui_summary_model_id or self.scoring_model_id

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
        settings = type(self)(_env_file=None, **{**data, **filtered})
        if reset_artifacts_dir:
            settings.model_fields_set.discard("artifacts_dir")
        if reset_runs_dir:
            settings.model_fields_set.discard("runs_dir")
        if reset_llm_cache_dir:
            settings.model_fields_set.discard("llm_cache_dir")
        return settings
