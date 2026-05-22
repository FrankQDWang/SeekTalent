from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.providers.liepin.pi_executor import (
    LiepinPiCardSearchResult,
    PiLiepinCapabilityProbeResult,
    PiLiepinResultStatus,
    PiLiepinSessionProbeResult,
    PiLiepinStopReason,
)
from seektalent.providers.liepin.pi_worker_client import LiepinPiWorkerClient
from seektalent.providers.liepin.worker_contracts import (
    LiepinCardSearchResponse,
    LiepinWorkerCandidateCard,
    LiepinWorkerModeError,
    LiepinWorkerPartialSearchError,
)


def _request() -> SearchRequest:
    return SearchRequest(
        query_terms=["python"],
        query_role="primary",
        keyword_query="python",
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=10,
        provider_context={"liepin_max_pages": "2", "liepin_max_cards": "15"},
    )


def _card_response(candidate_id: str = "candidate-1") -> LiepinCardSearchResponse:
    return LiepinCardSearchResponse(
        cards=[
            LiepinWorkerCandidateCard(
                payload={"providerRank": 1},
                normalized_text="Python Engineer",
                provider_subject_id=candidate_id,
                provider_listing_id="listing-1",
                synthetic_candidate_fingerprint=f"liepin:{candidate_id}",
                identity_confidence="provider_subject_id",
                extraction_source="dom_fallback",
                extractor_version="pi-agent-liepin-card-v1",
                pii_classification="no_direct_contact",
                retention_policy="provider_snapshot_7d",
                access_scope="local_run_only",
                redaction_state="redacted",
            )
        ],
        raw_candidate_count=1,
    )


@dataclass
class FakeExecutor:
    result: LiepinPiCardSearchResult | None = None
    session_result: PiLiepinSessionProbeResult | None = None
    capability_ready: bool = True
    entered: threading.Event | None = None
    release: threading.Event | None = None
    captured_search_kwargs: dict[str, object] | None = None
    captured_capability_kwargs: dict[str, object] | None = None

    def probe_capabilities(
        self,
        *,
        expected_dokobot_tool_name: str,
        expected_observed_tool_names: Sequence[str] = (),
        expected_opencli_observed_tool_names: Sequence[str] = (),
        expected_opencli_declared_tool_names: Sequence[str] = (),
    ) -> PiLiepinCapabilityProbeResult:
        self.captured_capability_kwargs = {
            "expected_dokobot_tool_name": expected_dokobot_tool_name,
            "expected_observed_tool_names": tuple(expected_observed_tool_names),
            "expected_opencli_observed_tool_names": tuple(expected_opencli_observed_tool_names),
            "expected_opencli_declared_tool_names": tuple(expected_opencli_declared_tool_names),
        }
        return PiLiepinCapabilityProbeResult(
            ready=self.capability_ready,
            safe_reason_code=None if self.capability_ready else "blocked_backend_unavailable",
        )

    def search_cards(self, **kwargs: object) -> LiepinPiCardSearchResult:
        self.captured_search_kwargs = kwargs
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=1.0)
        if self.result is None:
            raise AssertionError("missing fake result")
        return self.result

    def probe_session(self, *, connection_id: str) -> PiLiepinSessionProbeResult:
        if self.session_result is None:
            return PiLiepinSessionProbeResult(status="login_required", connection_id=connection_id)
        return self.session_result


@dataclass
class FakeOpenCliStatusProbe:
    ok: bool = True
    safe_reason_code: str = "configured"
    calls: int = 0
    results: tuple[tuple[bool, str], ...] = ()

    def status(self):
        self.calls += 1
        ok = self.ok
        safe_reason_code = self.safe_reason_code
        if self.results:
            index = min(self.calls - 1, len(self.results) - 1)
            ok, safe_reason_code = self.results[index]
        return type(
            "OpenCliStatus",
            (),
            {"ok": ok, "safe_reason_code": safe_reason_code},
        )()


def _client(executor: FakeExecutor) -> LiepinPiWorkerClient:
    return LiepinPiWorkerClient(
        executor=executor,
        session_id="session-1",
        connection_id="connection-1",
        provider_account_lock_key="account-1",
    )


def test_pi_worker_client_maps_blocked_capability_to_worker_error() -> None:
    client = _client(FakeExecutor(capability_ready=False))

    with pytest.raises(LiepinWorkerModeError, match="not ready") as error:
        asyncio.run(client.ensure_ready())

    assert error.value.code == "blocked_backend_unavailable"


def test_pi_worker_client_preserves_specific_capability_reason() -> None:
    executor = FakeExecutor(capability_ready=False)

    def probe_capabilities(
        *,
        expected_dokobot_tool_name: str,
        expected_observed_tool_names: Sequence[str] = (),
        expected_opencli_observed_tool_names: Sequence[str] = (),
        expected_opencli_declared_tool_names: Sequence[str] = (),
    ) -> PiLiepinCapabilityProbeResult:
        del expected_dokobot_tool_name
        del expected_observed_tool_names
        del expected_opencli_observed_tool_names
        del expected_opencli_declared_tool_names
        return PiLiepinCapabilityProbeResult(
            ready=False,
            safe_reason_code="liepin_pi_dokobot_tool_unobserved",
        )

    executor.probe_capabilities = probe_capabilities  # type: ignore[method-assign]
    client = _client(executor)

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(client.ensure_ready())

    assert error.value.code == "liepin_pi_dokobot_tool_unobserved"


