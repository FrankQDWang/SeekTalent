from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import sqlite3
from typing import Protocol

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import (
    RuntimeControlCandidateEvidence,
    RuntimeControlCandidateFinalizationRevision,
    RuntimeControlCandidateIdentity,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge
from seektalent_ui.workbench_store import WorkbenchUser


@dataclass(frozen=True)
class RuntimeProjectionBatchResult:
    runtime_run_id: str
    attempted_count: int
    projected_count: int
    failed_count: int


class _ProjectionResult(Protocol):
    workbench_event_global_seq: int


class _CandidateTruthProjectionResult(Protocol):
    revision: int
    projected_ref: str


class _RuntimeEventProjector(Protocol):
    def project_runtime_event(
        self,
        *,
        user: WorkbenchUser,
        runtime_run_id: str,
        event_id: str,
        projected_at: str | None = None,
    ) -> _ProjectionResult:
        raise NotImplementedError

    def project_candidate_finalization_revision(
        self,
        *,
        user: WorkbenchUser,
        runtime_run_id: str,
        revision: int,
        finalization_revision: RuntimeControlCandidateFinalizationRevision | None = None,
        identities: Sequence[RuntimeControlCandidateIdentity] | None = None,
        evidence: Sequence[RuntimeControlCandidateEvidence] | None = None,
        projected_at: str | None = None,
    ) -> _CandidateTruthProjectionResult:
        raise NotImplementedError


class RuntimeControlProjectionService:
    def __init__(
        self,
        *,
        runtime_store: RuntimeControlStore,
        bridge: RuntimeWorkbenchBridge | _RuntimeEventProjector,
        user: WorkbenchUser,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.runtime_store = runtime_store
        self.bridge = bridge
        self.user = user
        self.now = now or _now

    def project_unprojected_public_events(
        self,
        *,
        runtime_run_id: str,
        limit: int = 100,
    ) -> RuntimeProjectionBatchResult:
        events = self.runtime_store.list_unprojected_public_events(runtime_run_id=runtime_run_id, limit=limit)
        projected = 0
        failed = 0
        for event in events:
            projected_at = self.now()
            try:
                projection = self.bridge.project_runtime_event(
                    user=self.user,
                    runtime_run_id=runtime_run_id,
                    event_id=event.event_id,
                    projected_at=projected_at,
                )
                self.runtime_store.mark_event_projection_success(
                    runtime_run_id=runtime_run_id,
                    event_id=event.event_id,
                    workbench_event_global_seq=projection.workbench_event_global_seq,
                    projected_at=projected_at,
                )
                projected += 1
            except (RuntimeControlError, RuntimeError, ValueError, sqlite3.Error) as exc:
                self.runtime_store.mark_event_projection_failure(
                    runtime_run_id=runtime_run_id,
                    event_id=event.event_id,
                    error_code=_projection_error_code(exc),
                )
                failed += 1
        self.project_unprojected_candidate_truth(runtime_run_id=runtime_run_id, limit=limit)
        return RuntimeProjectionBatchResult(
            runtime_run_id=runtime_run_id,
            attempted_count=len(events),
            projected_count=projected,
            failed_count=failed,
        )

    def project_unprojected_candidate_truth(
        self,
        *,
        runtime_run_id: str,
        limit: int = 20,
    ) -> RuntimeProjectionBatchResult:
        revisions = self.runtime_store.list_unprojected_candidate_finalization_revisions(
            runtime_run_id=runtime_run_id,
            projector="workbench",
            limit=limit,
        )
        projected = 0
        failed = 0
        identities = (
            self.runtime_store.list_candidate_identities(runtime_run_id=runtime_run_id) if revisions else []
        )
        evidence = self.runtime_store.list_candidate_evidence(runtime_run_id=runtime_run_id) if revisions else []
        for revision in revisions:
            projected_at = self.now()
            target_id = str(revision.revision)
            try:
                projection = self.bridge.project_candidate_finalization_revision(
                    user=self.user,
                    runtime_run_id=runtime_run_id,
                    revision=revision.revision,
                    finalization_revision=revision,
                    identities=identities,
                    evidence=evidence,
                    projected_at=projected_at,
                )
                self.runtime_store.mark_projection_success(
                    runtime_run_id=runtime_run_id,
                    target_kind="candidate_finalization_revision",
                    target_id=target_id,
                    projector="workbench",
                    target_version=revision.payload_hash,
                    projected_ref=projection.projected_ref,
                    projected_at=projected_at,
                )
                projected += 1
            except (RuntimeControlError, RuntimeError, ValueError, sqlite3.Error) as exc:
                self.runtime_store.mark_projection_failure(
                    runtime_run_id=runtime_run_id,
                    target_kind="candidate_finalization_revision",
                    target_id=target_id,
                    projector="workbench",
                    target_version=revision.payload_hash,
                    error_code=_projection_error_code(exc),
                    failed_at=projected_at,
                )
                failed += 1
        return RuntimeProjectionBatchResult(
            runtime_run_id=runtime_run_id,
            attempted_count=len(revisions),
            projected_count=projected,
            failed_count=failed,
        )


def _projection_error_code(exc: Exception) -> str:
    if isinstance(exc, RuntimeControlError):
        return exc.reason_code
    if isinstance(exc, ValueError) and str(exc) in {"workbench_session_missing", "runtime_link_broken"}:
        return str(exc)
    return "runtime_projection_failed"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
