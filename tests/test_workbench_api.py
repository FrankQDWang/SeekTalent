from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter
from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderSnapshot
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import ResumeCandidate
from seektalent.progress import ProgressEvent
from seektalent.providers.liepin.worker_contracts import LoginHandoff
from seektalent.providers.liepin.worker_contracts import LoginRelayCompleteResult
from seektalent.providers.liepin.worker_contracts import LoginRelayInputResult
from seektalent.providers.liepin.worker_contracts import LoginRelaySnapshot
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent_ui.server import RunRegistry, create_app
from seektalent_ui.workbench_store import WorkbenchUser
from tests.settings_factory import make_settings


CSRF_COOKIE_NAME = "seektalent_workbench_csrf"


class FakeWorkbenchRuntime:
    started = threading.Event()
    release = threading.Event()
    calls: list[dict[str, str]] = []
    error_message: str | None = None
    progress_events: list[ProgressEvent] = []
    artifacts: object = object()

    def __init__(self, settings: AppSettings) -> None:
        del settings

    def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> object:
        self.calls.append({"job_title": job_title, "jd": jd, "notes": notes})
        self.started.set()
        for event in self.progress_events:
            if progress_callback is not None:
                progress_callback(event)
        self.release.wait(timeout=2)
        if self.error_message is not None:
            raise RuntimeError(self.error_message)
        return self.artifacts


def _reset_fake_runtime() -> None:
    FakeWorkbenchRuntime.started = threading.Event()
    FakeWorkbenchRuntime.release = threading.Event()
    FakeWorkbenchRuntime.calls = []
    FakeWorkbenchRuntime.error_message = None
    FakeWorkbenchRuntime.progress_events = []
    FakeWorkbenchRuntime.artifacts = object()


def _client(tmp_path: Path, *, runtime_factory=FakeWorkbenchRuntime) -> TestClient:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    return TestClient(
        create_app(RunRegistry(settings, runtime_factory=runtime_factory), settings=settings),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / ".seektalent" / "workbench.sqlite3"


def _session_digest(client: TestClient) -> str:
    from seektalent_ui.auth import session_token_digest

    token = client.cookies.get("seektalent_workbench_session")
    assert token is not None
    return session_token_digest(token)


def _bootstrap_and_login(client: TestClient, *, email: str = "admin@example.com", password: str = "correct horse"):
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"email": email, "password": password, "displayName": "Admin User"},
    )
    assert bootstrap.status_code == 201, bootstrap.text
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 204, login.text
    return bootstrap.json()


def _workbench_user_from_bootstrap(payload: dict) -> WorkbenchUser:
    user = payload["user"]
    return WorkbenchUser(
        user_id=user["userId"],
        email=user["email"],
        display_name=user["displayName"],
        role=user["role"],
        workspace_id=user["workspaceId"],
    )