def test_pi_worker_client_passes_configured_observed_tools_to_capability_probe() -> None:
    executor = FakeExecutor(capability_ready=True)
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session-1",
        connection_id="connection-1",
        provider_account_lock_key="account-1",
        dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    asyncio.run(client.ensure_ready())

    assert executor.captured_capability_kwargs == {
        "expected_dokobot_tool_name": "dokobot",
        "expected_observed_tool_names": ("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
        "expected_opencli_observed_tool_names": (),
        "expected_opencli_declared_tool_names": (),
    }


def test_pi_worker_client_uses_opencli_status_probe_without_llm_capability_probe() -> None:
    executor = FakeExecutor(capability_ready=True)
    status_probe = FakeOpenCliStatusProbe()
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session",
        connection_id="connection",
        provider_account_lock_key="lock",
        dokobot_tool_name="dokobot",
        expected_observed_tool_names=(),
        expected_opencli_observed_tool_names=("seektalent_opencli_status", "seektalent_opencli_capabilities"),
        expected_opencli_declared_tool_names=(
            "seektalent_opencli_status",
            "seektalent_opencli_capabilities",
            "seektalent_opencli_open_liepin_tab",
            "seektalent_opencli_state",
            "seektalent_opencli_fill",
            "seektalent_opencli_click",
        ),
        opencli_status_probe=status_probe,
    )

    asyncio.run(client.ensure_ready())

    assert status_probe.calls == 1
    assert executor.captured_capability_kwargs is None


def test_pi_worker_client_maps_opencli_status_probe_failure_to_worker_error() -> None:
    client = LiepinPiWorkerClient(
        executor=FakeExecutor(),
        session_id="session",
        connection_id="connection",
        provider_account_lock_key="lock",
        expected_opencli_observed_tool_names=("seektalent_opencli_status",),
        expected_opencli_declared_tool_names=("seektalent_opencli_status",),
        opencli_status_probe=FakeOpenCliStatusProbe(
            ok=False,
            safe_reason_code="liepin_opencli_extension_disconnected",
        ),
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(client.ensure_ready())

    assert error.value.code == "liepin_opencli_extension_disconnected"


def test_pi_worker_client_retries_transient_opencli_status_failure() -> None:
    status_probe = FakeOpenCliStatusProbe(
        results=(
            (False, "liepin_opencli_extension_disconnected"),
            (True, "configured"),
        )
    )
    client = LiepinPiWorkerClient(
        executor=FakeExecutor(),
        session_id="session",
        connection_id="connection",
        provider_account_lock_key="lock",
        expected_opencli_observed_tool_names=("seektalent_opencli_status",),
        expected_opencli_declared_tool_names=("seektalent_opencli_status",),
        opencli_status_probe=status_probe,
        opencli_status_retry_delay_seconds=0,
    )

    asyncio.run(client.ensure_ready())

    assert status_probe.calls == 2


def test_pi_worker_client_checks_opencli_status_before_card_search() -> None:
    executor = FakeExecutor(
        result=LiepinPiCardSearchResult(
            status=PiLiepinResultStatus.SUCCEEDED,
            stop_reason=PiLiepinStopReason.COMPLETED,
            safe_reason_code="completed",
            card_search=_card_response(),
        )
    )
    status_probe = FakeOpenCliStatusProbe()
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session",
        connection_id="connection",
        provider_account_lock_key="lock",
        expected_opencli_observed_tool_names=("seektalent_opencli_status",),
        expected_opencli_declared_tool_names=("seektalent_opencli_status",),
        opencli_status_probe=status_probe,
    )

    result = asyncio.run(client.search(_request(), round_no=1, trace_id="trace-1"))

    assert result.candidates[0].resume_id == "candidate-1"
    assert status_probe.calls == 1


def test_pi_worker_client_blocks_card_search_when_opencli_status_is_unavailable() -> None:
    executor = FakeExecutor(
        result=LiepinPiCardSearchResult(
            status=PiLiepinResultStatus.SUCCEEDED,
            stop_reason=PiLiepinStopReason.COMPLETED,
            safe_reason_code="completed",
            card_search=_card_response(),
        )
    )
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session",
        connection_id="connection",
        provider_account_lock_key="lock",
        expected_opencli_observed_tool_names=("seektalent_opencli_status",),
        expected_opencli_declared_tool_names=("seektalent_opencli_status",),
        opencli_status_probe=FakeOpenCliStatusProbe(
            ok=False,
            safe_reason_code="liepin_opencli_extension_disconnected",
        ),
        opencli_status_retry_delay_seconds=0,
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(client.search(_request(), round_no=1, trace_id="trace-1"))

    assert error.value.code == "liepin_opencli_extension_disconnected"
    assert executor.captured_search_kwargs is None


