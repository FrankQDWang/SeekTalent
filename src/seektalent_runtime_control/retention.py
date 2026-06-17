from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from seektalent_runtime_control.store import RuntimeControlStore


class RuntimeControlRetentionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_run_min_age_days: int = Field(default=30, ge=0)
    developer_event_ttl_days: int = Field(default=14, ge=0)
    internal_event_ttl_days: int = Field(default=30, ge=0)
    checkpoint_ttl_days: int = Field(default=30, ge=0)
    lease_ttl_days: int = Field(default=7, ge=0)
    command_ttl_days: int = Field(default=30, ge=0)
    non_required_stage_output_ttl_days: int = Field(default=30, ge=0)
    final_summary_ttl_days: int = Field(default=90, ge=0)
    batch_size: int = Field(default=500, ge=1, le=1000)
    database_budget_bytes: int | None = Field(default=None, ge=1)


@dataclass(frozen=True)
class RuntimeControlRetentionStats:
    nonpublic_event_count: int = 0
    checkpoint_count: int = 0
    executor_lease_count: int = 0
    command_count: int = 0
    stage_output_count: int = 0
    final_summary_count: int = 0
    nonpublic_event_estimated_bytes: int = 0
    checkpoint_estimated_bytes: int = 0
    executor_lease_estimated_bytes: int = 0
    command_estimated_bytes: int = 0
    stage_output_estimated_bytes: int = 0
    final_summary_estimated_bytes: int = 0
    database_size_bytes: int = 0
    wal_size_bytes: int = 0
    database_budget_bytes: int | None = None

    @property
    def total_count(self) -> int:
        return (
            self.nonpublic_event_count
            + self.checkpoint_count
            + self.executor_lease_count
            + self.command_count
            + self.stage_output_count
            + self.final_summary_count
        )

    @property
    def total_estimated_bytes(self) -> int:
        return (
            self.nonpublic_event_estimated_bytes
            + self.checkpoint_estimated_bytes
            + self.executor_lease_estimated_bytes
            + self.command_estimated_bytes
            + self.stage_output_estimated_bytes
            + self.final_summary_estimated_bytes
        )

    @property
    def total_database_bytes(self) -> int:
        return self.database_size_bytes + self.wal_size_bytes

    @property
    def over_database_budget(self) -> bool:
        return self.database_budget_bytes is not None and self.total_database_bytes > self.database_budget_bytes


@dataclass(frozen=True)
class RuntimeRetentionResult:
    dry_run: bool = False
    stats: RuntimeControlRetentionStats = RuntimeControlRetentionStats()
    deleted_nonpublic_event_count: int = 0
    deleted_checkpoint_count: int = 0
    deleted_executor_lease_count: int = 0
    deleted_command_count: int = 0
    deleted_stage_output_count: int = 0
    deleted_final_summary_count: int = 0

    @property
    def compacted_event_payload_count(self) -> int:
        return 0

    @property
    def total_deleted_count(self) -> int:
        return (
            self.deleted_nonpublic_event_count
            + self.deleted_checkpoint_count
            + self.deleted_executor_lease_count
            + self.deleted_command_count
            + self.deleted_stage_output_count
            + self.deleted_final_summary_count
        )


class RuntimeRetentionService:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        now: Callable[[], str] | None = None,
        policy: RuntimeControlRetentionPolicy | None = None,
        event_payload_retention_days: int | None = None,
        checkpoint_retention_days: int | None = None,
        final_summary_retention_days: int | None = None,
    ) -> None:
        self.store = store
        self.now = now or _now
        developer_event_ttl_days = 14 if event_payload_retention_days is None else event_payload_retention_days
        internal_event_ttl_days = 30 if event_payload_retention_days is None else event_payload_retention_days
        checkpoint_ttl_days = 30 if checkpoint_retention_days is None else checkpoint_retention_days
        final_summary_ttl_days = 90 if final_summary_retention_days is None else final_summary_retention_days
        self.policy = policy or RuntimeControlRetentionPolicy(
            developer_event_ttl_days=developer_event_ttl_days,
            internal_event_ttl_days=internal_event_ttl_days,
            checkpoint_ttl_days=checkpoint_ttl_days,
            final_summary_ttl_days=final_summary_ttl_days,
        )

    def cleanup(self, *, batch_size: int | None = None, dry_run: bool = False) -> RuntimeRetentionResult:
        policy = (
            self.policy
            if batch_size is None
            else self.policy.model_copy(update={"batch_size": max(1, min(batch_size, 1000))})
        )
        cutoffs = _cutoffs(now=self.now(), policy=policy)
        raw_stats = self.store.collect_runtime_control_retention_stats(**cutoffs)
        stats = _stats_from_store_counts(raw_stats, database_budget_bytes=policy.database_budget_bytes)
        deleted = self.store.cleanup_runtime_control_retention(
            **cutoffs,
            batch_size=policy.batch_size,
            dry_run=dry_run,
        )
        if dry_run:
            deleted = {key: 0 for key in deleted}
        return RuntimeRetentionResult(
            dry_run=dry_run,
            stats=stats,
            deleted_nonpublic_event_count=deleted.get("nonpublic_event", 0),
            deleted_checkpoint_count=deleted.get("checkpoint", 0),
            deleted_executor_lease_count=deleted.get("executor_lease", 0),
            deleted_command_count=deleted.get("command", 0),
            deleted_stage_output_count=deleted.get("stage_output", 0),
            deleted_final_summary_count=deleted.get("final_summary", 0),
        )


def _stats_from_store_counts(
    counts: dict[str, int],
    *,
    database_budget_bytes: int | None,
) -> RuntimeControlRetentionStats:
    return RuntimeControlRetentionStats(
        nonpublic_event_count=counts.get("nonpublic_event", 0),
        checkpoint_count=counts.get("checkpoint", 0),
        executor_lease_count=counts.get("executor_lease", 0),
        command_count=counts.get("command", 0),
        stage_output_count=counts.get("stage_output", 0),
        final_summary_count=counts.get("final_summary", 0),
        nonpublic_event_estimated_bytes=counts.get("nonpublic_event_estimated_bytes", 0),
        checkpoint_estimated_bytes=counts.get("checkpoint_estimated_bytes", 0),
        executor_lease_estimated_bytes=counts.get("executor_lease_estimated_bytes", 0),
        command_estimated_bytes=counts.get("command_estimated_bytes", 0),
        stage_output_estimated_bytes=counts.get("stage_output_estimated_bytes", 0),
        final_summary_estimated_bytes=counts.get("final_summary_estimated_bytes", 0),
        database_size_bytes=counts.get("database_size_bytes", 0),
        wal_size_bytes=counts.get("wal_size_bytes", 0),
        database_budget_bytes=database_budget_bytes,
    )


def _cutoffs(*, now: str, policy: RuntimeControlRetentionPolicy) -> dict[str, str]:
    return {
        "terminal_run_older_than": _minus_days(now, policy.terminal_run_min_age_days),
        "developer_event_older_than": _minus_days(now, policy.developer_event_ttl_days),
        "internal_event_older_than": _minus_days(now, policy.internal_event_ttl_days),
        "checkpoint_older_than": _minus_days(now, policy.checkpoint_ttl_days),
        "lease_older_than": _minus_days(now, policy.lease_ttl_days),
        "command_older_than": _minus_days(now, policy.command_ttl_days),
        "stage_output_older_than": _minus_days(now, policy.non_required_stage_output_ttl_days),
        "final_summary_older_than": _minus_days(now, policy.final_summary_ttl_days),
    }


def _minus_days(value: str, days: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed - timedelta(days=days)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
