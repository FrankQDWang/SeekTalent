from __future__ import annotations

import asyncio
import json
from dataclasses import fields
from types import SimpleNamespace

import pytest

from seektalent.models import ResumeCandidate
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.public_events import make_runtime_public_event, normalize_runtime_public_event
from seektalent.runtime.source_lanes import SourceQueryExecutionOutcome, build_runtime_source_plan
from seektalent.runtime.source_round_dispatch import SourceRoundAdapterResult, SourceRoundDispatchResult
from seektalent.source_adapters import build_source_enabled_runtime
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings
from tests.test_runtime_state_flow import (
    GenericFallbackScorer,
    SequenceController,
    _install_runtime_stubs,
    _sample_inputs,
)


def _workflow_runtime(*args, **kwargs) -> WorkflowRuntime:
    return build_source_enabled_runtime(*args, **kwargs)


def test_cts_only_rounds_emit_canonical_runtime_public_events(tmp_path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        provider_name="cts",
        min_rounds=1,
        max_rounds=1,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        source_plan = build_runtime_source_plan(source_kinds=["cts"], settings=settings, runtime_run_id=tracer.run_id)
        asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=DetailOpenClaimLedger(run_state.detail_open_claims_by_provider_key),
                tracer=tracer,
                source_plan=source_plan,
                progress_callback=progress_events.append,
            )
        )
    finally:
        tracer.close()

    public_events = _runtime_public_event_payloads(progress_events)

    assert [(event["stage"], event["roundNo"], event["sourceKind"]) for event in public_events] == [
        ("round_query", 1, None),
        ("source_dispatch", 1, "cts"),
        ("source_result", 1, "cts"),
        ("merge", 1, None),
        ("scoring", 1, None),
        ("feedback", 1, None),
    ]


def test_runtime_round_query_public_event_uses_logical_planned_groups(tmp_path) -> None:
    payloads = _multi_source_runtime_public_event_payloads(tmp_path, source_kinds=("cts", "liepin"))
    round_query = next(payload for payload in payloads if payload["stage"] == "round_query")

    [planned_group] = round_query["details"]["queryGroups"]
    assert planned_group["queryTerms"]
    assert planned_group["keywordQuery"]
    assert planned_group["lifecycle"] == "planned"
    assert "sourceKind" not in planned_group


def test_runtime_feedback_public_event_includes_liepin_execution_in_logical_group(tmp_path) -> None:
    payloads = _multi_source_runtime_public_event_payloads(tmp_path, source_kinds=("cts", "liepin"))
    feedback = next(payload for payload in payloads if payload["stage"] == "feedback")

    [executed_group] = feedback["details"]["queryGroups"]
    assert {item["sourceKind"] for item in executed_group["executions"]} >= {"cts", "liepin"}


def test_runtime_public_events_publish_one_logical_group_with_two_source_executions(tmp_path) -> None:
    payloads = _multi_source_runtime_public_event_payloads(tmp_path, source_kinds=("cts", "liepin"))
    round_query = next(payload for payload in payloads if payload["stage"] == "round_query")
    feedback = next(payload for payload in payloads if payload["stage"] == "feedback")

    [planned_group] = round_query["details"]["queryGroups"]
    assert planned_group["lifecycle"] == "planned"
    assert planned_group["executionStatus"] is None
    assert planned_group["attempted"] is False
    assert planned_group["executions"] == []
    assert "sourceKind" not in planned_group
    assert "requestedCount" not in planned_group

    [executed_group] = feedback["details"]["queryGroups"]
    assert executed_group["queryInstanceId"] == planned_group["queryInstanceId"]
    assert executed_group["termGroupKey"] == planned_group["termGroupKey"]
    assert executed_group["lifecycle"] == "executed"
    assert executed_group["executionStatus"] == "completed"
    assert executed_group["attempted"] is True
    assert [item["sourceKind"] for item in executed_group["executions"]] == ["cts", "liepin"]
    assert all("safeReasonCode" not in item for item in executed_group["executions"])


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("queryInstanceId", {"providerUrl": "https://provider.example/private/raw-identity"}),
        ("termGroupKey", ["https://provider.example/private/raw-identity"]),
        ("queryRole", {"rawIdentity": "https://provider.example/private/raw-identity"}),
        ("laneType", ["raw-identity", "https://provider.example/private"]),
        ("keywordQuery", {"providerUrl": "https://provider.example/private/raw-identity"}),
        ("lifecycle", ["executed", "https://provider.example/private/raw-identity"]),
        ("executionStatus", {"providerUrl": "https://provider.example/private/raw-identity"}),
    ],
)
def test_runtime_public_query_group_drops_non_string_required_scalars(field: str, bad_value: object) -> None:
    secret = "https://provider.example/private/raw-identity"
    group = _public_query_group(lifecycle="executed")
    group[field] = bad_value

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="feedback",
        event_seq=1,
        round_no=1,
        details={"queryGroups": [group]},
    )

    assert "queryGroups" not in event["details"]
    assert secret not in repr(event)


