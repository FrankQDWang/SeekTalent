from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Literal

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent.providers.liepin.client import LiepinWorkerClient
from seektalent_ui.workbench_note_writer import NOTE_WRITER_TICK_SECONDS, WorkbenchNoteWriter
from seektalent_ui.runtime_bridge import (
    RuntimeFactory,
    extract_requirement_triage,
    run_cts_source_run,
    run_liepin_card_source_run,
    run_runtime_sourcing_job,
)
from seektalent_ui.workbench_store import (
    DEFAULT_TENANT_ID,
    WorkbenchRequirementTriage,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchSession,
    WorkbenchSourceRunJobContext,
    WorkbenchStore,
    WorkbenchUser,
    _iso,
    _now,
)


LEASE_DURATION = timedelta(minutes=10)
LEASE_HEARTBEAT_SECONDS = 10.0
NOTE_WRITER_HEARTBEAT_SECONDS = float(NOTE_WRITER_TICK_SECONDS)
CTS_WORKER_COUNT = 2
LIEPIN_WORKER_COUNT = 1
RUNTIME_WORKER_COUNT = 2


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
        self.note_writer_heartbeat_interval_seconds = NOTE_WRITER_HEARTBEAT_SECONDS
        self.note_writer = WorkbenchNoteWriter(store=store, settings=settings, lease_owner=f"{self.owner_id}:note-writer")
        self._lock = threading.Lock()
        self._threads: dict[Literal["cts", "liepin"], list[threading.Thread]] = {"cts": [], "liepin": []}
        self._runtime_threads: list[threading.Thread] = []
        self._triage_threads: dict[str, threading.Thread] = {}

    def wake(self) -> None:
        with self._lock:
            self._start_runtime_workers(worker_count=RUNTIME_WORKER_COUNT)
            self._start_lane_workers(source_kind="cts", worker_count=CTS_WORKER_COUNT)
            self._start_lane_workers(source_kind="liepin", worker_count=LIEPIN_WORKER_COUNT)

    def start_requirement_triage(self, *, user: WorkbenchUser, session_id: str) -> bool:
        session = self.store.get_workbench_session(user=user, session_id=session_id)
        if session is None or _triage_has_input(session.requirement_triage):
            return False

        with self._lock:
            self._triage_threads = {
                thread_session_id: thread
                for thread_session_id, thread in self._triage_threads.items()
                if thread.is_alive()
            }
            if session_id in self._triage_threads:
                return False
            self._record_requirement_prepare_started(user=user, session=session)
            thread = threading.Thread(
                target=self._execute_requirement_triage,
                kwargs={"user": user, "session_id": session_id},
                name=f"seektalent-workbench-requirement-triage-{session_id}",
                daemon=True,
            )
            self._triage_threads[session_id] = thread
            thread.start()
            return True

    def _execute_requirement_triage(self, *, user: WorkbenchUser, session_id: str) -> None:
        stop_heartbeat = threading.Event()
        heartbeat_thread = self._start_note_writer_heartbeat(
            user=user,
            session_id=session_id,
            stop_event=stop_heartbeat,
        )
        try:
            session = self.store.get_workbench_session(user=user, session_id=session_id)
            if session is None:
                return
            if _triage_has_input(session.requirement_triage):
                return
            extracted = extract_requirement_triage(
                session=session,
                settings=self.settings,
                runtime_factory=self.runtime_factory,
                progress_callback=lambda event: self._record_requirement_progress(user=user, session=session, event=event),
            )
            triage = self.store.update_requirement_triage(
                user=user,
                session_id=session_id,
                must_haves=extracted.must_haves,
                nice_to_haves=extracted.nice_to_haves,
                synonyms=extracted.synonyms,
                seniority_filters=extracted.seniority_filters,
                exclusions=extracted.exclusions,
                generated_query_hints=extracted.generated_query_hints,
            )
            if triage is not None:
                self._tick_note_writer_for_session(user=user, session_id=session_id)
        except Exception:  # noqa: BLE001
            self.store.append_workbench_event(
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="runtime_requirements_failed",
                schema_version="runtime_progress_v1",
                payload={
                    "message": "Requirement extraction failed.",
                    "roundNo": None,
                    "stage": "requirements",
                    "errorType": "RequirementExtractionError",
                },
            )
            self.store.try_append_workbench_note(
                user=user,
                session_id=session_id,
                idempotency_key=f"workbench-running-note:{session_id}:triage-prepare-failed",
                text="需求拆解遇到问题，请稍后重试。",
                status_hint="failed",
                note_kind="progress",
            )
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1)
            with self._lock:
                current = self._triage_threads.get(session_id)
                if current is threading.current_thread():
                    self._triage_threads.pop(session_id, None)

    def _record_requirement_prepare_started(self, *, user: WorkbenchUser, session: WorkbenchSession) -> None:
        self.store.try_append_workbench_note(
            user=user,
            session_id=session.session_id,
            idempotency_key=f"workbench-running-note:{session.session_id}:triage-prepare-started",
            text="正在拆解岗位需求，准备生成可确认的检索标准。",
            status_hint="waiting",
            note_kind="waiting",
        )
        now = _iso(_now())
        for event_name, message in (
            ("runtime_run_started", "Starting SeekTalent requirement extraction."),
            ("runtime_requirements_started", "正在分析岗位标题、JD 和 notes。"),
        ):
            self.store.append_workbench_event(
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=session.workspace_id,
                user_id=session.owner_user_id,
                session_id=session.session_id,
                source_run_id=None,
                source_kind=None,
                event_name=event_name,
                schema_version="runtime_progress_v1",
                idempotency_key=f"{session.session_id}:triage-prepare:{event_name}",
                occurred_at=now,
                payload={"message": message, "roundNo": None, "stage": "requirements"},
            )

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

    def _start_runtime_workers(self, *, worker_count: int) -> None:
        self._runtime_threads = [thread for thread in self._runtime_threads if thread.is_alive()]
        while len(self._runtime_threads) < worker_count:
            worker_number = len(self._runtime_threads) + 1
            thread = threading.Thread(
                target=self._run_runtime_until_idle,
                name=f"seektalent-workbench-runtime-job-runner-{worker_number}",
                daemon=True,
            )
            self._runtime_threads.append(thread)
            thread.start()

    def _run_runtime_until_idle(self) -> None:
        while True:
            context = self.store.claim_next_runtime_sourcing_job(
                owner_id=self.owner_id,
                lease_expires_at=self._lease_expires_at(),
            )
            if context is None:
                return
            self._execute_runtime(context)

    def _execute_runtime(self, context: WorkbenchRuntimeSourcingJobContext) -> None:
        stop_heartbeat = threading.Event()
        heartbeat_thread = self._start_runtime_lease_heartbeat(context=context, stop_event=stop_heartbeat)
        try:
            self._tick_note_writer_for_session(
                user=self._user_for_session(context.session),
                session_id=context.session.session_id,
            )
            self.store.append_workbench_event(
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=None,
                source_kind=None,
                event_name="requirement_triage_used",
                payload={
                    "runtimeJobId": context.job.job_id,
                    "sourceKinds": list(context.job.source_kinds),
                    "mustHaveCount": len(context.triage.must_haves),
                    "niceToHaveCount": len(context.triage.nice_to_haves),
                    "generatedQueryHintCount": len(context.triage.generated_query_hints),
                },
            )
            run_runtime_sourcing_job(
                context=context,
                store=self.store,
                settings=self.settings,
                runtime_factory=self.runtime_factory,
                progress_callback=lambda event: self._record_runtime_sourcing_progress(context, event),
            )
        except Exception as exc:  # noqa: BLE001
            self.store.fail_runtime_sourcing_job(context=context, error_message=str(exc) or "Runtime sourcing failed.")
            self._tick_note_writer_for_session(
                user=self._user_for_session(context.session),
                session_id=context.session.session_id,
            )
            return
        finally:
            self._tick_note_writer_for_session(
                user=self._user_for_session(context.session),
                session_id=context.session.session_id,
            )
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1)

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
            self._tick_note_writer(context)
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
                    runtime_factory=self.runtime_factory,
                    worker_client=self.liepin_worker_client,
                )
            else:
                raise RuntimeError("Unsupported source run kind.")
        except Exception as exc:  # noqa: BLE001
            self.store.mark_source_run_failed(job=context.job, error_message=str(exc) or "Source run failed.")
            self._tick_note_writer(context)
            return
        finally:
            self._tick_note_writer(context)
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
            schema_version="runtime_progress_v1",
            idempotency_key=f"{context.job.source_run_id}:{event.type}:{event.round_no}:{event.timestamp}",
            occurred_at=event.timestamp,
            payload={
                "type": event.type,
                "message": event.message,
                "roundNo": event.round_no,
                "timestamp": event.timestamp,
                "payload": event.payload,
            },
        )
        self._tick_note_writer(context)

    def _record_runtime_sourcing_progress(self, context: WorkbenchRuntimeSourcingJobContext, event: ProgressEvent) -> None:
        self.store.append_workbench_event(
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
            session_id=context.session.session_id,
            source_run_id=None,
            source_kind=None,
            event_name=f"runtime_{_safe_event_suffix(event.type)}",
            schema_version="runtime_progress_v1",
            idempotency_key=f"{context.job.job_id}:{event.type}:{event.round_no}:{event.timestamp}",
            occurred_at=event.timestamp,
            payload={
                "type": event.type,
                "message": event.message,
                "roundNo": event.round_no,
                "timestamp": event.timestamp,
                "payload": event.payload,
            },
        )
        self._tick_note_writer_for_session(
            user=self._user_for_session(context.session),
            session_id=context.session.session_id,
        )

    def _record_requirement_progress(self, *, user: WorkbenchUser, session: WorkbenchSession, event: ProgressEvent) -> None:
        self.store.append_workbench_event(
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=session.workspace_id,
            user_id=session.owner_user_id,
            session_id=session.session_id,
            source_run_id=None,
            source_kind=None,
            event_name=f"runtime_{_safe_event_suffix(event.type)}",
            schema_version="runtime_progress_v1",
            idempotency_key=f"{session.session_id}:triage-prepare:{event.type}:{event.round_no}:{event.timestamp}",
            occurred_at=event.timestamp,
            payload={
                "message": event.message,
                "roundNo": event.round_no,
                **event.payload,
            },
        )
        self._tick_note_writer_for_session(user=user, session_id=session.session_id)

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
            args=(context, stop_event),
            name=f"seektalent-workbench-job-heartbeat-{context.job.job_id}",
            daemon=True,
        )
        thread.start()
        return thread

    def _start_runtime_lease_heartbeat(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        stop_event: threading.Event,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self._runtime_lease_heartbeat_loop,
            args=(context, stop_event),
            name=f"seektalent-workbench-runtime-job-heartbeat-{context.job.job_id}",
            daemon=True,
        )
        thread.start()
        return thread

    def _lease_heartbeat_loop(self, context: WorkbenchSourceRunJobContext, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.heartbeat_interval_seconds):
            renewed = self.store.extend_source_run_job_lease(
                job_id=context.job.job_id,
                owner_id=self.owner_id,
                lease_expires_at=self._lease_expires_at(),
            )
            if not renewed:
                return
            self._tick_note_writer(context)

    def _runtime_lease_heartbeat_loop(
        self,
        context: WorkbenchRuntimeSourcingJobContext,
        stop_event: threading.Event,
    ) -> None:
        user = self._user_for_session(context.session)
        while not stop_event.wait(self.heartbeat_interval_seconds):
            renewed = self.store.extend_runtime_sourcing_job_lease(
                job_id=context.job.job_id,
                owner_id=self.owner_id,
                lease_expires_at=self._lease_expires_at(),
            )
            if not renewed:
                return
            self._tick_note_writer_for_session(user=user, session_id=context.session.session_id)

    def _tick_note_writer(self, context: WorkbenchSourceRunJobContext) -> None:
        user = self._user_for_session(context.session)
        self._tick_note_writer_for_session(user=user, session_id=context.session.session_id)

    def _user_for_session(self, session: WorkbenchSession) -> WorkbenchUser:
        return WorkbenchUser(
            user_id=session.owner_user_id,
            email="",
            display_name="",
            role="member",
            workspace_id=session.workspace_id,
        )

    def _start_note_writer_heartbeat(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        stop_event: threading.Event,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self._note_writer_heartbeat_loop,
            kwargs={"user": user, "session_id": session_id, "stop_event": stop_event},
            name=f"seektalent-workbench-note-heartbeat-{session_id}",
            daemon=True,
        )
        thread.start()
        return thread

    def _note_writer_heartbeat_loop(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.wait(self.note_writer_heartbeat_interval_seconds):
            self._tick_note_writer_for_session(user=user, session_id=session_id)

    def _tick_note_writer_for_session(self, *, user: WorkbenchUser, session_id: str) -> None:
        try:
            self.note_writer.tick_session(user=user, session_id=session_id)
        except Exception:  # noqa: BLE001
            return


def _safe_event_suffix(value: str) -> str:
    suffix = "".join(character if character.isalnum() else "_" for character in value.strip().lower())
    suffix = "_".join(part for part in suffix.split("_") if part)
    return suffix or "progress"


def _triage_has_input(triage: WorkbenchRequirementTriage) -> bool:
    return any(
        value.strip()
        for values in (
            triage.must_haves,
            triage.nice_to_haves,
            triage.synonyms,
            triage.seniority_filters,
            triage.exclusions,
            triage.generated_query_hints,
        )
        for value in values
    )
