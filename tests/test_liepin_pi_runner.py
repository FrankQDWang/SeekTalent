from __future__ import annotations

from hashlib import sha256
import json

import pytest

from seektalent.providers.liepin.pi_runner import LiepinPiRunner, SearchCardsExecutor
from seektalent.providers.pi_agent.capabilities import DokoBotCapabilities
from seektalent.providers.pi_agent.contracts import (
    PiAgentFailureCode,
    PiAgentResult,
    PiAgentResultStatus,
    PiArtifactRef,
    PiBackendMode,
    ProtectedArtifactClass,
)
from seektalent.providers.pi_agent.locks import InMemoryPiConnectionLock


SEARCH_KWARGS = {
    "session_id": "session_1",
    "source_run_id": "source_run_1",
    "connection_id": "connection_1",
    "provider_account_lock_key": "provider_account_1",
    "keyword_query": "Python",
    "query_terms": ["Python"],
    "max_pages": 1,
    "max_cards": 10,
}


def _trace_writer(
    content: bytes,
    artifact_class: ProtectedArtifactClass,
    policy_id: str,
) -> PiArtifactRef:
    assert artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert policy_id == "liepin-trace-redaction-v1"
    payload = json.loads(content.decode("utf-8"))
    assert payload["schema_version"] == "pi-agent-action-trace-v1"
    if "provider_skill_id" in payload:
        assert payload["provider_skill_id"] == "liepin.search_cards.v1"
    content_hash = sha256(content).hexdigest()
    return PiArtifactRef(
        artifact_class=artifact_class,
        artifact_ref=f"trace:{content_hash}",
        content_sha256=content_hash,
        redaction_policy_id=policy_id,
    )


def _capabilities(*, action: bool) -> DokoBotCapabilities:
    return DokoBotCapabilities(
        cli_version="2.11.0",
        supports_read=True,
        supports_chunks_format=True,
        supports_session_continuation=True,
        supports_click=action,
        supports_type=action,
        supports_navigation=action,
        supports_pagination_action=action,
        action_manifest_id="manifest_1" if action else None,
        action_manifest_version="1" if action else None,
        action_manifest_tools=("click", "fill", "navigate", "turn_page") if action else (),
    )


def _runner(
    *,
    backend_mode: PiBackendMode,
    capabilities: DokoBotCapabilities | None = None,
    lock: InMemoryPiConnectionLock | None = None,
    dokobot_search_cards: SearchCardsExecutor | None = None,
    legacy_search_cards: SearchCardsExecutor | None = None,
) -> LiepinPiRunner:
    return LiepinPiRunner(
        backend_mode=backend_mode,
        dokobot_capabilities=capabilities,
        connection_lock=lock or InMemoryPiConnectionLock(),
        trace_artifact_writer=_trace_writer,
        dokobot_search_cards=dokobot_search_cards,
        legacy_search_cards=legacy_search_cards,
    )


def test_dokobot_action_mode_fails_closed_without_action_capability() -> None:
    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=False),
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE
    assert result.action_trace_ref.artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert result.action_trace_ref.content_sha256 != "0" * 64


def test_same_connection_concurrent_run_is_blocked() -> None:
    lock = InMemoryPiConnectionLock()
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_1",
        )
        is True
    )
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_2",
            source_run_id="source_run_2",
        )
        is False
    )


def test_same_provider_account_different_connection_is_blocked() -> None:
    lock = InMemoryPiConnectionLock()
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_1",
        )
        is True
    )
    assert (
        lock.acquire(
            connection_id="connection_2",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_2",
        )
        is False
    )


def test_same_source_run_cannot_reenter_connection_lock_without_release() -> None:
    lock = InMemoryPiConnectionLock()
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_1",
        )
        is True
    )
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_1",
        )
        is False
    )
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_2",
        )
        is False
    )

    lock.release(
        connection_id="connection_1",
        provider_account_lock_key="provider_account_1",
        source_run_id="source_run_1",
    )

    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_2",
        )
        is True
    )


def test_runner_returns_blocked_when_connection_or_provider_lock_is_held() -> None:
    lock = InMemoryPiConnectionLock()
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="other_run",
        )
        is True
    )
    runner = _runner(backend_mode=PiBackendMode.FAKE_FIXTURE, lock=lock)

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.PROVIDER_CONNECTION_LOCKED


def test_runner_releases_connection_lock_after_backend_error() -> None:
    lock = InMemoryPiConnectionLock()

    def legacy_search_cards(**kwargs: object) -> PiAgentResult:
        raise RuntimeError("backend crashed")

    runner = _runner(
        backend_mode=PiBackendMode.LEGACY_WORKER_COMPAT,
        lock=lock,
        legacy_search_cards=legacy_search_cards,
    )

    with pytest.raises(RuntimeError, match="backend crashed"):
        runner.search_cards(**SEARCH_KWARGS)

    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_2",
        )
        is True
    )


def test_legacy_worker_mode_is_explicit_not_silent_fallback() -> None:
    runner = _runner(
        backend_mode=PiBackendMode.LEGACY_WORKER_COMPAT,
    )

    assert runner.backend_mode == PiBackendMode.LEGACY_WORKER_COMPAT


def test_dokobot_action_mode_never_calls_legacy_backend_as_fallback() -> None:
    called = False

    def legacy_search_cards(**kwargs: object) -> PiAgentResult:
        nonlocal called
        called = True
        raise AssertionError("legacy fallback must not run")

    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=False),
        legacy_search_cards=legacy_search_cards,
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert called is False
    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE


def test_dokobot_action_mode_dispatches_when_capability_is_available() -> None:
    called = False

    def dokobot_search_cards(**kwargs: object) -> PiAgentResult:
        nonlocal called
        called = True
        return PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.SUCCEEDED,
            action_trace_ref=_trace_writer(
                b'{"schema_version":"pi-agent-action-trace-v1","interaction_id":"trace_ok"}',
                ProtectedArtifactClass.REDACTED_EVIDENCE,
                "liepin-trace-redaction-v1",
            ),
        )

    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=True),
        dokobot_search_cards=dokobot_search_cards,
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert called is True
    assert result.status == PiAgentResultStatus.SUCCEEDED


def test_dokobot_action_mode_requires_executor_when_capability_is_available() -> None:
    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=True),
    )

    with pytest.raises(RuntimeError, match="requires an explicit action executor"):
        runner.search_cards(**SEARCH_KWARGS)


@pytest.mark.parametrize("backend_mode", [PiBackendMode.DISABLED, PiBackendMode.DOKOBOT_READ_ONLY])
def test_modes_that_cannot_submit_search_are_blocked(backend_mode: PiBackendMode) -> None:
    runner = _runner(
        backend_mode=backend_mode,
        capabilities=_capabilities(action=False),
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE


def test_legacy_worker_mode_without_executor_is_blocked() -> None:
    runner = _runner(backend_mode=PiBackendMode.LEGACY_WORKER_COMPAT)

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE


def test_fake_fixture_mode_returns_success_with_real_trace_ref() -> None:
    runner = _runner(backend_mode=PiBackendMode.FAKE_FIXTURE)

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.SUCCEEDED
    assert result.action_trace_ref.artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert result.action_trace_ref.content_sha256 != "0" * 64