@pytest.mark.parametrize(
    "field",
    ["queryInstanceId", "termGroupKey", "queryRole", "laneType", "keywordQuery"],
)
def test_runtime_public_query_group_drops_sensitive_required_text(field: str) -> None:
    secret = "https://provider.example/private/raw-identity"
    group = _public_query_group(lifecycle="executed")
    group[field] = secret

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="feedback",
        event_seq=1,
        round_no=1,
        details={"queryGroups": [group]},
    )

    assert "queryGroups" not in event["details"]
    assert secret not in repr(event)


def test_runtime_public_query_group_scrubs_non_string_terms_and_execution_scalars() -> None:
    secret = "https://provider.example/private/raw-identity"
    group = _public_query_group(lifecycle="executed")
    group["queryTerms"] = [
        "safe term",
        {"providerUrl": secret},
        ["rawIdentity", secret],
        7,
        True,
        secret,
        "Bearer private-token",
    ]
    group["executions"] = [
        {
            "sourceKind": "cts",
            "status": "completed",
            "safeReasonCode": {"providerUrl": secret},
        },
        {
            "sourceKind": {"providerUrl": secret},
            "status": "completed",
        },
        {
            "sourceKind": "liepin",
            "status": ["completed", secret],
        },
        {
            "sourceKind": secret,
            "status": "completed",
        },
    ]

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="feedback",
        event_seq=1,
        round_no=1,
        details={"queryGroups": [group]},
    )

    [sanitized] = event["details"]["queryGroups"]
    assert sanitized["queryTerms"] == ["safe term"]
    assert sanitized["executions"] == [
        {
            "sourceKind": "cts",
            "status": "completed",
            "rawCandidateCount": 0,
            "uniqueCandidateCount": 0,
            "duplicateCandidateCount": 0,
        }
    ]
    assert secret not in repr(event)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "note: Authorization: Bearer private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_runtime_public_query_group_drops_shared_unsafe_text(unsafe_text: str) -> None:
    group = _public_query_group(lifecycle="executed")
    group["queryTerms"] = ["safe term", unsafe_text]

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="feedback",
        event_seq=1,
        round_no=1,
        details={"queryGroups": [group]},
    )

    [sanitized] = event["details"]["queryGroups"]
    assert sanitized["queryTerms"] == ["safe term"]
    assert unsafe_text not in json.dumps(event, ensure_ascii=False)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "note: Authorization: Bearer private-token",
        "debug secret=private-token",
        "apikey=private-token",
        "api-key=private-token",
        "X-API-Key: private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_runtime_public_event_drops_shared_unsafe_text_from_all_public_query_fields(unsafe_text: str) -> None:
    group = _public_query_group(lifecycle="executed")
    group["queryTerms"] = ["safe term", unsafe_text]
    group["executions"] = [
        {"sourceKind": "cts", "status": "completed"},
        {"sourceKind": unsafe_text, "status": "completed"},
    ]
    unsafe_keyword_group = _public_query_group(lifecycle="executed")
    unsafe_keyword_group["queryInstanceId"] = "query-unsafe-keyword"
    unsafe_keyword_group["keywordQuery"] = unsafe_text

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="feedback",
        event_seq=1,
        round_no=1,
        source_kind="internal_referrals",
        details={
            "queryGroups": [group, unsafe_keyword_group],
            "resumeQualityComment": unsafe_text,
            "reflectionSummary": unsafe_text,
            "suggestedActivateTerms": ["safe detail", unsafe_text],
        },
    )

    [sanitized] = event["details"]["queryGroups"]
    assert sanitized["queryInstanceId"] == "query-1"
    assert sanitized["termGroupKey"] == "group-1"
    assert sanitized["queryTerms"] == ["safe term"]
    assert sanitized["keywordQuery"] == "safe term"
    assert sanitized["executions"] == [
        {
            "sourceKind": "cts",
            "status": "completed",
            "rawCandidateCount": 0,
            "uniqueCandidateCount": 0,
            "duplicateCandidateCount": 0,
        }
    ]
    assert event["sourceKind"] == "internal_referrals"
    assert event["details"] == {
        "queryGroups": [sanitized],
        "suggestedActivateTerms": ["safe detail"],
    }
    assert unsafe_text not in json.dumps(event, ensure_ascii=False)


