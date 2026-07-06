from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from seektalent.dev_mode import DevModeStatus
from seektalent.source_adapters import public_source_reason_code
from seektalent_ui.candidate_identity import public_identity_id
from seektalent_ui.models import (
    WorkbenchCandidateEvidenceResponse,
    WorkbenchCandidateReviewItemResponse,
    WorkbenchDetailOpenCandidateSnapshotResponse,
    WorkbenchDetailOpenLedgerResponse,
    WorkbenchDetailOpenRequestResponse,
    WorkbenchDevModeComponentResponse,
    WorkbenchDevModeDataRootPostureResponse,
    WorkbenchDevModeDataRootResponse,
    WorkbenchDevModeStatusResponse,
    WorkbenchFinalTopCandidateEvidenceResponse,
    WorkbenchFinalTopCandidateResponse,
    WorkbenchGraphCandidateSummaryResponse,
    WorkbenchProviderActionResponse,
    WorkbenchRequirementReviewResponse,
    WorkbenchRuntimeSourceStateResponse,
    WorkbenchRuntimeSourcingJobResponse,
    WorkbenchSecurityAuditEventResponse,
    WorkbenchSecurityAuditMetadataResponse,
    WorkbenchSessionResponse,
    WorkbenchSessionStartBlockedSourceResponse,
    WorkbenchSourceCardResponse,
    WorkbenchSourceConnectionResponse,
    WorkbenchSourceRunPolicyResponse,
    WorkbenchSourceRunResponse,
    WorkbenchSourceStatus,
)
from seektalent_ui.workbench_store import (
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    RuntimeSourceCountProjection,
    WorkbenchCandidateEvidence,
    WorkbenchCandidateReviewItem,
    WorkbenchDetailOpenCandidateSnapshot,
    WorkbenchDetailOpenLedger,
    WorkbenchDetailOpenRequest,
    WorkbenchProviderAction,
    WorkbenchRequirementReview,
    WorkbenchRuntimeSourcingJob,
    WorkbenchSecurityAuditEvent,
    WorkbenchSession,
    WorkbenchSourceConnection,
    WorkbenchSourceRun,
    WorkbenchSourceRunPolicy,
)


SOURCE_CARD_STATUSES: dict[str, WorkbenchSourceStatus] = {
    "queued": "queued",
    "running": "running",
    "completed": "completed",
    "partial": "partial",
    "failed": "failed",
    "blocked": "blocked",
    "cancelled": "cancelled",
}


def session_response(
    session: WorkbenchSession,
    connections: dict[str, WorkbenchSourceConnection] | None = None,
    runtime_source_state: WorkbenchRuntimeSourceStateResponse | None = None,
    runtime_source_count_projection: Mapping[Literal["cts", "liepin"], RuntimeSourceCountProjection] | None = None,
    liepin_setup_reason: str | None = None,
) -> WorkbenchSessionResponse:
    connections = connections or {}
    runtime_source_count_projection = runtime_source_count_projection or {}
    source_runs = [source_run_response(source_run) for source_run in session.source_runs]
    source_cards = [
        source_card_response(
            source_run,
            connections.get(source_run.source_kind),
            runtime_source_count_projection.get(source_run.source_kind),
            liepin_setup_reason=liepin_setup_reason,
        )
        for source_run in session.source_runs
    ]
    return WorkbenchSessionResponse(
        sessionId=session.session_id,
        workspaceId=session.workspace_id,
        ownerUserId=session.owner_user_id,
        jobTitle=session.job_title,
        jdText=session.jd_text,
        notes=session.notes,
        status=session.status,
        requirement_review=requirement_review_response(session.requirement_review),
        sourceRuns=source_runs,
        sourceCards=source_cards,
        runtimeSourceState=runtime_source_state,
    )


def source_run_response(source_run: WorkbenchSourceRun) -> WorkbenchSourceRunResponse:
    return WorkbenchSourceRunResponse(
        sourceRunId=source_run.source_run_id,
        sourceKind=source_run.source_kind,
        status=source_run.status,
        authState=source_run.auth_state,
        warningCode=public_runtime_source_reason_code(source_run.warning_code),
        warningMessage=source_run.warning_message,
        cardsScannedCount=source_run.cards_scanned_count,
        uniqueCandidatesCount=source_run.unique_candidates_count,
        detailOpenUsedCount=source_run.detail_open_used_count,
        detailOpenBlockedCount=source_run.detail_open_blocked_count,
    )


