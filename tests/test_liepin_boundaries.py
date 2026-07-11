from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from seektalent.flywheel.outcomes import build_runtime_query_outcome_rows_from_hits
from seektalent.flywheel.runtime import query_hit_rows_from_hits
from seektalent.models import QueryResumeHit, RequirementSheet
from seektalent.core.retrieval.provider_contract import (
    ProviderFirstPageExpansionError,
    ProviderFirstPageExpansionResult,
    ProviderSearchContinuation,
    SearchResult,
)
from seektalent.providers.liepin.adapter import LiepinProviderAdapter
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.providers.liepin.client import LiepinWorkerModeError, build_liepin_worker_client
from seektalent.providers.liepin.mapper import map_liepin_worker_card, map_liepin_worker_detail
from seektalent.providers.liepin.security import issue_stream_token
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerCandidateCard, LiepinWorkerCandidateDetail
from seektalent.source_contracts import SourceBudget, SourceLaneRequest
from seektalent.sources.provider_card_lane import run_provider_card_lane
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


def test_provider_preserves_terminal_result_when_cleanup_fails() -> None:
    continuation = ProviderSearchContinuation(kind="first_page_detail_expansion",
        continuation_id="c", opaque_ref="artifact://protected/c", source_kind="liepin", round_no=1,
        query_instance_id="q", visible_candidate_count=1, eligible_candidate_count=1,
        initial_opened_count=0)
    class Worker:
        async def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action, **kwargs):
            del kwargs
            if action == "discard":
                raise OSError("cleanup exploded")
            return ProviderFirstPageExpansionResult(search_result=SearchResult(),
                first_page_visible_count=1, first_page_eligible_count=1, initial_opened_count=0,
                expansion_opened_count=0, expansion_skipped_seen_count=0,
                expansion_terminal_failure_count=0, status="partial",
                safe_reason_code="original_partial")
    result = asyncio.run(LiepinProviderAdapter(make_settings(), worker_client=Worker()).handle_first_page_continuation_with_detail_open_claim_ledger(
        action="expand", continuation=continuation, detail_open_claim_ledger=DetailOpenClaimLedger({}),
        logical_round_no=1, query_instance_id="q"))
    assert result.status == "partial"
    assert result.continuation_deleted is False
    assert result.safe_reason_code == "liepin_first_page_continuation_cleanup_failed"


def test_provider_does_not_swallow_programmer_runtime_error_from_cleanup() -> None:
    continuation = ProviderSearchContinuation(kind="first_page_detail_expansion",
        continuation_id="c", opaque_ref="artifact://protected/c", source_kind="liepin", round_no=1,
        query_instance_id="q", visible_candidate_count=1, eligible_candidate_count=1,
        initial_opened_count=0)
    class Worker:
        async def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action, **kwargs):
            del kwargs
            if action == "discard":
                raise RuntimeError("cleanup invariant violated")
            return ProviderFirstPageExpansionResult(search_result=SearchResult(),
                first_page_visible_count=1, first_page_eligible_count=1, initial_opened_count=0,
                expansion_opened_count=0, expansion_skipped_seen_count=0,
                expansion_terminal_failure_count=0, status="completed")
    with pytest.raises(RuntimeError, match="cleanup invariant violated"):
        asyncio.run(LiepinProviderAdapter(make_settings(), worker_client=Worker()).handle_first_page_continuation_with_detail_open_claim_ledger(
            action="expand", continuation=continuation, detail_open_claim_ledger=DetailOpenClaimLedger({}),
            logical_round_no=1, query_instance_id="q"))


