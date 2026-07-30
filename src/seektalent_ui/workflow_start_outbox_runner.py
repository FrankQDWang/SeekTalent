from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_ui.execution_health import ExecutionComponentHealth, ExecutionHealthTracker


logger = logging.getLogger(__name__)

CLAIM_TIMEOUT_SECONDS = 60
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_BATCH_SIZE = 25
DEFAULT_MAX_ATTEMPTS = 5
MAX_RETRY_BACKOFF_SECONDS = 60


class RetryableOutboxError(RuntimeError):
    """Explicitly classified transient outbox processing failure."""


class OutboxDispatchUnknownError(RuntimeError):
    """Dispatch intent exists but terminal downstream truth is unknown."""


class OutboxCommittedError(RuntimeError):
    """Downstream receipt proves the logical effect already committed."""


class _BaseOutboxRunner:
    event_type = ""
    thread_name = "seektalent-wts-outbox-runner"

    def __init__(
        self,
        *,
        service: ConversationAgentService,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.service = service
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._health = ExecutionHealthTracker(self.event_type)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name=self.thread_name,
                daemon=True,
            )
            self._thread.start()
            self._health.restarted()
            self._persist_health(alive=True)

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._persist_health(
            alive=thread is not None and thread.is_alive()
        )

    def wake(self) -> None:
        self.start()
        self._wake_event.set()

    def run_once(self) -> int:
        now = self.service.now()
        reclaim_before = _format_time(_parse_time(now) - timedelta(seconds=CLAIM_TIMEOUT_SECONDS))
        candidates = self.service.outbox_store.list_claimable_items(
            event_type=self.event_type,
            reclaim_before=reclaim_before,
            limit=self.batch_size,
        )
        processed = 0
        for item in candidates:
            if self._stop_event.is_set():
                break
            if item.status == "pending" and not _retry_due(item.updated_at, attempt_count=item.attempt_count, now=now):
                continue
            try:
                self._process_item(item.outbox_id)
            except Exception as exc:  # noqa: BLE001 - durable outbox retry remains bounded
                self._health.failure(exc)
                runtime_store = getattr(
                    getattr(
                        self.service,
                        "service_action_adapter",
                        None,
                    ),
                    "runtime_store",
                    None,
                )
                recorder = getattr(
                    runtime_store,
                    "record_execution_failure",
                    None,
                )
                if callable(recorder):
                    try:
                        recorder(
                            runtime_run_id=None,
                            component=self.event_type,
                            boundary="process_item",
                            safe_reason_code=(
                                "outbox_unexpected_failure"
                            ),
                            error=exc,
                            failure_role="primary",
                            occurred_at=self.service.now(),
                        )
                    except Exception as persistence_error:  # noqa: BLE001
                        logger.debug(
                            "outbox failure persistence failed: %s",
                            type(persistence_error).__name__,
                        )
                logger.warning(
                    "WTS outbox item failed: %s (%s)",
                    type(exc).__name__,
                    getattr(exc, "reason_code", "outbox_unexpected_failure"),
                    extra={
                        "event_type": self.event_type,
                        "exception_type": type(exc).__name__,
                    },
                )
                self._handle_processing_error(item.outbox_id, exc)
            processed += 1
        return processed

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._health.heartbeat()
            self._persist_health(alive=True)
            try:
                processed = self.run_once()
            except Exception as exc:  # noqa: BLE001 - keep polling after an unknown failure
                self._health.failure(exc)
                logger.warning(
                    "WTS outbox runner poll failed: %s",
                    type(exc).__name__,
                    extra={
                        "event_type": self.event_type,
                        "exception_type": type(exc).__name__,
                    },
                )
                processed = 0
            else:
                self._health.success()
                self._persist_health(alive=True)
            if processed > 0:
                continue
            self._wake_event.wait(self.poll_interval_seconds)
            self._wake_event.clear()

    def _handle_processing_error(
        self,
        outbox_id: str,
        error: Exception,
    ) -> None:
        updated_at = self.service.now()
        if isinstance(error, OutboxCommittedError):
            self.service.outbox_store.mark_done(
                outbox_id,
                updated_at=updated_at,
            )
            return
        if isinstance(error, OutboxDispatchUnknownError):
            self.service.outbox_store.mark_waiting_reconciliation(
                outbox_id,
                reason_code="outbox_dispatch_unknown",
                updated_at=updated_at,
            )
            return
        boundary = (
            "no_effect"
            if isinstance(error, RetryableOutboxError)
            else self._reconcile_effect_boundary(outbox_id, error)
        )
        if boundary == "committed":
            self.service.outbox_store.mark_done(
                outbox_id,
                updated_at=updated_at,
            )
            return
        if boundary == "unknown":
            self.service.outbox_store.mark_waiting_reconciliation(
                outbox_id,
                reason_code="outbox_dispatch_unknown",
                updated_at=updated_at,
            )
            return
        retryable = isinstance(
            error,
            (RetryableOutboxError, sqlite3.OperationalError, TimeoutError),
        )
        if boundary != "no_effect" or not retryable:
            self.service.outbox_store.mark_quarantined(
                outbox_id,
                reason_code="outbox_unexpected_failure",
                updated_at=updated_at,
            )
            return
        item = self.service.outbox_store.get(outbox_id)
        if item.attempt_count >= self.max_attempts:
            self._mark_final_failure(item.aggregate_id, updated_at=updated_at)
            self.service.outbox_store.mark_done(outbox_id, updated_at=updated_at)
            return
        self.service.outbox_store.mark_pending_retry(outbox_id, updated_at=updated_at)

    def _reconcile_effect_boundary(
        self,
        outbox_id: str,
        error: Exception,
    ) -> str:
        del outbox_id, error
        return "programming"

    def _process_item(self, outbox_id: str) -> object:
        raise NotImplementedError

    def _mark_final_failure(self, aggregate_id: str, *, updated_at: str) -> None:
        del aggregate_id, updated_at

    def health_snapshot(self) -> ExecutionComponentHealth:
        thread = self._thread
        return self._health.snapshot(alive=thread is not None and thread.is_alive())

    def _persist_health(self, *, alive: bool) -> None:
        snapshot = self._health.snapshot(alive=alive)
        runtime_store = getattr(
            getattr(
                self.service,
                "service_action_adapter",
                None,
            ),
            "runtime_store",
            None,
        )
        if runtime_store is None:
            return
        try:
            runtime_store.record_component_health(
                component=snapshot.name,
                alive=snapshot.alive,
                last_heartbeat_at=snapshot.last_heartbeat_at,
                last_success_at=snapshot.last_success_at,
                first_failure_at=snapshot.first_failure_at,
                first_failure_type=snapshot.first_failure_type,
                failure_count=snapshot.failure_count,
                restart_count=snapshot.restart_count,
                observed_at=self.service.now(),
            )
        except Exception:  # noqa: BLE001 - diagnostics cannot stop execution
            logger.debug(
                "outbox component health persistence failed",
                exc_info=True,
            )