def _csrf_header(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {"X-CSRF-Token": token}


def _create_session(client: TestClient) -> dict:
    response = client.post(
        "/api/workbench/sessions",
        headers=_csrf_header(client),
        json={
            "jobTitle": "Python Engineer",
            "jdText": "Build Python agents and ranking systems.",
            "notes": "Prefer retrieval experience.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve_triage(client: TestClient, session_id: str) -> dict:
    response = client.post(
        f"/api/workbench/sessions/{session_id}/triage/approve",
        headers=_csrf_header(client),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _candidate_artifacts(*, resume_id: str = "resume-1", source_resume_id: str = "provider-secret-id") -> object:
    return SimpleNamespace(
        final_result=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    resume_id=resume_id,
                    final_score=91,
                    fit_bucket="fit",
                    match_summary="Strong FastAPI and retrieval systems background.",
                    why_selected="Best match for backend agent workflow.",
                    strengths=["Built SSE APIs", "Owned retrieval ranking"],
                    weaknesses=["Limited public benchmark ownership"],
                    matched_must_haves=["FastAPI", "retrieval systems"],
                    matched_preferences=["agent tooling"],
                    risk_flags=["benchmark depth unclear"],
                    source_round=1,
                )
            ]
        ),
        candidate_store={
            resume_id: SimpleNamespace(
                source_resume_id=source_resume_id,
                raw={"Cookie": "secret-cookie", "fullText": "raw private resume"},
            )
        },
        normalized_store={
            resume_id: SimpleNamespace(
                candidate_name="Lin Qian",
                headline="Backend platform engineer",
                current_title="Senior Backend Engineer",
                current_company="SearchCo",
                locations=["Shanghai"],
                raw_text_excerpt="Private full text excerpt should not be returned by ordinary API.",
            )
        },
    )


def _wait_for_source_status(
    client: TestClient,
    session_id: str,
    source_run_id: str,
    expected: str,
    *,
    timeout: float = 2.0,
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/workbench/sessions/{session_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        run = next(item for item in payload["sourceRuns"] if item["sourceRunId"] == source_run_id)
        if run["status"] == expected:
            return run
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for sourceRunId={source_run_id} status={expected}")


def _insert_user(tmp_path: Path, *, email: str, password_hash: str) -> str:
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        user_id = "user-b"
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, password_hash, disabled_at, created_at)
            VALUES (?, ?, ?, ?, NULL, '2026-01-01T00:00:00+00:00')
            """,
            (user_id, email, "User B", password_hash),
        )
        conn.execute(
            """
            INSERT INTO workspace_memberships (workspace_id, user_id, role, created_at)
            VALUES ('default', ?, 'member', '2026-01-01T00:00:00+00:00')
            """,
            (user_id,),
        )
    return user_id


def test_workbench_session_routes_are_exposed_by_router_module(tmp_path: Path) -> None:
    from seektalent_ui import workbench_routes

    assert isinstance(workbench_routes.router, APIRouter)

    client = _client(tmp_path)
    paths = {route.path for route in client.app.routes}
    assert "/api/workbench/sessions" in paths
    assert "/api/workbench/sessions/{session_id}" in paths
    assert "/api/workbench/settings" in paths


def test_unauthenticated_requests_cannot_list_workbench_sessions(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/workbench/sessions")

    assert response.status_code == 401


def test_authenticated_session_creation_returns_default_source_cards(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)

    payload = _create_session(client)
    assert payload["jobTitle"] == "Python Engineer"
    assert payload["jdText"] == "Build Python agents and ranking systems."
    assert payload["notes"] == "Prefer retrieval experience."
    assert payload["workspaceId"] == "default"
    assert payload["ownerUserId"]
    assert {card["sourceKind"] for card in payload["sourceCards"]} == {"cts", "liepin"}
    cards = {card["sourceKind"]: card for card in payload["sourceCards"]}
    assert cards["cts"]["status"] == "queued"
    assert cards["cts"]["authState"] == "not_required"
    assert cards["liepin"]["status"] == "blocked"
    assert cards["liepin"]["authState"] == "login_required"
    assert cards["liepin"]["warningCode"] == "login_required"
    assert {run["sourceKind"] for run in payload["sourceRuns"]} == {"cts", "liepin"}
    assert payload["requirementTriage"]["status"] == "draft"
    assert payload["requirementTriage"]["mustHaves"] == []
    assert payload["requirementTriage"]["generatedQueryHints"] == []

    list_response = client.get("/api/workbench/sessions")
    assert list_response.status_code == 200
    listed = list_response.json()["sessions"]
    assert [item["sessionId"] for item in listed] == [payload["sessionId"]]
    assert listed[0]["sourceCards"] == payload["sourceCards"]


def test_user_cannot_read_another_users_sessions(tmp_path: Path) -> None:
    from seektalent_ui.auth import hash_password

    client = _client(tmp_path)
    _bootstrap_and_login(client)
    _insert_user(tmp_path, email="user-b@example.com", password_hash=hash_password("correct horse"))

    created = client.post(
        "/api/workbench/sessions",
        headers=_csrf_header(client),
        json={"jobTitle": "Backend Engineer", "jdText": "Own APIs and data stores."},
    )
    assert created.status_code == 201
    session_id = created.json()["sessionId"]

    login_b = client.post("/api/auth/login", json={"email": "user-b@example.com", "password": "correct horse"})
    assert login_b.status_code == 204

    list_b = client.get("/api/workbench/sessions")
    assert list_b.status_code == 200
    assert list_b.json()["sessions"] == []

    read_b = client.get(f"/api/workbench/sessions/{session_id}")
    assert read_b.status_code == 404


def test_session_creation_rejects_empty_and_oversized_jd_text(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)

    empty = client.post(
        "/api/workbench/sessions",
        headers=_csrf_header(client),
        json={"jobTitle": "Engineer", "jdText": "   "},
    )
    assert empty.status_code == 400
    assert "jdText must not be empty" in empty.text

    oversized = client.post(
        "/api/workbench/sessions",
        headers=_csrf_header(client),
        json={"jobTitle": "Engineer", "jdText": "x" * 20001},
    )
    assert oversized.status_code == 400
    assert "20000" in oversized.text


def test_session_creation_rejects_oversized_job_title_and_notes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)

    oversized_title = client.post(
        "/api/workbench/sessions",
        headers=_csrf_header(client),
        json={"jobTitle": "x" * 257, "jdText": "Own APIs and data stores."},
    )
    assert oversized_title.status_code == 400

    oversized_notes = client.post(
        "/api/workbench/sessions",
        headers=_csrf_header(client),
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores.", "notes": "x" * 5001},
    )
    assert oversized_notes.status_code == 400


def test_session_creation_requires_session_bound_csrf_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)

    missing = client.post(
        "/api/workbench/sessions",
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )
    assert missing.status_code == 403

    wrong = client.post(
        "/api/workbench/sessions",
        headers={"X-CSRF-Token": "wrong-token"},
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )
    assert wrong.status_code == 403

    valid = client.post(
        "/api/workbench/sessions",
        headers=_csrf_header(client),
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )
    assert valid.status_code == 201


def test_settings_entry_requires_auth_and_returns_sources(tmp_path: Path) -> None:
    client = _client(tmp_path)
    unauthenticated = client.get("/api/workbench/settings")
    assert unauthenticated.status_code == 401

    _bootstrap_and_login(client)
    response = client.get("/api/workbench/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspaceId"] == "default"
    assert {source["sourceKind"] for source in payload["sources"]} == {"cts", "liepin"}


def test_liepin_source_connection_routes_are_scoped_and_csrf_protected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    unauthenticated = client.get("/api/workbench/source-connections")
    assert unauthenticated.status_code == 401

    _bootstrap_and_login(client)
    missing_csrf = client.post("/api/workbench/source-connections/liepin")
    assert missing_csrf.status_code == 403

    created = client.post("/api/workbench/source-connections/liepin", headers=_csrf_header(client))
    assert created.status_code == 201, created.text
    connection = created.json()
    assert connection["sourceKind"] == "liepin"
    assert connection["status"] == "login_required"
    assert connection["connectionId"].startswith("conn_")

    duplicate = client.post("/api/workbench/source-connections/liepin", headers=_csrf_header(client))
    assert duplicate.status_code == 200
    assert duplicate.json()["connectionId"] == connection["connectionId"]

    listed = client.get("/api/workbench/source-connections")
    assert listed.status_code == 200
    assert [item["connectionId"] for item in listed.json()["connections"]] == [connection["connectionId"]]


def test_liepin_login_handoff_is_safe_and_updates_source_card_state(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)

    connection_response = client.post("/api/workbench/source-connections/liepin", headers=_csrf_header(client))
    connection_id = connection_response.json()["connectionId"]

    missing_csrf = client.post(f"/api/workbench/source-connections/{connection_id}/login")
    assert missing_csrf.status_code == 403

    handoff = client.post(
        f"/api/workbench/source-connections/{connection_id}/login",
        headers=_csrf_header(client),
    )
    assert handoff.status_code == 200, handoff.text
    payload = handoff.json()
    assert payload["connectionId"] == connection_id
    assert payload["sourceKind"] == "liepin"
    assert payload["status"] == "login_in_progress"
    assert payload["handoffMode"] == "server_managed_browser"
    assert payload["safeFrameUrl"] is None
    forbidden = handoff.text.lower()
    for secret_word in ["cookie", "storage", "authorization", "cdp", "websocket", "workerurl"]:
        assert secret_word not in forbidden

    refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}")
    assert refreshed.status_code == 200
    cards = {card["sourceKind"]: card for card in refreshed.json()["sourceCards"]}
    assert cards["liepin"]["connectionId"] == connection_id
    assert cards["liepin"]["connectionStatus"] == "login_in_progress"
    assert cards["liepin"]["connectionWarningCode"] == "relay_pending_worker"

    events = client.get("/api/workbench/events")
    assert events.status_code == 200
    event_names = [event["eventName"] for event in events.json()["events"]]
    assert "source_connection_status_changed" in event_names


class FakeLiepinLoginRelayClient:
    def __init__(self) -> None:
        self.handoff_calls: list[dict[str, str | None]] = []
        self.inputs: list[dict[str, object]] = []
        self.complete_error: LiepinWorkerModeError | None = None

    async def login_handoff(
        self,
        *,
        connection_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        provider_account_hash: str | None = None,
    ) -> LoginHandoff:
        self.handoff_calls.append(
            {
                "connection_id": connection_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        return LoginHandoff.model_validate(
            {
                "connectionId": connection_id,
                "handoffToken": "redacted-handoff-token",
                "loginUrl": "seektalent://internal-login",
                "expiresAt": "2026-01-01T00:05:00+00:00",
            }
        )

    async def login_relay_snapshot(self, *, connection_id: str) -> LoginRelaySnapshot:
        return LoginRelaySnapshot.model_validate(
            {
                "connectionId": connection_id,
                "status": "login_in_progress",
                "pageTitle": "猎聘登录",
                "pageOrigin": "https://www.liepin.com",
                "imageMimeType": "image/jpeg",
                "imageBase64": "ZmFrZS1qcGVn",
                "updatedAt": "2026-01-01T00:00:01+00:00",
            }
        )

    async def submit_login_relay_input(
        self,
        *,
        connection_id: str,
        action: str,
        x: float | None = None,
        y: float | None = None,
        text: str | None = None,
        key: str | None = None,
    ) -> LoginRelayInputResult:
        self.inputs.append({"connection_id": connection_id, "action": action, "x": x, "y": y, "text": text, "key": key})
        return LoginRelayInputResult.model_validate(
            {
                "connectionId": connection_id,
                "accepted": True,
                "updatedAt": "2026-01-01T00:00:02+00:00",
            }
        )

    async def complete_login_relay(self, *, connection_id: str) -> LoginRelayCompleteResult:
        if self.complete_error is not None:
            raise self.complete_error
        return LoginRelayCompleteResult.model_validate(
            {
                "connectionId": connection_id,
                "status": "ready",
                "providerAccountHash": "acct_hash_123",
                "fixtureOnly": False,
            }
        )


def test_liepin_login_handoff_rejects_unknown_connection_before_worker_call(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fake_worker = FakeLiepinLoginRelayClient()
    client.app.state.liepin_worker_client = fake_worker
    _bootstrap_and_login(client)

    response = client.post(
        "/api/workbench/source-connections/conn_missing/login",
        headers=_csrf_header(client),
    )

    assert response.status_code == 404
    assert fake_worker.handoff_calls == []


def test_liepin_login_relay_exposes_safe_frame_and_marks_connection_connected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fake_worker = FakeLiepinLoginRelayClient()
    client.app.state.liepin_worker_client = fake_worker
    _bootstrap_and_login(client)
    session = _create_session(client)
    connection_response = client.post("/api/workbench/source-connections/liepin", headers=_csrf_header(client))
    connection_id = connection_response.json()["connectionId"]

    handoff = client.post(
        f"/api/workbench/source-connections/{connection_id}/login",
        headers=_csrf_header(client),
    )

    assert handoff.status_code == 200, handoff.text
    payload = handoff.json()
    assert payload["handoffState"] == "safe_frame_available"
    assert payload["safeFrameUrl"] == f"/api/workbench/source-connections/{connection_id}/login/frame"
    assert fake_worker.handoff_calls[0]["tenant_id"] == "local"
    assert fake_worker.handoff_calls[0]["workspace_id"] == "default"
    assert fake_worker.handoff_calls[0]["provider_account_hash"] is not None
    forbidden = handoff.text.lower()
    for secret_word in ["storage", "authorization", "cdp", "websocket", "workerurl"]:
        assert secret_word not in forbidden

    frame = client.get(payload["safeFrameUrl"])
    assert frame.status_code == 200, frame.text
    assert f"/api/workbench/source-connections/{connection_id}/login/snapshot" in frame.text

    snapshot = client.get(f"/api/workbench/source-connections/{connection_id}/login/snapshot")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json() == {
        "connectionId": connection_id,
        "status": "login_in_progress",
        "pageTitle": "猎聘登录",
        "pageOrigin": "https://www.liepin.com",
        "imageMimeType": "image/jpeg",
        "imageBase64": "ZmFrZS1qcGVn",
        "updatedAt": "2026-01-01T00:00:01+00:00",
    }

    missing_csrf = client.post(
        f"/api/workbench/source-connections/{connection_id}/login/input",
        json={"action": "click", "x": 42, "y": 24},
    )
    assert missing_csrf.status_code == 403

    accepted = client.post(
        f"/api/workbench/source-connections/{connection_id}/login/input",
        headers=_csrf_header(client),
        json={"action": "click", "x": 42, "y": 24},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] is True
    assert fake_worker.inputs == [
        {"connection_id": connection_id, "action": "click", "x": 42.0, "y": 24.0, "text": None, "key": None}
    ]

    complete = client.post(
        f"/api/workbench/source-connections/{connection_id}/login/complete",
        headers=_csrf_header(client),
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "connected"
    assert complete.json()["warningCode"] is None

    refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}")
    assert refreshed.status_code == 200
    cards = {card["sourceKind"]: card for card in refreshed.json()["sourceCards"]}
    assert cards["liepin"]["connectionStatus"] == "connected"
    assert cards["liepin"]["connectionWarningCode"] is None


def test_liepin_login_relay_complete_keeps_connection_unconnected_when_worker_cannot_verify_login(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    fake_worker = FakeLiepinLoginRelayClient()
    fake_worker.complete_error = LiepinWorkerModeError(
        "login_not_verified: Liepin login has not been verified.",
        setup_status="login_not_verified",
    )
    client.app.state.liepin_worker_client = fake_worker
    _bootstrap_and_login(client)
    session = _create_session(client)
    connection_response = client.post("/api/workbench/source-connections/liepin", headers=_csrf_header(client))
    connection_id = connection_response.json()["connectionId"]
    handoff = client.post(
        f"/api/workbench/source-connections/{connection_id}/login",
        headers=_csrf_header(client),
    )
    assert handoff.status_code == 200, handoff.text

    complete = client.post(
        f"/api/workbench/source-connections/{connection_id}/login/complete",
        headers=_csrf_header(client),
    )

    assert complete.status_code == 409
    assert complete.json()["detail"] == "Liepin login has not been verified."
    listed = client.get("/api/workbench/source-connections")
    assert listed.status_code == 200
    assert listed.json()["connections"][0]["status"] == "login_in_progress"
    refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}")
    assert refreshed.status_code == 200
    cards = {card["sourceKind"]: card for card in refreshed.json()["sourceCards"]}
    assert cards["liepin"]["connectionStatus"] == "login_in_progress"


class FakeLiepinCardWorkerClient:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []
        self.open_details_calls = 0

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        self.search_calls.append(
            {
                "keyword_query": request.keyword_query,
                "provider_context": request.provider_context,
                "round_no": round_no,
                "trace_id": trace_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        return SearchResult(
            candidates=[
                ResumeCandidate(
                    resume_id="provider-cand-1",
                    source_resume_id="provider-cand-1",
                    dedup_key="liepin-fingerprint-1",
                    search_text="Senior Backend Engineer at Redacted Cloud. FastAPI, ranking, retrieval.",
                    raw={"Cookie": "must-not-leak"},
                )
            ],
            provider_snapshots=[
                ProviderSnapshot(
                    provider_name="liepin",
                    payload_kind="card",
                    raw_payload={
                        "candidateId": "provider-cand-1",
                        "title": "Senior Backend Engineer",
                        "company": "Redacted Cloud",
                        "location": "Shanghai",
                        "summary": "FastAPI ranking and retrieval systems.",
                    },
                    normalized_text="Senior Backend Engineer Redacted Cloud Shanghai FastAPI ranking retrieval",
                    provider_subject_id="provider-cand-1",
                    provider_listing_id="listing-1",
                    synthetic_candidate_fingerprint="liepin-fingerprint-1",
                    identity_confidence="provider_subject_id",
                    extraction_source="network",
                    extractor_version="test",
                    pii_classification="no_direct_contact",
                    retention_policy="provider_snapshot_7d",
                    access_scope="local_run_only",
                    redaction_state="raw_provider_payload",
                    score_evidence_source="card_only",
                )
            ],
            diagnostics=["card_search:network"],
            exhausted=True,
            raw_candidate_count=1,
            request_payload={"keyword": request.keyword_query, "pageSize": request.page_size},
        )

    async def open_details(self, request) -> object:
        del request
        self.open_details_calls += 1
        raise AssertionError("M4 card-level search must not open Liepin detail pages.")


def test_liepin_card_level_source_run_persists_card_evidence_without_opening_details(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fake_worker = FakeLiepinCardWorkerClient()
    client.app.state.workbench_job_runner.liepin_worker_client = fake_worker
    bootstrap = _bootstrap_and_login(client)
    user = _workbench_user_from_bootstrap(bootstrap)
    session = _create_session(client)
    session_id = session["sessionId"]
    _approve_triage(client, session_id)
    connection_response = client.post("/api/workbench/source-connections/liepin", headers=_csrf_header(client))
    connection_id = connection_response.json()["connectionId"]
    connected = client.app.state.workbench_store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection_id,
        provider_account_hash="acct_hash_123",
    )
    assert connected is not None

    start = client.post(
        f"/api/workbench/sessions/{session_id}/source-runs",
        headers=_csrf_header(client),
        json={"sourceKind": "liepin"},
    )

    assert start.status_code == 202, start.text
    source_run_id = start.json()["sourceRunId"]
    run = _wait_for_source_status(client, session_id, source_run_id, "completed")
    assert run["status"] == "completed"
    assert fake_worker.search_calls[0]["provider_account_hash"] == "acct_hash_123"
    assert fake_worker.open_details_calls == 0

    refreshed = client.get(f"/api/workbench/sessions/{session_id}")
    assert refreshed.status_code == 200
    cards = {card["sourceKind"]: card for card in refreshed.json()["sourceCards"]}
    assert cards["liepin"]["status"] == "completed"
    assert cards["liepin"]["cardsScannedCount"] == 1
    assert cards["liepin"]["uniqueCandidatesCount"] == 1
    assert cards["liepin"]["warningCode"] is None

    events = client.get("/api/workbench/events")
    assert events.status_code == 200
    event_names = [event["eventName"] for event in events.json()["events"]]
    assert "liepin_card_search_completed" in event_names
    assert "candidate_review_item_upserted" in event_names
    assert "source_run_completed" in event_names
    assert not any("detail" in event_name for event_name in event_names)

    queue = client.get(f"/api/workbench/sessions/{session_id}/candidates")
    assert queue.status_code == 200
    items = queue.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Senior Backend Engineer"
    assert items[0]["company"] == "Redacted Cloud"
    assert items[0]["location"] == "Shanghai"
    assert items[0]["sourceBadges"] == ["Liepin"]
    assert items[0]["evidenceLevel"] == "card"
    assert items[0]["evidence"][0]["sourceKind"] == "liepin"
    assert items[0]["evidence"][0]["evidenceLevel"] == "card"
    assert "must-not-leak" not in queue.text
    assert "provider-cand-1" not in queue.text


def test_triage_update_and_approve_are_scoped_and_csrf_protected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    session_id = session["sessionId"]

    missing_csrf = client.put(
        f"/api/workbench/sessions/{session_id}/triage",
        json={"mustHaves": ["Python"], "niceToHaves": [], "synonyms": []},
    )
    assert missing_csrf.status_code == 403

    updated = client.put(
        f"/api/workbench/sessions/{session_id}/triage",
        headers=_csrf_header(client),
        json={
            "mustHaves": ["Python", "<script>plain text</script>"],
            "niceToHaves": ["retrieval"],
            "synonyms": ["LLM agent"],
            "seniorityFilters": ["senior"],
            "exclusions": ["frontend-only"],
            "generatedQueryHints": ["python agent ranking"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "draft"
    assert updated.json()["mustHaves"][1] == "<script>plain text</script>"

    approve_missing_csrf = client.post(f"/api/workbench/sessions/{session_id}/triage/approve")
    assert approve_missing_csrf.status_code == 403

    approved = _approve_triage(client, session_id)
    assert approved["status"] == "approved"
    assert approved["mustHaves"] == ["Python", "<script>plain text</script>"]

    read_back = client.get(f"/api/workbench/sessions/{session_id}/triage")
    assert read_back.status_code == 200
    assert read_back.json()["status"] == "approved"


def test_source_run_start_requires_approved_triage_and_blocks_liepin(tmp_path: Path) -> None:
    _reset_fake_runtime()
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    runs = {run["sourceKind"]: run for run in session["sourceRuns"]}

    blocked = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{runs['cts']['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert blocked.status_code == 409
    assert not FakeWorkbenchRuntime.started.is_set()

    _approve_triage(client, session["sessionId"])
    liepin = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{runs['liepin']['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert liepin.status_code == 409
    assert liepin.json()["detail"] == "liepin_connection_not_connected"
    refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}")
    cards = {card["sourceKind"]: card for card in refreshed.json()["sourceCards"]}
    assert cards["liepin"]["status"] == "blocked"
    assert cards["liepin"]["authState"] == "login_required"
    assert cards["liepin"]["warningCode"] == "login_required"
    assert not FakeWorkbenchRuntime.started.is_set()


def test_cts_source_run_start_creates_job_and_completes_with_events(tmp_path: Path) -> None:
    _reset_fake_runtime()
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(session_id=session["sessionId"], client=client)

    started_at = time.time()
    response = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    elapsed = time.time() - started_at

    assert response.status_code == 202, response.text
    assert elapsed < 0.5
    payload = response.json()
    assert payload["sourceRunId"] == cts_run["sourceRunId"]
    assert payload["sourceKind"] == "cts"
    assert payload["job"]["status"] in {"queued", "running"}
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    running = _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "running")
    assert running["status"] == "running"

    duplicate = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["job"]["jobId"] == payload["job"]["jobId"]

    FakeWorkbenchRuntime.release.set()
    completed = _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")
    assert completed["status"] == "completed"
    assert len(FakeWorkbenchRuntime.calls) == 1
    assert "Approved requirement triage:" in FakeWorkbenchRuntime.calls[0]["notes"]

    events = client.get("/api/workbench/events?after_seq=0")
    assert events.status_code == 200
    event_names = [event["eventName"] for event in events.json()["events"]]
    assert "source_run_started" in event_names
    assert "requirement_triage_used" in event_names
    assert "source_run_completed" in event_names
    assert "session_completed" not in event_names


def test_source_run_start_by_source_kind_is_idempotent(tmp_path: Path) -> None:
    _reset_fake_runtime()
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])

    first = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs",
        headers=_csrf_header(client),
        json={"sourceKind": "cts", "idempotencyKey": "same-key"},
    )
    assert first.status_code == 202, first.text
    assert first.json()["sourceRunId"] == cts_run["sourceRunId"]
    assert FakeWorkbenchRuntime.started.wait(timeout=1)

    second = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs",
        headers=_csrf_header(client),
        json={"sourceKind": "cts", "idempotencyKey": "same-key"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["job"]["jobId"] == first.json()["job"]["jobId"]

    active_duplicate = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs",
        headers=_csrf_header(client),
        json={"sourceKind": "cts", "idempotencyKey": "different-key"},
    )
    assert active_duplicate.status_code == 200
    assert active_duplicate.json()["job"]["jobId"] == first.json()["job"]["jobId"]

    liepin = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs",
        headers=_csrf_header(client),
        json={"sourceKind": "liepin"},
    )
    assert liepin.status_code == 409
    assert liepin.json()["detail"] == "liepin_connection_not_connected"
    FakeWorkbenchRuntime.release.set()
    _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")


def test_runtime_failure_messages_are_redacted_outside_events(tmp_path: Path) -> None:
    _reset_fake_runtime()
    FakeWorkbenchRuntime.error_message = (
        "Cookie=abc Authorization: Bearer token wsEndpoint=wss://debug playwright "
        "access_token=abc api_key=def secret password failed"
    )
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])

    start = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert start.status_code == 202
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()
    failed = _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "failed")

    session_payload = client.get(f"/api/workbench/sessions/{session['sessionId']}").json()
    events_payload = client.get("/api/workbench/events?after_seq=0").json()
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        job_error = conn.execute(
            "SELECT error_message FROM source_run_jobs WHERE source_run_id = ?",
            (cts_run["sourceRunId"],),
        ).fetchone()[0]
    serialized = f"{failed} {session_payload} {events_payload} {job_error}"
    assert "[REDACTED]" in serialized
    for forbidden in [
        "Cookie",
        "Authorization",
        "Bearer",
        "wsEndpoint",
        "playwright",
        "wss://debug",
        "access_token",
        "api_key",
        "secret",
        "password",
    ]:
        assert forbidden not in serialized


def test_runtime_progress_callback_persists_redacted_workbench_event(tmp_path: Path) -> None:
    _reset_fake_runtime()
    FakeWorkbenchRuntime.progress_events = [
        ProgressEvent(
            type="search_started",
            message="query started with Cookie secret",
            round_no=1,
            payload={
                "stage": "search",
                "Authorization": "Bearer secret",
                "accessToken": "abc",
                "api_key": "def",
                "password": "hidden",
                "safe": "visible",
            },
        )
    ]
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])

    response = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert response.status_code == 202
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()
    _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")

    events = client.get("/api/workbench/events?after_seq=0").json()["events"]
    progress = [event for event in events if event["eventName"] == "runtime_search_started"]
    assert progress
    assert progress[0]["sessionId"] == session["sessionId"]
    assert progress[0]["sourceRunId"] == cts_run["sourceRunId"]
    serialized = str(progress[0])
    assert "visible" in serialized
    assert "Cookie" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
    assert "accessToken" not in serialized
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "session_completed" not in [event["eventName"] for event in events]


def test_cts_runtime_results_create_candidate_review_queue_without_raw_payload(tmp_path: Path) -> None:
    _reset_fake_runtime()
    FakeWorkbenchRuntime.artifacts = _candidate_artifacts(
        resume_id="provider-external-id-123",
        source_resume_id="provider-external-id-123",
    )
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])

    start = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert start.status_code == 202
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()
    _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/candidates")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["displayName"] == "Lin Qian"
    assert item["title"] == "Senior Backend Engineer"
    assert item["company"] == "SearchCo"
    assert item["location"] == "Shanghai"
    assert item["aggregateScore"] == 91
    assert item["fitBucket"] == "fit"
    assert item["sourceBadges"] == ["CTS"]
    assert item["evidenceLevel"] == "final"
    assert item["matchedMustHaves"] == ["FastAPI", "retrieval systems"]
    assert item["missingRisks"] == ["Limited public benchmark ownership", "benchmark depth unclear"]
    assert item["evidence"][0]["sourceKind"] == "cts"
    assert item["evidence"][0]["sourceRunId"] == cts_run["sourceRunId"]
    serialized = str(item)
    assert "secret-cookie" not in serialized
    assert "raw private resume" not in serialized
    assert "provider-external-id-123" not in serialized
    assert "run_dir" not in serialized
    assert "trace_log_path" not in serialized
    event_payload = client.get("/api/workbench/events?after_seq=0").json()
    assert "provider-external-id-123" not in str(event_payload)


def test_candidate_review_action_and_note_persist_with_csrf(tmp_path: Path) -> None:
    _reset_fake_runtime()
    FakeWorkbenchRuntime.artifacts = _candidate_artifacts()
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])
    start = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert start.status_code == 202
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()
    _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")
    item = client.get(f"/api/workbench/sessions/{session['sessionId']}/candidates").json()["items"][0]

    rejected = client.put(
        f"/api/workbench/sessions/{session['sessionId']}/candidates/{item['reviewItemId']}",
        json={"status": "promising", "note": "Call this person first."},
    )
    assert rejected.status_code == 403

    empty_update = client.put(
        f"/api/workbench/sessions/{session['sessionId']}/candidates/{item['reviewItemId']}",
        headers=_csrf_header(client),
        json={},
    )
    assert empty_update.status_code == 400

    updated = client.put(
        f"/api/workbench/sessions/{session['sessionId']}/candidates/{item['reviewItemId']}",
        headers=_csrf_header(client),
        json={"status": "promising", "note": "Call this person first."},
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "promising"
    assert updated.json()["note"] == "Call this person first."
    events_after_update = client.get("/api/workbench/events?after_seq=0").json()["events"]
    repeated = client.put(
        f"/api/workbench/sessions/{session['sessionId']}/candidates/{item['reviewItemId']}",
        headers=_csrf_header(client),
        json={"status": "promising", "note": "Call this person first."},
    )
    assert repeated.status_code == 200
    events_after_repeated_update = client.get("/api/workbench/events?after_seq=0").json()["events"]
    assert len(events_after_repeated_update) == len(events_after_update)
    refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}/candidates").json()["items"][0]
    assert refreshed["status"] == "promising"
    assert refreshed["note"] == "Call this person first."


def test_workbench_events_after_seq_and_redaction(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    store = client.app.state.workbench_store
    first_seq = store.append_workbench_event(
        tenant_id="local",
        workspace_id="default",
        user_id=session["ownerUserId"],
        session_id=session["sessionId"],
        source_run_id=None,
        source_kind=None,
        event_name="unsafe_payload_seen",
        payload={
            "Cookie": "secret-cookie",
            "nested": {"Authorization": "Bearer abc", "safe": "ok"},
            "message": "connect to wsEndpoint with playwright",
        },
    ).global_seq
    second_seq = store.append_workbench_event(
        tenant_id="local",
        workspace_id="default",
        user_id=session["ownerUserId"],
        session_id=session["sessionId"],
        source_run_id=None,
        source_kind=None,
        event_name="safe_event",
        payload={"safe": "value"},
    ).global_seq

    response = client.get(f"/api/workbench/events?after_seq={first_seq}")
    assert response.status_code == 200
    payload = response.json()
    assert [event["globalSeq"] for event in payload["events"]] == [second_seq]

    all_events = client.get("/api/workbench/events?after_seq=0").json()["events"]
    unsafe = next(event for event in all_events if event["eventName"] == "unsafe_payload_seen")
    serialized = str(unsafe["payload"])
    assert "secret-cookie" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
    assert "wsEndpoint" not in serialized
    assert "playwright" not in serialized


def test_workbench_sse_stream_uses_event_stream_and_last_event_id(tmp_path: Path) -> None:
    from seektalent_ui.event_routes import _sequence_from_header, stream_events

    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    store = client.app.state.workbench_store
    first = store.append_workbench_event(
        tenant_id="local",
        workspace_id="default",
        user_id=session["ownerUserId"],
        session_id=session["sessionId"],
        source_run_id=None,
        source_kind=None,
        event_name="first_event",
        payload={"value": 1},
    )
    second = store.append_workbench_event(
        tenant_id="local",
        workspace_id="default",
        user_id=session["ownerUserId"],
        session_id=session["sessionId"],
        source_run_id=None,
        source_kind=None,
        event_name="second_event",
        payload={"value": 2},
    )

    token_query = client.get("/api/workbench/events/stream?token=abc")
    assert token_query.status_code == 400
    assert _sequence_from_header(str(first.global_seq)) == first.global_seq
    sse_response = stream_events(
        request=SimpleNamespace(query_params={}, app=client.app),
        user=client.app.state.workbench_store.get_user_by_session(
            session_digest=_session_digest(client)
        ),
        session_id=client.cookies.get("seektalent_workbench_session"),
        after_seq=0,
        last_event_id=str(first.global_seq),
    )
    assert sse_response.media_type == "text/event-stream"

    recovered = client.get(f"/api/workbench/events?after_seq={first.global_seq}")
    assert [event["globalSeq"] for event in recovered.json()["events"]] == [second.global_seq]


def test_sse_generator_stops_after_session_revoke(tmp_path: Path) -> None:
    from seektalent_ui.event_routes import _event_generator

    class StreamingRequest:
        def __init__(self, app) -> None:
            self.app = app

        async def is_disconnected(self) -> bool:
            return False

    async def next_or_closed(generator) -> dict[str, str] | None:
        try:
            return await asyncio.wait_for(anext(generator), timeout=0.5)
        except StopAsyncIteration:
            return None

    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    store = client.app.state.workbench_store
    session_digest = _session_digest(client)
    user = store.get_user_by_session_readonly(session_digest=session_digest)
    assert user is not None
    first_event = store.append_workbench_event(
        tenant_id="local",
        workspace_id="default",
        user_id=session["ownerUserId"],
        session_id=session["sessionId"],
        source_run_id=None,
        source_kind=None,
        event_name="first_event",
        payload={"value": 1},
    )

    generator = _event_generator(
        request=StreamingRequest(client.app),
        user=user,
        session_digest=session_digest,
        after_seq=first_event.global_seq - 1,
    )

    async def consume_until_revoked() -> tuple[dict[str, str] | None, dict[str, str] | None, dict[str, str] | None]:
        first = await next_or_closed(generator)
        first_custom = await next_or_closed(generator)
        store.revoke_user_session(session_digest=session_digest)
        store.append_workbench_event(
            tenant_id="local",
            workspace_id="default",
            user_id=session["ownerUserId"],
            session_id=session["sessionId"],
            source_run_id=None,
            source_kind=None,
            event_name="second_event",
            payload={"value": 2},
        )
        after_revoke = await next_or_closed(generator)
        return first, first_custom, after_revoke

    first, first_custom, after_revoke = asyncio.run(consume_until_revoked())
    assert first is not None
    assert first["event"] == "workbench_event"
    assert first_custom is not None
    assert first_custom["event"] == "first_event"

    assert after_revoke is None


def test_sse_stream_auth_does_not_update_last_seen_at(tmp_path: Path) -> None:
    from seektalent_ui.auth import require_current_user_readonly

    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    session_digest = _session_digest(client)
    old_last_seen = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE user_sessions SET last_seen_at = ? WHERE session_id = ?",
            (old_last_seen, session_digest),
        )

    user = require_current_user_readonly(
        request=SimpleNamespace(app=client.app),
        cookie_session_id=client.cookies.get("seektalent_workbench_session"),
    )

    assert user.email == "admin@example.com"
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        current_last_seen = conn.execute(
            "SELECT last_seen_at FROM user_sessions WHERE session_id = ?",
            (session_digest,),
        ).fetchone()[0]
    assert current_last_seen == old_last_seen

    recovered = client.get("/api/workbench/events?after_seq=0")
    assert recovered.status_code == 200
    assert recovered.json()["events"][0]["sessionId"] == session["sessionId"]

    with sqlite3.connect(_db_path(tmp_path)) as conn:
        current_last_seen = conn.execute(
            "SELECT last_seen_at FROM user_sessions WHERE session_id = ?",
            (session_digest,),
        ).fetchone()[0]
    assert current_last_seen == old_last_seen


def test_expired_running_job_is_reconciled_on_app_startup(tmp_path: Path) -> None:
    _reset_fake_runtime()
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])
    start = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert start.status_code == 202
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    job_id = start.json()["job"]["jobId"]
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE source_run_jobs
            SET status = 'running', lease_expires_at = '2026-01-01T00:00:00+00:00'
            WHERE job_id = ?
            """,
            (job_id,),
        )
        conn.execute("UPDATE source_runs SET status = 'running' WHERE source_run_id = ?", (cts_run["sourceRunId"],))

    new_client = _client(tmp_path)
    new_client.cookies.update(client.cookies)
    reconciled = new_client.get(f"/api/workbench/sessions/{session['sessionId']}")
    assert reconciled.status_code == 200
    run = next(item for item in reconciled.json()["sourceRuns"] if item["sourceRunId"] == cts_run["sourceRunId"])
    assert run["status"] == "failed"
    assert run["warningCode"] == "job_lease_expired"

    FakeWorkbenchRuntime.release.set()


def test_expired_running_job_is_reconciled_on_session_read_without_app_restart(tmp_path: Path) -> None:
    _reset_fake_runtime()
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])
    start = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert start.status_code == 202
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    job_id = start.json()["job"]["jobId"]
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE source_run_jobs
            SET status = 'running', lease_expires_at = '2026-01-01T00:00:00+00:00'
            WHERE job_id = ?
            """,
            (job_id,),
        )
        conn.execute("UPDATE source_runs SET status = 'running' WHERE source_run_id = ?", (cts_run["sourceRunId"],))

    refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}")
    assert refreshed.status_code == 200
    run = next(item for item in refreshed.json()["sourceRuns"] if item["sourceRunId"] == cts_run["sourceRunId"])
    assert run["status"] == "failed"
    assert run["warningCode"] == "job_lease_expired"

    FakeWorkbenchRuntime.release.set()


def test_active_running_job_lease_is_renewed_before_session_reconcile(tmp_path: Path) -> None:
    _reset_fake_runtime()
    client = _client(tmp_path)
    client.app.state.workbench_job_runner.heartbeat_interval_seconds = 0.02
    _bootstrap_and_login(client)
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_triage(client, session["sessionId"])
    start = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert start.status_code == 202
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    job_id = start.json()["job"]["jobId"]
    old_lease = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE source_run_jobs
            SET status = 'running', lease_expires_at = ?
            WHERE job_id = ?
            """,
            (old_lease, job_id),
        )
        conn.execute("UPDATE source_runs SET status = 'running' WHERE source_run_id = ?", (cts_run["sourceRunId"],))

    deadline = time.time() + 1
    while time.time() < deadline:
        with sqlite3.connect(_db_path(tmp_path)) as conn:
            lease = conn.execute(
                "SELECT lease_expires_at FROM source_run_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        if lease != old_lease:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("job lease heartbeat did not renew the active job")

    refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}")
    assert refreshed.status_code == 200
    run = next(item for item in refreshed.json()["sourceRuns"] if item["sourceRunId"] == cts_run["sourceRunId"])
    assert run["status"] == "running"

    FakeWorkbenchRuntime.release.set()
    _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")


def test_user_cannot_operate_on_another_users_triage_or_source_run(tmp_path: Path) -> None:
    from seektalent_ui.auth import hash_password

    client = _client(tmp_path)
    _bootstrap_and_login(client)
    _insert_user(tmp_path, email="user-b@example.com", password_hash=hash_password("correct horse"))
    session = _create_session(client)
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")

    login_b = client.post("/api/auth/login", json={"email": "user-b@example.com", "password": "correct horse"})
    assert login_b.status_code == 204

    triage = client.get(f"/api/workbench/sessions/{session['sessionId']}/triage")
    assert triage.status_code == 404

    update = client.put(
        f"/api/workbench/sessions/{session['sessionId']}/triage",
        headers=_csrf_header(client),
        json={"mustHaves": ["Python"]},
    )
    assert update.status_code == 404

    start = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/{cts_run['sourceRunId']}/start",
        headers=_csrf_header(client),
    )
    assert start.status_code == 404
