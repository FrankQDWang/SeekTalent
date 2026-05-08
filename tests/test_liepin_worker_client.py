from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError

from seektalent.providers.liepin import client as liepin_client_module
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.providers.liepin.client import (
    ExternalHttpLiepinWorkerClient,
    FakeLiepinWorkerClient,
    LiepinWorkerModeError,
    ManagedLocalLiepinWorkerClient,
    _default_http_json,
    build_liepin_worker_client,
)
from seektalent.providers.liepin.worker_contracts import (
    LoginHandoff,
    RedactedWorkerDiagnostics,
    SessionStatus,
    WorkerHealth,
    decode_login_handoff,
    decode_redacted_diagnostics,
    decode_session_status,
    decode_worker_health,
)
from tests.settings_factory import make_settings


def _request() -> SearchRequest:
    return SearchRequest(
        query_terms=["python"],
        query_role="primary",
        keyword_query="python",
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=10,
        provider_filters={"city": "上海", "skills": ["python"]},
        provider_context={
            "liepin_tenant_id": "tenant-a",
            "liepin_workspace_id": "workspace-a",
            "liepin_actor_id": "actor-a",
            "liepin_connection_id": "conn-1",
            "liepin_compliance_gate_ref": "gate-a",
        },
        cursor="cursor-1",
    )


def test_fake_fixture_client_requires_mode_and_explicit_allow_flag() -> None:
    settings = make_settings(
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
    )

    assert isinstance(build_liepin_worker_client(settings), FakeLiepinWorkerClient)

    managed_settings = make_settings(
        liepin_worker_mode="managed_local",
        liepin_allow_fake_fixture_worker=True,
    )
    with pytest.raises(LiepinWorkerModeError, match="fake_fixture"):
        FakeLiepinWorkerClient(managed_settings)


def test_fake_fixture_search_is_deterministic_and_labeled_fixture_only() -> None:
    settings = make_settings(
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
    )
    client = FakeLiepinWorkerClient(settings)

    first = asyncio.run(client.search(_request(), round_no=1, trace_id="trace-1"))
    second = asyncio.run(client.search(_request(), round_no=1, trace_id="trace-1"))

    assert first == second
    assert first.request_payload["fixture_only"] is True
    assert first.diagnostics == ["liepin fake fixture worker"]


def test_fake_fixture_mode_is_rejected_when_live_enabled() -> None:
    settings = make_settings(
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
        liepin_live_enabled=True,
    )

    with pytest.raises(LiepinWorkerModeError, match="live"):
        build_liepin_worker_client(settings)


def test_build_managed_local_client_for_live_capable_local_mode() -> None:
    settings = make_settings(liepin_worker_mode="managed_local")

    client = build_liepin_worker_client(settings)

    assert isinstance(client, ManagedLocalLiepinWorkerClient)


def test_external_http_client_requires_external_mode() -> None:
    settings = make_settings(
        liepin_worker_mode="external_http",
        liepin_worker_base_url="http://127.0.0.1:8123",
    )

    assert isinstance(build_liepin_worker_client(settings), ExternalHttpLiepinWorkerClient)

    managed_settings = make_settings(liepin_worker_mode="managed_local")
    with pytest.raises(LiepinWorkerModeError, match="external_http"):
        ExternalHttpLiepinWorkerClient(managed_settings)


def test_missing_external_http_worker_url_fails_before_search_dispatch() -> None:
    settings = make_settings(liepin_worker_mode="managed_local").model_copy(
        update={"liepin_worker_mode": "external_http", "liepin_worker_base_url": None}
    )

    with pytest.raises(LiepinWorkerModeError, match="liepin_worker_base_url"):
        build_liepin_worker_client(settings)


def test_worker_contracts_decode_internal_payloads() -> None:
    health = decode_worker_health({"status": "ok", "workerVersion": "fixture-worker"})
    session = decode_session_status(
        {
            "connectionId": "conn-1",
            "status": "ready",
            "providerAccountHash": "acct-hash",
            "fixtureOnly": False,
        }
    )
    handoff = decode_login_handoff(
        {
            "connectionId": "conn-1",
            "handoffToken": "handoff-token",
            "loginUrl": "https://example.test/login",
            "expiresAt": "2026-05-07T12:00:00Z",
        }
    )
    diagnostics = decode_redacted_diagnostics(
        {
            "code": "worker_failed",
            "message": "worker exited",
            "stdout": "[redacted]",
            "stderr": "[redacted]",
        }
    )

    assert isinstance(health, WorkerHealth)
    assert isinstance(session, SessionStatus)
    assert isinstance(handoff, LoginHandoff)
    assert isinstance(diagnostics, RedactedWorkerDiagnostics)
    assert health.worker_version == "fixture-worker"
    assert session.fixture_only is False
    assert handoff.handoff_token == "handoff-token"
    assert diagnostics.stdout == "[redacted]"