def test_provider_preserves_expected_error_and_attaches_cleanup_ack() -> None:
    continuation = ProviderSearchContinuation(kind="first_page_detail_expansion",
        continuation_id="c", opaque_ref="artifact://protected/c", source_kind="liepin", round_no=1,
        query_instance_id="q", visible_candidate_count=1, eligible_candidate_count=1,
        initial_opened_count=0)
    class Worker:
        async def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action, **kwargs):
            del kwargs
            if action == "expand":
                raise LiepinWorkerModeError("blocked", code="original_blocked")
            return ProviderFirstPageExpansionResult(search_result=SearchResult(),
                first_page_visible_count=1, first_page_eligible_count=1, initial_opened_count=0,
                expansion_opened_count=0, expansion_skipped_seen_count=0,
                expansion_terminal_failure_count=0, status="completed", continuation_deleted=True)
    with pytest.raises(ProviderFirstPageExpansionError) as captured:
        asyncio.run(LiepinProviderAdapter(make_settings(), worker_client=Worker()).handle_first_page_continuation_with_detail_open_claim_ledger(
            action="expand", continuation=continuation, detail_open_claim_ledger=DetailOpenClaimLedger({}),
            logical_round_no=1, query_instance_id="q"))
    assert captured.value.safe_reason_code == "original_blocked"
    assert captured.value.continuation_deleted is True


def test_provider_preserves_primary_error_when_expected_cleanup_boundary_fails() -> None:
    continuation = ProviderSearchContinuation(kind="first_page_detail_expansion",
        continuation_id="c", opaque_ref="artifact://protected/c", source_kind="liepin", round_no=1,
        query_instance_id="q", visible_candidate_count=1, eligible_candidate_count=1,
        initial_opened_count=0)

    class Worker:
        async def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action, **kwargs):
            del kwargs
            if action == "expand":
                from seektalent.core.retrieval.provider_contract import ProviderSearchError
                raise ProviderSearchError(reason_code="primary_failed", message="primary failed")
            raise LiepinWorkerModeError("cleanup blocked", code="cleanup_blocked")

    with pytest.raises(ProviderFirstPageExpansionError) as captured:
        asyncio.run(LiepinProviderAdapter(make_settings(), worker_client=Worker()).handle_first_page_continuation_with_detail_open_claim_ledger(
            action="expand", continuation=continuation, detail_open_claim_ledger=DetailOpenClaimLedger({}),
            logical_round_no=1, query_instance_id="q"))

    assert captured.value.safe_reason_code == "primary_failed"
    assert captured.value.continuation_deleted is False


@pytest.mark.parametrize("status", ["completed", "partial", "failed"])
def test_provider_deletes_every_terminal_expansion_result(status: str) -> None:
    continuation = ProviderSearchContinuation(kind="first_page_detail_expansion",
        continuation_id="c", opaque_ref="artifact://protected/c", source_kind="liepin", round_no=1,
        query_instance_id="q", visible_candidate_count=1, eligible_candidate_count=1,
        initial_opened_count=0)
    class Worker:
        actions: list[str] = []
        async def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action, **kwargs):
            del kwargs
            self.actions.append(action)
            return ProviderFirstPageExpansionResult(search_result=SearchResult(),
                first_page_visible_count=1, first_page_eligible_count=1, initial_opened_count=0,
                expansion_opened_count=0, expansion_skipped_seen_count=0,
                expansion_terminal_failure_count=0, status=status,
                continuation_deleted=action == "discard")
    worker = Worker()
    result = asyncio.run(LiepinProviderAdapter(make_settings(), worker_client=worker).handle_first_page_continuation_with_detail_open_claim_ledger(
        action="expand", continuation=continuation, detail_open_claim_ledger=DetailOpenClaimLedger({}),
        logical_round_no=1, query_instance_id="q"))
    assert worker.actions == ["expand", "discard"]
    assert result.status == status
    assert result.continuation_deleted is True
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
    "src/seektalent/providers/liepin/liepin_search_workflow.py",
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
_ALLOWED_LIEPIN_CARD_RESUME_RAW_KEYS = _ALLOWED_LIEPIN_RESUME_RAW_KEYS | {"safe_card_summary"}
_ALLOWED_LIEPIN_DETAIL_RESUME_RAW_KEYS = _ALLOWED_LIEPIN_RESUME_RAW_KEYS | {
    "currentTitle",
    "currentCompany",
    "workExperienceList",
    "projectExperienceList",
    "educationList",
    "skills",
}
_LIEPIN_CARD_TEXT_TAIL_SCAN_PATHS = [
    ROOT / "src/seektalent/providers/liepin",
    ROOT / "src/seektalent/sources/liepin",
    ROOT / "src/seektalent/resume_normalizers/liepin.py",
    ROOT / "src/seektalent_runtime_control",
]
_LIEPIN_CARD_TEXT_TAIL_FIELDS = {"visible_text", "normalized_card_text"}
_LITERAL_CARD_TEXT_TAIL_CONSTANTS = {
    "src/seektalent/providers/liepin/liepin_site_parsing.py": {"FORBIDDEN_CARD_EVIDENCE_KEYS"},
    "src/seektalent/providers/liepin/liepin_site_payloads.py": {"FORBIDDEN_CARD_SUMMARY_KEYS"},
    "src/seektalent/providers/liepin/worker_contracts.py": {"LIEPIN_CARD_PAYLOAD_TEXT_TAIL_KEYS"},
}