def test_pi_worker_client_preserves_failed_session_probe_reason() -> None:
    client = _client(
        FakeExecutor(
            session_result=PiLiepinSessionProbeResult(
                status="failed",
                connection_id="connection-1",
                safe_reason_code="liepin_pi_dokobot_tool_unobserved",
            )
        )
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(client.session_status(connection_id="connection-1"))

    assert error.value.code == "liepin_pi_dokobot_tool_unobserved"


def test_pi_worker_client_maps_successful_card_response_to_search_result() -> None:
    client = _client(
        FakeExecutor(
            result=LiepinPiCardSearchResult(
                status=PiLiepinResultStatus.SUCCEEDED,
                stop_reason=PiLiepinStopReason.COMPLETED,
                safe_reason_code="completed",
                card_search=_card_response(),
            )
        )
    )

    result = asyncio.run(client.search(_request(), round_no=1, trace_id="trace-1"))

    assert result.candidates[0].resume_id == "candidate-1"
    assert result.provider_snapshots[0].payload_kind == "card"
    assert result.raw_candidate_count == 1


def test_pi_worker_client_passes_non_secret_session_correlation_to_executor() -> None:
    executor = FakeExecutor(
        result=LiepinPiCardSearchResult(
            status=PiLiepinResultStatus.SUCCEEDED,
            stop_reason=PiLiepinStopReason.COMPLETED,
            safe_reason_code="completed",
            card_search=_card_response(),
        )
    )
    client = _client(executor)

    request = _request()
    request.provider_context["liepin_connection_id"] = "context-connection"
    request.provider_context["liepin_provider_account_hash"] = "context-account"
    asyncio.run(client.search(request, round_no=1, trace_id="trace-1"))

    assert executor.captured_search_kwargs is not None
    assert executor.captured_search_kwargs["connection_id"] == "context-connection"
    assert executor.captured_search_kwargs["provider_account_hash"] == "context-account"
    assert "session_id" not in executor.captured_search_kwargs
    assert "provider_account_lock_key" not in executor.captured_search_kwargs


def test_blocked_pi_result_becomes_safe_worker_mode_error_with_structured_code() -> None:
    client = _client(
        FakeExecutor(
            result=LiepinPiCardSearchResult(
                status=PiLiepinResultStatus.BLOCKED,
                stop_reason=PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
                safe_reason_code="blocked_backend_unavailable",
            )
        )
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(client.search(_request(), round_no=1, trace_id="trace-1"))

    assert error.value.code == "blocked_backend_unavailable"
    assert str(error.value) == "Liepin PI card search blocked."


def test_partial_pi_result_raises_partial_worker_error_with_mapped_search_result() -> None:
    client = _client(
        FakeExecutor(
            result=LiepinPiCardSearchResult(
                status=PiLiepinResultStatus.PARTIAL,
                stop_reason=PiLiepinStopReason.PARTIAL_TIMEOUT,
                safe_reason_code="partial_timeout",
                card_search=_card_response("candidate-partial"),
            )
        )
    )

    with pytest.raises(LiepinWorkerPartialSearchError) as error:
        asyncio.run(client.search(_request(), round_no=1, trace_id="trace-1"))

    assert error.value.code == "partial_timeout"
    assert error.value.cards_collected == 1
    assert error.value.partial_search_result.candidates[0].resume_id == "candidate-partial"


def test_pi_worker_client_search_does_not_block_event_loop_with_sync_executor() -> None:
    async def run_search() -> None:
        entered = threading.Event()
        release = threading.Event()
        client = _client(
            FakeExecutor(
                result=LiepinPiCardSearchResult(
                    status=PiLiepinResultStatus.SUCCEEDED,
                    stop_reason=PiLiepinStopReason.COMPLETED,
                    safe_reason_code="completed",
                    card_search=_card_response(),
                ),
                entered=entered,
                release=release,
            )
        )
        task = asyncio.create_task(client.search(_request(), round_no=1, trace_id="trace-1"))
        await asyncio.sleep(0.05)
        assert entered.is_set()
        assert not task.done()
        release.set()
        await task

    asyncio.run(run_search())


def test_session_status_exposes_ready_provider_hash_for_workbench_account_comparison() -> None:
    client = _client(
        FakeExecutor(
            session_result=PiLiepinSessionProbeResult(
                status="ready",
                connection_id="connection-1",
                provider_account_hash="other-acct",
            )
        )
    )

    status = asyncio.run(
        client.session_status(connection_id="connection-1", provider_account_hash="expected-acct")
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "other-acct"


def test_opencli_session_status_uses_connection_bound_hash_without_pi_session_probe() -> None:
    executor = FakeExecutor()
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session-1",
        connection_id="connection-1",
        provider_account_lock_key="opencli-lock",
        expected_opencli_observed_tool_names=("seektalent_opencli_status",),
        expected_opencli_declared_tool_names=("seektalent_opencli_status",),
    )

    status = asyncio.run(
        client.session_status(connection_id="connection-1", provider_account_hash="wb-account-hash")
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "wb-account-hash"
    assert executor.captured_capability_kwargs is None