def test_worker_contract_decoders_reject_worker_internals() -> None:
    with pytest.raises(ValidationError):
        decode_worker_health({"status": "ok", "workerVersion": "fixture-worker", "browserDebugUrl": "ws://debug"})

    with pytest.raises(ValidationError):
        decode_session_status(
            {
                "connectionId": "conn-1",
                "status": "ready",
                "storageStatePath": "/tmp/storage-state.json",
            }
        )

    with pytest.raises(ValidationError):
        decode_login_handoff(
            {
                "connectionId": "conn-1",
                "handoffToken": "handoff-token",
                "loginUrl": "https://example.test/login",
                "expiresAt": "2026-05-07T12:00:00Z",
                "workerBaseUrl": "http://127.0.0.1:8123",
            }
        )


class RecordingHttpJson:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, object] | None,
        timeout: float,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout": timeout,
            }
        )
        return self.responses.pop(0)


def test_external_http_client_sends_worker_auth_and_decodes_health_status_and_handoff() -> None:
    settings = make_settings(
        liepin_worker_mode="external_http",
        liepin_worker_base_url="http://127.0.0.1:8123/internal-should-not-leak/..",
        liepin_api_token="worker-token",
    ).model_copy(update={"liepin_worker_base_url": "http://127.0.0.1:8123"})
    http_json = RecordingHttpJson(
        {"status": "ok", "workerVersion": "liepin-worker-v1"},
        {"connectionId": "conn-1", "status": "ready", "providerAccountHash": "acct-hash", "fixtureOnly": False},
        {
            "connectionId": "conn-1",
            "handoffToken": "handoff-token",
            "loginUrl": "https://www.liepin.com/",
            "expiresAt": "2026-05-08T12:05:00Z",
        },
    )
    client = ExternalHttpLiepinWorkerClient(settings, http_json=http_json)

    asyncio.run(client.ensure_ready())
    status = asyncio.run(client.session_status(connection_id="conn-1"))
    handoff = asyncio.run(client.login_handoff(connection_id="conn-1"))

    assert status.status == "ready"
    assert handoff.handoff_token == "handoff-token"
    assert http_json.calls == [
        {
            "method": "GET",
            "url": "http://127.0.0.1:8123/internal/health",
            "headers": {"Authorization": "Bearer worker-token"},
            "json_body": None,
            "timeout": settings.liepin_worker_timeout_seconds,
        },
        {
            "method": "GET",
            "url": "http://127.0.0.1:8123/internal/session/status?connectionId=conn-1",
            "headers": {"Authorization": "Bearer worker-token"},
            "json_body": None,
            "timeout": settings.liepin_worker_timeout_seconds,
        },
        {
            "method": "POST",
            "url": "http://127.0.0.1:8123/internal/session/login-handoff",
            "headers": {"Authorization": "Bearer worker-token"},
            "json_body": {"connectionId": "conn-1"},
            "timeout": settings.liepin_worker_timeout_seconds,
        },
    ]


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        (
            "health",
            {"status": "ok", "workerVersion": "liepin-worker-v1", "workerBaseUrl": "http://127.0.0.1:8123"},
        ),
        (
            "session_status",
            {
                "connectionId": "conn-1",
                "status": "ready",
                "fixtureOnly": False,
                "storageStatePath": "/tmp/storage-state-secret.json",
            },
        ),
        (
            "login_handoff",
            {
                "connectionId": "conn-1",
                "handoffToken": "handoff-secret",
                "loginUrl": "https://www.liepin.com/",
                "expiresAt": "2026-05-08T12:05:00Z",
                "cookies": [{"name": "lt", "value": "cookie-secret"}],
            },
        ),
    ],
)
def test_external_http_client_replaces_invalid_success_payload_with_safe_error(
    action: str,
    payload: dict[str, object],
) -> None:
    settings = make_settings(
        liepin_worker_mode="external_http",
        liepin_worker_base_url="http://127.0.0.1:8123",
    )
    client = ExternalHttpLiepinWorkerClient(settings, http_json=RecordingHttpJson(payload))

    with pytest.raises(LiepinWorkerModeError) as error:
        if action == "health":
            asyncio.run(client.ensure_ready())
        elif action == "session_status":
            asyncio.run(client.session_status(connection_id="conn-1"))
        else:
            asyncio.run(client.login_handoff(connection_id="conn-1"))

    rendered = str(error.value).lower()
    assert error.value.setup_status == "invalid_worker_response"
    assert rendered == "liepin worker returned an invalid response."
    for unsafe_fragment in (
        "127.0.0.1",
        "workerbaseurl",
        "storage",
        "cookie",
        "secret",
        "handoff-secret",
        "cdp",
    ):
        assert unsafe_fragment not in rendered


