from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.providers.liepin.client import LiepinWorkerClient, build_liepin_worker_client
from seektalent.providers.liepin.policy import LiepinCardCandidate, build_detail_open_plan
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.providers.liepin.worker_contracts import OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT
from seektalent.source_adapters import build_source_enabled_runtime

WorkflowRuntime = build_source_enabled_runtime

_PUBLIC_LIEPIN_PIPELINE_REASON_CODES = {
    "job_lease_expired",
    "relay_pending_worker",
    "runtime_failed",
    "source_login_required",
    "source_account_mismatch",
    "source_browser_timeout",
    "source_browser_backend_unavailable",
    "source_browser_extension_disconnected",
    "source_browser_policy_blocked",
    "source_risk_or_verification_required",
    "source_browser_interaction_required",
    "source_budget_exhausted",
    "source_filter_applied",
    "source_filter_partial",
    "source_filter_unavailable",
    "source_filter_unsupported",
    "source_filter_degraded",
    "source_location_filter_unsupported",
    "source_age_filter_unsupported",
    "source_provider_failed",
    "source_partial",
    "source_unknown",
}
_LIEPIN_PIPELINE_REASON_MAP = {
    "blocked_backend_unavailable": "source_browser_backend_unavailable",
    "blocked_login_required": "source_login_required",
    "failed_provider_error": "source_provider_failed",
    "liepin_opencli_detail_not_opened": "source_browser_timeout",
    "login_required": "source_login_required",
    "partial_timeout": "source_browser_timeout",
    "runtime_failed": "source_provider_failed",
    "source_location_filter_partial": "source_filter_partial",
}


