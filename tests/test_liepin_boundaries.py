from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from seektalent.flywheel.outcomes import build_runtime_query_outcome_rows_from_hits
from seektalent.flywheel.runtime import query_hit_rows_from_hits
from seektalent.models import QueryResumeHit
from seektalent.providers.liepin.client import LiepinWorkerModeError, build_liepin_worker_client
from seektalent.providers.liepin.mapper import map_liepin_worker_card, map_liepin_worker_detail
from seektalent.providers.liepin.opencli_worker_client import LiepinOpenCliWorkerClient
from seektalent.providers.liepin.security import issue_stream_token
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerCandidateCard, LiepinWorkerCandidateDetail
from seektalent_ui import models as ui_models
from seektalent_ui.liepin_routes import (
    LIEPIN_EVENT_BATCH_LIMIT,
    LIEPIN_EVENT_POLL_INTERVAL_SECONDS,
    LIEPIN_STREAM_TOKEN_COOKIE_MAX_AGE_SECONDS,
    LIEPIN_STREAM_TOKEN_COOKIE_NAME,
    create_liepin_router,
)
from seektalent_ui.server import create_app
from tests.settings_factory import make_settings


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OPENCLI_PYTHON_ALLOWLIST = {
    "src/seektalent/opencli_browser/__init__.py",
    "src/seektalent/opencli_browser/contracts.py",
    "src/seektalent/opencli_browser/reason_codes.py",
    "src/seektalent/opencli_browser/runtime.py",
    "src/seektalent/opencli_browser/automation.py",
    "src/seektalent/providers/liepin/client.py",
    "src/seektalent/providers/liepin/opencli_worker_client.py",
    "src/seektalent/providers/liepin/opencli_retriever.py",
    "src/seektalent/providers/liepin/opencli_browser_cli.py",
    "src/seektalent/providers/liepin/liepin_opencli_policy.py",
    "src/seektalent/providers/liepin/liepin_site_adapter.py",
    "src/seektalent/providers/liepin/liepin_site_parsing.py",
    "src/seektalent/providers/liepin/liepin_site_payloads.py",
    "src/seektalent/providers/liepin/liepin_drift_smoke.py",
}
_ALLOWED_LIEPIN_RESUME_RAW_KEYS = {
    "provider",
    "provider_subject_id",
    "provider_listing_id",
    "synthetic_candidate_fingerprint",
    "identity_confidence",
    "extraction_source",
    "extractor_version",
    "pii_classification",
    "retention_policy",
    "access_scope",
    "redaction_state",
    "raw_payload_artifact_ref",
    "score_evidence_source",
}


def test_production_python_does_not_import_opencli():
    offenders: list[str] = []
    for path in _python_source_files(SRC):
        if path.relative_to(ROOT).as_posix() in OPENCLI_PYTHON_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "opencli" in alias.name.lower():
                        offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module and "opencli" in node.module.lower():
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_liepin_provider_does_not_import_pi_agent_namespace():
    offenders: list[str] = []
    forbidden_module = ".".join(("seektalent", "providers", "pi_agent"))
    for path in _python_source_files(SRC / "seektalent" / "providers" / "liepin"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == forbidden_module or alias.name.startswith(f"{forbidden_module}."):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == forbidden_module or node.module.startswith(f"{forbidden_module}."):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.module}")

    assert offenders == []


def test_removed_pi_agent_opencli_duplicate_is_absent():
    removed_paths = (
        SRC / "seektalent" / "providers" / "pi_agent" / "opencli_browser.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "opencli_browser_cli.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "pi_extensions" / "seektalent_opencli_browser.ts",
        ROOT / "tests" / "test_pi_opencli_browser.py",
    )

    assert [path.relative_to(ROOT).as_posix() for path in removed_paths if path.exists()] == []


def test_removed_pi_agent_local_setup_harness_is_absent():
    removed_paths = (
        SRC / "seektalent" / "providers" / "pi_agent" / "local_setup.py",
        ROOT / "tests" / "test_pi_dokobot_local_setup.py",
    )

    assert [path.relative_to(ROOT).as_posix() for path in removed_paths if path.exists()] == []


def test_removed_pi_agent_dokobot_client_harness_is_absent():
    removed_paths = (
        SRC / "seektalent" / "providers" / "pi_agent" / "capabilities.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "dokobot_client.py",
        ROOT / "tests" / "test_dokobot_capabilities.py",
    )

    assert [path.relative_to(ROOT).as_posix() for path in removed_paths if path.exists()] == []


