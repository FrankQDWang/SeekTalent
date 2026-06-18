from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent.models import QueryTermCandidate, RequirementExtractionDraft, RequirementSheet
from seektalent.requirements.normalization import normalize_requirement_draft
from seektalent_conversation_agent.factory import build_agent_service
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_conversation_agent.store import ConversationStore
from seektalent_conversation_agent.tools import AgentToolAdapter
from seektalent_runtime_control.service import RuntimeControlService
from seektalent_runtime_control.store import RuntimeControlStore
from tests.conversation_agent_test_support import build_service as build_full_service
from tests.settings_factory import make_settings


WTS_TABLES = {
    "wts_job_request_revisions",
    "wts_requirement_draft_job_requests",
    "wts_workflow_start_intents",
    "wts_outbox",
    "wts_requirement_transcript_snapshots",
}


class FakeRequirementExecutor:
    def __init__(self) -> None:
        self.received_job_titles: list[str | None] = []

    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
    ) -> RequirementSheet:
        del notes
        self.received_job_titles.append(job_title)
        extracted_title = job_title.strip() if isinstance(job_title, str) and job_title.strip() else _derive_title(jd_text)
        return _requirement_sheet(job_title=extracted_title)

    def normalize_requirement_text(
        self,
        *,
        text: str,
        target_section_hint: str | None,
        current_draft: object,
    ) -> dict[str, object]:
        del current_draft
        if "需要确认" in text:
            return {
                "reviewItems": [
                    {
                        "reviewItemId": "review_item_1",
                        "rawText": text,
                        "candidateText": "有平台治理经验",
                        "candidateSection": target_section_hint or "must_have_capabilities",
                        "reasonCode": "requirement_amendment_ambiguous",
                    }
                ]
            }
        return {
            "additions": [
                {
                    "sectionId": target_section_hint or "must_have_capabilities",
                    "text": text,
                    "source": "runtime_normalized",
                }
            ]
        }


class FactoryRuntimeDerivingTitle:
    received_job_titles: list[str | None] = []

    def __init__(self, settings: object) -> None:
        del settings

    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd: str,
        notes: str,
        progress_callback=None,
        requirement_cache_scope: str | None = None,
    ) -> object:
        del notes, progress_callback, requirement_cache_scope
        type(self).received_job_titles.append(job_title)
        title = job_title.strip() if isinstance(job_title, str) and job_title.strip() else _derive_title(jd)
        return _requirement_sheet(job_title=title)


def test_submit_jd_persists_canonical_job_request_revision(tmp_path: Path) -> None:
    service, extractor = _service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )

    result = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title=None,
        jd_text="需要 Python 平台负责人，负责 API 与平台工程。",
        notes="优先华东，接受远程协作",
        source_kinds=["cts", "liepin"],
        idempotency_key="submit-jd-1",
    )

    assert result.job_request_revision_id
    assert result.requirement_draft_revision_id
    assert extractor.received_job_titles == [None]
    revision = service.get_job_request_revision(result.job_request_revision_id)
    assert revision.jd_text == "需要 Python 平台负责人，负责 API 与平台工程。"
    assert revision.user_job_title is None
    assert revision.extracted_job_title == "Python 平台负责人"
    assert revision.effective_job_title == "Python 平台负责人"
    assert revision.notes == "优先华东，接受远程协作"
    assert revision.source_kinds == ["cts", "liepin"]
    assert revision.request_hash

    link = service.get_requirement_draft_job_request_link(result.requirement_draft_revision_id)
    assert link.draft_revision_id == result.requirement_draft_revision_id
    assert link.workspace_id == "workspace_1"
    assert link.conversation_id == conversation.conversation_id
    assert link.job_request_revision_id == result.job_request_revision_id


def test_submit_jd_idempotency_reuses_same_body_and_rejects_different_body(tmp_path: Path) -> None:
    service, extractor = _service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )
    request = {
        "conversation_id": conversation.conversation_id,
        "owner_user_id": "user_1",
        "workspace_id": "workspace_1",
        "job_title": None,
        "jd_text": "需要 Python 平台负责人，负责 API 与平台工程。",
        "notes": "优先华东，接受远程协作",
        "source_kinds": ["cts", "liepin"],
        "idempotency_key": "submit-jd-1",
    }

    first = service.submit_jd(**request)
    replay = service.submit_jd(**request)

    assert replay.job_request_revision_id == first.job_request_revision_id
    assert replay.requirement_draft_revision_id == first.requirement_draft_revision_id
    assert extractor.received_job_titles == [None]

    with pytest.raises(ConversationAgentError) as exc_info:
        service.submit_jd(
            **{
                **request,
                "jd_text": "需要 Go 平台负责人，负责 API 与平台工程。",
            }
        )

    assert exc_info.value.reason_code == "idempotency_key_conflict"


