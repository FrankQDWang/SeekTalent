from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from seektalent_ui.final_top_candidates import project_final_top_candidates
from seektalent_ui.server import create_app
from seektalent_ui.workbench_store import WorkbenchStore, WorkbenchUser
from tests.settings_factory import make_settings


CSRF_COOKIE_NAME = "seektalent_workbench_csrf"


def _store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / ".seektalent" / "workbench.sqlite3")


def _user(store: WorkbenchStore) -> WorkbenchUser:
    user, _created = store.bootstrap_admin(
        email="admin@example.com",
        display_name="Admin",
        password_hash="hash",
    )
    return user


def _client(tmp_path: Path) -> TestClient:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    return TestClient(
        create_app(settings=settings),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )


def _bootstrap_and_login(client: TestClient) -> None:
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"email": "admin@example.com", "password": "correct horse", "displayName": "Admin"},
    )
    assert bootstrap.status_code == 201, bootstrap.text
    login = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"})
    assert login.status_code == 204, login.text


def _csrf_header(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {"X-CSRF-Token": token}


def _create_api_session(client: TestClient, *, source_kinds: list[str] | None = None) -> dict:
    payload: dict[str, object] = {
        "jobTitle": "Python Engineer",
        "jdText": "Build Python agents and ranking systems.",
        "notes": "Prefer retrieval experience.",
    }
    if source_kinds is not None:
        payload["sourceKinds"] = source_kinds
    response = client.post("/api/workbench/sessions", headers=_csrf_header(client), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _requirement_sheet(job_title: str = "Python Engineer"):
    from seektalent.models import RequirementSheet

    return RequirementSheet(
        job_title=job_title,
        title_anchor_terms=[job_title],
        title_anchor_rationale=f"{job_title} is the searchable title anchor.",
        role_summary="Build Python agents and ranking systems.",
        must_have_capabilities=["5 年以上 Python"],
        preferred_capabilities=[],
        exclusion_signals=[],
        hard_constraints={},
        preferences={"preferred_query_terms": ["python engineer"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize Python agent and ranking evidence.",
    )


def _approve_requirement_review_with_visible_criteria(
    store: WorkbenchStore, *, user: WorkbenchUser, session_id: str, job_title: str = "Python Engineer"
) -> None:
    store.update_requirement_review(
        user=user,
        session_id=session_id,
        requirement_sheet=_requirement_sheet(job_title),
    )
    review = store.approve_requirement_review(user=user, session_id=session_id)
    assert review is not None
    assert review.status == "approved"


def test_backend_rejects_blank_requirement_review_approval(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="Python Engineer",
        jd_text="Build Python agents and ranking systems.",
        notes="",
        source_kinds=["cts"],
    )

    try:
        store.approve_requirement_review(user=user, session_id=session.session_id)
    except PermissionError as exc:
        assert str(exc) == "requirement_review_empty"
    else:
        raise AssertionError("blank requirement review approval should be rejected")

    _approve_requirement_review_with_visible_criteria(
        store, user=user, session_id=session.session_id, job_title=session.job_title
    )


def test_http_rejects_blank_requirement_review_approval(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_api_session(client, source_kinds=["cts"])

    blank = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/requirements/approve",
        headers=_csrf_header(client),
    )

    assert blank.status_code == 409
    assert blank.json()["detail"] == "requirement_review_empty"

    update = client.put(
        f"/api/workbench/sessions/{session['sessionId']}/requirements",
        headers=_csrf_header(client),
        json={"requirement_sheet": _requirement_sheet().model_dump(mode="json")},
    )
    assert update.status_code == 200, update.text
    approved = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/requirements/approve",
        headers=_csrf_header(client),
    )
    assert approved.status_code == 200, approved.text


def test_review_items_expose_precise_source_badges(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="Python Engineer",
        jd_text="Build Python agents.",
        notes="",
        source_kinds=["cts", "liepin"],
    )
    cts_run = next(run for run in session.source_runs if run.source_kind == "cts")
    liepin_run = next(run for run in session.source_runs if run.source_kind == "liepin")
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=cts_run.source_run_id,
        review_item_id="review-cts",
        evidence_id="ev-cts-final",
        source_kind="cts",
        evidence_level="final",
        provider_hash="hash-cts",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        review_item_id="review-card",
        evidence_id="ev-liepin-card",
        source_kind="liepin",
        evidence_level="card",
        provider_hash="hash-card",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        review_item_id="review-detail",
        evidence_id="ev-liepin-detail",
        source_kind="liepin",
        evidence_level="detail",
        provider_hash="hash-detail",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=cts_run.source_run_id,
        review_item_id="review-multi",
        evidence_id="ev-multi-cts",
        source_kind="cts",
        evidence_level="final",
        provider_hash="hash-multi-cts",
    )
    _insert_evidence(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        review_item_id="review-multi",
        evidence_id="ev-multi-liepin",
        source_kind="liepin",
        evidence_level="detail",
        provider_hash="hash-multi-liepin",
    )

    items = {item.review_item_id: item for item in store.list_candidate_review_items(user=user, session_id=session.session_id)}

    assert items["review-cts"].source_badges == ["CTS final"]
    assert items["review-card"].source_badges == ["Liepin card"]
    assert items["review-detail"].source_badges == ["Liepin detail"]
    assert items["review-multi"].source_badges == ["CTS final", "Liepin detail", "Multiple sources"]


def test_final_top10_groups_candidates_by_runtime_identity_id(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_api_session(client, source_kinds=["cts", "liepin"])
    store: WorkbenchStore = client.app.state.workbench_store
    user = store.get_user_by_session(session_digest=_session_digest(client))
    assert user is not None
    source_runs = store.get_workbench_session(user=user, session_id=session["sessionId"]).source_runs
    cts_run = next(run for run in source_runs if run.source_kind == "cts")
    liepin_run = next(run for run in source_runs if run.source_kind == "liepin")

    _insert_review_item(
        store,
        user=user,
        session_id=session["sessionId"],
        source_run_id=cts_run.source_run_id,
        review_item_id="review-same-cts",
        evidence_id="ev-same-cts",
        source_kind="cts",
        evidence_level="final",
        provider_hash="provider-a",
        runtime_identity_id="identity-same",
        score=93,
        display_name="Lin Qian",
        company="OldCo",
        summary="OldCo platform work 2019.01-2021.05.",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session["sessionId"],
        source_run_id=liepin_run.source_run_id,
        review_item_id="review-same-liepin",
        evidence_id="ev-same-liepin",
        source_kind="liepin",
        evidence_level="detail",
        provider_hash="provider-b",
        runtime_identity_id="identity-same",
        score=88,
        display_name="Lin Qian",
        company="NewCo",
        summary="NewCo platform work 2024.05-至今.",
    )
    for index in range(12):
        _insert_review_item(
            store,
            user=user,
            session_id=session["sessionId"],
            source_run_id=cts_run.source_run_id,
            review_item_id=f"review-extra-{index}",
            evidence_id=f"ev-extra-{index}",
            source_kind="cts",
            evidence_level="final",
            provider_hash=f"provider-extra-{index}",
            runtime_identity_id=f"identity-extra-{index}",
            score=80 - index,
            display_name=f"Extra {index}",
        )

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/final-top10")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["coverageStatus"] == session["runtimeSourceState"]["coverageStatus"]
    assert len(payload["items"]) == 10
    review_ids = {item["reviewItemId"] for item in payload["items"]}
    assert {"review-same-cts", "review-same-liepin"} & review_ids
    assert not {"review-same-cts", "review-same-liepin"} <= review_ids
    merged = next(item for item in payload["items"] if item["runtimeIdentityId"] == "identity-same")
    assert merged["sourceBadges"] == ["CTS final", "Liepin detail", "Multiple sources"]
    assert merged["company"] == "NewCo"
    assert merged["aggregateScore"] == 93


def test_final_top10_projection_uses_connected_components_instead_of_group_scan() -> None:
    source = Path("src/seektalent_ui/final_top_candidates.py").read_text(encoding="utf-8")

    assert "key_to_group.items()" not in source


def test_resume_snapshot_privacy_helpers_have_single_home() -> None:
    graph_source = Path("src/seektalent_ui/workbench_candidate_graph.py").read_text(encoding="utf-8")
    snapshot_source = Path("src/seektalent_ui/resume_snapshot_projection.py").read_text(encoding="utf-8")

    for helper_name in (
        "_json_object",
        "_json_list",
        "_snapshot_materialization_allowed",
        "_safe_text",
    ):
        assert f"def {helper_name}" not in graph_source
        assert f"def {helper_name}" not in snapshot_source


def test_final_top10_connected_components_merge_transitive_identity_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="Platform Engineer",
        jd_text="Build Python platform systems.",
        notes="Prefer Shanghai candidates.",
        source_kinds=["cts", "liepin"],
    )
    source_runs = {run.source_kind: run for run in session.source_runs}
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=source_runs["cts"].source_run_id,
        review_item_id="review-identity-anchor",
        evidence_id="ev-identity-anchor",
        source_kind="cts",
        evidence_level="final",
        provider_hash="provider-cts-anchor",
        runtime_identity_id="identity-chain",
        score=91,
        display_name="Lin Qian",
        company="AnchorCo",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=source_runs["liepin"].source_run_id,
        review_item_id="review-bridge",
        evidence_id="ev-bridge",
        source_kind="liepin",
        evidence_level="detail",
        provider_hash="provider-liepin-bridge",
        runtime_identity_id="identity-chain",
        score=89,
        display_name="Lin Qian",
        company="BridgeCo",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=source_runs["liepin"].source_run_id,
        review_item_id="review-provider-match",
        evidence_id="ev-provider-match",
        source_kind="liepin",
        evidence_level="card",
        provider_hash="provider-liepin-bridge",
        score=87,
        display_name="Lin Qian",
        company="ProviderCo",
    )

    final_items = project_final_top_candidates(store.list_candidate_review_items(user=user, session_id=session.session_id))

    assert len(final_items) == 1
    assert final_items[0].runtimeIdentityId == "identity-chain"
    assert final_items[0].aggregateScore == 91
    assert final_items[0].mergedReviewItemIds == [
        "review-bridge",
        "review-identity-anchor",
        "review-provider-match",
    ]


def test_final_top10_does_not_merge_weak_name_title_location_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="Platform Engineer",
        jd_text="Build Python platform systems.",
        notes="Prefer Shanghai candidates.",
        source_kinds=["cts", "liepin"],
    )
    source_runs = {run.source_kind: run for run in session.source_runs}
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=source_runs["cts"].source_run_id,
        review_item_id="review-cts-old",
        evidence_id="ev-cts-old",
        source_kind="cts",
        evidence_level="final",
        provider_hash="provider-old",
        score=95,
        display_name="Lin Qian",
        title="Platform Engineer",
        company="OldCo",
        location="Shanghai",
        summary="OldCo platform work 2019.01-2021.05.",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=source_runs["liepin"].source_run_id,
        review_item_id="review-liepin-new",
        evidence_id="ev-liepin-new",
        source_kind="liepin",
        evidence_level="card",
        provider_hash="provider-new",
        score=66,
        display_name="Lin Qian",
        title="Platform Engineer",
        company="NewCo",
        location="Shanghai",
        summary="NewCo platform work 2024.05-至今.",
    )

    final_items = project_final_top_candidates(store.list_candidate_review_items(user=user, session_id=session.session_id))

    assert len(final_items) == 2
    assert {item.company for item in final_items} == {"OldCo", "NewCo"}


