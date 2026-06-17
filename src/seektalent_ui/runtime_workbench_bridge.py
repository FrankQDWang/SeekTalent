from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from seektalent.runtime.public_events import PUBLIC_EVENT_SCHEMA_VERSION
from seektalent_runtime_control.models import (
    RuntimeControlCandidateEvidence,
    RuntimeControlCandidateFinalizationRevision,
    RuntimeControlCandidateIdentity,
    RuntimeControlEvent,
    RuntimeRunRecord,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_ui.workbench_store import WorkbenchStore, WorkbenchUser


@dataclass(frozen=True)
class WorkbenchRunLink:
    runtime_run_id: str
    workbench_session_id: str | None
    reason_code: str | None = None


@dataclass(frozen=True)
class WorkbenchEventProjection:
    runtime_run_id: str
    runtime_event_id: str
    workbench_event_global_seq: int


@dataclass(frozen=True)
class WorkbenchCandidateTruthProjection:
    runtime_run_id: str
    revision: int
    projected_ref: str


class RuntimeWorkbenchBridge:
    def __init__(
        self,
        *,
        runtime_store: RuntimeControlStore,
        workbench_store: WorkbenchStore,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.runtime_store = runtime_store
        self.workbench_store = workbench_store
        self.now = now or _now

    def ensure_workbench_session_for_run(
        self,
        *,
        user: WorkbenchUser,
        runtime_run_id: str,
        job_title: str,
        jd_text: str,
        notes: str,
    ) -> WorkbenchRunLink:
        run = self.runtime_store.get_run(runtime_run_id)
        if run.workbench_session_id is not None:
            session = self.workbench_store.get_workbench_session(user=user, session_id=run.workbench_session_id)
            if session is not None:
                return WorkbenchRunLink(
                    runtime_run_id=runtime_run_id,
                    workbench_session_id=run.workbench_session_id,
                )
        session = self.workbench_store.get_workbench_session_by_runtime_run_id(
            user=user,
            runtime_run_id=runtime_run_id,
        )
        if session is not None:
            linked = self.runtime_store.link_workbench_session(
                runtime_run_id=runtime_run_id,
                workbench_session_id=session.session_id,
                updated_at=self.now(),
            )
            return WorkbenchRunLink(
                runtime_run_id=runtime_run_id,
                workbench_session_id=linked.workbench_session_id,
            )
        session = self.workbench_store.create_workbench_session(
            user=user,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_kinds=_workbench_source_kinds(run.source_ids),
            runtime_run_id=runtime_run_id,
        )
        linked = self.runtime_store.link_workbench_session(
            runtime_run_id=runtime_run_id,
            workbench_session_id=session.session_id,
            updated_at=self.now(),
        )
        return WorkbenchRunLink(runtime_run_id=runtime_run_id, workbench_session_id=linked.workbench_session_id)

    def reconcile_run_link(self, *, user: WorkbenchUser, runtime_run_id: str) -> WorkbenchRunLink:
        run = self.runtime_store.get_run(runtime_run_id)
        if run.workbench_session_id is None:
            return WorkbenchRunLink(
                runtime_run_id=runtime_run_id,
                workbench_session_id=None,
                reason_code="workbench_session_missing",
            )
        session = self.workbench_store.get_workbench_session(user=user, session_id=run.workbench_session_id)
        if session is None:
            return WorkbenchRunLink(
                runtime_run_id=runtime_run_id,
                workbench_session_id=run.workbench_session_id,
                reason_code="runtime_link_broken",
            )
        return WorkbenchRunLink(runtime_run_id=runtime_run_id, workbench_session_id=run.workbench_session_id)

    def project_runtime_event(
        self,
        *,
        user: WorkbenchUser,
        runtime_run_id: str,
        event_id: str,
        projected_at: str | None = None,
    ) -> WorkbenchEventProjection:
        run = self.runtime_store.get_run(runtime_run_id)
        workbench_session_id = self._workbench_session_id_for_projection(user=user, run=run)
        if workbench_session_id is None:
            if run.workbench_session_id is None:
                raise ValueError("workbench_session_missing")
            raise ValueError("runtime_link_broken")
        event = self.runtime_store.get_event(runtime_run_id=runtime_run_id, event_id=event_id)
        if event.workbench_event_global_seq is not None:
            return WorkbenchEventProjection(
                runtime_run_id=runtime_run_id,
                runtime_event_id=event_id,
                workbench_event_global_seq=event.workbench_event_global_seq,
            )
        workbench_event = self.workbench_store.append_runtime_public_event_by_ids(
            tenant_id="local",
            workspace_id=user.workspace_id,
            user_id=user.user_id,
            session_id=workbench_session_id,
            source_kind=_workbench_source_kind(event.source_id),
            payload=_public_event_payload(event),
        )
        updated = self.runtime_store.mark_event_projection_success(
            runtime_run_id=runtime_run_id,
            event_id=event_id,
            workbench_event_global_seq=workbench_event.global_seq,
            projected_at=projected_at or self.now(),
        )
        return WorkbenchEventProjection(
            runtime_run_id=runtime_run_id,
            runtime_event_id=event_id,
            workbench_event_global_seq=updated.workbench_event_global_seq or workbench_event.global_seq,
        )

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
    ) -> WorkbenchCandidateTruthProjection:
        run = self.runtime_store.get_run(runtime_run_id)
        workbench_session_id = self._workbench_session_id_for_projection(user=user, run=run)
        if workbench_session_id is None:
            if run.workbench_session_id is None:
                raise ValueError("workbench_session_missing")
            raise ValueError("runtime_link_broken")
        target_revision = finalization_revision if finalization_revision is not None else None
        if target_revision is not None and target_revision.revision != revision:
            raise ValueError("runtime_candidate_finalization_revision_mismatch")
        if target_revision is None:
            revisions = self.runtime_store.list_candidate_finalization_revisions(runtime_run_id=runtime_run_id)
            target_revision = next((item for item in revisions if item.revision == revision), None)
        if target_revision is None:
            raise ValueError("runtime_candidate_finalization_revision_missing")
        resolved_identities = (
            list(identities)
            if identities is not None
            else self.runtime_store.list_candidate_identities(runtime_run_id=runtime_run_id)
        )
        resolved_evidence = (
            list(evidence)
            if evidence is not None
            else self.runtime_store.list_candidate_evidence(runtime_run_id=runtime_run_id)
        )
        projected_ref = self.workbench_store.persist_runtime_candidate_truth_from_control(
            user=user,
            session_id=workbench_session_id,
            runtime_run_id=runtime_run_id,
            identities=resolved_identities,
            evidence=resolved_evidence,
            finalization_revision=target_revision,
            projected_at=projected_at or self.now(),
        )
        return WorkbenchCandidateTruthProjection(
            runtime_run_id=runtime_run_id,
            revision=revision,
            projected_ref=projected_ref,
        )

    def _workbench_session_id_for_projection(
        self,
        *,
        user: WorkbenchUser,
        run: RuntimeRunRecord,
    ) -> str | None:
        if run.workbench_session_id is not None:
            session = self.workbench_store.get_workbench_session(user=user, session_id=run.workbench_session_id)
            if session is not None:
                return session.session_id
        session = self.workbench_store.get_workbench_session_by_runtime_run_id(
            user=user,
            runtime_run_id=run.runtime_run_id,
        )
        if session is None:
            return None
        linked = self.runtime_store.link_workbench_session(
            runtime_run_id=run.runtime_run_id,
            workbench_session_id=session.session_id,
            updated_at=self.now(),
        )
        return linked.workbench_session_id


def _public_event_payload(event: RuntimeControlEvent) -> dict[str, object]:
    return {
        "schemaVersion": PUBLIC_EVENT_SCHEMA_VERSION,
        "runtimeRunId": event.runtime_run_id,
        "eventId": event.event_id,
        "eventSeq": event.event_seq,
        "stage": _public_stage(event),
        "roundNo": event.round_no,
        "sourceKind": _workbench_source_kind(event.source_id),
        "status": event.status,
        "counts": event.payload.get("counts") if isinstance(event.payload.get("counts"), dict) else {},
        "details": event.payload.get("details") if isinstance(event.payload.get("details"), dict) else {},
        "safeReasonCode": event.payload.get("safeReasonCode"),
        "createdAt": event.created_at,
    }


def _public_stage(event: RuntimeControlEvent) -> str:
    if event.stage in {
        "round_query",
        "source_dispatch",
        "source_result",
        "merge",
        "scoring",
        "feedback",
        "finalization",
    }:
        return event.stage
    if "source" in event.event_type:
        return "source_result"
    if "scoring" in event.event_type or "score" in event.event_type:
        return "scoring"
    if "final" in event.event_type or event.event_type == "runtime_run_completed":
        return "finalization"
    return "round_query"


def _workbench_source_kinds(source_ids: list[str]) -> list[Literal["cts", "liepin"]]:
    supported = [_workbench_source_kind(source_id) for source_id in source_ids]
    return [source for source in supported if source is not None]


def _workbench_source_kind(value: object) -> Literal["cts", "liepin"] | None:
    if value == "cts":
        return "cts"
    if value == "liepin":
        return "liepin"
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