def test_workbench_contract_does_not_depend_on_removed_pi_external_test():
    script = (ROOT / "scripts" / "verify-dev-workbench.sh").read_text(encoding="utf-8")

    assert "tests/test_pi_external_agent.py" not in script


def test_workbench_contract_does_not_depend_on_removed_pi_payload_test():
    script = (ROOT / "scripts" / "verify-dev-workbench.sh").read_text(encoding="utf-8")

    assert "tests/test_pi_payload_firewall.py" not in script


def test_removed_pi_rpc_external_agent_harness_is_absent():
    removed_paths = (
        SRC / "seektalent" / "providers" / "pi_agent" / "pi_external.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "pi_extensions" / "bailian_deepseek.ts",
        SRC / "seektalent" / "providers" / "pi_agent" / "pi_extensions" / "tsconfig.json",
        ROOT / "tests" / "test_pi_external_agent.py",
    )
    settings_factory = (ROOT / "tests" / "settings_factory.py").read_text(encoding="utf-8")

    assert [path.relative_to(ROOT).as_posix() for path in removed_paths if path.exists()] == []
    assert "make_pi_agent_settings" not in settings_factory


def test_removed_pi_agent_contract_payload_harness_is_absent():
    removed_paths = (
        SRC / "seektalent" / "providers" / "pi_agent" / "artifacts.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "connection_safety.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "contracts.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "locks.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "payload_firewall.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "validation_errors.py",
        ROOT / "tests" / "test_pi_agent_artifacts.py",
        ROOT / "tests" / "test_pi_agent_connection_safety.py",
        ROOT / "tests" / "test_pi_agent_contracts.py",
        ROOT / "tests" / "test_pi_payload_firewall.py",
    )

    assert [path.relative_to(ROOT).as_posix() for path in removed_paths if path.exists()] == []


def test_removed_pi_agent_boundary_scanner_tool_is_absent():
    removed_paths = (
        ROOT / "tools" / "check_pi_agent_boundaries.py",
        ROOT / "tests" / "test_pi_agent_boundaries.py",
    )

    assert [path.relative_to(ROOT).as_posix() for path in removed_paths if path.exists()] == []


def test_removed_pi_agent_provider_package_is_absent():
    removed_paths = (
        SRC / "seektalent" / "providers" / "pi_agent" / "__init__.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "boundary_patterns.py",
        SRC / "seektalent" / "providers" / "pi_agent" / "boundary_registry.json",
    )
    workbench_probe = (ROOT / "tests" / "test_workbench_liepin_browser_session_probe.py").read_text(
        encoding="utf-8"
    )

    assert [path.relative_to(ROOT).as_posix() for path in removed_paths if path.exists()] == []
    assert "src/seektalent/providers/pi_agent/" not in workbench_probe


def test_ui_response_models_do_not_expose_worker_or_provider_internals():
    forbidden_fields = {
        "authHeaders",
        "authorization",
        "browserDebugUrl",
        "cdpEndpoint",
        "cdpUrl",
        "cookies",
        "handoffToken",
        "rawProviderPayload",
        "storageState",
        "workerBaseUrl",
        "workerUrl",
    }
    response_models = [
        value
        for name, value in vars(ui_models).items()
        if name.endswith("Response") and isinstance(value, type) and hasattr(value, "model_fields")
    ]

    assert response_models
    for model in response_models:
        assert set(model.model_fields).isdisjoint(forbidden_fields), model.__name__