def test_provider_private_continuation_contract_stays_out_of_public_payloads() -> None:
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="continuation-1",
        opaque_ref="artifact://protected/private.json",
        source_kind="liepin",
        round_no=2,
        query_instance_id="query-2-exploit",
        visible_candidate_count=3,
        eligible_candidate_count=2,
        initial_opened_count=1,
    )
    result = SearchResult(private_continuations=(continuation,))

    assert result.private_continuations == (continuation,)
    assert not hasattr(result, "to_public_payload")
    assert not hasattr(continuation, "to_public_payload")
    assert not hasattr(ProviderFirstPageExpansionResult, "to_public_payload")


def test_provider_private_continuation_is_omitted_by_canonical_source_lane_mapping() -> None:
    private_ref = "SENTINEL-PRIVATE-REF"
    detail_url = "https://h.liepin.com/SENTINEL-DETAIL-URL"
    opaque_ref = f"artifact://protected/pi-detail/SENTINEL-OPAQUE?ref={private_ref}&url={detail_url}"
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="continuation-1",
        opaque_ref=opaque_ref,
        source_kind="liepin",
        round_no=2,
        query_instance_id="query-2-exploit",
        visible_candidate_count=1,
        eligible_candidate_count=1,
        initial_opened_count=0,
    )

    async def search(**kwargs: object) -> SearchResult:
        del kwargs
        return SearchResult(
            request_payload={"safe": "value"},
            private_continuations=(continuation,),
        )

    result = asyncio.run(
        run_provider_card_lane(
            request=SourceLaneRequest(
                source_id="liepin",
                lane_mode="card",
                runtime_run_id="run-1",
                source_plan_id="plan-1",
                source_lane_run_id="lane-1",
                job_title="AI Engineer",
                jd="Build AI systems.",
                notes="",
                requirement_sheet=RequirementSheet(
                    job_title="AI Engineer",
                    title_anchor_terms=["AI Engineer"],
                    title_anchor_rationale="Job title.",
                    role_summary="Build AI systems.",
                    scoring_rationale="Score relevant experience.",
                ),
                source_query_terms=("AI Engineer",),
                budget=SourceBudget(card_target=1, detail_target=0, scan_limit=1),
            ),
            search=search,
        )
    )
    assert result.private_first_page_continuations == (continuation,)


def test_liepin_card_evidence_does_not_emit_text_tail_fields() -> None:
    hits: list[str] = []
    for path in _liepin_card_text_tail_scan_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        parents = _ast_parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or node.value not in _LIEPIN_CARD_TEXT_TAIL_FIELDS:
                continue
            constant_name = _enclosing_assignment_name(node, parents)
            if constant_name in _LITERAL_CARD_TEXT_TAIL_CONSTANTS.get(rel, set()):
                continue
            hits.append(f"{rel}:{node.lineno}:{node.value}")
    assert hits == []


