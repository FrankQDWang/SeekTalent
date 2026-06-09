from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from seektalent_runtime_control.store import RuntimeControlStore


@dataclass(frozen=True)
class RuntimeRetentionResult:
    compacted_event_payload_count: int = 0
    deleted_checkpoint_count: int = 0
    deleted_final_summary_count: int = 0


class RuntimeRetentionService:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        now: Callable[[], str] | None = None,
        event_payload_retention_days: int = 30,
        checkpoint_retention_days: int = 30,
        final_summary_retention_days: int = 90,
    ) -> None:
        self.store = store
        self.now = now or _now
        self.event_payload_retention_days = event_payload_retention_days
        self.checkpoint_retention_days = checkpoint_retention_days
        self.final_summary_retention_days = final_summary_retention_days

    def cleanup(self, *, batch_size: int = 500) -> RuntimeRetentionResult:
        compacted = self.store.compact_terminal_event_payloads(
            older_than=_minus_days(self.now(), self.event_payload_retention_days),
            batch_size=batch_size,
        )
        deleted_checkpoints = self.store.delete_terminal_checkpoints(
            older_than=_minus_days(self.now(), self.checkpoint_retention_days),
            batch_size=batch_size,
        )
        deleted_summaries = self.store.delete_terminal_final_summaries(
            older_than=_minus_days(self.now(), self.final_summary_retention_days),
            batch_size=batch_size,
        )
        return RuntimeRetentionResult(
            compacted_event_payload_count=compacted,
            deleted_checkpoint_count=deleted_checkpoints,
            deleted_final_summary_count=deleted_summaries,
        )


def _minus_days(value: str, days: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed - timedelta(days=days)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