def test_final_top10_does_not_merge_cross_source_provider_hash_collision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="Platform Engineer",
        jd_text="Build Python platform systems.",
        notes="Prefer Shanghai candidates.",
        source_kinds=["cts", "liepin"],
    )
    source_runs = {run.source_kind: run for run in session.source_runs}
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=source_runs["cts"].source_run_id,
        review_item_id="review-cts-collision",
        evidence_id="ev-cts-collision",
        source_kind="cts",
        evidence_level="final",
        provider_hash="same-provider-hash",
        score=91,
        display_name="Alice Chen",
        title="Backend Engineer",
        company="CTSCo",
        location="Shanghai",
        summary="CTS evidence for a backend engineer.",
    )
    _insert_review_item(
        store,
        user=user,
        session_id=session.session_id,
        source_run_id=source_runs["liepin"].source_run_id,
        review_item_id="review-liepin-collision",
        evidence_id="ev-liepin-collision",
        source_kind="liepin",
        evidence_level="card",
        provider_hash="same-provider-hash",
        score=74,
        display_name="Bob Wang",
        title="Frontend Engineer",
        company="LiepinCo",
        location="Beijing",
        summary="Liepin card for a different candidate.",
    )

    final_items = project_final_top_candidates(store.list_candidate_review_items(user=user, session_id=session.session_id))

    assert len(final_items) == 2
    assert {item.displayName for item in final_items} == {"Alice Chen", "Bob Wang"}


