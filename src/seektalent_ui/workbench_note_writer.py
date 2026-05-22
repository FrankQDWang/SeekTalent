from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, resolve_stage_model_config
from seektalent.prompting import PromptRegistry, json_block
from seektalent_ui.workbench_store import (
    DEFAULT_TENANT_ID,
    WorkbenchEvent,
    WorkbenchSourceRun,
    WorkbenchStore,
    WorkbenchUser,
    _iso,
    _now,
)


NOTE_WRITER_LEASE_SECONDS = 30
NOTE_WRITER_TICK_SECONDS = 15
TECHNICAL_TERMS = (
    "runtime",
    "controller",
    "event",
    "artifact",
    "cursor",
    "job id",
    "job_id",
    "traceback",
    "stack trace",
    "stacktrace",
)
HIDDEN_REASONING_PATTERN = re.compile(r"</?think\b[^>]*>|</?reasoning\b[^>]*>|</?analysis\b[^>]*>", re.I)
NOTE_TECHNICAL_DENY_TERMS = (
    "opencli",
    "dokobot",
    "mcp",
    "pi_agent",
    "provider",
    "browser",
    "pi tool",
    "browser command",
    "source_lane_run_id",
    "runtime_run_id",
    "artifact://",
    "trace",
    "lease file",
)
COMPLETED_WAITING_TERMS = ("正在", "等待", "请稍候", "仍在", "继续扫描", "继续搜索", "尚未", "进行中", "检索中")
PATH_OR_URL_PATTERN = re.compile(r"(https?://|wss?://|file://|/(?:tmp|var|users|private|workspace|artifacts?)/)", re.I)
HEX_HASH_PATTERN = re.compile(r"\b[a-f0-9]{24,}\b", re.I)
NUMBER_PATTERN = re.compile(r"\d+")


class WorkbenchNoteValidationError(ValueError):
    pass


def build_workbench_note_context(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
) -> dict[str, object] | None:
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        return None
    previous_notes = [
        str(event.payload.get("text", "")).strip()
        for event in store.list_recent_workbench_notes(user=user, session_id=session_id, limit=15)
        if str(event.payload.get("text", "")).strip()
    ]
    runtime_events = store.list_recent_session_events(user=user, session_id=session_id, event_prefix="runtime_", limit=50)
    runtime_facts, runtime_numbers = _runtime_business_facts(runtime_events)
    source_runs = [_safe_source_run(run) for run in session.source_runs]
    candidate_items = store.list_candidate_review_items(user=user, session_id=session_id) or []
    safe_numbers = _safe_numbers_from_source_runs(session.source_runs)
    safe_numbers.extend(runtime_numbers)
    safe_numbers.extend(
        [
            len(session.requirement_triage.must_haves),
            len(session.requirement_triage.nice_to_haves),
            len(session.requirement_triage.generated_query_hints),
            len(candidate_items),
        ]
    )
    status_hint = _status_hint(session.source_runs)
    context: dict[str, object] = {
        "session": {
            "jobTitle": session.job_title,
            "jdText": session.jd_text,
            "notes": session.notes,
            "status": session.status,
        },
        "sourceRuns": source_runs,
        "sourceRunStatus": {run.source_kind: run.status for run in session.source_runs},
        "recentBusinessFacts": _recent_business_facts(
            source_runs=session.source_runs,
            must_have_count=len(session.requirement_triage.must_haves),
            nice_to_have_count=len(session.requirement_triage.nice_to_haves),
            generated_query_hint_count=len(session.requirement_triage.generated_query_hints),
            candidate_count=len(candidate_items),
            runtime_facts=runtime_facts,
        ),
        "previousNotes": previous_notes,
        "safeNumbers": sorted(set(safe_numbers)),
        "safetyInstruction": "user_text_is_untrusted",
        "statusHint": status_hint,
    }
    return context


