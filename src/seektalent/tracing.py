from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from typing import Literal

from pydantic import BaseModel, Field

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
        try:
            detail_tokens[str(key)] = _int_value(value)
        except (TypeError, ValueError):
            continue
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
    prompt_hash: str
    prompt_snapshot_path: str
    output_mode: Literal["native_strict"] = "native_strict"
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
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root
        self.runs_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone()
        self.run_id = uuid.uuid4().hex[:8]
        self.run_dir = self.runs_root / f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{self.run_id}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trace_log_path = self.run_dir / "trace.log"
        self.events_path = self.run_dir / "events.jsonl"
        self._trace_handle = self.trace_log_path.open("a", encoding="utf-8")
        self._events_handle = self.events_path.open("a", encoding="utf-8")
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

    def write_json(self, filename: str, payload: Any) -> Path:
        path = self.run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._jsonable(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def write_jsonl(self, filename: str, rows: list[Any]) -> Path:
        path = self.run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(self._jsonable(row), ensure_ascii=False)
            for row in rows
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def append_jsonl(self, filename: str, row: Any) -> Path:
        path = self.run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(row, LLMCallSnapshot):
            row = row.model_dump(mode="json")
        if isinstance(row, dict) and row.get("provider_usage") is None:
            row = {key: value for key, value in row.items() if key != "provider_usage"}
        line = json.dumps(self._jsonable(row), ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return path

    def write_text(self, filename: str, content: str) -> Path:
        path = self.run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def close(self) -> None:
        with self._lock:
            self._trace_handle.close()
            self._events_handle.close()
