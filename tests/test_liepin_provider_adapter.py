from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.providers import get_provider_adapter
from seektalent.providers.liepin.adapter import LiepinProviderAdapter
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.mapper import map_liepin_worker_card
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinDetailOpenResponse
from seektalent.providers.liepin.worker_contracts import LiepinDetailOpenResult
from seektalent.providers.liepin.worker_contracts import LiepinDetailWorkerDiagnostics
from seektalent.providers.liepin.worker_contracts import LiepinWorkerCandidateCard
from seektalent.providers.liepin.worker_contracts import LiepinWorkerCandidateDetail
from seektalent.providers.liepin.worker_contracts import SessionStatus
from tests.settings_factory import make_settings


class RecordingWorkerClient:
    def __init__(
        self,
        *,
        fail_ready: bool = False,
        session_status: str = "ready",
        session_provider_account_hash: str | None = "account-hash-a",
        search_result: SearchResult | None = None,
        detail_response: LiepinDetailOpenResponse | None = None,
    ) -> None:
        self.fail_ready = fail_ready
        self.session_status_value = session_status
        self.session_provider_account_hash = session_provider_account_hash
        self.search_result = search_result
        self.detail_response = detail_response
        self.calls: list[str] = []
        self.detail_requests: list[object] = []

    @property
    def ready_called(self) -> bool:
        return "ensure_ready" in self.calls

    @property
    def search_called(self) -> bool:
        return "search" in self.calls

    async def ensure_ready(self, *, on_event=None) -> None:
        self.calls.append("ensure_ready")
        if self.fail_ready:
            if on_event is not None:
                on_event("worker_start_timeout", {"mode": "managed_local", "setup_status": "timeout"})
            raise LiepinWorkerModeError("worker_start_timeout")

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        self.calls.append("session_status")
        return SessionStatus(
            connectionId=connection_id,
            status=self.session_status_value,
            providerAccountHash=self.session_provider_account_hash,
        )

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str):
        self.calls.append("search")
        if self.search_result is not None:
            return self.search_result
        raise AssertionError("search dispatch should not happen")

    async def open_details(self, request: object) -> LiepinDetailOpenResponse:
        self.calls.append("open_details")
        self.detail_requests.append(request)
        if self.detail_response is not None:
            return self.detail_response
        raise AssertionError("detail dispatch should not happen")


def _request(
    *,
    fetch_mode: str = "summary",
    provider_filters: dict[str, str | int | list[str]] | None = None,
    provider_context: dict[str, str] | None = None,
) -> SearchRequest:
    return SearchRequest(
        query_terms=["python"],
        query_role="primary",
        keyword_query="python",
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode=fetch_mode,
        page_size=10,
        provider_filters=provider_filters or {},
        provider_context=provider_context or {},
    )


def _gate(**overrides: object) -> ComplianceGate:
    data: dict[str, object] = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "actor_id": "actor-a",
        "provider_account_hash": "account-hash-a",
        "status": "approved",
        "candidate_personal_info_processing_basis": "candidate recruiting lawful basis",
        "personal_information_processor": "Acme Recruiting",
        "operator_audit_owner": "Ops Owner",
        "account_holder_authorized": True,
        "human_initiated_recruiting": True,
        "allowed_purposes": ["search"],
        "retention_policy": "run_debug_short",
        "deletion_sla_days": 14,
        "deletion_path": "settings/delete",
        "raw_payload_access_scope": "run_only",
        "raw_detail_retention_allowed_after_debug": False,
        "fixture_export_allowed": False,
        "policy_ref": "policy-v1",
    }
    data.update(overrides)
    return ComplianceGate.model_validate(data)


