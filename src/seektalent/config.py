from __future__ import annotations

import json
import os
import shlex
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
    package_repo_root,
    package_spec_file,
    resolve_path_from_root,
    resolve_user_path,
)
from seektalent_runtime_control.artifact_policy import normalize_artifact_output_mode


ReasoningEffort = Literal["off", "low", "medium", "high", "xhigh", "max"]
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
LiepinWorkerMode = Literal["disabled", "fake_fixture", "managed_local", "external_http", "opencli"]
LiepinBrowserActionBackend = Literal["disabled", "opencli"]
OpenCliWindowMode = Literal["foreground", "background"]
DEV_ARTIFACTS_DIR = "artifacts"
DEV_RUNS_DIR = "runs"
DEV_LLM_CACHE_DIR = ".seektalent/cache"
PROD_ARTIFACTS_DIR = "~/.seektalent/artifacts"
PROD_RUNS_DIR = "~/.seektalent/runs"
PROD_LLM_CACHE_DIR = "~/.seektalent/cache"
DEFAULT_RUNTIME_CONTROL_DB_PATH = ".seektalent/runtime_control.sqlite3"
DEFAULT_CONVERSATION_AGENT_DB_PATH = ".seektalent/conversation_agent.sqlite3"
DEFAULT_AGENT_MEMORY_DB_PATH = ".seektalent/agent_memory.sqlite3"
DEFAULT_AGENT_MEMORY_WORKSPACE_PATH = ".seektalent/agent_memory_workspace"
DEFAULT_LIEPIN_OPENCLI_COMMAND = f"{shlex.quote(sys.executable)} -m seektalent.opencli_launcher"
DEFAULT_LIEPIN_OPENCLI_SESSION = "seektalent-liepin"
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
    "workbench_conversation_model_id",
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


@dataclass(frozen=True)
class RuntimeLimitsSettings:
    min_rounds: int
    max_rounds: int
    scoring_max_concurrency: int
    scoring_timeout_seconds: float
    judge_max_concurrency: int
    search_max_pages_per_round: int
    search_max_attempts_per_round: int
    search_no_progress_limit: int


@dataclass(frozen=True)
class SourceProviderSettings:
    provider_name: ProviderName
    cts_base_url: str
    cts_tenant_key: str | None
    cts_tenant_secret: str | None
    cts_timeout_seconds: float
    cts_spec_path: str
    liepin_worker_mode: LiepinWorkerMode
    liepin_allow_fake_fixture_worker: bool
    liepin_worker_base_url: str | None
    liepin_browser_action_backend: LiepinBrowserActionBackend
    liepin_opencli_command: str
    liepin_opencli_session: str
    liepin_opencli_window_mode: OpenCliWindowMode
    liepin_opencli_allowed_hosts_json: str
    liepin_opencli_allowed_start_urls_json: str
    liepin_opencli_max_actions_per_task: int
    liepin_opencli_max_pages_per_task: int
    liepin_opencli_max_cards_per_task: int
    liepin_opencli_timeout_seconds: int
    liepin_opencli_detail_open_timeout_seconds: int
    liepin_opencli_idle_close_seconds: int
    liepin_opencli_close_blank_window: bool
    liepin_opencli_pacing_enabled: bool
    liepin_opencli_pacing_min_ms: int
    liepin_opencli_pacing_max_ms: int
    liepin_worker_host: str
    liepin_worker_port: int
    liepin_worker_startup_timeout_seconds: float
    liepin_worker_timeout_seconds: float
    liepin_connector_db_path: str
    liepin_session_store_dir: str
    liepin_session_store_key_id: str
    liepin_api_token: str
    liepin_account_binding_secret: str | None
    liepin_stream_token_secret: str | None
    liepin_detail_open_approval_secret: str | None
    liepin_default_daily_detail_budget: int
    liepin_exploit_detail_target: int
    liepin_explore_detail_target: int
    liepin_live_enabled: bool