def test_managed_local_client_uses_runtime_internal_base_url_for_http_calls() -> None:
    settings = make_settings(liepin_worker_mode="managed_local", liepin_api_token="worker-token")
    runtime = SimpleNamespace(
        ensure_started=lambda **_: SimpleNamespace(internal_base_url="http://127.0.0.1:4567")
    )
    http_json = RecordingHttpJson(
        {"connectionId": "conn-1", "status": "login_required", "fixtureOnly": False},
    )
    client = ManagedLocalLiepinWorkerClient(settings, runtime=runtime, http_json=http_json)

    status = asyncio.run(client.session_status(connection_id="conn-1"))

    assert status.status == "login_required"
    assert http_json.calls[0]["url"] == "http://127.0.0.1:4567/internal/session/status?connectionId=conn-1"
    assert http_json.calls[0]["headers"] == {"Authorization": "Bearer worker-token"}


def test_external_http_client_search_posts_safe_body_and_maps_worker_cards() -> None:
    settings = make_settings(
        liepin_worker_mode="external_http",
        liepin_worker_base_url="http://127.0.0.1:8123",
        liepin_api_token="worker-token",
    )
    http_json = RecordingHttpJson(_worker_card_search_response())
    client = ExternalHttpLiepinWorkerClient(settings, http_json=http_json)

    result = asyncio.run(
        client.search(
            _request(),
            round_no=3,
            trace_id="trace-3",
            provider_account_hash="acct-hash",
        )
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].resume_id == "candidate-1"
    assert result.provider_snapshots[0].provider_name == "liepin"
    assert result.provider_snapshots[0].payload_kind == "card"
    assert result.raw_candidate_count == 1
    assert result.next_cursor == "cursor-2"
    assert "acct-hash" not in str(result.request_payload)
    assert http_json.calls == [
        {
            "method": "POST",
            "url": "http://127.0.0.1:8123/internal/search/cards",
            "headers": {"Authorization": "Bearer worker-token"},
            "json_body": _expected_search_body(),
            "timeout": settings.liepin_worker_timeout_seconds,
        }
    ]


def test_managed_local_client_search_uses_runtime_url_and_maps_worker_cards() -> None:
    settings = make_settings(liepin_worker_mode="managed_local", liepin_api_token="worker-token")
    runtime = SimpleNamespace(
        ensure_started=lambda **_: SimpleNamespace(internal_base_url="http://127.0.0.1:4567")
    )
    http_json = RecordingHttpJson(_worker_card_search_response())
    client = ManagedLocalLiepinWorkerClient(settings, runtime=runtime, http_json=http_json)

    result = asyncio.run(
        client.search(
            _request(),
            round_no=3,
            trace_id="trace-3",
            provider_account_hash="acct-hash",
        )
    )

    assert len(result.candidates) == 1
    assert result.provider_snapshots[0].synthetic_candidate_fingerprint == "liepin:candidate-1"
    assert result.request_payload == {
        "keyword": "python",
        "pageSize": 10,
        "cursor": "cursor-1",
        "round": 3,
        "traceId": "trace-3",
    }
    assert http_json.calls == [
        {
            "method": "POST",
            "url": "http://127.0.0.1:4567/internal/search/cards",
            "headers": {"Authorization": "Bearer worker-token"},
            "json_body": _expected_search_body(),
            "timeout": settings.liepin_worker_timeout_seconds,
        }
    ]


def test_default_http_json_decodes_worker_json_error_without_leaking_internals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_http_error(*args: object, **kwargs: object) -> object:
        raise HTTPError(
            "http://127.0.0.1:8123/internal/session/status?token=secret",
            409,
            "Conflict",
            {},
            io.BytesIO(
                b'{"error":{"code":"session_not_ready","message":"Login required","workerBaseUrl":"http://127.0.0.1:8123","storageStatePath":"/tmp/secret.json","cookies":["secret"]}}'
            ),
        )

    monkeypatch.setattr(liepin_client_module.urllib_request, "urlopen", raise_http_error)

    with pytest.raises(LiepinWorkerModeError) as error:
        _default_http_json(
            "GET",
            "http://127.0.0.1:8123/internal/session/status?token=secret",
            headers={"Authorization": "Bearer worker-token"},
            json_body=None,
            timeout=1.0,
        )

    assert error.value.setup_status == "session_not_ready"
    assert "session_not_ready" in str(error.value)
    assert "Liepin worker session is not ready." in str(error.value)
    assert "Login required" not in str(error.value)
    assert "127.0.0.1" not in str(error.value)
    assert "secret" not in str(error.value)
    assert "storage" not in str(error.value).lower()