def _live_store(tmp_path: Path, *, gate: ComplianceGate | None = None) -> tuple[LiepinStore, str, str]:
    db_path = tmp_path / "liepin.sqlite3"
    store = LiepinStore(db_path)
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=gate or _gate(),
        purpose="search",
    )
    connection_id = store.create_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        compliance_gate_ref=gate_ref,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE liepin_connections
            SET status = 'connected', provider_account_hash = ?
            WHERE connection_id = ?
            """,
            ("account-hash-a", connection_id),
        )
    return store, gate_ref, connection_id


def _live_filters(gate_ref: str, connection_id: str, **overrides: str) -> dict[str, str]:
    context = {
        "liepin_tenant_id": "tenant-a",
        "liepin_workspace_id": "workspace-a",
        "liepin_actor_id": "actor-a",
        "liepin_connection_id": connection_id,
        "liepin_compliance_gate_ref": gate_ref,
    }
    context.update(overrides)
    return context


def _detail_context(gate_ref: str, connection_id: str, **overrides: str) -> dict[str, str]:
    context = _live_filters(
        gate_ref,
        connection_id,
        liepin_detail_open_plan_ref="artifact:detail-plan",
        liepin_detail_candidates_json=json.dumps(
            [
                {
                    "candidate_id": "candidate-1",
                    "stable_provider_id": "candidate-1",
                    "weak_fingerprint": "candidate-1",
                    "card_value_score": 91,
                }
            ]
        ),
        liepin_detail_daily_budget="3",
        liepin_detail_budget_date="2026-05-08",
        liepin_detail_provider_day_key="liepin:account-hash-a:2026-05-08",
        liepin_detail_timezone="Asia/Shanghai",
        liepin_detail_open_policy_version="detail-policy-v1",
    )
    context.update(overrides)
    return context


def test_adapter_never_substitutes_fake_worker_when_no_client_is_passed() -> None:
    settings = make_settings(
        provider_name="liepin",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
    )
    adapter = LiepinProviderAdapter(settings)

    with pytest.raises(LiepinWorkerModeError, match="worker client"):
        asyncio.run(adapter.search(_request(), round_no=1, trace_id="trace-1"))


def test_summary_search_requires_compliance_gate_and_ready_session(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    result = SearchResult(candidates=[], diagnostics=["ok"], exhausted=True)
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    actual = asyncio.run(
        adapter.search(
            _request(
                provider_filters={"city": "上海"},
                provider_context=_live_filters(gate_ref, connection_id),
            ),
            round_no=1,
            trace_id="trace-1",
        )
    )

    assert actual is result
    assert worker.calls == ["ensure_ready", "session_status", "search"]


@pytest.mark.parametrize(
    ("gate", "match"),
    [
        (_gate(status="denied"), "denied"),
        (_gate(provider_account_hash=None, status="pending_account_binding"), "pending_account_binding"),
        (_gate(provider_account_hash="other-account-hash"), "provider_account_mismatch"),
    ],
)
def test_compliance_gate_blocks_before_worker_calls(
    tmp_path: Path,
    gate: ComplianceGate,
    match: str,
) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path, gate=gate)
    worker = RecordingWorkerClient()
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match=match):
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert worker.calls == []


def test_missing_compliance_gate_blocks_before_worker_calls(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, _gate_ref, connection_id = _live_store(tmp_path)
    worker = RecordingWorkerClient()
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match="compliance gate"):
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters("missing-gate", connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert worker.calls == []


def test_missing_connection_id_blocks_before_worker_calls(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, _connection_id = _live_store(tmp_path)
    context = _live_filters(gate_ref, "conn-a")
    del context["liepin_connection_id"]
    worker = RecordingWorkerClient()
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match="connection"):
        asyncio.run(adapter.search(_request(provider_context=context), round_no=1, trace_id="trace-1"))

    assert worker.calls == []


def test_non_ready_session_blocks_before_search(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    worker = RecordingWorkerClient(session_status="login_required")
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match="session"):
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert worker.calls == ["ensure_ready", "session_status"]


def test_session_account_hash_mismatch_blocks_before_search(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    worker = RecordingWorkerClient(session_provider_account_hash="other-account-hash")
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match="provider account"):
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert worker.calls == ["ensure_ready", "session_status"]


def test_registry_fake_fixture_mode_builds_explicit_fixture_worker() -> None:
    settings = make_settings(
        provider_name="liepin",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
    )
    adapter = get_provider_adapter(settings)

    result = asyncio.run(adapter.search(_request(), round_no=1, trace_id="trace-1"))

    assert result.request_payload["fixture_only"] is True


def test_detail_fetch_requires_detail_open_plan_before_worker_calls(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    worker = RecordingWorkerClient()
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match="detail-open plan") as error:
        asyncio.run(
            adapter.search(
                _request(fetch_mode="detail", provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert type(error.value).__name__ == "LiepinDetailOpenPlanRequired"
    assert worker.calls == ["ensure_ready", "session_status"]


def test_detail_fetch_executes_open_plan_and_returns_mapped_detail_results(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    worker = RecordingWorkerClient(
        detail_response=LiepinDetailOpenResponse(
            worker_command_id="cmd-detail",
            results=[
                LiepinDetailOpenResult(
                    request_id="detail:candidate-1",
                    attempt_id="placeholder",
                    idempotency_key="open:candidate-1",
                    status="completed",
                    worker_response_id="worker-response-1",
                    worker_command_id="cmd-detail",
                    raw_evidence_ref="worker://details/candidate-1.json",
                    diagnostics=LiepinDetailWorkerDiagnostics(
                        page_loaded=True,
                        payload_seen=True,
                        extraction_source="network",
                    ),
                    candidate=_detail("candidate-1"),
                )
            ],
        )
    )
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    result = asyncio.run(
        adapter.search(
            _request(
                fetch_mode="detail",
                provider_context=_detail_context(gate_ref, connection_id),
            ),
            round_no=2,
            trace_id="trace-detail",
        )
    )

    assert worker.calls == ["ensure_ready", "session_status", "open_details"]
    assert len(worker.detail_requests) == 1
    detail_request = worker.detail_requests[0]
    assert detail_request.requests[0].idempotency_key == "open:candidate-1"
    assert result.raw_candidate_count == 1
    assert result.candidates[0].raw["score_evidence_source"] == "detail_enriched"
    assert result.candidates[0].raw["raw_payload_artifact_ref"] == "worker://details/candidate-1.json"
    assert result.provider_snapshots[0].payload_kind == "detail"
    assert result.provider_snapshots[0].score_evidence_source == "detail_enriched"
    assert result.request_payload["liepin_detail_open_plan_ref"] == "artifact:detail-plan"


def test_detail_fetch_missing_required_context_blocks_before_detail_dispatch(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    worker = RecordingWorkerClient()
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)
    context = _detail_context(gate_ref, connection_id)
    del context["liepin_detail_candidates_json"]

    with pytest.raises(LiepinWorkerModeError, match="liepin_detail_candidates_json"):
        asyncio.run(
            adapter.search(
                _request(fetch_mode="detail", provider_context=context),
                round_no=2,
                trace_id="trace-detail",
            )
        )

    assert worker.calls == ["ensure_ready", "session_status"]


def test_adapter_preserves_provider_snapshots_and_keeps_candidate_raw_safe(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    first = map_liepin_worker_card(_card("candidate-a", {"rawProviderPayload": {"secret": "blocked"}}))
    second = map_liepin_worker_card(_card("candidate-b", {"title": "Python Engineer"}))
    result = SearchResult(
        candidates=[first.candidate, second.candidate],
        provider_snapshots=[first.provider_snapshot, second.provider_snapshot],
        raw_candidate_count=2,
    )
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    actual = asyncio.run(
        adapter.search(
            _request(provider_context=_live_filters(gate_ref, connection_id)),
            round_no=1,
            trace_id="trace-1",
        )
    )

    assert actual.provider_snapshots == [first.provider_snapshot, second.provider_snapshot]
    assert {snapshot.raw_payload["id"] for snapshot in actual.provider_snapshots} == {"candidate-a", "candidate-b"}
    assert all("rawProviderPayload" not in candidate.raw for candidate in actual.candidates)
    assert all("raw_provider_payload" not in candidate.raw for candidate in actual.candidates)


def test_live_scope_stays_out_of_provider_filters(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    result = SearchResult(candidates=[], diagnostics=["ok"], exhausted=True)
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)
    provider_filters = {"city": "上海", "experience_years": 5}

    actual = asyncio.run(
        adapter.search(
            _request(
                provider_filters=provider_filters,
                provider_context=_live_filters(gate_ref, connection_id),
            ),
            round_no=1,
            trace_id="trace-1",
        )
    )

    assert actual is result
    assert worker.calls == ["ensure_ready", "session_status", "search"]
    assert provider_filters == {"city": "上海", "experience_years": 5}
    assert all(not key.startswith("liepin_") for key in provider_filters)


def test_adapter_rejects_missing_provider_snapshots(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    mapped = map_liepin_worker_card(_card("candidate-a", {"title": "Python Engineer"}))
    result = SearchResult(candidates=[mapped.candidate], provider_snapshots=[], raw_candidate_count=1)
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match="snapshot count mismatch"):
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert worker.calls == ["ensure_ready", "session_status", "search"]


def test_adapter_rejects_unsafe_candidate_raw(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    mapped = map_liepin_worker_card(_card("candidate-a", {"title": "Python Engineer"}))
    unsafe_candidate = mapped.candidate.model_copy(
        update={"raw": {"resumeId": "candidate-a", "rawProviderPayload": {"secret": "blocked"}}}
    )
    result = SearchResult(
        candidates=[unsafe_candidate],
        provider_snapshots=[mapped.provider_snapshot],
        raw_candidate_count=1,
    )
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError, match="unsafe candidate raw"):
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert worker.calls == ["ensure_ready", "session_status", "search"]


@pytest.mark.parametrize(
    "candidate_raw",
    [
        {"resumeId": "candidate-a", "raw_payload": {"secret": "blocked"}},
        {"resumeId": "candidate-a", "authorization": "Bearer blocked-secret"},
        {"resumeId": "candidate-a", "auth_headers": {"authorization": "Bearer blocked-secret"}},
        {"resumeId": "candidate-a", "safe": [{"authorization": "Bearer blocked-secret"}]},
    ],
)
def test_adapter_rejects_normalized_and_nested_unsafe_candidate_raw_keys(
    tmp_path: Path,
    candidate_raw: dict[str, object],
) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    mapped = map_liepin_worker_card(_card("candidate-a", {"title": "Python Engineer"}))
    unsafe_candidate = mapped.candidate.model_copy(update={"raw": candidate_raw})
    result = SearchResult(
        candidates=[unsafe_candidate],
        provider_snapshots=[mapped.provider_snapshot],
        raw_candidate_count=1,
    )
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    message = str(error.value)
    assert message == "Liepin unsafe candidate raw value rejected."
    assert "blocked-secret" not in message
    assert worker.calls == ["ensure_ready", "session_status", "search"]


@pytest.mark.parametrize(
    ("candidate_raw", "blocked_value"),
    [
        ({"resumeId": "candidate-a", "note": "Authorization: Bearer blocked-secret"}, "blocked-secret"),
        ({"resumeId": "candidate-a", "message": "rawProviderPayload={blocked}"}, "rawProviderPayload={blocked}"),
        ({"resumeId": "candidate-a", "message": "raw_provider_payload={blocked}"}, "raw_provider_payload={blocked}"),
        ({"resumeId": "candidate-a", "diagnostics": "internal-worker-observed-account-a"}, "internal-worker-observed-account-a"),
    ],
)
def test_adapter_rejects_unsafe_candidate_raw_values_without_leaking_values(
    tmp_path: Path,
    candidate_raw: dict[str, object],
    blocked_value: str,
) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    mapped = map_liepin_worker_card(_card("candidate-a", {"title": "Python Engineer"}))
    unsafe_candidate = mapped.candidate.model_copy(update={"raw": candidate_raw})
    result = SearchResult(
        candidates=[unsafe_candidate],
        provider_snapshots=[mapped.provider_snapshot],
        raw_candidate_count=1,
    )
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    message = str(error.value)
    assert message == "Liepin unsafe candidate raw value rejected."
    assert blocked_value not in message
    assert worker.calls == ["ensure_ready", "session_status", "search"]


def test_adapter_allows_raw_payload_artifact_ref_candidate_raw_key(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    mapped = map_liepin_worker_card(_card("candidate-a", {"title": "Python Engineer"}))
    candidate = mapped.candidate.model_copy(update={"raw": {"raw_payload_artifact_ref": "worker://cards/a.json"}})
    result = SearchResult(
        candidates=[candidate],
        provider_snapshots=[mapped.provider_snapshot],
        raw_candidate_count=1,
    )
    worker = RecordingWorkerClient(search_result=result)
    adapter = LiepinProviderAdapter(settings, worker_client=worker, store=store)

    actual = asyncio.run(
        adapter.search(
            _request(provider_context=_live_filters(gate_ref, connection_id)),
            round_no=1,
            trace_id="trace-1",
        )
    )

    assert actual is result
    assert worker.calls == ["ensure_ready", "session_status", "search"]


def _card(candidate_id: str, payload: dict[str, object]) -> LiepinWorkerCandidateCard:
    data = {"id": candidate_id}
    data.update(payload)
    return LiepinWorkerCandidateCard(
        payload=data,
        normalized_text=f"{candidate_id} Python Engineer",
        provider_subject_id=candidate_id,
        provider_listing_id=f"listing-{candidate_id}",
        synthetic_candidate_fingerprint=f"liepin:{candidate_id}",
        identity_confidence="provider_subject_id",
        extraction_source="network",
        extractor_version="test",
        pii_classification="no_direct_contact",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _detail(candidate_id: str) -> LiepinWorkerCandidateDetail:
    return LiepinWorkerCandidateDetail(
        payload={
            "candidateId": candidate_id,
            "listingId": f"listing-{candidate_id}",
            "resumeText": f"Private detail payload for {candidate_id}",
        },
        normalized_text=f"{candidate_id} Python Engineer detail",
        provider_subject_id=candidate_id,
        provider_listing_id=f"listing-{candidate_id}",
        synthetic_candidate_fingerprint=candidate_id,
        identity_confidence="provider_subject_id",
        extraction_source="network",
        extractor_version="test",
        pii_classification="direct_contact_possible",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def test_adapter_records_worker_start_timeout_and_does_not_dispatch_search(tmp_path: Path) -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="managed_local")
    store, gate_ref, connection_id = _live_store(tmp_path)
    events: list[tuple[str, dict[str, object]]] = []
    worker = RecordingWorkerClient(fail_ready=True)
    adapter = LiepinProviderAdapter(
        settings,
        worker_client=worker,
        worker_event_callback=lambda name, payload: events.append((name, payload)),
        store=store,
    )

    with pytest.raises(LiepinWorkerModeError, match="worker_start_timeout"):
        asyncio.run(
            adapter.search(
                _request(provider_context=_live_filters(gate_ref, connection_id)),
                round_no=1,
                trace_id="trace-1",
            )
        )

    assert worker.calls == ["ensure_ready"]
    assert events == [("worker_start_timeout", {"mode": "managed_local", "setup_status": "timeout"})]
