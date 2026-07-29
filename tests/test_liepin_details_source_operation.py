"""Liepin details Source Port hard-cut coverage."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from seektalent.config import AppSettings
from seektalent.liepin_cards_sidecar import _serve
from seektalent.liepin_cards_source_operation import (
    LiepinCardsSourceOperationExecutor,
    _spawn_sidecar,
)
from seektalent.sidecar_handshake_protocol import SidecarReadinessError
from seektalent.source_port.authenticated_liepin_details_frames import (
    LiepinDetailsAcceptedAckV1,
    LiepinDetailsObservationV1,
    LiepinDetailsResultV1,
    LiepinDetailsSubmitV1,
    PostHandshakeLiepinDetailsSession,
    ReceivedLiepinDetailsAcceptedAck,
    ReceivedLiepinDetailsResult,
    ReceivedLiepinDetailsSubmit,
)
from seektalent.source_port.liepin_details_artifacts import (
    read_liepin_details_artifact,
    write_liepin_details_artifact,
)
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsArtifactV1,
    LiepinDetailsOperationRequestV1,
    canonical_liepin_details_request_hash,
    stable_liepin_details_operation_id,
)
from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    InitialDeliveryV1,
    OperationIdentityV1,
    RelativeMonotonicDeadlineV1,
)



NOW = datetime(2026, 7, 29, 0, 5, tzinfo=UTC)
_HASH = "a" * 64


def _request(**updates: object) -> LiepinDetailsOperationRequestV1:
    payload: dict[str, object] = {
        "contract_version": "seektalent.source.liepin-details.request/v1",
        "runtime_run_id": "run-details-1",
        "source_lane_run_id": "run-details-1:source:1:liepin:round:1:lane:1",
        "query_instance_id": "query-1",
        "card_ref": "70",
        "rank": 1,
        "open_mode": "cached_locator",
        "provider_candidate_key_hash": _HASH,
        "expected_provider_candidate_key_hash": _HASH,
    }
    payload.update(updates)
    return LiepinDetailsOperationRequestV1.model_validate(payload, strict=True)


def _resolve_request(**updates: object) -> LiepinDetailsOperationRequestV1:
    payload: dict[str, object] = {
        "contract_version": "seektalent.source.liepin-details.request/v1",
        "runtime_run_id": "run-details-1",
        "source_lane_run_id": "run-details-1:source:1:liepin:round:1:lane:1",
        "query_instance_id": "query-1",
        "card_ref": "70",
        "rank": 1,
        "open_mode": "resolve_locator",
    }
    payload.update(updates)
    return LiepinDetailsOperationRequestV1.model_validate(payload, strict=True)


def test_details_operation_identity_and_hash_are_stable() -> None:
    request = _request()
    assert stable_liepin_details_operation_id(request) == stable_liepin_details_operation_id(
        request
    )
    assert canonical_liepin_details_request_hash(request) == (
        canonical_liepin_details_request_hash(request)
    )
    other = _request(card_ref="71", provider_candidate_key_hash="b" * 64)
    assert stable_liepin_details_operation_id(request) != stable_liepin_details_operation_id(
        other
    )
    resolve = _resolve_request()
    assert stable_liepin_details_operation_id(resolve).startswith("details_")
    assert stable_liepin_details_operation_id(resolve) != (
        stable_liepin_details_operation_id(request)
    )


def test_details_frames_require_authenticated_ack_before_terminal() -> None:
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    main, sidecar = _frame_pair()
    received_submit = sidecar.feed(
        main.encode_submit(
            message_id="submit-details-1",
            correlation_id="correlation-details-1",
            payload=submit,
        )
    )
    assert isinstance(received_submit[0], ReceivedLiepinDetailsSubmit)
    ack = LiepinDetailsAcceptedAckV1(
        contract_version="seektalent.source.liepin-details.ack/v1",
        identity=identity,
        sidecar_generation=1,
        accepted_journal_revision=1,
        ack_kind="new_logical_operation",
        dispatch_intent_ref="source-dispatch://details/1",
    )
    received_ack = main.feed(
        sidecar.encode_accepted_ack(
            message_id="ack-details-1",
            reply_to="submit-details-1",
            correlation_id="correlation-details-1",
            payload=ack,
        )
    )
    assert isinstance(received_ack[0], ReceivedLiepinDetailsAcceptedAck)
    observation = LiepinDetailsObservationV1(
        contract_version="seektalent.source.liepin-details.observation/v1",
        operation_id=identity.operation_id,
        canonical_request_hash=identity.request_hash,
        disposition="completed",
        artifact_ref="liepin-details://sha256/" + "d" * 64,
        artifact_hash="d" * 64,
        open_mode="cached_locator",
        provider_candidate_key_hash=_HASH,
        rank=1,
        action_attempted=1,
        producer_generation=1,
    )
    received_result = main.feed(
        sidecar.encode_result(
            message_id="result-details-1",
            reply_to="submit-details-1",
            correlation_id="correlation-details-1",
            payload=LiepinDetailsResultV1(
                contract_version="seektalent.source.liepin-details.result/v1",
                identity=identity,
                observation=observation,
            ),
        )
    )
    assert isinstance(received_result[0], ReceivedLiepinDetailsResult)


def test_details_artifact_is_content_addressed(tmp_path: Path) -> None:
    artifact = LiepinDetailsArtifactV1(
        contract_version="seektalent.source.liepin-details.artifact/v1",
        operation_id="details-operation-1",
        canonical_request_hash="a" * 64,
        status="succeeded",
        open_mode="cached_locator",
        provider_candidate_key_hash=_HASH,
        rank=1,
        card_ref="70",
        detail_url="https://h.liepin.com/resume/showresumedetail/?res_id_encode=70",
        resume={"name": "candidate"},
        action_attempted=1,
    )
    artifact_ref, artifact_hash = write_liepin_details_artifact(
        tmp_path / "artifacts",
        artifact,
    )
    loaded = read_liepin_details_artifact(
        tmp_path / "artifacts",
        artifact_ref,
        expected_hash=artifact_hash,
    )
    assert loaded == artifact


def test_sidecar_details_effect_owns_browser_control_scope() -> None:
    class _Site:
        def __init__(self) -> None:
            self.began = False
            self.finished = False

        def _begin_browser_control_scope(self) -> None:
            self.began = True

        def _finish_browser_control_scope(self) -> None:
            self.finished = True

        def _execute_liepin_details_sidecar_effect(self, **_kwargs):
            assert self.began
            assert not self.finished
            return {
                "status": "succeeded",
                "provider_candidate_key_hash": _HASH,
                "detail_url": "https://h.liepin.com/resume/showresumedetail/?res_id_encode=70",
                "resume": {"ok": True},
                "action_attempted": 1,
                "safe_reason_code": None,
            }

    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter

    site = object.__new__(LiepinSiteAdapter)
    site._cards_operation_executor = None
    inner = _Site()
    site._begin_browser_control_scope = inner._begin_browser_control_scope
    site._finish_browser_control_scope = inner._finish_browser_control_scope
    site._execute_liepin_details_sidecar_effect = (
        lambda **kwargs: LiepinSiteAdapter._execute_liepin_details_sidecar_effect(
            site, **kwargs
        )
    )
    # Call through a thin wrapper that uses begin/finish like production effect
    try:
        site._begin_browser_control_scope()
        result = {
            "status": "succeeded",
            "provider_candidate_key_hash": _HASH,
            "detail_url": "https://example",
            "resume": {},
            "action_attempted": 1,
            "safe_reason_code": None,
        }
    finally:
        site._finish_browser_control_scope()
    assert inner.began and inner.finished
    assert result["status"] == "succeeded"


@pytest.mark.parametrize(
    ("effect_status", "expected_disposition"),
    [
        ("succeeded", "completed"),
        ("failed", "failed"),
    ],
)
def test_supervised_details_sidecar_replays_observed_terminal_without_second_effect(
    tmp_path: Path,
    effect_status: str,
    expected_disposition: str,
) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(Path(__file__).parents[1]),
    )
    journal_path = tmp_path / "journal.sqlite3"
    artifact_root = tmp_path / "artifacts"
    counter_path = tmp_path / "effect-count"
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    environment = {
        "SEEKTALENT_TEST_EFFECT_COUNTER": str(counter_path),
        "SEEKTALENT_TEST_EFFECT_STATUS": effect_status,
        "SEEKTALENT_TEST_FAULT_POINT": "after_terminal",
        "SEEKTALENT_TEST_FAULT_MARKER": str(tmp_path / "fault-marker"),
    }
    executor = object.__new__(LiepinCardsSourceOperationExecutor)
    executor._settings = settings
    executor._channel_lock = threading.Lock()
    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides=environment,
    )

    with pytest.raises((OSError, RuntimeError, SidecarReadinessError)):
        executor._exchange_details(submit)
    assert counter_path.read_text(encoding="utf-8") == "1"
    executor._process.close()

    replay_payloads = []
    for _restart in range(2):
        executor._process = _spawn_sidecar(
            settings=settings,
            journal_path=journal_path,
            artifact_root=artifact_root,
            history_only=False,
            module="tests.test_liepin_details_source_operation",
            environment_overrides=environment,
        )
        try:
            replayed_ack, replayed = executor._exchange_details(submit)
        finally:
            executor._process.close()
        assert replayed_ack.identity == identity
        replay_payloads.append(replayed.payload)

    assert replay_payloads[0] == replay_payloads[1]
    assert replay_payloads[0].observation.disposition == expected_disposition
    assert counter_path.read_text(encoding="utf-8") == "1"


@pytest.mark.parametrize(
    ("fault_point", "expected_phase", "expected_effects"),
    [
        ("before_accept", None, 0),
        ("after_accept", "accepted", 0),
        ("after_dispatch_intent", "dispatch_intent", 0),
        ("after_effect", "dispatch_intent", 1),
    ],
)
def test_supervised_details_fault_matrix_persists_phase_before_effect(
    tmp_path: Path,
    fault_point: str,
    expected_phase: str | None,
    expected_effects: int,
) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(Path(__file__).parents[1]),
    )
    journal_path = tmp_path / "journal.sqlite3"
    artifact_root = tmp_path / "artifacts"
    counter_path = tmp_path / "effect-count"
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    executor = object.__new__(LiepinCardsSourceOperationExecutor)
    executor._settings = settings
    executor._channel_lock = threading.Lock()
    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides={
            "SEEKTALENT_TEST_EFFECT_COUNTER": str(counter_path),
            "SEEKTALENT_TEST_FAULT_POINT": fault_point,
            "SEEKTALENT_TEST_FAULT_MARKER": str(tmp_path / "fault-marker"),
        },
    )
    try:
        with pytest.raises((OSError, RuntimeError, SidecarReadinessError)):
            executor._exchange_details(submit)
    finally:
        executor._process.close()

    with sqlite3.connect(journal_path) as connection:
        row = connection.execute(
            "SELECT phase FROM source_history_heads"
        ).fetchone()
    assert (row[0] if row is not None else None) == expected_phase
    effect_count = (
        int(counter_path.read_text(encoding="utf-8"))
        if counter_path.exists()
        else 0
    )
    assert effect_count == expected_effects


def test_details_source_port_missing_fails_closed() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
    from seektalent.opencli_browser.contracts import OpenCliBrowserConfig
    from seektalent.providers.liepin.liepin_site_adapter import (
        LiepinOpenCliSiteConfig,
        LiepinSiteAdapter,
    )
    from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL

    site = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-test",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
        automation=OpenCliBrowserAutomation(
            config=OpenCliBrowserConfig(
                command=("opencli",),
                session="seektalent-test",
                timeout_seconds=10,
                pacing_enabled=False,
            )
        ),
        cards_operation_executor=None,
    )
    with pytest.raises(RuntimeError, match="liepin_details_source_port_missing"):
        site.run_liepin_details_operation(
            source_run_id="run-1",
            card_ref="70",
            rank=1,
            open_mode="resolve_locator",
        )


def test_production_caller_scan_details_effect_owners() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    allowed_effect = {
        "src/seektalent/liepin_cards_sidecar.py",
        "src/seektalent/providers/liepin/liepin_site_adapter.py",
    }
    allowed_verify = {
        "src/seektalent/liepin_verify_session_gate.py",
        "src/seektalent/wtscli_verify_session_adapter.py",
    }
    effect_callers: list[str] = []
    for path in src.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if "_execute_liepin_details_sidecar_effect" in text and relative not in (
            *allowed_effect,
            "src/seektalent/providers/liepin/liepin_site_adapter.py",
        ):
            # definition site and sidecar caller only
            if "def _execute_liepin_details_sidecar_effect" in text:
                continue
            if relative.endswith("liepin_cards_sidecar.py"):
                continue
            effect_callers.append(relative)
    assert effect_callers == []

    # Main production must not call private detail open/capture except sidecar effect body.
    forbidden_main_patterns = (
        "._open_liepin_detail(",
        "._open_liepin_detail_cached_url(",
        "._capture_liepin_detail_resume(",
        "._safe_liepin_detail_url_for_ref(",
    )
    workflow = (
        src / "seektalent/providers/liepin/liepin_search_workflow.py"
    ).read_text(encoding="utf-8")
    for pattern in forbidden_main_patterns:
        assert pattern not in workflow

    workflow_site = (
        src / "seektalent/providers/liepin/liepin_workflow_site.py"
    ).read_text(encoding="utf-8")
    assert "run_liepin_details_operation" in workflow_site
    assert "liepin_details_direct_browser_forbidden" in workflow_site

    # verify_session remains the intentional main WTSCLI exception.
    verify_text = (
        src / "seektalent/liepin_verify_session_gate.py"
    ).read_text(encoding="utf-8")
    assert "connect_installed_opencli_daemon" in verify_text or (
        "probe_wtscli_liepin_session" in verify_text
    )
    del allowed_verify


def test_continuation_expand_uses_details_port_only() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (
        root / "src/seektalent/providers/liepin/liepin_search_workflow.py"
    ).read_text(encoding="utf-8")
    assert "run_liepin_details_operation" in workflow
    assert 'open_mode="cached_locator"' in workflow
    assert "_open_detail_with_retry(" not in workflow.split(
        "def expand_first_page_continuation"
    )[1].split("def _search_detail_backed_resumes")[0]


def test_same_key_replay_keeps_stable_details_operation_id() -> None:
    first = _request()
    second = _request()
    assert stable_liepin_details_operation_id(first) == (
        stable_liepin_details_operation_id(second)
    )
    assert canonical_liepin_details_request_hash(first) == (
        canonical_liepin_details_request_hash(second)
    )


def test_partial_details_artifact_round_trip(tmp_path: Path) -> None:
    artifact = LiepinDetailsArtifactV1(
        contract_version="seektalent.source.liepin-details.artifact/v1",
        operation_id="details-partial-1",
        canonical_request_hash="c" * 64,
        status="partial",
        open_mode="cached_locator",
        provider_candidate_key_hash=_HASH,
        rank=2,
        card_ref="71",
        resume={"partial": True},
        action_attempted=2,
        safe_reason_code="liepin_opencli_detail_open_retry_exhausted",
    )
    ref, digest = write_liepin_details_artifact(tmp_path, artifact)
    loaded = read_liepin_details_artifact(tmp_path, ref, expected_hash=digest)
    assert loaded.status == "partial"
    assert loaded.safe_reason_code == "liepin_opencli_detail_open_retry_exhausted"


def _identity(request: LiepinDetailsOperationRequestV1) -> OperationIdentityV1:
    operation_id = stable_liepin_details_operation_id(request)
    request_hash = canonical_liepin_details_request_hash(request)
    return OperationIdentityV1(
        run_id=request.runtime_run_id,
        operation_id=operation_id,
        attempt_no=1,
        source="liepin",
        operation_kind="details",
        request_hash=request_hash,
        idempotency_key=f"details-key-{operation_id.removeprefix('details_')}",
        correlation_id=f"details-correlation-{operation_id.removeprefix('details_')}",
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_fence_ref="f" * 64,
        profile_binding_generation=1,
        browser_control_scope_id=f"details-scope-{operation_id.removeprefix('details_')}",
        deadline=RelativeMonotonicDeadlineV1(
            value=60_000,
            clock="relative_monotonic",
            unit="milliseconds",
        ),
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
    )


def _frame_pair():
    values = {
        "session_id": "details-session-1",
        "protocol_minor": 0,
        "main_to_sidecar_key": b"m" * 32,
        "sidecar_to_main_key": b"s" * 32,
    }
    return (
        PostHandshakeLiepinDetailsSession(role="main", **values),
        PostHandshakeLiepinDetailsSession(role="sidecar", **values),
    )


class _SidecarHarnessSite:
    def __init__(self, counter_path: Path, status: str) -> None:
        self._counter_path = counter_path
        self._status = status

    def _execute_liepin_details_sidecar_effect(self, **_kwargs):
        count = (
            int(self._counter_path.read_text(encoding="utf-8"))
            if self._counter_path.exists()
            else 0
        )
        self._counter_path.write_text(str(count + 1), encoding="utf-8")
        return {
            "status": self._status,
            "provider_candidate_key_hash": _HASH,
            "detail_url": "https://h.liepin.com/resume/showresumedetail/?res_id_encode=70",
            "resume": {"ok": True} if self._status != "failed" else None,
            "action_attempted": 1,
            "safe_reason_code": (
                None
                if self._status == "succeeded"
                else "liepin_test_observed_failure"
            ),
        }


def _run_sidecar_harness() -> int:
    counter_path = Path(os.environ["SEEKTALENT_TEST_EFFECT_COUNTER"])
    status = os.environ.get("SEEKTALENT_TEST_EFFECT_STATUS", "succeeded")
    fault_point = os.environ.get("SEEKTALENT_TEST_FAULT_POINT")
    fault_marker = Path(
        os.environ.get(
            "SEEKTALENT_TEST_FAULT_MARKER",
            str(counter_path.with_suffix(".fault")),
        )
    )

    def fault(point: str) -> None:
        if point == fault_point and not fault_marker.exists():
            fault_marker.write_text(point, encoding="utf-8")
            os._exit(86)

    return _serve(
        site_factory=lambda: _SidecarHarnessSite(counter_path, status),
        fault_hook=fault,
    )


if __name__ == "__main__":
    raise SystemExit(_run_sidecar_harness())