def test_submit_jd_replay_repairs_conversation_after_partial_crash(tmp_path: Path) -> None:
    service, extractor = _service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )
    request = {
        "conversation_id": conversation.conversation_id,
        "owner_user_id": "user_1",
        "workspace_id": "workspace_1",
        "job_title": None,
        "jd_text": "需要 Python 平台负责人，负责 API 与平台工程。",
        "notes": "优先华东，接受远程协作",
        "source_kinds": ["cts", "liepin"],
        "idempotency_key": "submit-jd-crash-replay-1",
    }
    first = service.submit_jd(**request)
    message_count_before_replay = len(service.reopen_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    ).messages)
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            """
            UPDATE agent_conversations
            SET status = 'draft',
                latest_draft_revision_id = NULL,
                pending_requirement_review_count = 0,
                updated_at = '2026-06-09T00:00:99.000000Z'
            WHERE conversation_id = ?
            """,
            (conversation.conversation_id,),
        )
    crashed = service.store.get_conversation(conversation.conversation_id)
    assert crashed.status == "draft"
    assert crashed.latest_draft_revision_id is None

    replay = service.submit_jd(**request)

    assert replay.job_request_revision_id == first.job_request_revision_id
    assert replay.requirement_draft_revision_id == first.requirement_draft_revision_id
    assert replay.conversation_reopen_state.status == "awaiting_requirement_confirmation"
    assert replay.conversation_reopen_state.latest_draft_revision_id == first.requirement_draft_revision_id
    assert replay.conversation_reopen_state.pending_requirement_review_count == 0
    assert len(replay.messages) == message_count_before_replay
    repaired = service.store.get_conversation(conversation.conversation_id)
    assert repaired.status == "awaiting_requirement_confirmation"
    assert repaired.latest_draft_revision_id == first.requirement_draft_revision_id
    assert extractor.received_job_titles == [None]


def test_submit_jd_replay_preserves_running_workflow_state(tmp_path: Path) -> None:
    service, _conversation_store, _runtime_store = build_full_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )
    request = {
        "conversation_id": conversation.conversation_id,
        "owner_user_id": "user_1",
        "workspace_id": "workspace_1",
        "job_title": None,
        "jd_text": "需要 Python 平台负责人，负责 API 与平台工程。",
        "notes": "优先华东，接受远程协作",
        "source_kinds": ["cts"],
        "idempotency_key": "submit-jd-running-replay-1",
    }
    submitted = service.submit_jd(**request)
    confirmed = service.confirm_requirements(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=submitted.requirement_draft_revision_id,
        base_revision_id=submitted.requirement_draft_revision_id,
        idempotency_key="confirm-running-replay-1",
    )
    started = service.start_workflow(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title="Python 平台负责人",
        jd_text="需要 Python 平台负责人，负责 API 与平台工程。",
        notes="优先华东，接受远程协作",
        source_ids=["cts"],
    )
    runtime_run_id = started.conversation_reopen_state.runtime_run_id
    linked_run_before = started.conversation_reopen_state.linked_runtime_runs[0]
    message_count_before_replay = len(started.messages)

    replay = service.submit_jd(**request)

    assert replay.job_request_revision_id == submitted.job_request_revision_id
    assert replay.requirement_draft_revision_id == submitted.requirement_draft_revision_id
    assert replay.conversation_reopen_state.status == "running"
    assert replay.conversation_reopen_state.runtime_run_id == runtime_run_id
    assert replay.conversation_reopen_state.approved_requirement_revision_id == (
        confirmed.conversation_reopen_state.approved_requirement_revision_id
    )
    assert replay.conversation_reopen_state.latest_draft_revision_id == submitted.requirement_draft_revision_id
    assert len(replay.conversation_reopen_state.linked_runtime_runs) == 1
    assert replay.conversation_reopen_state.linked_runtime_runs[0].model_dump(mode="json") == (
        linked_run_before.model_dump(mode="json")
    )
    assert len(replay.messages) == message_count_before_replay
    repaired = service.store.get_conversation(conversation.conversation_id)
    assert repaired.status == "running"
    assert repaired.runtime_run_id == runtime_run_id