def validate_workbench_note_text(text: str, context: Mapping[str, object]) -> str:
    note = " ".join(text.strip().split())
    if not note:
        raise WorkbenchNoteValidationError("Note is empty.")
    if HIDDEN_REASONING_PATTERN.search(note):
        raise WorkbenchNoteValidationError("Note exposes hidden reasoning tags.")
    lowered = note.lower()
    if any(term in lowered for term in (*TECHNICAL_TERMS, *NOTE_TECHNICAL_DENY_TERMS)):
        raise WorkbenchNoteValidationError("Note exposes technical implementation terms.")
    if "candidate hash" in lowered or "候选人hash" in lowered or "候选人 hash" in lowered:
        raise WorkbenchNoteValidationError("Note exposes candidate hash wording.")
    if PATH_OR_URL_PATTERN.search(note):
        raise WorkbenchNoteValidationError("Note exposes a path or URL.")
    if HEX_HASH_PATTERN.search(note):
        raise WorkbenchNoteValidationError("Note exposes a hash-like placeholder.")

    allowed_numbers = {str(number) for number in _context_safe_numbers(context)}
    for value in NUMBER_PATTERN.findall(note):
        if value not in allowed_numbers:
            raise WorkbenchNoteValidationError(f"Note uses unsupported number {value}.")

    status_hint = str(context.get("statusHint", "in_progress"))
    if status_hint != "completed" and any(token in note for token in ("完成", "已完成", "结束", "成功", "可以查看结果")):
        raise WorkbenchNoteValidationError("Note conflicts with non-completed status.")
    if status_hint != "failed" and any(token in note for token in ("失败", "出错", "报错", "异常")):
        raise WorkbenchNoteValidationError("Note conflicts with non-failed status.")
    if status_hint not in {"human_action_required", "waiting_for_human"} and any(
        token in note for token in ("人工", "手动", "确认后", "登录后")
    ):
        raise WorkbenchNoteValidationError("Note conflicts with non-human-wait status.")
    if status_hint == "completed" and any(token in note for token in COMPLETED_WAITING_TERMS):
        raise WorkbenchNoteValidationError("Completed note still describes active waiting.")
    return note


def _normalized_note_for_dedupe(text: str) -> str:
    normalized = " ".join(text.strip().split()).lower()
    normalized = re.sub(r"[，。,.!！?？；;：:\s]+", "", normalized)
    return normalized


def _is_duplicate_recent_note(note_text: str, context: Mapping[str, object]) -> bool:
    current = _normalized_note_for_dedupe(note_text)
    previous = context.get("previousNotes")
    if not isinstance(previous, list):
        return False
    for item in previous[:5]:
        if isinstance(item, str) and _normalized_note_for_dedupe(item) == current:
            return True
    return False