@pytest.mark.parametrize(
    ("worker_code", "safe_message"),
    [
        ("detail_open_approval_not_configured", "Liepin worker detail-open approval is not configured."),
        ("detail_open_not_configured", "Liepin worker detail open is not configured."),
    ],
)
def test_default_http_json_maps_detail_open_setup_errors(
    monkeypatch: pytest.MonkeyPatch,
    worker_code: str,
    safe_message: str,
) -> None:
    def raise_http_error(*args: object, **kwargs: object) -> object:
        raise HTTPError(
            "http://127.0.0.1:8123/internal/details/open?token=secret",
            501,
            "Detail setup failed",
            {},
            io.BytesIO((f'{{"error":{{"code":"{worker_code}","message":"unsafe secret"}}}}').encode()),
        )

    monkeypatch.setattr(liepin_client_module.urllib_request, "urlopen", raise_http_error)

    with pytest.raises(LiepinWorkerModeError) as error:
        _default_http_json(
            "POST",
            "http://127.0.0.1:8123/internal/details/open?token=secret",
            headers={"Authorization": "Bearer worker-token"},
            json_body={"workerCommandId": "cmd-1", "requests": []},
            timeout=1.0,
        )

    assert error.value.setup_status == worker_code
    assert safe_message in str(error.value)
    assert "unsafe secret" not in str(error.value)


def test_default_http_json_replaces_unknown_worker_error_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe_code = "http://127.0.0.1:8123/cdp?token=secret"
    unsafe_message = "storageState=/tmp/secret.json cookie=lt-secret cdp=ws://127.0.0.1:9222/devtools"

    def raise_http_error(*args: object, **kwargs: object) -> object:
        raise HTTPError(
            "http://127.0.0.1:8123/internal/search/cards?token=secret",
            409,
            "Conflict",
            {},
            io.BytesIO(
                (
                    '{"error":{"code":'
                    f"{unsafe_code!r},"
                    '"message":'
                    f"{unsafe_message!r}"
                    "}}"
                ).replace("'", '"').encode()
            ),
        )

    monkeypatch.setattr(liepin_client_module.urllib_request, "urlopen", raise_http_error)

    with pytest.raises(LiepinWorkerModeError) as error:
        _default_http_json(
            "POST",
            "http://127.0.0.1:8123/internal/search/cards?token=secret",
            headers={"Authorization": "Bearer worker-token"},
            json_body={"connectionId": "conn-1"},
            timeout=1.0,
        )

    rendered = str(error.value)
    assert error.value.setup_status == "worker_request_failed"
    assert "Liepin worker request failed." in rendered
    assert unsafe_code not in rendered
    assert unsafe_message not in rendered
    for unsafe_fragment in ("127.0.0.1", "secret", "storage", "cookie", "cdp", "devtools"):
        assert unsafe_fragment not in rendered.lower()


def _worker_card_search_response() -> dict[str, object]:
    return {
        "cards": [
            {
                "payload": {"id": "candidate-1", "title": "Python Engineer"},
                "normalized_text": "Python Engineer",
                "provider_subject_id": "candidate-1",
                "provider_listing_id": "listing-1",
                "synthetic_candidate_fingerprint": "liepin:candidate-1",
                "identity_confidence": "provider_subject_id",
                "extraction_source": "network",
                "extractor_version": "liepin-passive-extractor-v1",
                "pii_classification": "no_direct_contact",
                "retention_policy": "provider_snapshot_7d",
                "access_scope": "local_run_only",
                "redaction_state": "raw_provider_payload",
            }
        ],
        "diagnostics": ["network"],
        "exhausted": False,
        "nextCursor": "cursor-2",
        "rawCandidateCount": 1,
        "requestPayload": {
            "keyword": "python",
            "pageSize": 10,
            "cursor": "cursor-1",
            "round": 3,
            "traceId": "trace-3",
        },
    }


def _expected_search_body() -> dict[str, object]:
    return {
        "tenantId": "tenant-a",
        "workspaceId": "workspace-a",
        "connectionId": "conn-1",
        "providerAccountHash": "acct-hash",
        "keyword": "python",
        "pageSize": 10,
        "cursor": "cursor-1",
        "round": 3,
        "traceId": "trace-3",
        "providerFilters": {"city": "上海", "skills": ["python"]},
    }
