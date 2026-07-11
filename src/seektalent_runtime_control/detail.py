from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from seektalent_runtime_control.events import public_event_payload
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeControlEvent, RuntimeDetailResponse, RuntimeFinalSummary
from seektalent_runtime_control.store import RuntimeControlStore


_TERMINAL_RUN_STATUSES = {"cancelled", "completed", "failed"}
_SAFE_ARTIFACT_VISIBILITIES = {"public", "safe", "workbench_visible"}
_PUBLIC_DETAIL_STAGE_LABELS = {
    "round_query": "查询策略",
    "source_dispatch": "候选人检索",
    "source_result": "候选人检索结果",
    "merge": "候选人合并",
    "scoring": "候选人评分",
    "feedback": "检索复盘",
    "finalization": "最终候选人",
}
_PUBLIC_DETAIL_STATUS_LABELS = {
    "pending": "待处理",
    "running": "进行中",
    "completed": "已完成",
    "partial": "部分完成",
    "blocked": "受阻",
    "failed": "未完成",
    "cancelled": "已取消",
}
_PUBLIC_DETAIL_COUNT_LABELS = {
    "roundReturned": "本轮返回简历",
    "roundIdentities": "本轮新增候选人",
    "sourceCumulativeReturned": "来源累计返回简历",
    "sourceCumulativeIdentities": "来源累计候选人",
    "roundUniqueIdentities": "本轮去重候选人",
    "mergedIdentities": "合并候选人",
    "topPoolCount": "Top Pool 候选人",
    "selectedIdentityCount": "最终入选候选人",
    "feedbackCandidateCount": "复盘候选人",
}
_PUBLIC_DETAIL_TEXT_LABELS = {
    "resumeQualityComment": "简历质量",
    "reflectionSummary": "Reflection",
    "suggestedStopReason": "建议停止原因",
    "finalizationReasonCode": "最终候选人原因",
}
_PUBLIC_DETAIL_LIST_LABELS = {
    "suggestedActivateTerms": "建议激活关键词",
    "suggestedAddFilterFields": "建议添加筛选条件",
    "suggestedDeprioritizeTerms": "建议降低优先级关键词",
    "suggestedDropFilterFields": "建议移除筛选条件",
    "suggestedDropTerms": "建议移除关键词",
    "suggestedKeepFilterFields": "建议保留筛选条件",
    "suggestedKeepTerms": "建议保留关键词",
}


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
        events = self.store.list_public_events(runtime_run_id=runtime_run_id, after_seq=0, limit=500).events
        selected = _select_public_event(
            events,
            kind=kind,
            round_no=round_no,
            event_id=event_id,
            command_id=command_id,
        )
        if selected is None:
            return RuntimeDetailResponse(
                kind=kind,
                runtime_run_id=runtime_run_id,
                title="Detail unavailable",
                summary="No persisted runtime event matched the detail request.",
                reason_code="runtime_event_not_found",
            )
        _, public_payload = selected
        source_event_id = _public_text(public_payload, "eventId")
        if source_event_id is None:
            return RuntimeDetailResponse(
                kind=kind,
                runtime_run_id=runtime_run_id,
                title="Detail unavailable",
                summary="No persisted runtime event matched the detail request.",
                reason_code="runtime_event_not_found",
            )
        return RuntimeDetailResponse(
            kind=kind,
            runtime_run_id=runtime_run_id,
            title=_public_detail_title(public_payload),
            summary=_public_detail_summary(public_payload),
            facts=_public_detail_facts(public_payload, source_event_id=source_event_id),
            source_event_ids=[source_event_id],
            artifact_refs=_safe_artifact_refs(public_payload, include_artifacts=include_artifacts),
        )

    def prepare_final_summary(
        self,
        *,
        runtime_run_id: str,
        user_instruction: str | None,
        source_snapshot_event_seq: int,
        idempotency_key: str,
    ) -> RuntimeFinalSummary:
        del user_instruction
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
                user_instruction=None,
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
            summary=_summary_text(run_status=run.status, facts=facts),
            facts=facts,
            source_event_ids=[event.event_id for event in events],
            source_snapshot_event_seq=source_snapshot_event_seq,
            latest_snapshot_event_seq=latest_snapshot_event_seq,
            user_instruction=None,
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