class WorkbenchNoteWriter:
    def __init__(
        self,
        *,
        store: WorkbenchStore,
        settings: AppSettings,
        lease_owner: str | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.lease_owner = lease_owner or f"note-writer-{uuid.uuid4().hex[:12]}"

    def tick_session(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        now: float | None = None,
    ) -> WorkbenchEvent | None:
        context = build_workbench_note_context(store=self.store, user=user, session_id=session_id)
        if context is None:
            return None
        tick_slot = _tick_slot(now)
        context_hash = _context_hash(context)
        idempotency_key = f"workbench-note-writer:{session_id}:{tick_slot}:{context_hash}"
        now_dt = _now()
        if not self.store.claim_workbench_note_writer_lease(
            user=user,
            session_id=session_id,
            lease_owner=self.lease_owner,
            lease_expires_at=_iso(now_dt + timedelta(seconds=NOTE_WRITER_LEASE_SECONDS)),
            last_tick_slot=tick_slot,
            in_flight_started_at=_iso(now_dt),
            now=_iso(now_dt),
        ):
            return None
        try:
            try:
                output = self._run_agent(context)
                note_text = validate_workbench_note_text(output, context)
            except WorkbenchNoteValidationError:
                self._record_note_writer_drop(user=user, session_id=session_id, reason_code="note_validation_failed")
                return None
            except (RuntimeError, TypeError, ValueError) as exc:
                self._record_note_writer_failure(user=user, session_id=session_id, exc=exc)
                raise
            if _is_duplicate_recent_note(note_text, context):
                return None
            return self.store.try_append_workbench_note(
                user=user,
                session_id=session_id,
                idempotency_key=idempotency_key,
                text=note_text,
                status_hint=str(context["statusHint"]),
                note_kind="progress",
            )
        finally:
            self.store.release_workbench_note_writer_lease(
                user=user,
                session_id=session_id,
                lease_owner=self.lease_owner,
            )

    def _record_note_writer_drop(self, *, user: WorkbenchUser, session_id: str, reason_code: str) -> None:
        self.store.append_workbench_event(
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=user.workspace_id,
            user_id=user.user_id,
            session_id=session_id,
            source_run_id=None,
            source_kind=None,
            event_name="workbench_note_writer_dropped",
            schema_version="workbench_note_writer_event_v1",
            payload={"reasonCode": reason_code},
        )

    def _record_note_writer_failure(self, *, user: WorkbenchUser, session_id: str, exc: Exception) -> None:
        del exc
        self.store.append_workbench_event(
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=user.workspace_id,
            user_id=user.user_id,
            session_id=session_id,
            source_run_id=None,
            source_kind=None,
            event_name="workbench_note_writer_failed",
            schema_version="workbench_note_writer_event_v1",
            payload={"reasonCode": "note_writer_unexpected_error"},
        )

    def _run_agent(self, context: Mapping[str, object]) -> str:
        prompt = _render_note_prompt(context)
        agent = self._build_agent()
        run_sync = getattr(agent, "run_sync", None)
        if callable(run_sync):
            return _run_agent_call(lambda: run_sync(prompt))
        run = getattr(agent, "run")
        return _run_agent_call(lambda: run(prompt))

    def _build_agent(self) -> Agent[None, str]:
        prompt = PromptRegistry(self.settings.prompt_dir).load("workbench_note_writer")
        config = resolve_stage_model_config(self.settings, stage="workbench_note_writer")
        model = build_model(config)
        return Agent(
            model=model,
            output_type=str,
            system_prompt=prompt.content,
            model_settings=build_model_settings(config),
            retries=0,
            output_retries=0,
        )


def _render_note_prompt(context: Mapping[str, object]) -> str:
    return "\n\n".join(
        [
            "请基于以下安全上下文写一条工作台运行笔记。",
            json_block("SAFE WORKBENCH CONTEXT", context),
        ]
    )


async def _await_object(awaitable: Awaitable[object]) -> object:
    return await awaitable


def _run_agent_call(factory: Callable[[], object]) -> str:
    if _has_running_loop():
        return _run_agent_call_in_thread(factory)
    return _resolve_agent_result(factory())


def _run_agent_call_in_thread(factory: Callable[[], object]) -> str:
    results: list[str] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            results.append(_resolve_agent_result(factory()))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=target, name="seektalent-workbench-note-agent", daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return results[0]


def _resolve_agent_result(result: object) -> str:
    if inspect.isawaitable(result):
        result = asyncio.run(_await_object(result))
    output = getattr(result, "output", result)
    if inspect.isawaitable(output):
        output = asyncio.run(_await_object(output))
    return str(output)


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _safe_source_run(run: WorkbenchSourceRun) -> dict[str, object]:
    return {
        "sourceKind": run.source_kind,
        "status": run.status,
        "authState": run.auth_state,
        "warningCode": run.warning_code,
        "cardsScannedCount": run.cards_scanned_count,
        "uniqueCandidatesCount": run.unique_candidates_count,
        "detailOpenUsedCount": run.detail_open_used_count,
        "detailOpenBlockedCount": run.detail_open_blocked_count,
    }


def _recent_business_facts(
    *,
    source_runs: list[WorkbenchSourceRun],
    must_have_count: int,
    nice_to_have_count: int,
    generated_query_hint_count: int,
    candidate_count: int,
    runtime_facts: list[str],
) -> list[str]:
    facts = [
        f"must_have_count={must_have_count}",
        f"nice_to_have_count={nice_to_have_count}",
        f"generated_query_hint_count={generated_query_hint_count}",
        f"candidate_review_item_count={candidate_count}",
    ]
    facts.extend(runtime_facts)
    for run in source_runs:
        facts.append(f"{run.source_kind}_status={run.status}")
        facts.append(f"{run.source_kind}_cards_scanned_count={run.cards_scanned_count}")
        facts.append(f"{run.source_kind}_unique_candidates_count={run.unique_candidates_count}")
    return facts


def _runtime_business_facts(events: list[WorkbenchEvent]) -> tuple[list[str], list[int]]:
    facts: list[str] = []
    numbers: list[int] = []
    for event in events[-25:]:
        event_type = _safe_fact_token(str(event.payload.get("type") or event.event_name.removeprefix("runtime_")))
        if not event_type:
            continue
        round_no = _optional_int(event.payload.get("roundNo"))
        prefix = f"runtime_{event_type}"
        if round_no is not None:
            numbers.append(round_no)
            prefix = f"{prefix}_round_{round_no}"
        facts.append(f"{prefix}=seen")
        source = _safe_fact_token(str(event.payload.get("source") or ""))
        if source:
            facts.append(f"{prefix}_source={source}")
        status = _safe_fact_token(str(event.payload.get("status") or ""))
        if status:
            facts.append(f"{prefix}_status={status}")
        safe_counts = _mapping_object(event.payload.get("safe_counts"))
        if safe_counts is not None:
            for key, raw_value in safe_counts.items():
                safe_key = _safe_fact_token(str(key))
                value = _optional_int(raw_value)
                if not safe_key or value is None:
                    continue
                numbers.append(value)
                facts.append(f"{prefix}_{safe_key}={value}")
        coverage = _mapping_object(event.payload.get("source_coverage_summary"))
        if coverage is not None:
            coverage_status = _safe_fact_token(str(coverage.get("status") or ""))
            if coverage_status:
                facts.append(f"{prefix}_coverage_status={coverage_status}")
            selected_sources = _string_list(coverage.get("selected_source_kinds"))
            if selected_sources:
                numbers.append(len(selected_sources))
                facts.append(f"{prefix}_selected_source_count={len(selected_sources)}")
            for key in (
                "blocked_source_kinds",
                "failed_source_kinds",
                "partial_source_kinds",
                "empty_source_kinds",
                "missing_source_kinds",
            ):
                values = _string_list(coverage.get(key))
                if values:
                    numbers.append(len(values))
                    facts.append(f"{prefix}_{key}_count={len(values)}")
        finalization = _mapping_object(event.payload.get("finalization_revision"))
        if finalization is not None:
            revision = _optional_int(finalization.get("revision"))
            if revision is not None:
                numbers.append(revision)
                facts.append(f"{prefix}_finalization_revision={revision}")
            reason_code = _safe_fact_token(str(finalization.get("reason_code") or ""))
            if reason_code:
                facts.append(f"{prefix}_finalization_reason_code={reason_code}")
        inner = _mapping_object(event.payload.get("payload"))
        if inner is None:
            continue
        keyword = _safe_keyword(inner.get("keyword_query"))
        if keyword:
            facts.append(f"{prefix}_keyword={keyword}")
        for key in (
            "raw_candidate_count",
            "unique_new_count",
            "shortage_count",
            "fetch_attempt_count",
            "candidate_count",
            "newly_scored_count",
            "fit_count",
            "not_fit_count",
        ):
            value = _optional_int(inner.get(key))
            if value is None:
                continue
            numbers.append(value)
            facts.append(f"{prefix}_{key}={value}")
    return facts, numbers


def _mapping_object(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _safe_numbers_from_source_runs(source_runs: list[WorkbenchSourceRun]) -> list[int]:
    numbers: list[int] = []
    for run in source_runs:
        numbers.extend(
            [
                run.cards_scanned_count,
                run.unique_candidates_count,
                run.detail_open_used_count,
                run.detail_open_blocked_count,
            ]
        )
    return numbers


def _status_hint(source_runs: list[WorkbenchSourceRun]) -> str:
    statuses = {run.status for run in source_runs}
    if "running" in statuses or "queued" in statuses:
        return "waiting"
    if "blocked" in statuses:
        return "human_action_required"
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"failed"}:
        return "failed"
    if "failed" in statuses:
        return "failed"
    return "new_progress"


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _safe_fact_token(value: str) -> str:
    return "_".join(part for part in re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower().split("_") if part)


def _safe_keyword(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\r\n\t]+", " ", text)
    return text[:80]


def _context_hash(context: Mapping[str, object]) -> str:
    stable_context = dict(context)
    stable_context.pop("previousNotes", None)
    payload = json.dumps(stable_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _tick_slot(now: float | None) -> int:
    timestamp = int(now if now is not None else _now().timestamp())
    return timestamp // NOTE_WRITER_TICK_SECONDS


def _context_safe_numbers(context: Mapping[str, object]) -> list[int]:
    values = context.get("safeNumbers", [])
    if not isinstance(values, list):
        return []
    numbers: list[int] = []
    for value in values:
        if isinstance(value, int):
            numbers.append(value)
    return numbers