def source_card_response(
    source_run: WorkbenchSourceRun,
    connection: WorkbenchSourceConnection | None = None,
    runtime_count_projection: RuntimeSourceCountProjection | None = None,
    *,
    liepin_setup_reason: str | None = None,
) -> WorkbenchSourceCardResponse:
    warning_code = source_run.warning_code
    warning_message = source_run.warning_message
    status = source_run.status
    cards_scanned_count = source_run.cards_scanned_count
    unique_candidates_count = source_run.unique_candidates_count
    has_runtime_status_projection = False
    if runtime_count_projection is not None:
        runtime_status = SOURCE_CARD_STATUSES.get(runtime_count_projection.status or "")
        if runtime_status is not None:
            status = runtime_status
            has_runtime_status_projection = True
            warning_code = runtime_count_projection.warning_code
            warning_message = (
                source_runtime_warning_message(runtime_count_projection.warning_code)
                if runtime_count_projection.warning_code is not None
                else None
            )
        if runtime_count_projection.cards_scanned_count is not None:
            cards_scanned_count = runtime_count_projection.cards_scanned_count
        if runtime_count_projection.unique_candidates_count is not None:
            unique_candidates_count = runtime_count_projection.unique_candidates_count
    if (
        source_run.source_kind == "liepin"
        and liepin_setup_reason is not None
        and not has_runtime_status_projection
        and warning_code in {None, "login_required", LIEPIN_BROWSER_LOGIN_REQUIRED_CODE}
    ):
        warning_code = liepin_setup_reason
        warning_message = liepin_start_probe_warning_message(liepin_setup_reason)
    return WorkbenchSourceCardResponse(
        sourceRunId=source_run.source_run_id,
        sourceKind=source_run.source_kind,
        label="CTS" if source_run.source_kind == "cts" else "Liepin",
        status=status,
        authState=source_run.auth_state,
        warningCode=public_runtime_source_reason_code(warning_code),
        warningMessage=warning_message,
        cardsScannedCount=cards_scanned_count,
        uniqueCandidatesCount=unique_candidates_count,
        detailOpenUsedCount=source_run.detail_open_used_count,
        detailOpenBlockedCount=source_run.detail_open_blocked_count,
        connectionId=connection.connection_id if connection is not None else None,
        connectionStatus=connection.status if connection is not None else None,
        connectionWarningCode=public_runtime_source_reason_code(connection.warning_code)
        if connection is not None
        else None,
        connectionWarningMessage=connection.warning_message if connection is not None else None,
    )


def source_connection_response(connection: WorkbenchSourceConnection) -> WorkbenchSourceConnectionResponse:
    return WorkbenchSourceConnectionResponse(
        connectionId=connection.connection_id,
        sourceKind=connection.source_kind,
        label="Liepin",
        status=connection.status,
        warningCode=public_runtime_source_reason_code(connection.warning_code),
        warningMessage=connection.warning_message,
        createdAt=connection.created_at,
        updatedAt=connection.updated_at,
        connectedAt=connection.connected_at,
    )


def source_run_policy_response(policy: WorkbenchSourceRunPolicy) -> WorkbenchSourceRunPolicyResponse:
    return WorkbenchSourceRunPolicyResponse(
        sessionId=policy.session_id,
        sourceKind=policy.source_kind,
        detailOpenMode=policy.detail_open_mode,
        updatedAt=policy.updated_at,
    )


def detail_open_request_response(
    detail_request: WorkbenchDetailOpenRequest,
) -> WorkbenchDetailOpenRequestResponse:
    return WorkbenchDetailOpenRequestResponse(
        requestId=detail_request.request_id,
        sessionId=detail_request.session_id,
        reviewItemId=detail_request.review_item_id,
        status=detail_request.status,
        detailOpenMode=detail_request.detail_open_mode,
        decisionNote=detail_request.decision_note,
        candidate=(
            detail_open_candidate_snapshot_response(detail_request.candidate)
            if detail_request.candidate is not None
            else None
        ),
        blockedReason=detail_request.blocked_reason,
        ledger=detail_open_ledger_response(detail_request.ledger) if detail_request.ledger is not None else None,
        providerAction=(
            provider_action_response(detail_request.provider_action)
            if detail_request.provider_action is not None
            else None
        ),
        createdAt=detail_request.created_at,
        updatedAt=detail_request.updated_at,
    )


def detail_open_candidate_snapshot_response(
    candidate: WorkbenchDetailOpenCandidateSnapshot,
) -> WorkbenchDetailOpenCandidateSnapshotResponse:
    return WorkbenchDetailOpenCandidateSnapshotResponse(
        reviewItemId=candidate.review_item_id,
        displayName=candidate.display_name,
        title=candidate.title,
        company=candidate.company,
        location=candidate.location,
        summary=candidate.summary,
        aggregateScore=candidate.aggregate_score,
        evidenceLevel=candidate.evidence_level,
        sourceBadges=candidate.source_badges,
        matchedMustHaves=candidate.matched_must_haves,
        matchedPreferences=candidate.matched_preferences,
        missingRisks=candidate.missing_risks,
    )