@pytest.mark.parametrize(
    ("stage", "lifecycle"),
    [
        ("round_query", "executed"),
        ("feedback", "planned"),
        ("source_result", "planned"),
    ],
)
def test_runtime_public_query_groups_require_the_stage_lifecycle(stage: str, lifecycle: str) -> None:
    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage=stage,
        event_seq=1,
        round_no=1,
        details={"queryGroups": [_public_query_group(lifecycle=lifecycle)]},
    )

    assert "queryGroups" not in event["details"]


def test_runtime_public_event_drops_non_scalar_or_sensitive_public_text_before_control_projection() -> None:
    from seektalent.progress import ProgressEvent
    from seektalent_runtime_control.events import normalize_progress_event, public_event_payload

    provider_url = "https://provider.example/private/raw-identity"
    private_token = "private-token"
    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="feedback",
        event_seq=1,
        round_no=1,
        status={"opaque": provider_url},
        created_at={"opaque": provider_url},
        details={
            "resumeQualityComment": "Safe quality note.",
            "reflectionSummary": {"opaque": provider_url},
            "suggestedStopReason": f"Authorization=Bearer {private_token}",
            "suggestedActivateTerms": [
                "safe term",
                {"opaque": provider_url},
                ["nested", provider_url],
                7,
                True,
                provider_url,
                f"Authorization=Bearer {private_token}",
            ],
        },
    )

    assert event["status"] == "completed"
    assert event["createdAt"] is None
    assert event["details"] == {
        "resumeQualityComment": "Safe quality note.",
        "suggestedActivateTerms": ["safe term"],
    }

    control_event = normalize_progress_event(
        ProgressEvent(
            type="runtime_public_event",
            message="feedback",
            timestamp="2026-07-11T00:00:00Z",
            round_no=1,
            payload=dict(event),
        ),
        runtime_run_id="runtime-run-1",
        now="2026-07-11T00:00:01Z",
    )
    projected = public_event_payload(control_event)

    assert projected is not None
    assert projected["details"] == event["details"]
    serialized = json.dumps([event, control_event.model_dump(mode="json"), projected], ensure_ascii=False)
    assert provider_url not in serialized
    assert private_token not in serialized


def test_runtime_public_event_preserves_valid_status_and_created_at() -> None:
    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=1,
        round_no=1,
        source_kind="cts",
        status="blocked",
        created_at="2026-07-11T00:00:00Z",
    )

    assert event["status"] == "blocked"
    assert event["createdAt"] == "2026-07-11T00:00:00Z"


@pytest.mark.parametrize("field", ["runtimeRunId", "eventId"])
def test_runtime_public_event_rejects_unsafe_public_identifiers(field: str) -> None:
    secret = "https://example.invalid/private/raw-identity"
    payload = dict(
        make_runtime_public_event(
            runtime_run_id="run-1",
            stage="source_result",
            event_seq=1,
            round_no=1,
            source_kind="cts",
        )
    )
    payload[field] = secret

    with pytest.raises(ValueError):
        normalize_runtime_public_event(payload)


@pytest.mark.parametrize(
    "source_kind",
    [
        "https://provider.example/private/raw-identity",
        "Authorization=Bearer private-token",
        "debug secret=private-token",
        "api-key=private-token",
        "X-API-Key: private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
        "source/with/path",
    ],
)
def test_runtime_public_event_rejects_unsafe_source_kind(source_kind: str) -> None:
    with pytest.raises(ValueError, match="runtime_public_event_source_kind_invalid"):
        make_runtime_public_event(
            runtime_run_id="run-1",
            stage="source_result",
            event_seq=1,
            round_no=1,
            source_kind=source_kind,
        )