def test_ui_api_translates_store_and_worker_dtos_through_external_models_only(tmp_path):
    settings = make_settings(
        liepin_api_token="unit-api-token",
        liepin_connector_db_path=str(tmp_path / "liepin.sqlite3"),
        liepin_session_store_key_id="unit-key-id",
        liepin_stream_token_secret="unit-stream-secret",
        workspace_root=str(tmp_path),
        mock_cts=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app)
    forbidden_modules = (
        "seektalent.providers.liepin.models",
        "seektalent.providers.liepin.store",
        "seektalent.providers.liepin.worker_contracts",
    )

    checked_routes = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not _is_liepin_client_route(route.path):
            continue
        checked_routes += 1
        annotation = get_type_hints(route.endpoint).get(
            "return",
            inspect.signature(route.endpoint).return_annotation,
        )
        assert not _annotation_uses_module(annotation, forbidden_modules), route.path
        assert _annotation_is_external_api_boundary(annotation), route.path

    assert checked_routes >= 9

    gate = client.post("/api/liepin/compliance-gates", headers=_api_headers(), json=_gate_payload())
    assert gate.status_code == 201, gate.text
    assert set(gate.json()) == set(ui_models.LiepinComplianceGateResponse.model_fields)
    gate_ref = gate.json()["gateRef"]

    connection = client.post(
        "/api/liepin/connections",
        headers=_api_headers(),
        json={"complianceGateRef": gate_ref},
    )
    assert connection.status_code == 201, connection.text
    assert set(connection.json()) == set(ui_models.LiepinConnectionResponse.model_fields)
    connection_id = connection.json()["connectionId"]

    login = client.post(f"/api/liepin/connections/{connection_id}/login-url", headers=_api_headers())
    assert login.status_code == 200, login.text
    assert set(login.json()) == set(ui_models.LiepinLoginUrlResponse.model_fields)

    for payload in (gate.json(), connection.json(), login.json()):
        serialized = str(payload).lower()
        assert "worker" not in serialized
        assert "storage" not in serialized
        assert "cookie" not in serialized
        assert "authorization" not in serialized


def test_liepin_api_is_fastapi_uvicorn_and_not_legacy_stdlib_routes(tmp_path):
    settings = make_settings(
        liepin_api_token="unit-api-token",
        liepin_connector_db_path=str(tmp_path / "liepin.sqlite3"),
        liepin_session_store_key_id="unit-key-id",
        liepin_stream_token_secret="unit-stream-secret",
        workspace_root=str(tmp_path),
        mock_cts=True,
    )
    app = create_app(settings=settings)

    assert isinstance(app, FastAPI)
    server_source = _read_source(SRC / "seektalent_ui" / "server.py")
    legacy_runs_path = "/" + "api" + "/" + "runs"
    assert "uvicorn.run(" in server_source
    assert "create_app(" in server_source
    assert "ThreadingHTTPServer" not in server_source
    assert "BaseHTTPRequestHandler" not in server_source
    assert legacy_runs_path not in server_source


def test_sse_routes_use_persisted_scoped_bounded_event_streams():
    router_source = inspect.getsource(create_liepin_router)
    generator_source = _function_source(SRC / "seektalent_ui" / "liepin_routes.py", "_event_generator")
    store_source = _read_source(SRC / "seektalent" / "providers" / "liepin" / "store.py")

    assert "EventSourceResponse(" in router_source
    assert 'Header(alias="Last-Event-ID")' in router_source
    assert "_scope_from_stream_cookie(" in router_source
    assert LIEPIN_STREAM_TOKEN_COOKIE_NAME in router_source
    assert "StreamingResponse" not in router_source
    assert "asyncio.Queue" not in router_source
    assert "queue.Queue" not in router_source

    assert "store.iter_events_after(" in generator_source
    assert LIEPIN_EVENT_BATCH_LIMIT == 100
    assert "limit=LIEPIN_EVENT_BATCH_LIMIT" in generator_source
    assert "json.dumps(row.payload" in generator_source
    assert LIEPIN_EVENT_POLL_INTERVAL_SECONDS == 0.25
    assert "await asyncio.sleep(LIEPIN_EVENT_POLL_INTERVAL_SECONDS)" in generator_source
    assert "liepin_events" in store_source
    assert "LIMIT ?" in store_source
    assert "with self._connect() as conn" in store_source
    assert "if has_unsafe_payload(payload)" in store_source


