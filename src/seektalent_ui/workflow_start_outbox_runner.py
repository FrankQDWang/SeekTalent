from __future__ import annotations

import logging
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

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

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
                logger.warning(
                    "WTS outbox item failed: %s (%s)",
                    type(exc).__name__,
                    getattr(exc, "reason_code", "outbox_unexpected_failure"),
                    extra={
                        "event_type": self.event_type,
                        "exception_type": type(exc).__name__,
                    },
                )
                self._handle_processing_error(item.outbox_id)
            processed += 1
        return processed

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._health.heartbeat()
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
            if processed > 0:
                continue
            self._wake_event.wait(self.poll_interval_seconds)
            self._wake_event.clear()

    def _handle_processing_error(self, outbox_id: str) -> None:
        updated_at = self.service.now()
        item = self.service.outbox_store.get(outbox_id)
        if item.attempt_count >= self.max_attempts:
            self._mark_final_failure(item.aggregate_id, updated_at=updated_at)
            self.service.outbox_store.mark_done(outbox_id, updated_at=updated_at)
            return
        self.service.outbox_store.mark_pending_retry(outbox_id, updated_at=updated_at)

    def _process_item(self, outbox_id: str) -> object:
        raise NotImplementedError

    def _mark_final_failure(self, aggregate_id: str, *, updated_at: str) -> None:
        del aggregate_id, updated_at

    def health_snapshot(self) -> ExecutionComponentHealth:
        thread = self._thread
        return self._health.snapshot(alive=thread is not None and thread.is_alive())


class WorkflowStartOutboxRunner(_BaseOutboxRunner):
    event_type = "workflow_start_requested"
    thread_name = "seektalent-wts-workflow-start-outbox-runner"

    def _process_item(self, outbox_id: str) -> object:
        return self.service.process_workflow_start_outbox_item(outbox_id)

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
