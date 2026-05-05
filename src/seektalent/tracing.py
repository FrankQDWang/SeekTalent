from __future__ import annotations

import json
import threading
from contextlib import suppress
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from typing import Literal

from pydantic import BaseModel, Field
from seektalent.artifacts import ArtifactStore

EVENT_STRING_LIMIT = 60
EVENT_LIST_LIMIT = 5


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(jsonable(item) for item in value)
    return value


def stable_json_text(value: Any) -> str:
    return json.dumps(
        jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def json_sha256(value: Any) -> str:
    return sha256(stable_json_text(value).encode("utf-8")).hexdigest()


def json_char_count(value: Any) -> int:
    return len(stable_json_text(value))


def text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def text_char_count(value: str) -> int:
    return len(value)


def _int_value(value: Any) -> int:
    return int(value)


class ProviderUsageSnapshot(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    details: dict[str, int] = Field(default_factory=dict)


def combine_provider_usage(
    left: ProviderUsageSnapshot | None,
    right: ProviderUsageSnapshot | None,
) -> ProviderUsageSnapshot | None:
    if left is None:
        return right
    if right is None:
        return left
    details = dict(left.details)
    for key, value in right.details.items():
        details[key] = details.get(key, 0) + value
    return ProviderUsageSnapshot(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        cache_read_tokens=left.cache_read_tokens + right.cache_read_tokens,
        cache_write_tokens=left.cache_write_tokens + right.cache_write_tokens,
        details=details,
    )


def provider_usage_from_result(result: Any) -> ProviderUsageSnapshot | None:
    usage_fn = getattr(result, "usage", None)
    if not callable(usage_fn):
        return None
    usage = usage_fn()
    if usage is None:
        return None
    details = getattr(usage, "details", {}) or {}
    input_tokens = _int_value(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = _int_value(getattr(usage, "output_tokens", 0) or 0)
    detail_tokens: dict[str, int] = {}
    for key, value in details.items():
        if isinstance(value, bool):
            continue
        with suppress(TypeError, ValueError):
            detail_tokens[str(key)] = _int_value(value)
    return ProviderUsageSnapshot(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cache_read_tokens=_int_value(getattr(usage, "cache_read_tokens", 0) or 0),
        cache_write_tokens=_int_value(getattr(usage, "cache_write_tokens", 0) or 0),
        details=detail_tokens,
    )


class TraceEvent(BaseModel):
    timestamp: str
    run_id: str
    event_type: str
    round_no: int | None = None
    resume_id: str | None = None
    branch_id: str | None = None
    model: str | None = None
    tool_name: str | None = None
    call_id: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    summary: str | None = None
    stop_reason: str | None = None
    error_message: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class LLMCallSnapshot(BaseModel):
    stage: str
    call_id: str
    round_no: int | None = None
    resume_id: str | None = None
    branch_id: str | None = None
    model_id: str
    provider: str
    protocol_family: Literal["openai_chat_completions_compatible", "anthropic_messages_compatible"]
    endpoint_kind: str
    endpoint_region: str
    prompt_hash: str
    prompt_snapshot_path: str
    output_mode: Literal["native_strict"] = "native_strict"
    structured_output_mode: Literal["native_json_schema", "prompted_json"]
    thinking_mode: bool
    reasoning_effort: Literal["off", "low", "medium", "high", "xhigh", "max"]
    retries: int
    output_retries: int
    started_at: str
    latency_ms: int | None = None
    status: Literal["succeeded", "failed"]
    input_artifact_refs: list[str] = Field(default_factory=list)
    output_artifact_refs: list[str] = Field(default_factory=list)
    input_payload_sha256: str
    structured_output_sha256: str | None = None
    prompt_chars: int
    input_payload_chars: int
    output_chars: int
    input_summary: str
    output_summary: str | None = None
    error_message: str | None = None
    failure_kind: Literal[
        "timeout",
        "transport_error",
        "provider_error",
        "response_validation_error",
        "structured_output_parse_error",
        "insufficient_prf_seed_support",
        "settings_migration_error",
        "unsupported_capability",
    ] | None = None
    provider_failure_kind: Literal[
        "provider_auth_error",
        "provider_access_denied",
        "provider_quota_exceeded",
        "provider_rate_limited",
        "provider_model_not_found",
        "provider_endpoint_mismatch",
        "provider_invalid_request",
        "provider_unsupported_parameter",
        "provider_content_safety_block",
        "provider_schema_error",
        "provider_timeout",
        "provider_unknown_error",
    ] | None = None
    provider_status_code: int | None = None
    provider_error_type: str | None = None
    provider_error_code: str | None = None
    provider_request_id: str | None = None
    validator_retry_count: int = 0
    validator_retry_reasons: list[str] = Field(default_factory=list)
    provider_usage: ProviderUsageSnapshot | None = None
    cache_hit: bool = False
    cache_key: str | None = None
    cache_lookup_latency_ms: int | None = None
    prompt_cache_key: str | None = None
    prompt_cache_retention: str | None = None
    cached_input_tokens: int | None = None
    repair_attempt_count: int = 0
    repair_succeeded: bool = False
    repair_model: str | None = None
    repair_reason: str | None = None
    full_retry_count: int = 0


class RunTracer:
    def __init__(self, artifacts_root: Path) -> None:
        self.store = ArtifactStore(artifacts_root)
        self.session = self.store.create_root(
            kind="run",
            display_name="seek talent workflow run",
            producer="WorkflowRuntime",
        )
        self.run_id = self.session.manifest.artifact_id
        self.run_dir = self.session.root
        self.trace_log_path, self._trace_handle = self.session.open_text_stream("runtime.trace_log")
        self.events_path, self._events_handle = self.session.open_text_stream("runtime.events")
        self._lock = threading.Lock()

    def _jsonable(self, value: Any) -> Any:
        return jsonable(value)

    def _capped_event_payload(self, value: Any) -> Any:
        value = self._jsonable(value)
        if isinstance(value, str):
            if len(value) <= EVENT_STRING_LIMIT:
                return value
            return f"{value[:EVENT_STRING_LIMIT].rstrip()}..."
        if isinstance(value, list):
            capped = [self._capped_event_payload(item) for item in value[:EVENT_LIST_LIMIT]]
            if len(value) > EVENT_LIST_LIMIT:
                capped.append({"truncated_count": len(value) - EVENT_LIST_LIMIT})
            return capped
        if isinstance(value, dict):
            return {
                key: (item if key == "error_message" else self._capped_event_payload(item))
                for key, item in value.items()
            }
        return value

    def emit(
        self,
        event_type: str,
        *,
        round_no: int | None = None,
        resume_id: str | None = None,
        branch_id: str | None = None,
        model: str | None = None,
        tool_name: str | None = None,
        call_id: str | None = None,
        status: str | None = None,
        latency_ms: int | None = None,
        summary: str | None = None,
        stop_reason: str | None = None,
        error_message: str | None = None,
        artifact_paths: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        event_summary = summary
        if summary is not None and event_type != "run_finished":
            event_summary = self._capped_event_payload(summary)
        event = TraceEvent(
            timestamp=timestamp,
            run_id=self.run_id,
            event_type=event_type,
            round_no=round_no,
            resume_id=resume_id,
            branch_id=branch_id,
            model=model,
            tool_name=tool_name,
            call_id=call_id,
            status=status,
            latency_ms=latency_ms,
            summary=event_summary,
            stop_reason=stop_reason,
            error_message=error_message,
            artifact_paths=self._jsonable(artifact_paths or []),
            payload=self._capped_event_payload(payload or {}),
        )
        human_parts = [f"[{timestamp}]", event_type]
        if round_no is not None:
            human_parts.append(f"round={round_no}")
        if resume_id:
            human_parts.append(f"resume={resume_id}")
        if branch_id:
            human_parts.append(f"branch={branch_id}")
        if tool_name:
            human_parts.append(f"tool={tool_name}")
        if call_id:
            human_parts.append(f"call={call_id}")
        if status:
            human_parts.append(f"status={status}")
        if model:
            human_parts.append(f"model={model}")
        if latency_ms is not None:
            human_parts.append(f"latency={latency_ms}ms")
        if stop_reason:
            human_parts.append(f"stop_reason={stop_reason}")
        if summary:
            human_parts.append(summary)
        line = " | ".join(human_parts)
        with self._lock:
            self._trace_handle.write(line + "\n")
            self._trace_handle.flush()
            self._events_handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
            self._events_handle.flush()

    def write_json(self, logical_name: str, payload: Any) -> Path:
        payload = self._jsonable(payload)
        try:
            return self.session.write_json(logical_name, payload)
        except KeyError:
            return self._write_legacy_json(logical_name, payload)

    def write_jsonl(self, logical_name: str, rows: list[Any]) -> Path:
        rows = [self._jsonable(row) for row in rows]
        try:
            return self.session.write_jsonl(logical_name, rows)
        except KeyError:
            return self._write_legacy_jsonl(logical_name, rows)

    def append_jsonl(self, logical_name: str, row: Any) -> Path:
        if isinstance(row, LLMCallSnapshot):
            row = row.model_dump(mode="json")
        if isinstance(row, dict) and row.get("provider_usage") is None:
            row = {key: value for key, value in row.items() if key != "provider_usage"}
        row = self._jsonable(row)
        try:
            return self.session.append_jsonl(logical_name, row)
        except KeyError:
            return self._append_legacy_jsonl(logical_name, row)

    def write_text(self, logical_name: str, content: str) -> Path:
        try:
            return self.session.write_text(logical_name, content)
        except KeyError:
            return self._write_legacy_text(logical_name, content)

    def close(self, *, status: str = "completed", failure_summary: str | None = None) -> None:
        with self._lock:
            self._trace_handle.close()
            self._events_handle.close()
        self.session.finalize(status=status, failure_summary=failure_summary)

    def _legacy_path(self, relative_path: str) -> Path:
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _register_legacy_path(self, relative_path: str) -> None:
        content_type, schema_version = _legacy_entry_metadata(relative_path)
        self.session.register_path(
            relative_path,
            relative_path,
            content_type=content_type,
            schema_version=schema_version,
            collection=False,
        )

    def _write_legacy_json(self, relative_path: str, payload: Any) -> Path:
        self._register_legacy_path(relative_path)
        path = self._legacy_path(relative_path)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_legacy_jsonl(self, relative_path: str, rows: list[Any]) -> Path:
        self._register_legacy_path(relative_path)
        path = self._legacy_path(relative_path)
        lines = [json.dumps(row, ensure_ascii=False) for row in rows]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def _append_legacy_jsonl(self, relative_path: str, row: Any) -> Path:
        self._register_legacy_path(relative_path)
        path = self._legacy_path(relative_path)
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return path

    def _write_legacy_text(self, relative_path: str, content: str) -> Path:
        self._register_legacy_path(relative_path)
        path = self._legacy_path(relative_path)
        path.write_text(content, encoding="utf-8")
        return path


def _legacy_entry_metadata(relative_path: str) -> tuple[str, str | None]:
    path = Path(relative_path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json", "v1"
    if suffix == ".jsonl":
        return "application/jsonl", "v1"
    if suffix == ".md":
        return "text/markdown", None
    if suffix in {".log", ".txt"}:
        return "text/plain", None
    return "application/octet-stream", None