def test_runtime_public_event_preserves_source_neutral_identifier() -> None:
    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=1,
        round_no=1,
        source_kind="internal_referrals",
    )

    assert event["sourceKind"] == "internal_referrals"


def test_source_result_public_event_maps_liepin_stale_ref_to_browser_backend_unavailable() -> None:
    from seektalent.source_adapters import public_source_reason_code

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=131,
        round_no=1,
        source_kind="liepin",
        status="blocked",
        safe_reason_code=public_source_reason_code("liepin_opencli_stale_ref"),
    )

    assert event["safeReasonCode"] == "source_browser_backend_unavailable"


@pytest.mark.parametrize(
    "reason_code",
    [
        "liepin_opencli_search_not_ready",
        "liepin_opencli_results_not_ready",
        "liepin_opencli_removed_config",
    ],
)
def test_source_result_public_event_maps_liepin_opencli_backend_unavailable_reasons(reason_code: str) -> None:
    from seektalent.source_adapters import public_source_reason_code

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=131,
        round_no=1,
        source_kind="liepin",
        status="blocked",
        safe_reason_code=public_source_reason_code(reason_code),
    )

    assert event["safeReasonCode"] == "source_browser_backend_unavailable"


def test_source_result_public_event_maps_liepin_opencli_bootstrap_failed() -> None:
    from seektalent.source_adapters import public_source_reason_code

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=131,
        round_no=1,
        source_kind="liepin",
        status="blocked",
        safe_reason_code=public_source_reason_code("liepin_opencli_bootstrap_failed"),
    )

    assert event["safeReasonCode"] == "source_browser_backend_unavailable"


def test_source_result_public_event_maps_liepin_extension_disconnected() -> None:
    from seektalent.source_adapters import public_source_reason_code

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=131,
        round_no=1,
        source_kind="liepin",
        status="blocked",
        safe_reason_code=public_source_reason_code("liepin_opencli_extension_disconnected"),
    )

    assert event["safeReasonCode"] == "source_browser_extension_disconnected"


def test_cts_only_run_emits_finalization_public_event(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        provider_name="cts",
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    progress_events = []

    runtime.run(
        job_title="Senior Python Engineer",
        jd="Senior Python Engineer responsible for resume matching workflows.",
        notes="Prefer retrieval experience and shipping production AI features.",
        source_kinds=["cts"],
        progress_callback=progress_events.append,
    )

    finalization_events = [
        event for event in _runtime_public_event_payloads(progress_events) if event["stage"] == "finalization"
    ]

    assert [(event["roundNo"], event["sourceKind"]) for event in finalization_events] == [(None, None)]
    assert finalization_events[0]["counts"]["selectedIdentityCount"] > 0


def test_source_round_empty_coverage_does_not_block_next_runtime_step(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="opencli")
    runtime = _workflow_runtime(settings)
    source_plan = build_runtime_source_plan(source_kinds=["cts"], settings=settings, runtime_run_id="run-1")
    dispatch_result = SourceRoundDispatchResult(
        source_results=(
            SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(),
                raw_candidate_count=0,
            ),
        ),
        candidates=(),
        raw_candidate_count=0,
    )

    coverage_summary = runtime._source_coverage_summary_from_dispatch(
        source_plan=source_plan,
        dispatch_result=dispatch_result,
    )

    assert coverage_summary.status == "empty"
    assert coverage_summary.empty_source_kinds == ("cts",)
    assert (
        runtime._source_round_not_ready_reason(
            coverage_summary=coverage_summary,
            dispatch_result=dispatch_result,
        )
        is None
    )


def test_source_round_unknown_coverage_status_remains_blocking(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="opencli")
    runtime = _workflow_runtime(settings)

    reason = runtime._source_round_not_ready_reason(
        coverage_summary=SimpleNamespace(
            status="unexpected",
            blocked_source_kinds=(),
            failed_source_kinds=(),
            partial_source_kinds=(),
            empty_source_kinds=(),
            missing_source_kinds=(),
        ),
        dispatch_result=SourceRoundDispatchResult(
            source_results=(),
            candidates=(),
            raw_candidate_count=0,
        ),
    )

    assert reason == "source_coverage_unexpected"