def _select_public_event(
    events: list[RuntimeControlEvent],
    *,
    kind: str,
    round_no: int | None,
    event_id: str | None,
    command_id: str | None,
) -> tuple[RuntimeControlEvent, dict[str, object]] | None:
    public_events = [
        (event, payload)
        for event in events
        if (payload := public_event_payload(event)) is not None
    ]
    if event_id is not None:
        return next(((event, payload) for event, payload in public_events if event.event_id == event_id), None)
    candidates = [(event, payload) for event, payload in public_events if _public_event_matches_kind(payload, kind)]
    if round_no is not None:
        candidates = [
            (event, payload)
            for event, payload in candidates
            if payload.get("roundNo") == round_no
        ]
    if command_id is not None:
        candidates = []
    return candidates[-1] if candidates else None


def _public_event_matches_kind(payload: dict[str, object], kind: str) -> bool:
    stage = payload.get("stage")
    if kind == "reflection":
        return stage == "feedback"
    if kind == "command":
        return False
    if kind == "round_query":
        return stage == "round_query"
    if kind == "source_result":
        return stage == "source_result"
    if kind == "candidate_score":
        return stage == "scoring"
    if kind == "final_candidate":
        return stage == "finalization"
    return stage == kind


def _public_detail_title(payload: dict[str, object]) -> str:
    stage = _public_text(payload, "stage")
    label = _PUBLIC_DETAIL_STAGE_LABELS.get(stage, "运行详情") if stage is not None else "运行详情"
    round_no = payload.get("roundNo")
    if isinstance(round_no, int) and not isinstance(round_no, bool):
        return f"{label}（第 {round_no} 轮）"
    return label


def _public_detail_summary(payload: dict[str, object]) -> str:
    stage = _public_text(payload, "stage")
    status = _public_text(payload, "status")
    label = _PUBLIC_DETAIL_STAGE_LABELS.get(stage, "招聘流程状态") if stage is not None else "招聘流程状态"
    status_label = _PUBLIC_DETAIL_STATUS_LABELS.get(status, "已更新") if status is not None else "已更新"
    round_no = payload.get("roundNo")
    round_prefix = f"第 {round_no} 轮" if isinstance(round_no, int) and not isinstance(round_no, bool) else "本轮"
    return f"{round_prefix}{label}{status_label}。"


def _public_detail_facts(payload: dict[str, object], *, source_event_id: str) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    counts = _string_key_dict(payload.get("counts"))
    for key, label in _PUBLIC_DETAIL_COUNT_LABELS.items():
        value = counts.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            facts.append({"label": label, "value": value, "sourceEventId": source_event_id})

    details = _string_key_dict(payload.get("details"))
    for key, label in _PUBLIC_DETAIL_TEXT_LABELS.items():
        value = details.get(key)
        if isinstance(value, str):
            facts.append({"label": label, "value": value, "sourceEventId": source_event_id})
    for key, label in _PUBLIC_DETAIL_LIST_LABELS.items():
        values = details.get(key)
        if isinstance(values, list):
            text_values = [value for value in values if isinstance(value, str)]
            if len(text_values) == len(values):
                facts.append({"label": label, "value": "、".join(text_values), "sourceEventId": source_event_id})
    suggest_stop = details.get("suggestStop")
    if isinstance(suggest_stop, bool):
        facts.append({"label": "建议停止", "value": "是" if suggest_stop else "否", "sourceEventId": source_event_id})
    finalization_revision = details.get("finalizationRevision")
    if isinstance(finalization_revision, int) and not isinstance(finalization_revision, bool):
        facts.append({"label": "最终候选人版本", "value": finalization_revision, "sourceEventId": source_event_id})
    query_groups = details.get("queryGroups")
    if isinstance(query_groups, list):
        facts.append({"label": "关键词组", "value": len(query_groups), "sourceEventId": source_event_id})
    return facts


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
            key: value
            for key in ("artifactRefId", "safeUri", "visibility", "artifactKind")
            if isinstance(value := ref.get(key), str)
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


def _summary_text(*, run_status: str, facts: list[dict[str, object]]) -> str:
    fact_text = "; ".join(str(fact["value"]) for fact in facts)
    return " ".join(part for part in [f"Run status: {run_status}.", fact_text] if part)


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


def _public_text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None