def test_stream_tokens_are_short_lived_cookie_only_and_scope_bound(tmp_path):
    settings = make_settings(
        liepin_api_token="unit-api-token",
        liepin_connector_db_path=str(tmp_path / "liepin.sqlite3"),
        liepin_session_store_key_id="unit-key-id",
        liepin_stream_token_secret="unit-stream-secret",
        workspace_root=str(tmp_path),
        mock_cts=True,
    )
    client = TestClient(create_app(settings=settings))
    router_source = inspect.getsource(create_liepin_router)

    assert "status_code=204" in router_source
    assert "response.set_cookie(" in router_source
    assert "httponly=True" in router_source
    assert LIEPIN_STREAM_TOKEN_COOKIE_MAX_AGE_SECONDS == 60
    assert "max_age=LIEPIN_STREAM_TOKEN_COOKIE_MAX_AGE_SECONDS" in router_source
    assert 'path="/api/liepin/connections"' in router_source
    assert "subject_id=connection.connection_id" in router_source
    assert "subject_id=connection_id,\n        )\n        response.set_cookie(" not in router_source
    assert "Stream tokens are not accepted in URL query parameters." in _read_source(
        SRC / "seektalent_ui" / "liepin_routes.py"
    )

    gate = client.post("/api/liepin/compliance-gates", headers=_api_headers(), json=_gate_payload())
    assert gate.status_code == 201, gate.text
    connection = client.post(
        "/api/liepin/connections",
        headers=_api_headers(),
        json={"complianceGateRef": gate.json()["gateRef"]},
    )
    assert connection.status_code == 201, connection.text
    connection_id = connection.json()["connectionId"]

    LiepinStore(tmp_path / "liepin.sqlite3").append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id=connection_id,
        event_name="stream_end",
        payload={"reason": "boundary_test"},
    )
    valid_token = issue_stream_token(
        secret=settings.liepin_stream_token_secret,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id=connection_id,
    )
    stream = client.get(
        f"/api/liepin/connections/{connection_id}/events",
        headers={"Cookie": f"liepin_stream_token={valid_token}"},
    )
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: stream_end" in stream.text

    expired_token = issue_stream_token(
        secret=settings.liepin_stream_token_secret,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id=connection_id,
        ttl_seconds=-1,
    )
    expired = client.get(
        f"/api/liepin/connections/{connection_id}/events",
        headers={"Cookie": f"liepin_stream_token={expired_token}"},
    )
    assert expired.status_code == 403

    wrong_scope_token = issue_stream_token(
        secret=settings.liepin_stream_token_secret,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id="other-run",
    )
    wrong_scope = client.get(
        f"/api/liepin/connections/{connection_id}/events",
        headers={"Cookie": f"liepin_stream_token={wrong_scope_token}"},
    )
    assert wrong_scope.status_code == 403

    key_id_signed_token = issue_stream_token(
        secret=settings.liepin_session_store_key_id,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id=connection_id,
    )
    key_id_signed = client.get(
        f"/api/liepin/connections/{connection_id}/events",
        headers={"Cookie": f"liepin_stream_token={key_id_signed_token}"},
    )
    assert key_id_signed.status_code == 403

    query_token = client.get(f"/api/liepin/connections/{connection_id}/events?token=abc")
    assert query_token.status_code == 400


def test_managed_local_worker_mode_uses_opencli_compatibility_path():
    settings = make_settings(liepin_worker_mode="managed_local")
    client = build_liepin_worker_client(settings)
    client_source = _read_source(SRC / "seektalent" / "providers" / "liepin" / "client.py")

    assert isinstance(client, LiepinOpenCliWorkerClient)
    assert "worker_runtime" not in client_source


def test_fake_fixture_mode_is_not_reachable_when_live_enabled():
    settings = make_settings(
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
        liepin_live_enabled=True,
    )

    with pytest.raises(LiepinWorkerModeError, match="live"):
        build_liepin_worker_client(settings)


def test_liepin_mapper_keeps_provider_payload_out_of_resume_candidate_raw():
    card = _worker_card()
    detail = _worker_detail()

    card_mapping = map_liepin_worker_card(card, raw_payload_artifact_ref="worker://cards/candidate-1.json")
    detail_mapping = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert card_mapping.provider_snapshot.raw_payload == card.payload
    assert detail_mapping.provider_snapshot.raw_payload == detail.payload
    for mapped in (card_mapping, detail_mapping):
        assert set(mapped.candidate.raw) == _ALLOWED_LIEPIN_RESUME_RAW_KEYS
        serialized_raw = str(mapped.candidate.raw)
        assert "13800000000" not in serialized_raw
        assert "one@example.com" not in serialized_raw
        assert "Private card resume summary" not in serialized_raw
        assert "Liepin private detail body" not in serialized_raw
        assert "Bearer secret" not in serialized_raw
        assert "storageState" not in serialized_raw
        assert "cookies" not in serialized_raw


