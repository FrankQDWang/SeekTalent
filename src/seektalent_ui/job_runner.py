from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Literal

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent.providers.liepin.client import LiepinWorkerClient
from seektalent_ui.runtime_bridge import RuntimeFactory, run_cts_source_run, run_liepin_card_source_run
from seektalent_ui.workbench_store import WorkbenchSourceRunJobContext, WorkbenchStore, _iso, _now


LEASE_DURATION = timedelta(minutes=10)
LEASE_HEARTBEAT_SECONDS = 30.0
CTS_WORKER_COUNT = 2
LIEPIN_WORKER_COUNT = 1


class WorkbenchJobRunner:
    def __init__(
        self,
        *,
        store: WorkbenchStore,
        settings: AppSettings,
        runtime_factory: RuntimeFactory,
        liepin_worker_client: LiepinWorkerClient | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.runtime_factory = runtime_factory
        self.liepin_worker_client = liepin_worker_client
        self.owner_id = f"local-{uuid.uuid4().hex[:12]}"
        self.lease_duration = LEASE_DURATION
        self.heartbeat_interval_seconds = LEASE_HEARTBEAT_SECONDS
        self._lock = threading.Lock()
        self._threads: dict[Literal["cts", "liepin"], list[threading.Thread]] = {"cts": [], "liepin": []}

    def wake(self) -> None:
        with self._lock:
            self._start_lane_workers(source_kind="cts", worker_count=CTS_WORKER_COUNT)
            self._start_lane_workers(source_kind="liepin", worker_count=LIEPIN_WORKER_COUNT)

    def _start_lane_workers(self, *, source_kind: Literal["cts", "liepin"], worker_count: int) -> None:
        live_threads = [thread for thread in self._threads[source_kind] if thread.is_alive()]
        self._threads[source_kind] = live_threads
        while len(self._threads[source_kind]) < worker_count:
            worker_number = len(self._threads[source_kind]) + 1
            thread = threading.Thread(
                target=self._run_until_idle,
                kwargs={"source_kind": source_kind},
                name=f"seektalent-workbench-{source_kind}-job-runner-{worker_number}",
                daemon=True,
            )
            self._threads[source_kind].append(thread)
            thread.start()

    def _run_until_idle(self, *, source_kind: Literal["cts", "liepin"]) -> None:
        while True:
            context = self.store.claim_next_source_run_job(
                owner_id=self.owner_id,
                lease_expires_at=self._lease_expires_at(),
                source_kind=source_kind,
            )
            if context is None:
                return
            self._execute(context)

    def _execute(self, context: WorkbenchSourceRunJobContext) -> None:
        stop_heartbeat = threading.Event()
        heartbeat_thread = self._start_lease_heartbeat(context=context, stop_event=stop_heartbeat)
        try:
            self.store.append_workbench_event(
                tenant_id="local",
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.job.source_run_id,
                source_kind=context.job.source_kind,
                event_name="requirement_triage_used",
                payload={
                    "sourceRunId": context.job.source_run_id,
                    "sourceKind": context.job.source_kind,
                    "mustHaveCount": len(context.triage.must_haves),
                    "niceToHaveCount": len(context.triage.nice_to_haves),
                    "generatedQueryHintCount": len(context.triage.generated_query_hints),
                },
            )
            if context.job.source_kind == "cts":
                run_cts_source_run(
                    context=context,
                    store=self.store,
                    settings=self.settings,
                    runtime_factory=self.runtime_factory,
                    progress_callback=lambda event: self._record_runtime_progress(context, event),
                )
            elif context.job.source_kind == "liepin":
                run_liepin_card_source_run(
                    context=context,
                    store=self.store,
                    settings=self.settings,
                    worker_client=self.liepin_worker_client,
                )
            else:
                raise RuntimeError("Unsupported source run kind.")
        except Exception as exc:  # noqa: BLE001
            self.store.mark_source_run_failed(job=context.job, error_message=str(exc) or "Source run failed.")
            return
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1)

    def _record_runtime_progress(self, context: WorkbenchSourceRunJobContext, event: ProgressEvent) -> None:
        self.store.append_workbench_event(
            tenant_id="local",
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
            session_id=context.session.session_id,
            source_run_id=context.job.source_run_id,
            source_kind=context.job.source_kind,
            event_name=f"runtime_{_safe_event_suffix(event.type)}",
            payload={
                "type": event.type,
                "message": event.message,
                "roundNo": event.round_no,
                "timestamp": event.timestamp,
                "payload": event.payload,
            },
        )

    def _lease_expires_at(self) -> str:
        return _iso(_now() + self.lease_duration)

    def _start_lease_heartbeat(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        stop_event: threading.Event,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self._lease_heartbeat_loop,
            args=(context.job.job_id, stop_event),
            name=f"seektalent-workbench-job-heartbeat-{context.job.job_id}",
            daemon=True,
        )
        thread.start()
        return thread

    def _lease_heartbeat_loop(self, job_id: str, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.heartbeat_interval_seconds):
            renewed = self.store.extend_source_run_job_lease(
                job_id=job_id,
                owner_id=self.owner_id,
                lease_expires_at=self._lease_expires_at(),
            )
            if not renewed:
                return


def _safe_event_suffix(value: str) -> str:
    suffix = "".join(character if character.isalnum() else "_" for character in value.strip().lower())
    suffix = "_".join(part for part in suffix.split("_") if part)
    return suffix or "progress"
