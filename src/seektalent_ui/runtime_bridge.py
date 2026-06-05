from __future__ import annotations

from collections.abc import Callable, Sequence
import inspect
from typing import Any

from seektalent.config import AppSettings
from seektalent.models import QueryTermCandidate, RequirementSheet
from seektalent.progress import ProgressCallback
from seektalent.providers.liepin.client import LiepinWorkerClient
from seektalent.runtime.source_lanes import RuntimeApprovedDetailLease, RuntimeSourceLaneRequest
from seektalent_ui.workbench_store import (
    LIEPIN_DAILY_DETAIL_OPEN_LIMIT,
    WorkbenchLiepinDetailOpenJobContext,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchStore,
)


RuntimeFactory = Callable[[AppSettings], object]


def extract_requirement_review(
    *,
    session,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> RequirementSheet:
    runtime = runtime_factory(settings)
    extractor = getattr(runtime, "extract_requirements", None)
    if extractor is None:
        raise RuntimeError("Runtime does not support requirement extraction.")
    extract_kwargs: dict[str, object] = {
        "job_title": session.job_title,
        "jd": session.jd_text,
        "notes": session.notes,
        "progress_callback": progress_callback,
    }
    if _callable_accepts_keyword(extractor, "requirement_cache_scope"):
        extract_kwargs["requirement_cache_scope"] = session.session_id
    return extractor(**extract_kwargs)


def run_runtime_sourcing_job(
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> None:
    runtime = runtime_factory(settings)
    run_method = getattr(runtime, "run", None)
    if not callable(run_method):
        raise RuntimeError("Runtime does not support Workbench sourcing jobs.")
    approved_requirement_sheet = _approved_requirement_sheet(context)
    source_run_ids = set(context.job.source_run_ids)
    source_kinds = set(context.job.source_kinds)

    def source_run_is_in_scope(source_run) -> bool:
        return source_run.source_run_id in source_run_ids or (
            not source_run_ids and source_run.source_kind in source_kinds
        )

    runnable_source_kinds = tuple(
        source_run.source_kind
        for source_run in context.session.source_runs
        if source_run_is_in_scope(source_run) and source_run.status != "blocked"
    )
    if not runnable_source_kinds:
        raise RuntimeError("selected_source_blocked")
    run_kwargs: dict[str, object] = {
        "job_title": context.session.job_title,
        "jd": context.session.jd_text,
        "notes": context.session.notes,
        "approved_requirement_sheet": approved_requirement_sheet,
        "progress_callback": progress_callback,
        "source_kinds": runnable_source_kinds,
        "requirement_cache_scope": context.session.session_id,
    }
    if _runtime_run_accepts_start_callback(run_method):
        run_kwargs["runtime_start_callback"] = lambda run_id: store.attach_runtime_sourcing_job_runtime_run_id(
            context=context,
            runtime_run_id=run_id,
        )
    if _callable_accepts_keyword(run_method, "runtime_checkpoint_callback"):
        run_kwargs["runtime_checkpoint_callback"] = lambda artifacts: store.refresh_runtime_candidate_index_with_artifacts(
            context=context,
            artifacts=artifacts,
        )
    connection = store.get_liepin_source_connection_for_job_context(context=context)
    if connection is not None and "liepin" in runnable_source_kinds:
        run_kwargs["source_context"] = {
            "tenant_id": "local",
            "workspace_id": context.session.workspace_id,
            "actor_id": context.session.owner_user_id,
            "connection_id": connection.connection_id,
            "compliance_gate_ref": connection.compliance_gate_ref,
            "provider_account_hash": connection.provider_account_hash,
        }
        run_kwargs["liepin_posture"] = {
            "connection_status": connection.status,
            "auth_state": "connected" if connection.status == "connected" else "login_required",
        }
    if not _callable_accepts_keyword(run_method, "source_kinds"):
        run_kwargs.pop("source_kinds", None)
    if not _callable_accepts_keyword(run_method, "requirement_cache_scope"):
        run_kwargs.pop("requirement_cache_scope", None)
    if not _callable_accepts_keyword(run_method, "source_context"):
        run_kwargs.pop("source_context", None)
    if not _callable_accepts_keyword(run_method, "liepin_posture"):
        run_kwargs.pop("liepin_posture", None)
    if not _callable_accepts_keyword(run_method, "runtime_checkpoint_callback"):
        run_kwargs.pop("runtime_checkpoint_callback", None)
    artifacts = run_method(**run_kwargs)
    store.reconcile_runtime_public_events_from_artifacts(context=context, artifacts=artifacts)
    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)


def run_liepin_detail_open_intent(
    *,
    context: WorkbenchLiepinDetailOpenJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    worker_client: LiepinWorkerClient | None = None,
) -> None:
    runtime = runtime_factory(settings)
    run_source_lane = getattr(runtime, "run_source_lane", None)
    if not callable(run_source_lane):
        raise RuntimeError("Runtime does not support source lane runs.")
    runtime_run_id = context.runtime_run_id or f"{context.intent_id}:runtime"
    source_plan_id = f"{runtime_run_id}:source:liepin:detail"
    source_lane_run_id = f"{source_plan_id}:request:{context.request_id}"
    approved_requirement_sheet = _approved_requirement_sheet(context)
    result = run_source_lane(
        RuntimeSourceLaneRequest(
            source="liepin",
            lane_mode="detail",
            job_title=context.session.job_title,
            jd=context.session.jd_text,
            notes=context.session.notes,
            requirement_sheet=approved_requirement_sheet,
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            source_query_terms=tuple(
                _requirement_query_terms(approved_requirement_sheet, fallback_job_title=context.session.job_title)
            ),
            approved_detail_lease=RuntimeApprovedDetailLease(
                lease_ref=f"lease://workbench/{context.ledger_id}",
                lease_id=context.ledger_id,
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                source_evidence_id=context.candidate_evidence_id,
                request_id=context.request_id,
                ledger_id=context.ledger_id,
                candidate_evidence_id=context.candidate_evidence_id,
                candidate_resume_id=context.candidate_resume_id,
                provider_candidate_key_hash=context.provider_candidate_key_hash,
                connection_id=context.connection_id,
                compliance_gate_ref=context.compliance_gate_ref,
                provider_account_hash=context.provider_account_hash,
                detail_candidates_json=context.detail_candidates_json,
                daily_budget=LIEPIN_DAILY_DETAIL_OPEN_LIMIT,
                budget_date=context.budget_day,
                provider_day_key=f"liepin:{context.provider_account_hash}:{context.budget_day}",
                timezone="Asia/Shanghai",
                open_policy_version="detail-policy-v1",
                expires_at=context.lease_expires_at,
            ),
            source_context={
                "tenant_id": "local",
                "workspace_id": context.session.workspace_id,
                "actor_id": context.session.owner_user_id,
                "connection_id": context.connection_id,
                "compliance_gate_ref": context.compliance_gate_ref,
                "provider_account_hash": context.provider_account_hash,
            },
        ),
        source_client=worker_client,
    )
    store.complete_liepin_detail_open_intent_with_lane_result(context=context, result=result)


def _approved_requirement_sheet(
    context: WorkbenchRuntimeSourcingJobContext | WorkbenchLiepinDetailOpenJobContext,
) -> RequirementSheet:
    sheet = context.requirement_review.requirement_sheet
    if sheet is None:
        raise PermissionError("requirement_review_empty")
    return sheet


def _requirement_query_terms(requirement_sheet: RequirementSheet, *, fallback_job_title: str) -> list[str]:
    terms: list[object] = [
        *requirement_sheet.initial_query_term_pool,
        *requirement_sheet.title_anchor_terms,
        *requirement_sheet.must_have_capabilities,
        *requirement_sheet.preferences.preferred_query_terms,
        fallback_job_title,
    ]
    values: list[str] = []
    for term in terms:
        if isinstance(term, QueryTermCandidate):
            values.append(term.term)
        elif isinstance(term, str):
            values.append(term)
        else:
            term_value = _object_attr(term, "term")
            if isinstance(term_value, str):
                values.append(term_value)
    return _unique_bounded_strings(values, max_items=8) or [fallback_job_title]


def _runtime_run_accepts_start_callback(run_method: Callable[..., object]) -> bool:
    try:
        signature = inspect.signature(run_method)
    except (AttributeError, TypeError, ValueError):
        return False
    parameters = signature.parameters
    if "runtime_start_callback" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def _callable_accepts_keyword(callable_object: Callable[..., object], keyword: str) -> bool:
    try:
        signature = inspect.signature(callable_object)
    except (TypeError, ValueError):
        return False
    parameters = signature.parameters
    if keyword in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def _object_attr(value: object, attr: str) -> Any:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}.get(attr)
    return getattr(value, attr, None)


def _unique_bounded_strings(values: Sequence[object], *, max_items: int = 20, max_chars: int = 180) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
        if len(results) >= max_items:
            break
    return results