def detail_open_ledger_response(ledger: WorkbenchDetailOpenLedger) -> WorkbenchDetailOpenLedgerResponse:
    return WorkbenchDetailOpenLedgerResponse(
        ledgerId=ledger.ledger_id,
        status=ledger.status,
        budgetDay=ledger.budget_day,
        leaseExpiresAt=ledger.lease_expires_at,
    )


def provider_action_response(action: WorkbenchProviderAction) -> WorkbenchProviderActionResponse:
    return WorkbenchProviderActionResponse(
        actionKind=action.action_kind,
        sourceKind=action.source_kind,
        connectionId=action.connection_id,
        reviewItemId=action.review_item_id,
        budgetImpact=action.budget_impact,
        message=action.message,
    )


def requirement_review_response(review: WorkbenchRequirementReview) -> WorkbenchRequirementReviewResponse:
    return WorkbenchRequirementReviewResponse(
        session_id=review.session_id,
        status=review.status,
        requirement_sheet=review.requirement_sheet,
        created_at=review.created_at,
        updated_at=review.updated_at,
        approved_at=review.approved_at,
    )


def dev_mode_status_response(payload: DevModeStatus) -> WorkbenchDevModeStatusResponse:
    component_responses = [
        WorkbenchDevModeComponentResponse(
            name=item.name,
            label=item.label,
            status=item.status,
            reasonCode=item.reasonCode,
            authNote=item.authNote,
        )
        for item in payload.components
    ]
    components_by_name = {item.name: item for item in component_responses}
    credential_names = {"text_llm", "cts", "liepin_account_binding_secret"}
    source_names = {
        "liepin_worker_mode",
        "liepin_opencli_browser",
    }
    roots = {
        item.name: WorkbenchDevModeDataRootResponse(
            name=item.name,
            label=item.label,
            status=item.status,
            reasonCode=item.reasonCode,
        )
        for item in payload.dataRoots
    }
    data_root_status = "safe"
    if any(root.status == "error" for root in roots.values()):
        data_root_status = "error"
    elif any(root.status == "warning" for root in roots.values()):
        data_root_status = "warning"
    elif any(root.status == "unknown" for root in roots.values()):
        data_root_status = "unknown"
    return WorkbenchDevModeStatusResponse(
        mode=payload.mode,
        overallStatus=payload.overallStatus,
        components=component_responses,
        credentials={name: item for name, item in components_by_name.items() if name in credential_names},
        sources={name: item for name, item in components_by_name.items() if name in source_names},
        dataRoots=WorkbenchDevModeDataRootPostureResponse(status=data_root_status, roots=roots),
    )


def candidate_review_item_response(
    item: WorkbenchCandidateReviewItem,
    graph_candidate: WorkbenchGraphCandidateSummaryResponse | None = None,
) -> WorkbenchCandidateReviewItemResponse:
    return WorkbenchCandidateReviewItemResponse(
        reviewItemId=item.review_item_id,
        sessionId=item.session_id,
        graphCandidateId=graph_candidate.graphCandidateId if graph_candidate is not None else None,
        canExpandResume=bool(graph_candidate is not None and graph_candidate.canExpandResume),
        status=item.status,
        note=item.note,
        displayName=item.display_name,
        title=item.title,
        company=item.company,
        location=item.location,
        summary=item.summary,
        aggregateScore=item.aggregate_score,
        fitBucket=item.fit_bucket,
        sourceBadges=item.source_badges,
        evidenceLevel=item.evidence_level,
        matchedMustHaves=item.matched_must_haves,
        matchedPreferences=item.matched_preferences,
        missingRisks=item.missing_risks,
        strengths=item.strengths,
        weaknesses=item.weaknesses,
        evidence=[candidate_evidence_response(evidence) for evidence in item.evidence],
        createdAt=item.created_at,
        updatedAt=item.updated_at,
    )


def candidate_evidence_response(evidence: WorkbenchCandidateEvidence) -> WorkbenchCandidateEvidenceResponse:
    return WorkbenchCandidateEvidenceResponse(
        evidenceId=evidence.evidence_id,
        sourceRunId=evidence.source_run_id,
        sourceKind=evidence.source_kind,
        evidenceLevel=evidence.evidence_level,
        score=evidence.score,
        fitBucket=evidence.fit_bucket,
        matchedMustHaves=evidence.matched_must_haves,
        matchedPreferences=evidence.matched_preferences,
        missingRisks=evidence.missing_risks,
        strengths=evidence.strengths,
        weaknesses=evidence.weaknesses,
        createdAt=evidence.created_at,
    )


