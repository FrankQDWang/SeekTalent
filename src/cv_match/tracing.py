from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    timestamp: str
    run_id: str
    event_type: str
    round_no: int | None = None
    resume_id: str | None = None
    branch_id: str | None = None
    model: str | None = None
    tool_name: str | None = None
    latency_ms: int | None = None
    summary: str | None = None
    stop_reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


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
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._jsonable(item) for item in value]
        if isinstance(value, set):
            return sorted(self._jsonable(item) for item in value)
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
        latency_ms: int | None = None,
        summary: str | None = None,
        stop_reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        event = TraceEvent(
            timestamp=timestamp,
            run_id=self.run_id,
            event_type=event_type,
            round_no=round_no,
            resume_id=resume_id,
            branch_id=branch_id,
            model=model,
            tool_name=tool_name,
            latency_ms=latency_ms,
            summary=summary,
            stop_reason=stop_reason,
            payload=self._jsonable(payload or {}),
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
        path.write_text(
            json.dumps(self._jsonable(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def write_text(self, filename: str, content: str) -> Path:
        path = self.run_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def close(self) -> None:
        with self._lock:
            self._trace_handle.close()
            self._events_handle.close()