def test_detail_enriched_score_evidence_reaches_flywheel_rows():
    hit = QueryResumeHit(
        run_id="run-1",
        query_instance_id="query-1",
        query_fingerprint="fingerprint-1",
        hit_sequence_no=1,
        snapshot_sha256="snapshot-1",
        resume_id="resume-1",
        round_no=1,
        lane_type="prf_probe",
        batch_no=1,
        rank_in_query=1,
        provider_name="liepin",
        was_new_to_pool=True,
        was_duplicate=False,
        scored_fit_bucket="fit",
        overall_score=88,
        must_have_match_score=86,
        risk_score=15,
        score_evidence_source="detail_enriched",
        card_scorecard_ref="artifact:scorecards/card/resume-1.json",
        detail_scorecard_ref="artifact:scorecards/detail/resume-1.json",
        score_delta=12,
        detail_open_reason="detail_budget_available",
        detail_open_policy_version="detail-policy-v1",
    )

    rows = query_hit_rows_from_hits([hit])
    outcomes = build_runtime_query_outcome_rows_from_hits(run_id="run-1", hits=rows)

    assert rows[0]["score_evidence_source"] == "detail_enriched"
    assert rows[0]["detail_scorecard_ref"] == "artifact:scorecards/detail/resume-1.json"
    assert "score_evidence:detail_enriched" in outcomes[0]["labels_json"]
    assert "detail_enriched" in outcomes[0]["reasons_json"]


def _api_headers() -> dict[str, str]:
    return {
        "X-SeekTalent-API-Key": "unit-api-token",
        "X-Tenant-ID": "tenant-a",
        "X-Workspace-ID": "workspace-a",
        "X-Actor-ID": "actor-a",
    }


def _gate_payload() -> dict[str, object]:
    return {
        "candidatePersonalInfoProcessingBasis": "candidate recruiting lawful basis",
        "personalInformationProcessor": "Acme Recruiting",
        "operatorAuditOwner": "Ops Owner",
        "accountHolderAuthorized": True,
        "humanInitiatedRecruiting": True,
        "allowedPurposes": ["search"],
        "retentionPolicy": "run_debug_short",
        "deletionSlaDays": 14,
        "deletionPath": "settings/delete",
        "rawPayloadAccessScope": "run_only",
        "rawDetailRetentionAllowedAfterDebug": False,
        "fixtureExportAllowed": False,
        "policyRef": "policy-v1",
    }


def _is_liepin_client_route(path: str) -> bool:
    return path.startswith("/api/liepin")


def _annotation_is_external_api_boundary(annotation: object) -> bool:
    if annotation is inspect.Signature.empty:
        return True
    return _annotation_uses_module(
        annotation,
        (
            "seektalent_ui.models",
            "starlette.responses",
            "sse_starlette.sse",
        ),
    )


def _annotation_uses_module(annotation: object, modules: tuple[str, ...]) -> bool:
    candidates = [annotation]
    origin = get_origin(annotation)
    if origin is not None:
        candidates.append(origin)
    candidates.extend(get_args(annotation))
    for candidate in candidates:
        module_name = getattr(candidate, "__module__", "")
        if module_name in modules:
            return True
    return False


def _worker_card() -> LiepinWorkerCandidateCard:
    return LiepinWorkerCandidateCard(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "name": "Candidate One",
            "headline": "Python backend engineer",
            "resumeText": "Private card resume summary with 13800000000 and one@example.com",
            "phone": "13800000000",
            "email": "one@example.com",
            "cookies": "session=secret",
            "storageState": {"cookies": [{"name": "session", "value": "secret"}]},
            "authorization": "Bearer secret",
        },
        normalized_text="Python backend engineer card summary",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="fp-card-1",
        identity_confidence="provider_subject_id",
        extraction_source="network",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_possible",
        retention_policy="provider_snapshot_30d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _worker_detail() -> LiepinWorkerCandidateDetail:
    return LiepinWorkerCandidateDetail(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "detailBody": "<html>Liepin private detail body</html>",
            "resumeText": "Detailed private resume text with one@example.com",
            "phone": "13800000000",
            "email": "one@example.com",
            "auth_headers": {"authorization": "Bearer secret"},
        },
        normalized_text="Python backend engineer detail summary",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="fp-detail-1",
        identity_confidence="provider_subject_id",
        extraction_source="dom_fallback",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_present",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _python_source_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, function_name: str) -> str:
    source = _read_source(path)
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name:
            assert node.end_lineno is not None
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{function_name} not found in {path}")