@dataclass(frozen=True)
class TextLLMSettings:
    protocol_family: TextLLMProtocolFamily
    provider_label: TextLLMProviderLabel
    endpoint_kind: TextLLMEndpointKind
    endpoint_region: TextLLMEndpointRegion
    base_url_override: str | None
    api_key: str | None
    requirements_model_id: str
    controller_model_id: str
    scoring_model_id: str
    finalize_model_id: str
    reflection_model_id: str
    requirements_enable_thinking: bool
    structured_repair_model_id: str
    structured_repair_reasoning_effort: ReasoningEffort
    judge_model_id: str
    tui_summary_model_id: str | None
    reasoning_effort: ReasoningEffort
    judge_reasoning_effort: ReasoningEffort | None
    controller_enable_thinking: bool
    reflection_enable_thinking: bool
    candidate_feedback_enabled: bool
    candidate_feedback_model_id: str
    candidate_feedback_reasoning_effort: ReasoningEffort
    prf_probe_phrase_proposal_model_id: str
    prf_probe_phrase_proposal_reasoning_effort: ReasoningEffortName
    workbench_note_writer_model_id: str
    workbench_note_writer_reasoning_effort: ReasoningEffort
    workbench_conversation_model_id: str
    workbench_conversation_reasoning_effort: ReasoningEffort
    prf_probe_phrase_proposal_timeout_seconds: float
    prf_probe_phrase_proposal_live_harness_timeout_seconds: float
    prf_probe_phrase_proposal_max_output_tokens: int


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


