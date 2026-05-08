from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from seektalent.corpus.runtime import ProviderReturnedCandidate
from seektalent.models import ScoredCandidate
from seektalent.providers.liepin.policy import LiepinCardCandidate
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.verified_loop import (
    build_detail_scorecard_metadata,
    execute_liepin_detail_open_plan,
)
from seektalent.providers.liepin.worker_contracts import (
    LiepinDetailOpenResponse,
    LiepinDetailOpenResult,
    LiepinDetailWorkerDiagnostics,
    LiepinWorkerCandidateDetail,
)


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
ACTOR = "actor-a"
ACCOUNT = "account-hash-a"
CONNECTION = "conn-a"


class RecordingWorker:
    def __init__(self, response: LiepinDetailOpenResponse) -> None:
        self.response = response
        self.requests: list[Any] = []

    async def open_details(self, request: Any) -> LiepinDetailOpenResponse:
        self.requests.append(request)
        return self.response


class CrashingWorker:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def open_details(self, request: Any) -> LiepinDetailOpenResponse:
        self.requests.append(request)
        raise RuntimeError("browser died after click")


def test_detail_loop_marks_reserved_attempt_unknown_when_worker_response_is_partial(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    worker = RecordingWorker(LiepinDetailOpenResponse(worker_command_id="cmd-partial", results=[]))

    with pytest.raises(ValueError, match="detail worker response mismatch"):
        asyncio.run(
            execute_liepin_detail_open_plan(
                store=store,
                worker_client=worker,
                card_candidates=[
                    LiepinCardCandidate(
                        candidate_id="candidate-missing",
                        stable_provider_id="candidate-missing",
                        weak_fingerprint="weak-missing",
                        card_value_score=91,
                    )
                ],
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                actor_id=ACTOR,
                connection_id=CONNECTION,
                provider_account_hash=ACCOUNT,
                budget_date="2026-05-07",
                provider_day_key="liepin:account-hash-a:2026-05-07",
                timezone="Asia/Shanghai",
                daily_detail_budget=3,
                detail_open_policy_version="detail-policy-v1",
                run_id="run-1",
                query_instance_id="query-1",
                query_fingerprint="fingerprint-1",
            )
        )

    attempt_id = worker.requests[0].requests[0].attempt_id
    assert _attempt_state(store, attempt_id) == ("unknown", "possibly_consumed")


def test_detail_loop_marks_reserved_attempt_unknown_when_worker_response_has_unexpected_key(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    worker = RecordingWorker(
        LiepinDetailOpenResponse(
            worker_command_id="cmd-unexpected",
            results=[
                LiepinDetailOpenResult(
                    request_id="detail:other",
                    attempt_id="other",
                    idempotency_key="open:other",
                    status="failed_after_possible_consumption",
                    worker_response_id="worker-response-other",
                    worker_command_id="cmd-unexpected",
                    raw_evidence_ref="worker://details/other.json",
                    diagnostics=LiepinDetailWorkerDiagnostics(page_loaded=True, payload_seen=False),
                )
            ],
        )
    )

    with pytest.raises(ValueError, match="detail worker response mismatch"):
        asyncio.run(
            execute_liepin_detail_open_plan(
                store=store,
                worker_client=worker,
                card_candidates=[
                    LiepinCardCandidate(
                        candidate_id="candidate-expected",
                        stable_provider_id="candidate-expected",
                        weak_fingerprint="weak-expected",
                        card_value_score=91,
                    )
                ],
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                actor_id=ACTOR,
                connection_id=CONNECTION,
                provider_account_hash=ACCOUNT,
                budget_date="2026-05-07",
                provider_day_key="liepin:account-hash-a:2026-05-07",
                timezone="Asia/Shanghai",
                daily_detail_budget=3,
                detail_open_policy_version="detail-policy-v1",
                run_id="run-1",
                query_instance_id="query-1",
                query_fingerprint="fingerprint-1",
            )
        )

    attempt_id = worker.requests[0].requests[0].attempt_id
    assert _attempt_state(store, attempt_id) == ("unknown", "possibly_consumed")


def test_detail_loop_marks_reserved_attempt_unknown_when_completed_result_has_no_candidate(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    worker = RecordingWorker(
        LiepinDetailOpenResponse(
            worker_command_id="cmd-missing-candidate",
            results=[
                LiepinDetailOpenResult(
                    request_id="detail:candidate-missing-candidate",
                    attempt_id="placeholder",
                    idempotency_key="open:candidate-missing-candidate",
                    status="completed",
                    worker_response_id="worker-response-missing-candidate",
                    worker_command_id="cmd-missing-candidate",
                    raw_evidence_ref="worker://details/missing-candidate.json",
                    diagnostics=LiepinDetailWorkerDiagnostics(page_loaded=False, payload_seen=False),
                    candidate=None,
                )
            ],
        )
    )

    with pytest.raises(ValueError, match="completed Liepin detail result requires a candidate payload"):
        asyncio.run(
            execute_liepin_detail_open_plan(
                store=store,
                worker_client=worker,
                card_candidates=[
                    LiepinCardCandidate(
                        candidate_id="candidate-missing-candidate",
                        stable_provider_id="candidate-missing-candidate",
                        weak_fingerprint="weak-missing-candidate",
                        card_value_score=91,
                    )
                ],
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                actor_id=ACTOR,
                connection_id=CONNECTION,
                provider_account_hash=ACCOUNT,
                budget_date="2026-05-07",
                provider_day_key="liepin:account-hash-a:2026-05-07",
                timezone="Asia/Shanghai",
                daily_detail_budget=3,
                detail_open_policy_version="detail-policy-v1",
                run_id="run-1",
                query_instance_id="query-1",
                query_fingerprint="fingerprint-1",
            )
        )

    attempt_id = worker.requests[0].requests[0].attempt_id
    assert _attempt_state(store, attempt_id) == ("unknown", "possibly_consumed")


def test_detail_loop_reserves_before_dispatch_and_records_completed_corpus_return(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    returned: list[ProviderReturnedCandidate] = []
    worker_response = LiepinDetailOpenResponse(
        worker_command_id="cmd-1",
        results=[
            LiepinDetailOpenResult(
                request_id="detail:candidate-1",
                attempt_id="placeholder",
                idempotency_key="open:candidate-1",
                status="completed",
                worker_response_id="worker-response-1",
                worker_command_id="cmd-1",
                raw_evidence_ref="worker://details/candidate-1.json",
                diagnostics=LiepinDetailWorkerDiagnostics(
                    page_loaded=True,
                    payload_seen=True,
                    extraction_source="network",
                ),
                candidate=_worker_detail(),
            )
        ],
    )
    worker = RecordingWorker(worker_response)

    result = asyncio.run(
        execute_liepin_detail_open_plan(
            store=store,
            worker_client=worker,
            card_candidates=[
                LiepinCardCandidate(
                    candidate_id="candidate-1",
                    stable_provider_id="candidate-1",
                    weak_fingerprint="weak-1",
                    card_value_score=91,
                )
            ],
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            connection_id=CONNECTION,
            provider_account_hash=ACCOUNT,
            budget_date="2026-05-07",
            provider_day_key="liepin:account-hash-a:2026-05-07",
            timezone="Asia/Shanghai",
            daily_detail_budget=3,
            detail_open_policy_version="detail-policy-v1",
            run_id="run-1",
            query_instance_id="query-1",
            query_fingerprint="fingerprint-1",
            record_provider_return=returned.append,
        )
    )

    assert len(worker.requests) == 1
    dispatched = worker.requests[0]
    assert dispatched.requests[0].attempt_id.startswith("detail_")
    assert dispatched.requests[0].idempotency_key == "open:candidate-1"
    assert _attempt_state(store, dispatched.requests[0].attempt_id) == ("completed", "consumed")
    assert result.detail_candidates[0].candidate.raw["score_evidence_source"] == "detail_enriched"
    assert result.detail_candidates[0].candidate.raw["raw_payload_artifact_ref"] == "worker://details/candidate-1.json"
    assert returned[0].provider_snapshot is not None
    assert returned[0].provider_snapshot.payload_kind == "detail"
    assert returned[0].provider_snapshot.score_evidence_source == "detail_enriched"


def test_detail_loop_replay_of_completed_attempt_does_not_reopen_detail(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    worker_response = LiepinDetailOpenResponse(
        worker_command_id="cmd-1",
        results=[
            LiepinDetailOpenResult(
                request_id="detail:candidate-1",
                attempt_id="placeholder",
                idempotency_key="open:candidate-1",
                status="completed",
                worker_response_id="worker-response-1",
                worker_command_id="cmd-1",
                raw_evidence_ref="worker://details/candidate-1.json",
                diagnostics=LiepinDetailWorkerDiagnostics(page_loaded=True, payload_seen=True),
                candidate=_worker_detail(),
            )
        ],
    )
    first_worker = RecordingWorker(worker_response)
    replay_worker = CrashingWorker()
    request = {
        "store": store,
        "card_candidates": [
            LiepinCardCandidate(
                candidate_id="candidate-1",
                stable_provider_id="candidate-1",
                weak_fingerprint="weak-1",
                card_value_score=91,
            )
        ],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "actor_id": ACTOR,
        "connection_id": CONNECTION,
        "provider_account_hash": ACCOUNT,
        "budget_date": "2026-05-07",
        "provider_day_key": "liepin:account-hash-a:2026-05-07",
        "timezone": "Asia/Shanghai",
        "daily_detail_budget": 3,
        "detail_open_policy_version": "detail-policy-v1",
        "run_id": "run-1",
        "query_instance_id": "query-1",
        "query_fingerprint": "fingerprint-1",
    }

    asyncio.run(execute_liepin_detail_open_plan(worker_client=first_worker, **request))
    first_attempt_id = first_worker.requests[0].requests[0].attempt_id

    replay_result = asyncio.run(execute_liepin_detail_open_plan(worker_client=replay_worker, **request))

    assert replay_worker.requests == []
    assert replay_result.detail_candidates == []
    assert _attempt_state(store, first_attempt_id) == ("completed", "consumed")


def test_detail_loop_marks_unknown_crash_after_dispatch_as_possibly_consumed(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    worker = CrashingWorker()

    with pytest.raises(RuntimeError, match="browser died"):
        asyncio.run(
            execute_liepin_detail_open_plan(
                store=store,
                worker_client=worker,
                card_candidates=[
                    LiepinCardCandidate(
                        candidate_id="candidate-crash",
                        stable_provider_id="candidate-crash",
                        weak_fingerprint="weak-crash",
                        card_value_score=91,
                    )
                ],
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                actor_id=ACTOR,
                connection_id=CONNECTION,
                provider_account_hash=ACCOUNT,
                budget_date="2026-05-07",
                provider_day_key="liepin:account-hash-a:2026-05-07",
                timezone="Asia/Shanghai",
                daily_detail_budget=3,
                detail_open_policy_version="detail-policy-v1",
                run_id="run-1",
                query_instance_id="query-1",
                query_fingerprint="fingerprint-1",
            )
        )

    assert len(worker.requests) == 1
    attempt_id = worker.requests[0].requests[0].attempt_id
    assert _attempt_state(store, attempt_id) == ("unknown", "possibly_consumed")


def test_detail_scorecard_metadata_keeps_card_and_detail_refs_separate() -> None:
    card = _scorecard("candidate-1", overall_score=72, score_evidence_source="card_only")
    detail = _scorecard("candidate-1", overall_score=86, score_evidence_source="detail_enriched")

    enriched = build_detail_scorecard_metadata(
        card_scorecard=card,
        detail_scorecard=detail,
        card_scorecard_ref="artifact:scorecards/card/candidate-1.json",
        detail_scorecard_ref="artifact:scorecards/detail/candidate-1.json",
        detail_open_reason="detail_budget_available",
        detail_open_policy_version="detail-policy-v1",
    )

    assert enriched.card_scorecard_ref == "artifact:scorecards/card/candidate-1.json"
    assert enriched.detail_scorecard_ref == "artifact:scorecards/detail/candidate-1.json"
    assert enriched.score_delta == 14
    assert enriched.score_evidence_source == "detail_enriched"
    assert enriched.detail_open_reason == "detail_budget_available"
    assert enriched.detail_open_policy_version == "detail-policy-v1"


def _attempt_state(store: LiepinStore, attempt_id: str) -> tuple[str, str]:
    row = sqlite3.connect(store.db_path).execute(
        "SELECT state, consumption_state FROM liepin_detail_attempts WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    assert row is not None
    return row[0], row[1]


def _worker_detail() -> LiepinWorkerCandidateDetail:
    return LiepinWorkerCandidateDetail(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "detailBody": "<html>Private detail</html>",
            "resumeText": "Private detail text",
        },
        normalized_text="Python backend engineer detail",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="weak-1",
        identity_confidence="provider_subject_id",
        extraction_source="network",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_possible",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _scorecard(resume_id: str, *, overall_score: int, score_evidence_source: str) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=overall_score,
        must_have_match_score=80,
        preferred_match_score=75,
        risk_score=20,
        reasoning_summary="evidence-grounded score",
        confidence="high",
        source_round=1,
        score_evidence_source=score_evidence_source,
    )