def test_submit_jd_with_missing_title_uses_factory_runtime_control_extraction(tmp_path: Path) -> None:
    FactoryRuntimeDerivingTitle.received_job_titles = []
    service = build_agent_service(
        settings=make_settings(workspace_root=str(tmp_path)),
        runtime_factory=FactoryRuntimeDerivingTitle,
    )
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )

    result = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title=None,
        jd_text="需要 Python 平台负责人，负责 API 与平台工程。",
        notes="优先华东，接受远程协作",
        source_kinds=["cts"],
        idempotency_key="submit-jd-factory-1",
    )

    assert FactoryRuntimeDerivingTitle.received_job_titles == [None]
    revision = service.get_job_request_revision(result.job_request_revision_id)
    assert revision.extracted_job_title == "Python 平台负责人"
    assert revision.effective_job_title == "Python 平台负责人"


def test_requirement_normalization_derives_job_title_from_extracted_anchor_when_user_title_missing() -> None:
    draft = RequirementExtractionDraft(
        title_anchor_terms=["Python 平台负责人"],
        title_anchor_rationale="JD 明确岗位标题。",
        jd_query_terms=["平台工程", "API"],
        role_summary="负责 Python API 与平台工程。",
        must_have_capabilities=["Python API", "平台工程"],
        preferred_capabilities=["远程协作"],
        scoring_rationale="按确认后的平台负责人需求评分。",
    )

    sheet = normalize_requirement_draft(draft, job_title=None)

    assert sheet.job_title == "Python 平台负责人"


def test_requirement_draft_lineage_keeps_job_request_link_through_edits(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )
    submitted = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title=None,
        jd_text="需要 Python 平台负责人，负责 API 与平台工程。",
        notes="优先华东，接受远程协作",
        source_kinds=["cts"],
        idempotency_key="submit-jd-lineage-1",
    )
    original_job_request_id = submitted.job_request_revision_id
    initial_draft = submitted.requirement_draft
    first_item_id = initial_draft.sections[0].items[0].item_id

    updated = service.update_requirement_draft(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=initial_draft.draft_revision_id,
        base_revision_id=initial_draft.draft_revision_id,
        operations=[{"op": "edit_text", "item_id": first_item_id, "text": "Python API 与平台工程负责人"}],
        idempotency_key="lineage-update-1",
    )
    updated_link = service.get_requirement_draft_job_request_link(updated.requirement_draft.draft_revision_id)
    assert updated_link.job_request_revision_id == original_job_request_id

    amended = service.amend_requirement_draft_from_text(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=updated.requirement_draft.draft_revision_id,
        base_revision_id=updated.requirement_draft.draft_revision_id,
        text="需要补充平台治理经验",
        target_section_hint="preferred_capabilities",
        idempotency_key="lineage-amend-1",
    )
    amended_link = service.get_requirement_draft_job_request_link(amended.requirement_draft.draft_revision_id)
    assert amended_link.job_request_revision_id == original_job_request_id

    review = service.amend_requirement_draft_from_text(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=amended.requirement_draft.draft_revision_id,
        base_revision_id=amended.requirement_draft.draft_revision_id,
        text="需要确认：平台治理经验",
        target_section_hint="must_have_capabilities",
        idempotency_key="lineage-review-1",
    )
    review_link = service.get_requirement_draft_job_request_link(review.requirement_draft.draft_revision_id)
    assert review_link.job_request_revision_id == original_job_request_id

    resolved = service.resolve_requirement_review(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=review.requirement_draft.draft_revision_id,
        base_revision_id=review.requirement_draft.draft_revision_id,
        amendment_id=review.requirement_draft.amendment.amendment_id,
        operations=[{"op": "accept_candidate", "review_item_id": "review_item_1"}],
        idempotency_key="lineage-resolve-1",
    )
    resolved_link = service.get_requirement_draft_job_request_link(resolved.requirement_draft.draft_revision_id)
    assert resolved_link.job_request_revision_id == original_job_request_id


def test_confirm_requirements_reads_job_request_link_and_returns_typed_ids(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )
    submitted = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title=None,
        jd_text="需要 Python 平台负责人，负责 API 与平台工程。",
        notes="优先华东，接受远程协作",
        source_kinds=["cts"],
        idempotency_key="submit-jd-confirm-1",
    )

    confirmed = service.confirm_requirements(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=submitted.requirement_draft_revision_id,
        base_revision_id=submitted.requirement_draft_revision_id,
        idempotency_key="confirm-wts-1",
    )

    assert confirmed.job_request_revision_id == submitted.job_request_revision_id
    assert confirmed.requirement_draft_revision_id == submitted.requirement_draft_revision_id
    link = service.get_requirement_draft_job_request_link(confirmed.requirement_draft_revision_id)
    assert link.job_request_revision_id == submitted.job_request_revision_id