def test_liepin_card_text_tail_forbidden_fields_are_not_computed() -> None:
    hits: list[str] = []
    for path in _liepin_card_text_tail_scan_files():
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        parents = _ast_parent_map(tree)
        constants = _constant_string_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) or _is_assignment_target_name(node):
                continue
            if _computed_forbidden_card_text_tail_field(node, source, parents, constants) is None:
                continue
            hits.append(f"{rel}:{getattr(node, 'lineno', '?')}:{ast.get_source_segment(source, node)!r}")
    assert hits == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('candidate_key = f\'{"visible"}_{"text"}\'', "visible_text"),
        ("candidate_key = '{}_{}'.format('visible', 'text')", "visible_text"),
        ("candidate_key = '%s_%s' % ('visible', 'text')", "visible_text"),
        ('candidate_key = "%(prefix)s_%(suffix)s" % {"prefix": "visible", "suffix": "text"}', "visible_text"),
        ("suffix = '_text'\ncandidate_key = 'visible' + suffix", "visible_text"),
        ('candidate_key = f\'{"normalized"}_{"card"}_{"text"}\'', "normalized_card_text"),
        ("candidate_key = '{}_{}_{}'.format('normalized', 'card', 'text')", "normalized_card_text"),
        ("candidate_key = '%s_%s_%s' % ('normalized', 'card', 'text')", "normalized_card_text"),
        (
            'candidate_key = "%(prefix)s_%(middle)s_%(suffix)s" % '
            '{"prefix": "normalized", "middle": "card", "suffix": "text"}',
            "normalized_card_text",
        ),
        ("suffix = '_card_text'\ncandidate_key = 'normalized' + suffix", "normalized_card_text"),
    ],
)
def test_liepin_card_text_tail_computed_detector_catches_adversarial_constructions(
    source: str,
    expected: str,
) -> None:
    assert _computed_card_text_tail_fields_from_source(source) == [expected]


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
    workbench_probe = (ROOT / "tests" / "test_workbench_liepin_browser_session_probe.py").read_text(encoding="utf-8")

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
        provider_name="cts",
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
        provider_name="cts",
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
        provider_name="cts",
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


def test_removed_local_worker_mode_is_not_a_live_compatibility_path():
    removed_mode = "managed" + "_local"
    client_source = _read_source(SRC / "seektalent" / "providers" / "liepin" / "client.py")

    with pytest.raises(ValueError, match=removed_mode):
        make_settings(liepin_worker_mode=removed_mode)
    assert "worker_runtime" not in client_source
    assert removed_mode not in client_source


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
    assert set(card_mapping.candidate.raw) == _ALLOWED_LIEPIN_CARD_RESUME_RAW_KEYS
    assert set(detail_mapping.candidate.raw) == _ALLOWED_LIEPIN_DETAIL_RESUME_RAW_KEYS
    for mapped in (card_mapping, detail_mapping):
        serialized_raw = str(mapped.candidate.raw)
        assert "13800000000" not in serialized_raw
        assert "one@example.com" not in serialized_raw
        assert "Private card note" not in serialized_raw
        assert "Liepin private detail body" not in serialized_raw
        assert "Detailed private resume note" not in serialized_raw
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
            "privateCardNote": "Private card note with 13800000000 and one@example.com",
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
        safe_card_summary={
            "current_or_recent_title": "Python backend engineer",
            "skill_tags": ("Python",),
            "masked_name": True,
        },
    )


def _worker_detail() -> LiepinWorkerCandidateDetail:
    return LiepinWorkerCandidateDetail(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "currentTitle": "Python backend engineer",
            "currentCompany": "Structured Boundary Co",
            "workExperienceList": [
                {
                    "company": "Structured Boundary Co",
                    "title": "Python backend engineer",
                    "summary": "Built structured backend systems",
                }
            ],
            "projectExperienceList": [
                {"name": "Boundary Platform", "role": "Backend engineer", "summary": "Improved retrieval quality"}
            ],
            "educationList": [{"school": "Boundary University", "degree": "Bachelor"}],
            "skills": ["Python", "SQL"],
            "privateDetailHtml": "<html>Liepin private detail body</html>",
            "privateDetailNote": "Detailed private resume note with one@example.com",
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


def _liepin_card_text_tail_scan_files() -> list[Path]:
    paths: list[Path] = []
    for scan_path in _LIEPIN_CARD_TEXT_TAIL_SCAN_PATHS:
        if scan_path.is_file():
            paths.append(scan_path)
        else:
            paths.extend(scan_path.rglob("*.py"))
    return sorted(paths)


def _ast_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_assignment_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Assign):
            for target in current.targets:
                if isinstance(target, ast.Name):
                    return target.id
        if isinstance(current, ast.AnnAssign) and isinstance(current.target, ast.Name):
            return current.target.id
    return None