def _json_string_tuple(raw: str, *, field_name: str) -> tuple[str, ...]:
    text = (raw or "").strip()
    if not text:
        return ()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be a JSON array of strings") from exc
    if not isinstance(loaded, list) or not all(isinstance(item, str) and item.strip() for item in loaded):
        raise ValueError(f"{field_name} must be a JSON array of non-empty strings")
    return tuple(item.strip() for item in loaded)


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
        str(key): str(value) for key, value in init_data.items() if value is not None and not str(key).startswith("_")
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

    removed_keys = [key for source in sources for key in sorted(REMOVED_PRF_ENV_KEYS) if key in source]
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
    liepin_worker_mode: LiepinWorkerMode = "opencli"
    liepin_allow_fake_fixture_worker: bool = False
    liepin_worker_base_url: str | None = None
    liepin_browser_action_backend: LiepinBrowserActionBackend = "opencli"
    liepin_opencli_command: str = DEFAULT_LIEPIN_OPENCLI_COMMAND
    liepin_opencli_session: str = DEFAULT_LIEPIN_OPENCLI_SESSION
    liepin_opencli_window_mode: OpenCliWindowMode = "background"
    liepin_opencli_allowed_hosts_json: str = '["www.liepin.com","h.liepin.com","c.liepin.com","lpt.liepin.com"]'
    liepin_opencli_allowed_start_urls_json: str = '["https://h.liepin.com/search/getConditionItem#session"]'
    liepin_opencli_max_actions_per_task: int = 80
    liepin_opencli_max_pages_per_task: int = 1
    liepin_opencli_max_cards_per_task: int = 20
    liepin_opencli_timeout_seconds: int = 900
    liepin_opencli_detail_open_timeout_seconds: int = 90
    liepin_opencli_idle_close_seconds: int = 120
    liepin_opencli_close_blank_window: bool = False
    liepin_opencli_pacing_enabled: bool = True
    liepin_opencli_pacing_min_ms: int = 700
    liepin_opencli_pacing_max_ms: int = 1800
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
    liepin_exploit_detail_target: int = 2
    liepin_explore_detail_target: int = 1
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
    requirements_enable_thinking: bool = False
    structured_repair_model_id: str = "deepseek-v4-flash"
    structured_repair_reasoning_effort: ReasoningEffort = "off"
    judge_model_id: str = "deepseek-v4-pro"
    tui_summary_model_id: str | None = None
    reasoning_effort: ReasoningEffort = "off"
    judge_reasoning_effort: ReasoningEffort | None = "off"
    controller_enable_thinking: bool = False
    controller_timeout_seconds: float = 60.0
    reflection_enable_thinking: bool = False
    candidate_feedback_enabled: bool = True
    candidate_feedback_model_id: str = "deepseek-v4-flash"
    candidate_feedback_reasoning_effort: ReasoningEffort = "off"
    prf_probe_phrase_proposal_model_id: str = "deepseek-v4-flash"
    prf_probe_phrase_proposal_reasoning_effort: ReasoningEffortName = "off"
    workbench_note_writer_model_id: str = "deepseek-v4-flash"
    workbench_note_writer_reasoning_effort: ReasoningEffort = "off"
    workbench_conversation_model_id: str = "deepseek-v4-flash"
    workbench_conversation_reasoning_effort: ReasoningEffort = "max"
    prf_probe_phrase_proposal_timeout_seconds: float = 3.0
    prf_probe_phrase_proposal_live_harness_timeout_seconds: float = 30.0
    prf_probe_phrase_proposal_max_output_tokens: int = 2048
    min_rounds: int = 3
    max_rounds: int = 10
    scoring_max_concurrency: int = 10
    scoring_timeout_seconds: float = 60.0
    judge_max_concurrency: int = 5
    search_max_pages_per_round: int = 3
    search_max_attempts_per_round: int = 3
    search_no_progress_limit: int = 2
    runtime_mode: RuntimeMode = "dev"
    workspace_root: str | None = None
    code_root: str | None = None
    workbench_enabled: bool = True
    workbench_legacy_liepin_login_relay_enabled: bool = False
    artifacts_dir: str | None = None
    runtime_artifact_output_mode: str | None = None
    llm_cache_dir: str | None = None
    enable_flywheel: bool | None = None
    flywheel_db_path: str = ".seektalent/flywheel.sqlite3"
    corpus_db_path: str = ".seektalent/corpus.sqlite3"
    runtime_control_db_path: str = DEFAULT_RUNTIME_CONTROL_DB_PATH
    conversation_agent_db_path: str = DEFAULT_CONVERSATION_AGENT_DB_PATH
    agent_memory_db_path: str = DEFAULT_AGENT_MEMORY_DB_PATH
    agent_memory_workspace_dir: str = DEFAULT_AGENT_MEMORY_WORKSPACE_PATH
    agent_turn_input_token_budget: int = 64_000
    agent_turn_output_token_budget: int = 4_096
    agent_conversation_token_budget: int = 128_000
    agent_compaction_trigger_token_budget: int = 96_000
    agent_monthly_cost_budget_cents: int | None = None
    agent_model_timeout_seconds: float = 60.0
    agent_tool_timeout_seconds: float = 30.0
    agent_stream_heartbeat_seconds: float = 15.0
    runtime_terminal_retention_days: int = 90
    runtime_checkpoint_retention_days: int = 30
    runtime_event_payload_retention_days: int = 30
    runtime_final_summary_retention_days: int = 90
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

    @field_validator("agent_monthly_cost_budget_cents", mode="before")
    @classmethod
    def normalize_empty_agent_monthly_cost_budget(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator(
        "workspace_root",
        "code_root",
        "artifacts_dir",
        "runs_dir",
        "llm_cache_dir",
        "runtime_control_db_path",
        "conversation_agent_db_path",
        "agent_memory_db_path",
        "agent_memory_workspace_dir",
        mode="before",
    )
    @classmethod
    def normalize_empty_local_path_string(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value

    @field_validator("runtime_artifact_output_mode", mode="before")
    @classmethod
    def normalize_runtime_artifact_output_mode(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return normalize_artifact_output_mode(value)

    @field_validator("liepin_browser_action_backend", mode="before")
    @classmethod
    def normalize_liepin_browser_action_backend(cls, value: str | None) -> str:
        text = (value or "disabled").strip().lower()
        return text or "disabled"

    @field_validator("liepin_opencli_command", mode="before")
    @classmethod
    def normalize_liepin_opencli_command(cls, value: str | None) -> str:
        text = (value or "").strip()
        return text or DEFAULT_LIEPIN_OPENCLI_COMMAND

    @field_validator("liepin_opencli_session", mode="before")
    @classmethod
    def normalize_liepin_opencli_session(cls, value: str | None) -> str:
        text = (value or "").strip()
        return text or DEFAULT_LIEPIN_OPENCLI_SESSION

    @field_validator(
        "liepin_worker_base_url",
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
        if self.runtime_artifact_output_mode is None:
            self.runtime_artifact_output_mode = "prod" if self.runtime_mode == "prod" else "dev"
            if "runtime_artifact_output_mode" not in provided_fields:
                self.model_fields_set.discard("runtime_artifact_output_mode")
        if self.enable_flywheel is None:
            self.enable_flywheel = self.runtime_mode != "prod"
            if "enable_flywheel" not in provided_fields:
                self.model_fields_set.discard("enable_flywheel")
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
        if self.scoring_timeout_seconds <= 0:
            raise ValueError("scoring_timeout_seconds must be > 0")
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
        if self.agent_turn_input_token_budget < 1:
            raise ValueError("agent_turn_input_token_budget must be >= 1")
        if self.agent_turn_output_token_budget < 1:
            raise ValueError("agent_turn_output_token_budget must be >= 1")
        if self.agent_conversation_token_budget < self.agent_turn_input_token_budget:
            raise ValueError("agent_conversation_token_budget must be >= agent_turn_input_token_budget")
        if not 1 <= self.agent_compaction_trigger_token_budget <= self.agent_conversation_token_budget:
            raise ValueError(
                "agent_compaction_trigger_token_budget must be between 1 and agent_conversation_token_budget"
            )
        if self.agent_monthly_cost_budget_cents is not None and self.agent_monthly_cost_budget_cents < 0:
            raise ValueError("agent_monthly_cost_budget_cents must be >= 0")
        if self.agent_model_timeout_seconds <= 0:
            raise ValueError("agent_model_timeout_seconds must be > 0")
        if self.controller_timeout_seconds <= 0:
            raise ValueError("controller_timeout_seconds must be > 0")
        if self.agent_tool_timeout_seconds <= 0:
            raise ValueError("agent_tool_timeout_seconds must be > 0")
        if self.agent_stream_heartbeat_seconds <= 0:
            raise ValueError("agent_stream_heartbeat_seconds must be > 0")
        if self.liepin_worker_startup_timeout_seconds <= 0:
            raise ValueError("liepin_worker_startup_timeout_seconds must be > 0")
        if self.liepin_worker_timeout_seconds <= 0:
            raise ValueError("liepin_worker_timeout_seconds must be > 0")
        if self.liepin_default_daily_detail_budget < 0:
            raise ValueError("liepin_default_daily_detail_budget must be >= 0")
        if not 1 <= self.liepin_exploit_detail_target <= 10:
            raise ValueError("liepin_exploit_detail_target must be between 1 and 10")
        if not 1 <= self.liepin_explore_detail_target <= 10:
            raise ValueError("liepin_explore_detail_target must be between 1 and 10")
        if (
            min(
                self.liepin_opencli_max_actions_per_task,
                self.liepin_opencli_max_pages_per_task,
                self.liepin_opencli_max_cards_per_task,
                self.liepin_opencli_timeout_seconds,
                self.liepin_opencli_detail_open_timeout_seconds,
                self.liepin_opencli_idle_close_seconds,
            )
            < 1
        ):
            raise ValueError("OpenCLI Liepin budgets and timeout must be >= 1")
        if self.liepin_opencli_pacing_min_ms < 0 or self.liepin_opencli_pacing_max_ms < 0:
            raise ValueError("liepin_opencli_pacing values must be non-negative")
        if self.liepin_opencli_pacing_min_ms > self.liepin_opencli_pacing_max_ms:
            raise ValueError("liepin_opencli_pacing_min_ms must be <= liepin_opencli_pacing_max_ms")
        return self

    @model_validator(mode="after")
    def validate_liepin_worker_config(self) -> "AppSettings":
        if self.liepin_worker_mode == "fake_fixture" and not self.liepin_allow_fake_fixture_worker:
            raise ValueError("liepin_worker_mode=fake_fixture requires liepin_allow_fake_fixture_worker=True")
        if self.liepin_worker_mode == "external_http" and self.liepin_worker_base_url is None:
            raise ValueError("liepin_worker_base_url is required when liepin_worker_mode=external_http")
        if self.liepin_browser_action_backend == "opencli":
            if not self.liepin_opencli_allowed_hosts:
                raise ValueError("liepin_opencli_allowed_hosts_json must not be empty")
            if not self.liepin_opencli_allowed_start_urls:
                raise ValueError("liepin_opencli_allowed_start_urls_json must not be empty")
            self.liepin_opencli_command_argv
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
    def runtime_limits(self) -> RuntimeLimitsSettings:
        return RuntimeLimitsSettings(
            min_rounds=self.min_rounds,
            max_rounds=self.max_rounds,
            scoring_max_concurrency=self.scoring_max_concurrency,
            scoring_timeout_seconds=self.scoring_timeout_seconds,
            judge_max_concurrency=self.judge_max_concurrency,
            search_max_pages_per_round=self.search_max_pages_per_round,
            search_max_attempts_per_round=self.search_max_attempts_per_round,
            search_no_progress_limit=self.search_no_progress_limit,
        )

    @property
    def source_providers(self) -> SourceProviderSettings:
        return SourceProviderSettings(
            provider_name=self.provider_name,
            cts_base_url=self.cts_base_url,
            cts_tenant_key=self.cts_tenant_key,
            cts_tenant_secret=self.cts_tenant_secret,
            cts_timeout_seconds=self.cts_timeout_seconds,
            cts_spec_path=self.cts_spec_path,
            liepin_worker_mode=self.liepin_worker_mode,
            liepin_allow_fake_fixture_worker=self.liepin_allow_fake_fixture_worker,
            liepin_worker_base_url=self.liepin_worker_base_url,
            liepin_browser_action_backend=self.liepin_browser_action_backend,
            liepin_opencli_command=self.liepin_opencli_command,
            liepin_opencli_session=self.liepin_opencli_session,
            liepin_opencli_window_mode=self.liepin_opencli_window_mode,
            liepin_opencli_allowed_hosts_json=self.liepin_opencli_allowed_hosts_json,
            liepin_opencli_allowed_start_urls_json=self.liepin_opencli_allowed_start_urls_json,
            liepin_opencli_max_actions_per_task=self.liepin_opencli_max_actions_per_task,
            liepin_opencli_max_pages_per_task=self.liepin_opencli_max_pages_per_task,
            liepin_opencli_max_cards_per_task=self.liepin_opencli_max_cards_per_task,
            liepin_opencli_timeout_seconds=self.liepin_opencli_timeout_seconds,
            liepin_opencli_detail_open_timeout_seconds=self.liepin_opencli_detail_open_timeout_seconds,
            liepin_opencli_idle_close_seconds=self.liepin_opencli_idle_close_seconds,
            liepin_opencli_close_blank_window=self.liepin_opencli_close_blank_window,
            liepin_opencli_pacing_enabled=self.liepin_opencli_pacing_enabled,
            liepin_opencli_pacing_min_ms=self.liepin_opencli_pacing_min_ms,
            liepin_opencli_pacing_max_ms=self.liepin_opencli_pacing_max_ms,
            liepin_worker_host=self.liepin_worker_host,
            liepin_worker_port=self.liepin_worker_port,
            liepin_worker_startup_timeout_seconds=self.liepin_worker_startup_timeout_seconds,
            liepin_worker_timeout_seconds=self.liepin_worker_timeout_seconds,
            liepin_connector_db_path=self.liepin_connector_db_path,
            liepin_session_store_dir=self.liepin_session_store_dir,
            liepin_session_store_key_id=self.liepin_session_store_key_id,
            liepin_api_token=self.liepin_api_token,
            liepin_account_binding_secret=self.liepin_account_binding_secret,
            liepin_stream_token_secret=self.liepin_stream_token_secret,
            liepin_detail_open_approval_secret=self.liepin_detail_open_approval_secret,
            liepin_default_daily_detail_budget=self.liepin_default_daily_detail_budget,
            liepin_exploit_detail_target=self.liepin_exploit_detail_target,
            liepin_explore_detail_target=self.liepin_explore_detail_target,
            liepin_live_enabled=self.liepin_live_enabled,
        )

    @property
    def text_llm(self) -> TextLLMSettings:
        return TextLLMSettings(
            protocol_family=self.text_llm_protocol_family,
            provider_label=self.text_llm_provider_label,
            endpoint_kind=self.text_llm_endpoint_kind,
            endpoint_region=self.text_llm_endpoint_region,
            base_url_override=self.text_llm_base_url_override,
            api_key=self.text_llm_api_key,
            requirements_model_id=self.requirements_model_id,
            controller_model_id=self.controller_model_id,
            scoring_model_id=self.scoring_model_id,
            finalize_model_id=self.finalize_model_id,
            reflection_model_id=self.reflection_model_id,
            requirements_enable_thinking=self.requirements_enable_thinking,
            structured_repair_model_id=self.structured_repair_model_id,
            structured_repair_reasoning_effort=self.structured_repair_reasoning_effort,
            judge_model_id=self.judge_model_id,
            tui_summary_model_id=self.tui_summary_model_id,
            reasoning_effort=self.reasoning_effort,
            judge_reasoning_effort=self.judge_reasoning_effort,
            controller_enable_thinking=self.controller_enable_thinking,
            reflection_enable_thinking=self.reflection_enable_thinking,
            candidate_feedback_enabled=self.candidate_feedback_enabled,
            candidate_feedback_model_id=self.candidate_feedback_model_id,
            candidate_feedback_reasoning_effort=self.candidate_feedback_reasoning_effort,
            prf_probe_phrase_proposal_model_id=self.prf_probe_phrase_proposal_model_id,
            prf_probe_phrase_proposal_reasoning_effort=self.prf_probe_phrase_proposal_reasoning_effort,
            workbench_note_writer_model_id=self.workbench_note_writer_model_id,
            workbench_note_writer_reasoning_effort=self.workbench_note_writer_reasoning_effort,
            workbench_conversation_model_id=self.workbench_conversation_model_id,
            workbench_conversation_reasoning_effort=self.workbench_conversation_reasoning_effort,
            prf_probe_phrase_proposal_timeout_seconds=self.prf_probe_phrase_proposal_timeout_seconds,
            prf_probe_phrase_proposal_live_harness_timeout_seconds=(
                self.prf_probe_phrase_proposal_live_harness_timeout_seconds
            ),
            prf_probe_phrase_proposal_max_output_tokens=self.prf_probe_phrase_proposal_max_output_tokens,
        )

    @property
    def project_root(self) -> Path:
        return self.runtime_context.workspace_root

    @property
    def code_base_root(self) -> Path:
        if self.code_root:
            return resolve_user_path(self.code_root).resolve(strict=False)
        return package_repo_root()

    @property
    def runtime_context(self) -> RuntimeContext:
        return RuntimeContext.from_value(self.workspace_root)

    def resolve_workspace_path(self, value: str) -> Path:
        return resolve_path_from_root(value, root=self.project_root)

    def resolve_code_path(self, value: str) -> Path:
        return resolve_path_from_root(value, root=self.code_base_root)

    @property
    def liepin_opencli_allowed_hosts(self) -> tuple[str, ...]:
        return _json_string_tuple(
            self.liepin_opencli_allowed_hosts_json,
            field_name="liepin_opencli_allowed_hosts_json",
        )

    @property
    def liepin_opencli_allowed_start_urls(self) -> tuple[str, ...]:
        return _json_string_tuple(
            self.liepin_opencli_allowed_start_urls_json,
            field_name="liepin_opencli_allowed_start_urls_json",
        )

    @property
    def liepin_opencli_command_argv(self) -> tuple[str, ...]:
        argv = tuple(shlex.split(self.liepin_opencli_command))
        if not argv:
            argv = (DEFAULT_LIEPIN_OPENCLI_COMMAND,)
        command_text = argv[0]
        command = Path(command_text)
        has_path_separator = os.sep in command_text or (os.altsep is not None and os.altsep in command_text)
        if not command.is_absolute() and has_path_separator:
            command = self.resolve_code_path(str(command))
            return (str(command), *argv[1:])
        return argv

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
    def runtime_control_path(self) -> Path:
        return self.resolve_workspace_path(self.runtime_control_db_path)

    @property
    def conversation_agent_path(self) -> Path:
        return self.resolve_workspace_path(self.conversation_agent_db_path)

    @property
    def agent_memory_path(self) -> Path:
        return self.resolve_workspace_path(self.agent_memory_db_path)

    @property
    def agent_memory_workspace_path(self) -> Path:
        return self.resolve_workspace_path(self.agent_memory_workspace_dir)

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
            raise ValueError(
                "The legacy runs/ root is decommissioned as an active output target. Use artifacts/ instead."
            )
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
            raise ValueError("Real CTS mode requires SEEKTALENT_CTS_TENANT_KEY and SEEKTALENT_CTS_TENANT_SECRET.")

    def with_overrides(self, **overrides: object) -> "AppSettings":
        filtered = {key: value for key, value in overrides.items() if value is not None}
        data = self.model_dump()
        reset_artifacts_dir = "artifacts_dir" not in filtered and "artifacts_dir" not in self.model_fields_set
        reset_runs_dir = "runs_dir" not in filtered and "runs_dir" not in self.model_fields_set
        reset_llm_cache_dir = "llm_cache_dir" not in filtered and "llm_cache_dir" not in self.model_fields_set
        reset_enable_flywheel = "enable_flywheel" not in filtered and "enable_flywheel" not in self.model_fields_set
        reset_runtime_artifact_output_mode = (
            "runtime_artifact_output_mode" not in filtered
            and "runtime_artifact_output_mode" not in self.model_fields_set
        )
        if reset_artifacts_dir:
            data["artifacts_dir"] = None
        if reset_runs_dir:
            data["runs_dir"] = None
        if reset_llm_cache_dir:
            data["llm_cache_dir"] = None
        if reset_enable_flywheel:
            data["enable_flywheel"] = None
        if reset_runtime_artifact_output_mode:
            data["runtime_artifact_output_mode"] = None
        settings = type(self)(_env_file=None, **{**data, **filtered})
        if reset_artifacts_dir:
            settings.model_fields_set.discard("artifacts_dir")
        if reset_runs_dir:
            settings.model_fields_set.discard("runs_dir")
        if reset_llm_cache_dir:
            settings.model_fields_set.discard("llm_cache_dir")
        if reset_enable_flywheel:
            settings.model_fields_set.discard("enable_flywheel")
        if reset_runtime_artifact_output_mode:
            settings.model_fields_set.discard("runtime_artifact_output_mode")
        return settings