def test_source_round_not_ready_uses_safe_diagnostic_for_generic_provider_failure(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="opencli")
    runtime = _workflow_runtime(settings)
    coverage = SimpleNamespace(
        status="degraded",
        blocked_source_kinds=("liepin",),
        failed_source_kinds=(),
        partial_source_kinds=(),
        empty_source_kinds=(),
        missing_source_kinds=(),
    )

    reason = runtime._source_round_not_ready_reason(
        coverage_summary=coverage,
        dispatch_result=SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(
                    source="liepin",
                    status="blocked",
                    safe_reason_code="failed_provider_error",
                    diagnostics=(
                        "LiepinWorkerModeError: failed_provider_error; Liepin OpenCLI resume search blocked.",
                    ),
                ),
            ),
            candidates=(),
            raw_candidate_count=0,
        ),
    )

    assert reason == "LiepinWorkerModeError: failed_provider_error; Liepin OpenCLI resume search blocked."


def _runtime_public_event_payloads(progress_events: list[object]) -> list[dict[str, object]]:
    return [
        event.payload
        for event in progress_events
        if event.type == "runtime_public_event" and event.payload.get("schemaVersion") == "runtime_public_event_v1"
    ]


def _public_query_group(*, lifecycle: str) -> dict[str, object]:
    group: dict[str, object] = {
        "queryInstanceId": "query-1",
        "termGroupKey": "group-1",
        "queryRole": "exploit",
        "laneType": "exploit",
        "queryTerms": ["safe term"],
        "keywordQuery": "safe term",
        "lifecycle": lifecycle,
        "executionStatus": None,
        "attempted": False,
        "rawCandidateCount": 0,
        "uniqueCandidateCount": 0,
        "duplicateCandidateCount": 0,
        "executions": [],
    }
    if lifecycle == "executed":
        group.update(executionStatus="completed", attempted=True)
    return group


def _multi_source_runtime_public_event_payloads(
    tmp_path,
    *,
    source_kinds: tuple[str, ...],
) -> list[dict[str, object]]:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts="cts" in source_kinds,
        liepin_worker_mode="fake_fixture" if "liepin" in source_kinds else "disabled",
        liepin_allow_fake_fixture_worker="liepin" in source_kinds,
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = WorkflowRuntime(settings, source_round_adapter_provider=_completed_source_round_adapters)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []
    source_context = {"backend_mode": "fake_fixture", "status": "ready"} if "liepin" in source_kinds else None
    source_plan = build_runtime_source_plan(
        source_kinds=source_kinds,
        settings=settings,
        runtime_run_id=tracer.run_id,
        source_context=source_context,
    )

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=DetailOpenClaimLedger(run_state.detail_open_claims_by_provider_key),
                tracer=tracer,
                source_plan=source_plan,
                source_context=source_context,
                progress_callback=progress_events.append,
            )
        )
    finally:
        tracer.close()

    return _runtime_public_event_payloads(progress_events)


def _completed_source_round_adapters(runtime: WorkflowRuntime, context):
    del runtime, context

    async def adapter(request, source_id: str):
        result_kwargs = {
            "source": source_id,
            "status": "completed",
            "candidates": (_public_event_candidate(source_id),),
            "raw_candidate_count": 1,
        }
        if "executed_query_packages" in {field.name for field in fields(SourceRoundAdapterResult)}:
            result_kwargs["executed_query_packages"] = tuple(
                SimpleNamespace(
                    source_kind=source_id,
                    query_role=intent.query_role,
                    lane_type=intent.lane_type,
                    query_terms=intent.query_terms,
                    keyword_query=intent.keyword_query,
                )
                for intent in request.source_query_intents_by_source.get(source_id, ())
            )
        if "query_execution_outcomes" in {field.name for field in fields(SourceRoundAdapterResult)}:
            result_kwargs["query_execution_outcomes"] = tuple(
                SourceQueryExecutionOutcome(
                    query_instance_id=intent.query_instance_id,
                    status="completed",
                    dispatch_started=True,
                )
                for intent in request.source_query_intents_by_source.get(source_id, ())
            )
        return SourceRoundAdapterResult(**result_kwargs)

    return {
        source_id: (lambda request, source_id=source_id: adapter(request, source_id)) for source_id in ("cts", "liepin")
    }


def _public_event_candidate(source: str) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=f"{source}-candidate-1",
        source_resume_id=f"{source}-candidate-1",
        dedup_key=f"dedup-{source}-candidate-1",
        search_text=f"{source} public event candidate",
        raw={"source": source},
    )