def _computed_forbidden_card_text_tail_field(
    node: ast.AST,
    source: str,
    parents: dict[ast.AST, ast.AST],
    constants: dict[str, str] | None = None,
) -> str | None:
    value = _static_string_value(node, constants)
    if value in _LIEPIN_CARD_TEXT_TAIL_FIELDS:
        return value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
        segment = _source_segment_with_generator(node, source, parents)
        for field in _LIEPIN_CARD_TEXT_TAIL_FIELDS:
            if all(_has_quoted_text_part(segment, part) for part in field.split("_")):
                return field
    return None


def _computed_card_text_tail_fields_from_source(source: str) -> list[str]:
    tree = ast.parse(source)
    parents = _ast_parent_map(tree)
    constants = _constant_string_names(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) or _is_assignment_target_name(node):
            continue
        field = _computed_forbidden_card_text_tail_field(node, source, parents, constants)
        if field is not None:
            hits.append(field)
    return hits


def _is_assignment_target_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load)


def _static_string_value(node: ast.AST, constants: dict[str, str] | None = None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and constants is not None:
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_value(node.left, constants)
        right = _static_string_value(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = _static_string_value(node.left, constants)
        values = _static_format_values(node.right, constants)
        if template is None or values is None:
            return None
        try:
            return template % values
        except (TypeError, ValueError):
            return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue) and value.conversion == -1 and value.format_spec is None:
                formatted = _static_string_value(value.value, constants)
                if formatted is not None:
                    parts.append(formatted)
                    continue
                return None
            return None
        return "".join(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
        separator = _static_string_value(node.func.value, constants)
        if separator is None or len(node.args) != 1:
            return None
        values = _static_string_sequence(node.args[0], constants)
        if values is not None:
            return separator.join(values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        template = _static_string_value(node.func.value, constants)
        if template is None:
            return None
        args = _static_format_args(node.args, constants)
        kwargs = _static_format_kwargs(node.keywords, constants)
        if args is None or kwargs is None:
            return None
        try:
            return template.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            return None
    return None


def _static_string_sequence(node: ast.AST, constants: dict[str, str] | None = None) -> tuple[str, ...] | None:
    if not isinstance(node, ast.List | ast.Tuple | ast.Set):
        return None
    values: list[str] = []
    for item in node.elts:
        value = _static_string_value(item, constants)
        if value is None:
            return None
        values.append(value)
    return tuple(values)


def _static_format_values(
    node: ast.AST,
    constants: dict[str, str] | None,
) -> str | tuple[str, ...] | dict[str, str] | None:
    values = _static_string_sequence(node, constants)
    if values is not None:
        return values
    mapping = _static_string_mapping(node, constants)
    if mapping is not None:
        return mapping
    return _static_string_value(node, constants)


def _static_string_mapping(node: ast.AST, constants: dict[str, str] | None) -> dict[str, str] | None:
    if not isinstance(node, ast.Dict):
        return None
    values: dict[str, str] = {}
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        if key_node is None:
            return None
        key = _static_string_value(key_node, constants)
        value = _static_string_value(value_node, constants)
        if key is None or value is None:
            return None
        values[key] = value
    return values


def _static_format_args(nodes: list[ast.expr], constants: dict[str, str] | None) -> tuple[str, ...] | None:
    values: list[str] = []
    for node in nodes:
        value = _static_string_value(node, constants)
        if value is None:
            return None
        values.append(value)
    return tuple(values)


def _static_format_kwargs(
    nodes: list[ast.keyword],
    constants: dict[str, str] | None,
) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for node in nodes:
        if node.arg is None:
            return None
        value = _static_string_value(node.value, constants)
        if value is None:
            return None
        values[node.arg] = value
    return values


def _constant_string_names(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = _static_string_value(node.value, constants)
            if value is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = _static_string_value(node.value, constants) if node.value is not None else None
            if value is not None:
                constants[node.target.id] = value
    return constants


def _source_segment_with_generator(node: ast.AST, source: str, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents and isinstance(parents[current], ast.GeneratorExp):
        current = parents[current]
    return ast.get_source_segment(source, current) or ""


def _has_quoted_text_part(segment: str, part: str) -> bool:
    return f'"{part}"' in segment or f"'{part}'" in segment


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