def test_fresh_conversation_store_initialize_creates_wts_tables_at_version_5(tmp_path: Path) -> None:
    db_path = tmp_path / "conversation_agent.sqlite3"

    ConversationStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = _tables(conn)

    assert version == 5
    assert WTS_TABLES <= tables
    _assert_wts_columns(conn)


def test_conversation_store_migrates_v4_database_to_wts_control_plane(tmp_path: Path) -> None:
    db_path = tmp_path / "conversation_agent_v4.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE agent_conversations (
                conversation_id TEXT PRIMARY KEY
            );
            PRAGMA user_version = 4;
            """
        )

    ConversationStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = _tables(conn)

    assert version == 5
    assert WTS_TABLES <= tables
    _assert_wts_columns(conn)


def _service(tmp_path: Path) -> tuple[ConversationAgentService, FakeRequirementExecutor]:
    conversation_store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    conversation_store.initialize()
    runtime_store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    runtime_store.initialize()
    executor = FakeRequirementExecutor()
    requirement_service = RuntimeControlService(
        store=runtime_store,
        executor=executor,
    )
    service = ConversationAgentService(
        store=conversation_store,
        tool_adapter=AgentToolAdapter(
            runtime_store=runtime_store,
            requirement_service=requirement_service,
        ),
        now=_clock(),
        conversation_id_factory=lambda: "agent_conv_1",
        message_id_factory=_sequence("agent_msg"),
        tool_call_id_factory=_sequence("agent_tool_call"),
    )
    return service, executor


def _requirement_sheet(*, job_title: str) -> RequirementSheet:
    return RequirementSheet(
        job_title=job_title,
        title_anchor_terms=[job_title],
        title_anchor_rationale="JD names the role.",
        role_summary="负责 Python API 与平台工程。",
        must_have_capabilities=["Python API", "平台工程"],
        preferred_capabilities=["远程协作"],
        exclusion_signals=[],
        hard_constraints={},
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Python 平台负责人",
                source="jd",
                category="role_anchor",
                priority=100,
                evidence="JD 明确岗位标题。",
                first_added_round=0,
            )
        ],
        scoring_rationale="按确认后的平台负责人需求评分。",
    )


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _assert_wts_columns(conn: sqlite3.Connection) -> None:
    columns = {
        table_name: {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
        for table_name in WTS_TABLES
    }
    assert {
        "workspace_id",
        "owner_user_id",
        "conversation_id",
        "jd_text",
        "user_job_title",
        "extracted_job_title",
        "notes",
        "source_kinds_json",
        "workspace_source_policy_id",
        "request_hash",
        "idempotency_key",
        "created_at",
    } <= columns["wts_job_request_revisions"]
    assert {
        "draft_revision_id",
        "workspace_id",
        "conversation_id",
        "job_request_revision_id",
        "created_at",
    } <= columns["wts_requirement_draft_job_requests"]
    assert {
        "workspace_id",
        "owner_user_id",
        "conversation_id",
        "draft_revision_id",
        "approved_requirement_revision_id",
        "job_request_revision_id",
        "idempotency_key",
        "request_hash",
        "deterministic_run_key",
        "status",
        "runtime_run_id",
        "reason_code",
        "created_at",
        "updated_at",
    } <= columns["wts_workflow_start_intents"]
    assert {
        "outbox_id",
        "workspace_id",
        "event_type",
        "aggregate_id",
        "payload_json",
        "status",
        "attempt_count",
        "created_at",
        "updated_at",
    } <= columns["wts_outbox"]
    assert {
        "transcript_message_id",
        "workspace_id",
        "conversation_id",
        "draft_revision_id",
        "snapshot_json",
        "created_at",
    } <= columns["wts_requirement_transcript_snapshots"]


def _derive_title(jd_text: str) -> str:
    assert "Python 平台负责人" in jd_text
    return "Python 平台负责人"


def _clock():
    values = [0]

    def now() -> str:
        values[0] += 1
        return f"2026-06-09T00:00:{values[0]:02d}.000000Z"

    return now


def _sequence(prefix: str):
    values = [0]

    def next_id() -> str:
        values[0] += 1
        return f"{prefix}_{values[0]}"

    return next_id
