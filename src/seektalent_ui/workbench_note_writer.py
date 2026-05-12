from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import cast

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, resolve_stage_model_config
from seektalent.prompting import PromptRegistry, json_block
from seektalent_ui.workbench_store import (
    WorkbenchEvent,
    WorkbenchSourceRun,
    WorkbenchStore,
    WorkbenchUser,
    _iso,
    _now,
)


NOTE_WRITER_LEASE_SECONDS = 30
NOTE_WRITER_TICK_SECONDS = 10
TERMINAL_SOURCE_RUN_STATUSES = {"completed", "failed"}
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
    if _is_terminal_session(session.source_runs):
        return None

    previous_notes = [
        str(event.payload.get("text", "")).strip()
        for event in store.list_recent_workbench_notes(user=user, session_id=session_id, limit=15)
        if str(event.payload.get("text", "")).strip()
    ]
    source_runs = [_safe_source_run(run) for run in session.source_runs]
    candidate_items = store.list_candidate_review_items(user=user, session_id=session_id) or []
    safe_numbers = _safe_numbers_from_source_runs(session.source_runs)
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
    lowered = note.lower()
    if any(term in lowered for term in TECHNICAL_TERMS):
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
    return note


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
        if _latest_note_matches_context(
            self.store.list_recent_workbench_notes(user=user, session_id=session_id, limit=1),
            context_hash=context_hash,
            status_hint=str(context["statusHint"]),
        ):
            return None
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
            output = self._run_agent(context)
            note_text = validate_workbench_note_text(output, context)
            return self.store.try_append_workbench_note(
                user=user,
                session_id=session_id,
                idempotency_key=idempotency_key,
                text=note_text,
                status_hint=str(context["statusHint"]),
                note_kind="progress",
            )
        except Exception:  # noqa: BLE001
            return None
        finally:
            self.store.release_workbench_note_writer_lease(
                user=user,
                session_id=session_id,
                lease_owner=self.lease_owner,
            )

    def _run_agent(self, context: Mapping[str, object]) -> str:
        result = asyncio.run(self._build_agent().run(_render_note_prompt(context)))
        return str(result.output)

    def _build_agent(self) -> Agent[None, str]:
        prompt = PromptRegistry(self.settings.prompt_dir).load("workbench_note_writer")
        config = resolve_stage_model_config(self.settings, stage="workbench_note_writer")
        model = build_model(config)
        return cast(
            Agent[None, str],
            Agent(
                model=model,
                output_type=str,
                system_prompt=prompt.content,
                model_settings=build_model_settings(config),
                retries=0,
                output_retries=0,
            ),
        )


def _render_note_prompt(context: Mapping[str, object]) -> str:
    return "\n\n".join(
        [
            "请基于以下安全上下文写一条工作台运行笔记。",
            json_block("SAFE WORKBENCH CONTEXT", context),
        ]
    )


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
) -> list[str]:
    facts = [
        f"must_have_count={must_have_count}",
        f"nice_to_have_count={nice_to_have_count}",
        f"generated_query_hint_count={generated_query_hint_count}",
        f"candidate_review_item_count={candidate_count}",
    ]
    for run in source_runs:
        facts.append(f"{run.source_kind}_status={run.status}")
        facts.append(f"{run.source_kind}_cards_scanned_count={run.cards_scanned_count}")
        facts.append(f"{run.source_kind}_unique_candidates_count={run.unique_candidates_count}")
    return facts


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


def _is_terminal_session(source_runs: list[WorkbenchSourceRun]) -> bool:
    return bool(source_runs) and all(run.status in TERMINAL_SOURCE_RUN_STATUSES for run in source_runs)


def _context_hash(context: Mapping[str, object]) -> str:
    stable_context = dict(context)
    stable_context.pop("previousNotes", None)
    payload = json.dumps(stable_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _latest_note_matches_context(
    recent_notes: list[WorkbenchEvent],
    *,
    context_hash: str,
    status_hint: str,
) -> bool:
    if not recent_notes:
        return False
    latest = recent_notes[0]
    if not latest.idempotency_key or not latest.idempotency_key.endswith(f":{context_hash}"):
        return False
    return str(latest.payload.get("statusHint", "")) == status_hint


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
