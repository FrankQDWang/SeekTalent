from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeControlEvent, RuntimeDetailResponse, RuntimeFinalSummary
from seektalent_runtime_control.store import RuntimeControlStore


_TERMINAL_RUN_STATUSES = {"cancelled", "completed", "failed"}
_SAFE_ARTIFACT_VISIBILITIES = {"public", "safe", "workbench_visible"}


class RuntimeDetailService:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        summary_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.summary_id_factory = summary_id_factory or (lambda: f"rtfinalsummary_{uuid4().hex}")
        self.now = now or _now

    def get_runtime_detail(
        self,
        *,
        runtime_run_id: str,
        kind: str,
        round_no: int | None = None,
        event_id: str | None = None,
        command_id: str | None = None,
        checkpoint_id: str | None = None,
        include_artifacts: bool = False,
    ) -> RuntimeDetailResponse:
        if kind == "checkpoint":
            return self._checkpoint_detail(
                runtime_run_id=runtime_run_id,
                checkpoint_id=checkpoint_id,
                include_artifacts=include_artifacts,
            )
        events = self.store.list_events(runtime_run_id=runtime_run_id, after_seq=0, limit=500).events
        event = _select_event(events, kind=kind, round_no=round_no, event_id=event_id, command_id=command_id)
        if event is None:
            return RuntimeDetailResponse(
                kind=kind,
                runtime_run_id=runtime_run_id,
                title="Detail unavailable",
                summary="No persisted runtime event matched the detail request.",
                reason_code="runtime_event_not_found",
            )
        facts = _event_facts(event)
        return RuntimeDetailResponse(
            kind=kind,
            runtime_run_id=runtime_run_id,
            title=_detail_title(kind, event),
            summary=event.summary,
            facts=facts,
            source_event_ids=[event.event_id],
            artifact_refs=_safe_artifact_refs(event.payload, include_artifacts=include_artifacts),
        )

    def prepare_final_summary(
        self,
        *,
        runtime_run_id: str,
        user_instruction: str | None,
        source_snapshot_event_seq: int,
        idempotency_key: str,
    ) -> RuntimeFinalSummary:
        run = self.store.get_run(runtime_run_id)
        if run.status not in _TERMINAL_RUN_STATUSES:
            raise RuntimeControlError("runtime_run_not_completed")
        snapshot = self.store.get_snapshot(runtime_run_id=runtime_run_id)
        latest_snapshot_event_seq = snapshot.latest_event_seq if snapshot is not None else run.latest_event_seq
        if source_snapshot_event_seq < latest_snapshot_event_seq:
            return RuntimeFinalSummary(
                runtime_run_id=runtime_run_id,
                status=run.status,
                summary="Snapshot cursor is stale.",
                source_snapshot_event_seq=source_snapshot_event_seq,
                latest_snapshot_event_seq=latest_snapshot_event_seq,
                user_instruction=user_instruction,
                reason_code="runtime_snapshot_stale",
            )
        existing = self.store.get_final_summary_by_idempotency(
            runtime_run_id=runtime_run_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing
        events = [
            event
            for event in self.store.list_events(runtime_run_id=runtime_run_id, after_seq=0, limit=source_snapshot_event_seq).events
            if event.event_seq <= source_snapshot_event_seq
        ]
        facts = _canonical_summary_facts(self.store, runtime_run_id=runtime_run_id) or _summary_facts(
            snapshot.snapshot if snapshot is not None else {},
            run_status=run.status,
        )
        summary = RuntimeFinalSummary(
            summary_id=self.summary_id_factory(),
            runtime_run_id=runtime_run_id,
            status=run.status,
            summary=_summary_text(run_status=run.status, facts=facts, user_instruction=user_instruction),
            facts=facts,
            source_event_ids=[event.event_id for event in events],
            source_snapshot_event_seq=source_snapshot_event_seq,
            latest_snapshot_event_seq=latest_snapshot_event_seq,
            user_instruction=user_instruction,
            created_at=self.now(),
        )
        return self.store.save_final_summary(summary, idempotency_key=idempotency_key)

    def _checkpoint_detail(
        self,
        *,
        runtime_run_id: str,
        checkpoint_id: str | None,
        include_artifacts: bool,
    ) -> RuntimeDetailResponse:
        if checkpoint_id is None:
            checkpoint = self.store.get_latest_checkpoint(runtime_run_id=runtime_run_id)
        else:
            checkpoint = self.store.get_checkpoint(runtime_run_id=runtime_run_id, checkpoint_id=checkpoint_id)
        if checkpoint is None:
            return RuntimeDetailResponse(
                kind="checkpoint",
                runtime_run_id=runtime_run_id,
                title="Checkpoint unavailable",
                summary="No persisted checkpoint matched the detail request.",
                reason_code="runtime_checkpoint_not_found",
            )
        facts: list[dict[str, object]] = [
            {"label": "Safe boundary", "value": checkpoint.safe_boundary, "checkpointId": checkpoint.checkpoint_id},
            {"label": "Stage", "value": checkpoint.stage, "checkpointId": checkpoint.checkpoint_id},
        ]
        return RuntimeDetailResponse(
            kind="checkpoint",
            runtime_run_id=runtime_run_id,
            title=f"Checkpoint {checkpoint.checkpoint_id}",
            summary=f"Checkpoint at {checkpoint.safe_boundary}.",
            facts=facts,
            checkpoint_ids=[checkpoint.checkpoint_id],
            artifact_refs=(
                [{"artifactManifestRef": checkpoint.artifact_manifest_ref}]
                if include_artifacts and checkpoint.artifact_manifest_ref is not None
                else []
            ),
        )


def _select_event(
    events: list[RuntimeControlEvent],
    *,
    kind: str,
    round_no: int | None,
    event_id: str | None,
    command_id: str | None,
) -> RuntimeControlEvent | None:
    if event_id is not None:
        return next((event for event in events if event.event_id == event_id), None)
    candidates = [event for event in events if _event_matches_kind(event, kind)]
    if round_no is not None:
        candidates = [event for event in candidates if event.round_no == round_no]
    if command_id is not None:
        candidates = [event for event in candidates if event.payload.get("commandId") == command_id]
    return candidates[-1] if candidates else None


def _event_matches_kind(event: RuntimeControlEvent, kind: str) -> bool:
    if kind == "reflection":
        return "reflection" in event.event_type
    if kind == "command":
        return event.event_type.startswith("runtime_command_")
    if kind == "round_query":
        return "query" in event.event_type or event.event_type == "runtime_round_input_locked"
    if kind == "source_result":
        return "source" in event.event_type
    if kind == "candidate_score":
        return "score" in event.event_type or "scoring" in event.event_type
    if kind == "final_candidate":
        return "final" in event.event_type and "candidate" in event.event_type
    return event.event_type == kind


def _event_facts(event: RuntimeControlEvent) -> list[dict[str, object]]:
    facts = event.payload.get("facts")
    if isinstance(facts, list) and facts:
        result: list[dict[str, object]] = []
        for raw_fact in facts:
            fact = _string_key_dict(raw_fact)
            if not fact:
                continue
            result.append(
                {
                    "label": str(fact.get("label") or "Fact"),
                    "value": str(fact.get("value") or ""),
                    "sourceEventId": event.event_id,
                }
            )
        if result:
            return result
    return [{"label": "Summary", "value": event.summary, "sourceEventId": event.event_id}]


def _safe_artifact_refs(payload: dict[str, object], *, include_artifacts: bool) -> list[dict[str, object]]:
    if not include_artifacts:
        return []
    refs = payload.get("artifactRefs")
    if not isinstance(refs, list):
        return []
    safe_refs: list[dict[str, object]] = []
    for raw_ref in refs:
        ref = _string_key_dict(raw_ref)
        if ref.get("visibility") not in _SAFE_ARTIFACT_VISIBILITIES:
            continue
        safe_ref: dict[str, object] = {
            key: ref[key]
            for key in ("artifactRefId", "safeUri", "visibility", "artifactKind")
            if key in ref
        }
        if safe_ref:
            safe_refs.append(safe_ref)
    return safe_refs


def _summary_facts(snapshot: dict[str, object], *, run_status: str) -> list[dict[str, object]]:
    candidates = snapshot.get("finalCandidates")
    if isinstance(candidates, list) and candidates:
        facts: list[dict[str, object]] = []
        for raw_candidate in candidates:
            candidate = _string_key_dict(raw_candidate)
            if not candidate:
                continue
            name = str(candidate.get("displayName") or candidate.get("candidateId") or "Candidate")
            rationale = str(candidate.get("rationale") or "").strip()
            value = f"{name}: {rationale}" if rationale else name
            facts.append({"label": "Candidate", "value": value})
        if facts:
            return facts
    return [{"label": "Run status", "value": run_status}]


def _canonical_summary_facts(store: RuntimeControlStore, *, runtime_run_id: str) -> list[dict[str, object]]:
    revisions = store.list_candidate_finalization_revisions(runtime_run_id=runtime_run_id)
    if not revisions:
        return []
    latest_revision = revisions[-1]
    identities = {
        identity.identity_id: identity
        for identity in store.list_candidate_identities(runtime_run_id=runtime_run_id)
    }
    facts: list[dict[str, object]] = []
    for identity_id in latest_revision.candidate_identity_ids:
        identity = identities.get(identity_id)
        if identity is None:
            continue
        name = identity.display_name or identity.canonical_resume_id or identity.identity_id
        summary = identity.summary.strip()
        value = f"{name}: {summary}" if summary else name
        facts.append(
            {
                "label": "Candidate",
                "value": value,
                "identityId": identity.identity_id,
                "revision": latest_revision.revision,
            }
        )
    return facts


def _summary_text(*, run_status: str, facts: list[dict[str, object]], user_instruction: str | None) -> str:
    fact_text = "; ".join(str(fact["value"]) for fact in facts)
    parts = [f"Run status: {run_status}.", fact_text]
    if user_instruction:
        parts.append(user_instruction)
    return " ".join(part for part in parts if part)


def _detail_title(kind: str, event: RuntimeControlEvent) -> str:
    if event.round_no is None:
        return kind.replace("_", " ").title()
    return f"{kind.replace('_', ' ').title()} Round {event.round_no}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}