class WorkflowStartOutboxRunner(_BaseOutboxRunner):
    event_type = "workflow_start_requested"
    thread_name = "seektalent-wts-workflow-start-outbox-runner"

    def _process_item(self, outbox_id: str) -> object:
        return self.service.process_workflow_start_outbox_item(outbox_id)

    def _reconcile_effect_boundary(
        self,
        outbox_id: str,
        error: Exception,
    ) -> str:
        if isinstance(error, (KeyError, TypeError, AssertionError)):
            return "programming"
        try:
            item = self.service.outbox_store.get(outbox_id)
            intent = self.service.workflow_start_intent_store.get(
                item.aggregate_id
            )
            runtime_store = (
                self.service.service_action_adapter.runtime_store
            )
            if runtime_store is None:
                return "unknown"
            run = runtime_store.get_run_by_start_idempotency_key(
                intent.deterministic_run_key
            )
            if run is None:
                return "no_effect"
            self.service._link_started_workflow_run(
                intent,
                runtime_run_id=run.runtime_run_id,
            )
            self.service.workflow_start_intent_store.mark_started(
                intent.workflow_start_intent_id,
                runtime_run_id=run.runtime_run_id,
                updated_at=self.service.now(),
            )
            return "committed"
        except Exception:  # noqa: BLE001 - inability to prove is unknown
            return "unknown"

    def _mark_final_failure(self, aggregate_id: str, *, updated_at: str) -> None:
        self.service.workflow_start_intent_store.mark_failed(
            aggregate_id,
            reason_code="workflow_start_outbox_failed",
            updated_at=updated_at,
        )


class RequirementExtractionOutboxRunner(_BaseOutboxRunner):
    event_type = "requirement_extraction_requested"
    thread_name = "seektalent-wts-requirement-extraction-outbox-runner"

    def wake(self) -> None:
        if self._thread is not None:
            self.start()
        self._wake_event.set()

    def _process_item(self, outbox_id: str) -> object:
        return self.service.process_requirement_extraction_outbox_item(outbox_id)

    def _reconcile_effect_boundary(
        self,
        outbox_id: str,
        error: Exception,
    ) -> str:
        if isinstance(error, (KeyError, TypeError, AssertionError)):
            return "programming"
        try:
            item = self.service.outbox_store.get(outbox_id)
            link = (
                self.service.job_request_store
                .get_requirement_draft_job_request_link_by_job_request(
                    item.aggregate_id
                )
            )
            if link is not None:
                return "committed"
            job_request = (
                self.service.job_request_store
                .get_job_request_revision(item.aggregate_id)
            )
            if job_request is None:
                return "programming"
            operation = (
                self.service
                ._extract_requirements_operation_audit_for_job_request(
                    conversation_id=job_request.conversation_id,
                    job_request=job_request,
                )
            )
            audits = self.service.store.list_operation_audits(
                conversation_id=job_request.conversation_id
            )
            if any(
                audit.operation_id == operation.operation_id
                for audit in audits
            ):
                return "unknown"
            return "no_effect"
        except Exception:  # noqa: BLE001 - inability to prove is unknown
            return "unknown"

    def _mark_final_failure(self, aggregate_id: str, *, updated_at: str) -> None:
        job_request = self.service.job_request_store.get_job_request_revision(aggregate_id)
        if job_request is None:
            return
        operation = self.service._extract_requirements_operation_audit_for_job_request(
            conversation_id=job_request.conversation_id,
            job_request=job_request,
        )
        self.service.store.save_operation_audit(
            operation_id=operation.operation_id,
            conversation_id=job_request.conversation_id,
            operation_name="extract_requirements",
            execution_origin="service",
            status="failed",
            args=operation.args,
            result=None,
            reason_code="requirement_extraction_outbox_failed",
            started_at=operation.started_at,
            completed_at=updated_at,
        )
        self.service.store.update_conversation_status(
            conversation_id=job_request.conversation_id,
            status="failed",
            updated_at=updated_at,
        )


def _retry_due(updated_at: str, *, attempt_count: int, now: str) -> bool:
    if attempt_count <= 0:
        return True
    backoff_seconds = min(2 ** min(attempt_count - 1, 6), MAX_RETRY_BACKOFF_SECONDS)
    return _parse_time(updated_at) + timedelta(seconds=backoff_seconds) <= _parse_time(now)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