def liepin_smoke_command(args: argparse.Namespace) -> int:
    if not args.live:
        print("validation failed: liepin-smoke requires --live", file=sys.stderr)
        return 1
    missing = [
        option_name
        for option_name, attr_name in [
            ("tenant-id", "tenant_id"),
            ("workspace-id", "workspace_id"),
            ("actor-id", "actor_id"),
            ("connection-id", "connection_id"),
            ("compliance-gate-ref", "compliance_gate_ref"),
        ]
        if not getattr(args, attr_name)
    ]
    if missing:
        required = ", ".join(f"--{option_name}" for option_name in missing)
        print(f"validation failed: liepin-smoke --live requires {required}", file=sys.stderr)
        return 1
    if args.max_detail_opens < 0:
        print("validation failed: --max-detail-opens must be >= 0", file=sys.stderr)
        return 1
    keyword = args.keyword.strip()
    if not keyword:
        print("validation failed: --keyword must not be empty", file=sys.stderr)
        return 1
    if args.page_size <= 0:
        print("validation failed: --page-size must be > 0", file=sys.stderr)
        return 1
    if args.pipeline:
        if not args.job_title or not args.job_title.strip():
            print("validation failed: --pipeline requires --job-title", file=sys.stderr)
            return 1
        if not args.jd_file:
            print("validation failed: --pipeline requires --jd-file", file=sys.stderr)
            return 1
        if args.min_final_candidates < 1:
            print("validation failed: --min-final-candidates must be > 0", file=sys.stderr)
            return 1

    store = LiepinStore(_liepin_cli_db_path(args))
    gate = store.get_compliance_gate(
        gate_ref=args.compliance_gate_ref,
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
    )
    if gate is None:
        print("validation failed: gate not found", file=sys.stderr)
        return 1
    connection = store.get_connection(
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
        connection_id=args.connection_id,
    )
    if connection is None or connection.compliance_gate_ref != args.compliance_gate_ref:
        print("validation failed: connection does not belong to compliance gate", file=sys.stderr)
        return 1
    reason = gate.denial_reason(provider_account_hash=connection.provider_account_hash, purpose="search")
    if reason is not None:
        print(f"validation failed: {reason}", file=sys.stderr)
        return 1

    settings = _liepin_smoke_settings(args)
    if settings.liepin_worker_mode == "fake_fixture":
        print("validation failed: live smoke refuses fake fixture worker mode", file=sys.stderr)
        return 1

    print("compliance: approved")
    print(f"worker setup: {settings.liepin_worker_mode}")
    worker_events: list[tuple[str, dict[str, object]]] = []
    try:
        worker_client = build_liepin_worker_client(settings)
        session = asyncio.run(
            _liepin_smoke_worker_session(
                worker_client=worker_client,
                connection_id=args.connection_id,
                tenant_id=args.tenant_id,
                workspace_id=args.workspace_id,
                provider_account_hash=connection.provider_account_hash or "",
                worker_events=worker_events,
            )
        )
    except LiepinWorkerModeError as exc:
        _print_liepin_worker_events(worker_events)
        setup_status = exc.setup_status or "failed"
        print(f"validation failed: worker setup failed: {setup_status}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, TimeoutError, ValueError):
        _print_liepin_worker_events(worker_events)
        print("validation failed: worker setup failed: unexpected_failure", file=sys.stderr)
        return 1

    print("worker health: ok")
    if getattr(session, "fixture_only", False):
        print("validation failed: live smoke refuses fake fixture worker mode", file=sys.stderr)
        return 1
    if getattr(session, "connection_id", None) != args.connection_id:
        print("validation failed: connection_id_mismatch", file=sys.stderr)
        return 1
    if getattr(session, "status", None) != "ready":
        print(f"validation failed: session not ready: {getattr(session, 'status', 'unknown')}", file=sys.stderr)
        return 1
    if not _liepin_smoke_session_provider_account_matches(
        session_provider_account_hash=getattr(session, "provider_account_hash", None),
        connection_provider_account_hash=connection.provider_account_hash,
        settings=settings,
    ):
        print("validation failed: provider_account_mismatch", file=sys.stderr)
        return 1

    if args.pipeline:
        if not _refresh_liepin_smoke_session_safety(
            store=store,
            args=args,
            settings=settings,
            provider_account_hash=connection.provider_account_hash or "",
        ):
            print("validation failed: session safety refresh failed", file=sys.stderr)
            return 1
        print("session: ready")
        return _liepin_smoke_pipeline_command(
            args=args,
            settings=settings,
            connection_provider_account_hash=connection.provider_account_hash or "",
        )

    try:
        search_result = asyncio.run(
            _liepin_smoke_worker_search(
                worker_client=worker_client,
                request=_liepin_smoke_search_request(args, keyword=keyword),
                provider_account_hash=connection.provider_account_hash or "",
            )
        )
    except LiepinWorkerModeError as exc:
        setup_status = exc.setup_status or "failed"
        print(f"validation failed: worker card search failed: {setup_status}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, TimeoutError, ValueError):
        print("validation failed: worker card search failed: unexpected_failure", file=sys.stderr)
        return 1

    plan = build_detail_open_plan(
        candidates=_liepin_smoke_card_candidates(search_result),
        already_opened_provider_ids=set(),
        daily_detail_budget=args.max_detail_opens,
        consumed_detail_budget=0,
    )
    planned_detail_opens = sum(1 for decision in plan.decisions if decision.action == "open_detail")
    print("session: ready")
    print(f"card_count: {len(search_result.candidates)}")
    print(f"raw_candidate_count: {search_result.raw_candidate_count}")
    print(f"detail_budget: {args.max_detail_opens}")
    print(f"detail_open_planned: {planned_detail_opens}")
    print("artifact_refs: []")
    return 0


def _liepin_smoke_settings(args: argparse.Namespace) -> AppSettings:
    base_settings = AppSettings()
    configured_mode = args.worker_mode or base_settings.liepin_worker_mode
    if args.worker_base_url is not None:
        configured_mode = "external_http"
    if configured_mode in {"fake_fixture", "external_http", "opencli"}:
        worker_mode = configured_mode
    else:
        worker_mode = "opencli"
    settings_data: dict[str, object] = {
        "provider_name": "liepin",
        "liepin_live_enabled": True,
        "liepin_worker_mode": worker_mode,
        "liepin_default_daily_detail_budget": args.max_detail_opens,
    }
    if args.db_path is not None:
        settings_data["liepin_connector_db_path"] = str(Path(args.db_path))
    if worker_mode == "opencli":
        settings_data["liepin_browser_action_backend"] = "opencli"
    if args.worker_base_url is not None:
        settings_data["liepin_worker_base_url"] = args.worker_base_url
    return base_settings.with_overrides(**settings_data)


def _liepin_smoke_session_provider_account_matches(
    *,
    session_provider_account_hash: object,
    connection_provider_account_hash: str | None,
    settings: AppSettings,
) -> bool:
    if session_provider_account_hash == connection_provider_account_hash:
        return True
    return (
        settings.liepin_worker_mode == "opencli"
        and session_provider_account_hash == OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT
    )


async def _liepin_smoke_worker_session(
    *,
    worker_client: LiepinWorkerClient,
    connection_id: str,
    tenant_id: str,
    workspace_id: str,
    provider_account_hash: str,
    worker_events: list[tuple[str, dict[str, object]]],
) -> object:
    await worker_client.ensure_ready(on_event=lambda name, payload: worker_events.append((name, payload)))
    return await worker_client.session_status(
        connection_id=connection_id,
        tenant=tenant_id,
        workspace=workspace_id,
        provider_account_hash=provider_account_hash,
    )


async def _liepin_smoke_worker_search(
    *,
    worker_client: LiepinWorkerClient,
    request: SearchRequest,
    provider_account_hash: str,
) -> SearchResult:
    return await worker_client.search(
        request,
        round_no=1,
        trace_id="liepin-smoke",
        provider_account_hash=provider_account_hash,
    )


def _liepin_smoke_pipeline_command(
    *,
    args: argparse.Namespace,
    settings: AppSettings,
    connection_provider_account_hash: str,
) -> int:
    try:
        jd_text = Path(args.jd_file).read_text(encoding="utf-8")
    except OSError:
        print("validation failed: --jd-file could not be read", file=sys.stderr)
        return 1

    try:
        artifacts = WorkflowRuntime(settings).run(
            job_title=args.job_title.strip(),
            jd=jd_text,
            notes=str(args.notes or ""),
            source_kinds=("liepin",),
            source_context={
                "tenant_id": args.tenant_id,
                "workspace_id": args.workspace_id,
                "actor_id": args.actor_id,
                "connection_id": args.connection_id,
                "compliance_gate_ref": args.compliance_gate_ref,
                "provider_account_hash": connection_provider_account_hash,
                "backend_mode": settings.liepin_worker_mode,
            },
        )
    except LiepinWorkerModeError as exc:
        reason_code = _safe_liepin_pipeline_failure_code(exc.code or exc.setup_status)
        print(f"validation failed: pipeline blocked: {reason_code or 'worker_failed'}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        reason_code = _safe_liepin_pipeline_failure_code(getattr(exc, "error_message", str(exc)))
        if getattr(exc, "stage", None) == "source_lanes" and reason_code is not None:
            print(f"validation failed: pipeline blocked: {reason_code}", file=sys.stderr)
        else:
            print("validation failed: pipeline failed: unexpected_failure", file=sys.stderr)
        return 1

    report = _liepin_smoke_pipeline_report(artifacts)
    detail_evidence_count = int(report["detail_evidence_count"])
    final_candidate_count = int(report["final_candidate_count"])
    coverage_status = str(report["coverage_status"])
    stop_reason = str(report["stop_reason"])
    if detail_evidence_count < 1:
        print("validation failed: pipeline detail evidence missing", file=sys.stderr)
        return 1
    if final_candidate_count < args.min_final_candidates:
        print("validation failed: pipeline final shortlist empty", file=sys.stderr)
        return 1
    accepts_degraded_finalization = coverage_status == "degraded" and stop_reason == "source_lanes_degraded"
    if coverage_status != "completed" and not accepts_degraded_finalization:
        print(f"validation failed: pipeline coverage: {coverage_status}", file=sys.stderr)
        return 1

    print("pipeline: completed")
    print(f"runtime_run_id: {report['runtime_run_id']}")
    print(f"source_coverage: {coverage_status}")
    print(f"detail_evidence_count: {detail_evidence_count}")
    print(f"final_candidate_count: {final_candidate_count}")
    print(f"artifact_run_dir: {report['artifact_run_dir']}")
    return 0


def _refresh_liepin_smoke_session_safety(
    *,
    store: LiepinStore,
    args: argparse.Namespace,
    settings: AppSettings,
    provider_account_hash: str,
) -> bool:
    if not provider_account_hash or not settings.liepin_session_store_key_id:
        return False
    state_hash = hashlib.sha256(f"{args.connection_id}:{provider_account_hash}".encode("utf-8")).hexdigest()
    session = store.record_session_metadata(
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
        connection_id=args.connection_id,
        provider_account_hash=provider_account_hash,
        session_store_key_id=settings.liepin_session_store_key_id,
        encrypted_state_sha256=state_hash,
    )
    return session is not None


def _safe_liepin_pipeline_failure_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text in _PUBLIC_LIEPIN_PIPELINE_REASON_CODES:
        return text
    return _LIEPIN_PIPELINE_REASON_MAP.get(text)


def _liepin_smoke_pipeline_report(artifacts: object) -> dict[str, str | int]:
    coverage = getattr(artifacts, "source_coverage_summary", None)
    revision = getattr(artifacts, "finalization_revision", None)
    if coverage is None:
        coverage = getattr(revision, "coverage_summary", None)
    blocked = tuple(getattr(coverage, "blocked_source_kinds", ()) or ())
    failed = tuple(getattr(coverage, "failed_source_kinds", ()) or ())
    partial = tuple(getattr(coverage, "partial_source_kinds", ()) or ())
    missing = tuple(getattr(coverage, "missing_source_kinds", ()) or ())
    empty = tuple(getattr(coverage, "empty_source_kinds", ()) or ())
    completed = tuple(getattr(coverage, "completed_source_kinds", ()) or ())
    raw_status = str(getattr(coverage, "status", "degraded") or "degraded")
    coverage_status = (
        "completed"
        if raw_status in {"complete", "completed"}
        and "liepin" in completed
        and not (blocked or failed or partial or missing or empty)
        else "degraded"
    )

    final_result = getattr(artifacts, "final_result", None)
    stop_reason = str(getattr(final_result, "stop_reason", "") or "")
    final_candidates = list(getattr(final_result, "candidates", ()) or ())
    run_state = getattr(artifacts, "run_state", None)
    evidence_by_resume_id = getattr(run_state, "source_evidence_by_resume_id", {}) or {}
    evidence_items = [item for items in evidence_by_resume_id.values() for item in (items or ())]
    detail_evidence_count = sum(
        1
        for item in evidence_items
        if getattr(item, "source", None) == "liepin" and getattr(item, "evidence_level", None) == "detail"
    )
    return {
        "runtime_run_id": str(getattr(artifacts, "run_id", "unknown")),
        "artifact_run_dir": str(getattr(artifacts, "run_dir", "")),
        "stop_reason": stop_reason,
        "coverage_status": coverage_status,
        "detail_evidence_count": detail_evidence_count,
        "final_candidate_count": len(final_candidates),
    }


def _liepin_smoke_search_request(args: argparse.Namespace, *, keyword: str) -> SearchRequest:
    return SearchRequest(
        query_terms=[keyword],
        query_role="primary",
        keyword_query=keyword,
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=args.page_size,
        provider_context={
            "liepin_tenant_id": args.tenant_id,
            "liepin_workspace_id": args.workspace_id,
            "liepin_actor_id": args.actor_id,
            "liepin_connection_id": args.connection_id,
            "liepin_compliance_gate_ref": args.compliance_gate_ref,
        },
    )


def _liepin_smoke_card_candidates(search_result: SearchResult) -> list[LiepinCardCandidate]:
    candidates: list[LiepinCardCandidate] = []
    for candidate in search_result.candidates:
        candidates.append(
            LiepinCardCandidate(
                candidate_id=candidate.resume_id,
                stable_provider_id=candidate.source_resume_id or candidate.resume_id,
                weak_fingerprint=candidate.dedup_key,
                card_value_score=1.0,
            )
        )
    return candidates


def _print_liepin_worker_events(events: list[tuple[str, dict[str, object]]]) -> None:
    for event_name, payload in events:
        setup_status = payload.get("setup_status")
        if isinstance(setup_status, str) and setup_status:
            print(f"worker event: {event_name} setup_status={setup_status}", file=sys.stderr)
        else:
            print(f"worker event: {event_name}", file=sys.stderr)


def _liepin_cli_db_path(args: argparse.Namespace) -> Path:
    if args.db_path is not None:
        return Path(args.db_path)
    settings = AppSettings()
    path = Path(settings.liepin_connector_db_path)
    if path.is_absolute() or settings.workspace_root is None:
        return path
    return Path(settings.workspace_root) / path