def _session_digest(client: TestClient) -> str:
    from seektalent_ui.auth import session_token_digest

    token = client.cookies.get("seektalent_workbench_session")
    assert token is not None
    return session_token_digest(token)


def _insert_review_item(
    store: WorkbenchStore,
    *,
    user: WorkbenchUser,
    session_id: str,
    source_run_id: str,
    review_item_id: str,
    evidence_id: str,
    source_kind: str,
    evidence_level: str,
    provider_hash: str,
    runtime_identity_id: str | None = None,
    score: int = 80,
    display_name: str = "Candidate",
    title: str = "Backend Engineer",
    company: str = "SearchCo",
    location: str = "Shanghai",
    summary: str = "Strong Python background.",
) -> None:
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_review_items (
                review_item_id, tenant_id, workspace_id, user_id, session_id,
                primary_evidence_id, display_name, title, company, location, summary,
                aggregate_score, fit_bucket, review_status, note, created_at, updated_at
            )
            VALUES (?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fit', 'new', '', ?, ?)
            """,
            (
                review_item_id,
                user.workspace_id,
                user.user_id,
                session_id,
                evidence_id,
                display_name,
                title,
                company,
                location,
                summary,
                score,
                now,
                now,
            ),
        )
    _insert_evidence(
        store,
        user=user,
        session_id=session_id,
        source_run_id=source_run_id,
        review_item_id=review_item_id,
        evidence_id=evidence_id,
        source_kind=source_kind,
        evidence_level=evidence_level,
        provider_hash=provider_hash,
        runtime_identity_id=runtime_identity_id,
        score=score,
        created_at=now,
    )


def _insert_evidence(
    store: WorkbenchStore,
    *,
    user: WorkbenchUser,
    session_id: str,
    source_run_id: str,
    review_item_id: str,
    evidence_id: str,
    source_kind: str,
    evidence_level: str,
    provider_hash: str,
    runtime_identity_id: str | None = None,
    score: int = 80,
    created_at: str | None = None,
) -> None:
    now = created_at or datetime.now(UTC).isoformat()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_evidence (
                evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                created_at
            )
            VALUES (?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fit', '[]', '[]', '[]', '[]', '[]', ?)
            """,
            (
                evidence_id,
                review_item_id,
                user.workspace_id,
                user.user_id,
                session_id,
                source_run_id,
                source_kind,
                evidence_level,
                provider_hash,
                runtime_identity_id,
                f"resume-{evidence_id}",
                score,
                now,
            ),
        )