def runtime_final_top_candidate_response(
    item: WorkbenchCandidateReviewItem,
    *,
    rank: int,
) -> WorkbenchFinalTopCandidateResponse:
    identity_id = next((evidence.runtime_identity_id for evidence in item.evidence if evidence.runtime_identity_id), None)
    return WorkbenchFinalTopCandidateResponse(
        reviewItemId=item.review_item_id,
        runtimeIdentityId=public_identity_id(f"identity:{identity_id}") if identity_id else item.review_item_id,
        canonicalReviewItemId=item.review_item_id,
        mergedReviewItemIds=[item.review_item_id],
        rank=rank,
        displayName=item.display_name,
        title=item.title,
        company=item.company,
        location=item.location,
        summary=item.summary,
        aggregateScore=item.aggregate_score,
        fitBucket=item.fit_bucket,
        whySelected=item.why_selected or item.summary,
        riskFlags=item.missing_risks,
        matchedMustHaves=item.matched_must_haves,
        matchedPreferences=item.matched_preferences,
        strengths=item.strengths,
        weaknesses=item.weaknesses,
        sourceRound=item.source_round,
        sourceBadges=item.source_badges,
        evidenceLevel=item.evidence_level,
        sourceEvidence=[
            WorkbenchFinalTopCandidateEvidenceResponse(
                evidenceId=evidence.evidence_id,
                sourceRunId=evidence.source_run_id,
                sourceKind=evidence.source_kind,
                evidenceLevel=evidence.evidence_level,
                score=evidence.score,
                fitBucket=evidence.fit_bucket,
            )
            for evidence in item.evidence
        ],
    )


def security_audit_event_response(event: WorkbenchSecurityAuditEvent) -> WorkbenchSecurityAuditEventResponse:
    return WorkbenchSecurityAuditEventResponse(
        auditId=event.audit_id,
        actorUserId=event.actor_user_id,
        actorRole=event.actor_role,
        workspaceId=event.workspace_id,
        requestIp=event.request_ip,
        userAgent=event.user_agent,
        targetType=event.target_type,
        targetId=event.target_id,
        action=event.action,
        result=event.result,
        reasonCode=event.reason_code,
        metadata=_security_audit_metadata_response(event.metadata),
        createdAt=event.created_at,
    )


def _security_audit_metadata_response(metadata: dict[str, object]) -> WorkbenchSecurityAuditMetadataResponse:
    payload: dict[str, object] = {}
    for field in WorkbenchSecurityAuditMetadataResponse.model_fields:
        if field not in metadata:
            continue
        value = metadata[field]
        if value is None:
            continue
        if field == "excludedData":
            if isinstance(value, list | tuple):
                payload[field] = [str(item) for item in value if isinstance(item, str)]
            continue
        if isinstance(value, str | int | float | bool):
            payload[field] = value
    return WorkbenchSecurityAuditMetadataResponse.model_validate(payload)


def runtime_sourcing_job_response(job: WorkbenchRuntimeSourcingJob) -> WorkbenchRuntimeSourcingJobResponse:
    return WorkbenchRuntimeSourcingJobResponse(
        jobId=job.job_id,
        status=job.status,
        sourceKinds=list(job.source_kinds),
        attemptCount=job.attempt_count,
        errorMessage=job.error_message,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )


def session_start_blocked_sources(
    session: WorkbenchSessionResponse,
) -> list[WorkbenchSessionStartBlockedSourceResponse]:
    return [
        WorkbenchSessionStartBlockedSourceResponse(
            sourceRunId=source_run.sourceRunId,
            sourceKind=source_run.sourceKind,
            reason=public_runtime_source_reason_code(source_run.warningCode) or "source_provider_failed",
        )
        for source_run in session.sourceRuns
        if source_run.status == "blocked"
    ]


def public_runtime_source_reason_code(reason_code: str | None) -> str | None:
    return public_source_reason_code(reason_code)


def liepin_start_probe_warning_message(reason_code: str) -> str:
    if reason_code == LIEPIN_BROWSER_LOGIN_REQUIRED_CODE:
        return LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE
    if reason_code == LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE:
        return LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE
    if reason_code == "liepin_opencli_removed_config":
        return "检测到已移除的 Liepin OpenCLI 清理配置，请删除旧的 tab cleanup 设置后重试。"
    return LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE


def source_runtime_warning_message(reason_code: str) -> str | None:
    if reason_code == "source_login_required":
        return LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE
    if reason_code == "source_account_mismatch":
        return LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE
    if reason_code in {
        "source_browser_timeout",
        "source_browser_backend_unavailable",
        "source_browser_extension_disconnected",
        "source_browser_policy_blocked",
        "source_risk_or_verification_required",
        "source_browser_interaction_required",
    }:
        return LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE
    if reason_code == "source_budget_exhausted":
        return "当前来源的检索预算已用完，本轮已停止继续打开更多结果。"
    if reason_code in {"source_provider_failed", "source_partial", "source_unknown"}:
        return "当前来源检索未完整完成，请稍后重试或切换来源。"
    return None
