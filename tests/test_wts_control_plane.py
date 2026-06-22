from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent.models import QueryTermCandidate, RequirementExtractionDraft, RequirementSheet
from seektalent.requirements.normalization import normalize_requirement_draft
from seektalent_conversation_agent.factory import build_agent_service
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_conversation_agent.store import CONVERSATION_AGENT_SCHEMA_VERSION, ConversationStore
from seektalent_conversation_agent.tools import AgentToolAdapter
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.service import RuntimeControlService
from seektalent_runtime_control.store import RuntimeControlStore
from tests.conversation_agent_test_support import build_service as build_full_service
from tests.settings_factory import make_settings


WTS_TABLES = {
    "wts_job_request_revisions",
    "wts_requirement_draft_job_requests",
    "wts_confirm_requirement_requests",
    "wts_workflow_start_intents",
    "wts_outbox",
    "wts_requirement_transcript_snapshots",
}


class FakeRequirementExecutor:
    def __init__(self) -> None:
        self.received_job_titles: list[str | None] = []
        self.received_jd_texts: list[str] = []

    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
    ) -> RequirementSheet:
        del notes
        self.received_job_titles.append(job_title)
        self.received_jd_texts.append(jd_text)
        extracted_title = job_title.strip() if isinstance(job_title, str) and job_title.strip() else _derive_title(jd_text)
        if "平台治理" in jd_text:
            return _requirement_sheet(job_title=extracted_title).model_copy(
                update={
                    "must_have_capabilities": ["平台治理经验"],
                    "preferred_capabilities": [],
                    "initial_query_term_pool": [
                        QueryTermCandidate(
                            term="平台治理",
                            source="notes",
                            category="domain",
                            priority=90,
                            evidence="用户补充了平台治理经验要求。",
                            first_added_round=0,
                        )
                    ],
                    "scoring_rationale": "补充关注平台治理经验。",
                }
            )
        return _requirement_sheet(job_title=extracted_title)


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


class CapturingWorkflowExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue_workflow_run(self, **kwargs: object) -> RuntimeRunRecord:
        self.calls.append(kwargs)
        approved_requirement = kwargs["approved_requirement"]
        return RuntimeRunRecord(
            runtime_run_id="runtime_run_from_intent_1",
            run_intent_id=str(kwargs["run_intent_id"]),
            start_idempotency_key=str(kwargs["start_idempotency_key"]),
            run_kind="primary",
            agent_conversation_id=str(kwargs["conversation_id"]),
            workbench_session_id=None,
            approved_requirement_revision_id=approved_requirement.approved_requirement_revision_id,
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=list(kwargs["source_ids"]),
            stop_reason_code=None,
            created_at="2026-06-09T00:01:00.000000Z",
            updated_at="2026-06-09T00:01:00.000000Z",
            completed_at=None,
        )


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


def test_submit_jd_persists_safe_requirement_transcript_snapshot(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
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
        idempotency_key="submit-jd-safe-transcript-snapshot-1",
    )
    [assistant_message] = [
        message for message in result.messages if message.message_type == "requirement_review"
    ]

    assert assistant_message.payload["requirementDraft"] == {
        "draftRevisionId": result.requirement_draft_revision_id
    }
    snapshot = assistant_message.payload["requirementDraftSnapshot"]
    assert isinstance(snapshot, dict)
    serialized_snapshot = str(snapshot)
    assert snapshot["draftRevisionId"] == result.requirement_draft_revision_id
    assert snapshot["sections"][0]["items"][0]["text"] == "Python API"
    assert "value" not in serialized_snapshot
    assert "source_span_refs" not in serialized_snapshot
    assert "amendment" not in serialized_snapshot

    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute(
            """
            SELECT transcript_message_id, workspace_id, draft_revision_id, snapshot_json
            FROM wts_requirement_transcript_snapshots
            WHERE conversation_id = ?
            """,
            (conversation.conversation_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == assistant_message.message_id
    assert row[1] == "workspace_1"
    assert row[2] == result.requirement_draft_revision_id
    assert result.requirement_draft_revision_id in row[3]


def test_requirement_update_replay_does_not_create_snapshotless_transcript_message(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
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
        source_kinds=["cts", "liepin"],
        idempotency_key="submit-jd-update-replay-snapshot-1",
    )
    base_draft = submitted.requirement_draft
    assert base_draft is not None
    first_item = base_draft.sections[0].items[0]

    first = service.update_requirement_draft(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=base_draft.draft_revision_id,
        base_revision_id=base_draft.draft_revision_id,
        operations=[
            {
                "op": "set_selected",
                "item_id": first_item.item_id,
                "selected": False,
            }
        ],
        idempotency_key="update-replay-snapshot-1",
    )
    replay = service.update_requirement_draft(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=base_draft.draft_revision_id,
        base_revision_id=base_draft.draft_revision_id,
        operations=[
            {
                "op": "set_selected",
                "item_id": first_item.item_id,
                "selected": False,
            }
        ],
        idempotency_key="update-replay-snapshot-1",
    )

    assert replay.requirement_draft_revision_id == first.requirement_draft_revision_id
    messages = service.store.get_messages(conversation_id=conversation.conversation_id)
    requirement_messages = [message for message in messages if message.message_type == "requirement_review"]
    with sqlite3.connect(service.store.path) as conn:
        snapshot_rows = conn.execute(
            """
            SELECT transcript_message_id
            FROM wts_requirement_transcript_snapshots
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation.conversation_id,),
        ).fetchall()

    assert len(requirement_messages) == 2
    assert len(snapshot_rows) == 2
    assert {row[0] for row in snapshot_rows} == {message.message_id for message in requirement_messages}


def test_conversation_store_duplicate_message_idempotency_raises_by_default(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    store.initialize()
    conversation = store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
        created_at="2026-06-09T00:00:01.000000Z",
    )
    store.append_message(
        conversation_id=conversation.conversation_id,
        role="user",
        message_type="user_text",
        text="第一条消息",
        payload={},
        created_at="2026-06-09T00:00:02.000000Z",
        message_id="agent_msg_1",
        idempotency_key="turn-1",
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.append_message(
            conversation_id=conversation.conversation_id,
            role="user",
            message_type="user_text",
            text="重复消息",
            payload={},
            created_at="2026-06-09T00:00:03.000000Z",
            message_id="agent_msg_2",
            idempotency_key="turn-1",
        )


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


def test_submit_jd_rejects_same_body_with_new_idempotency_key(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
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
    }
    service.submit_jd(**request, idempotency_key="submit-jd-1")

    with pytest.raises(ConversationAgentError) as exc_info:
        service.submit_jd(**request, idempotency_key="submit-jd-2")

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


def test_submit_jd_replay_repairs_missing_link_and_review_message_without_duplicates(tmp_path: Path) -> None:
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
        "idempotency_key": "submit-jd-missing-link-1",
    }
    first = service.submit_jd(**request)
    user_message_count = _message_count(service, conversation.conversation_id, message_type="user_text")
    tool_call_count = _tool_call_count(service, conversation.conversation_id, tool_name="extract_requirements")
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            "DELETE FROM wts_requirement_draft_job_requests WHERE draft_revision_id = ?",
            (first.requirement_draft_revision_id,),
        )
        conn.execute(
            """
            DELETE FROM wts_requirement_transcript_snapshots
            WHERE draft_revision_id = ?
            """,
            (first.requirement_draft_revision_id,),
        )
        conn.execute(
            """
            DELETE FROM agent_transcript_messages
            WHERE conversation_id = ? AND message_type = 'requirement_review'
            """,
            (conversation.conversation_id,),
        )

    replay = service.submit_jd(**request)

    assert replay.job_request_revision_id == first.job_request_revision_id
    assert replay.requirement_draft_revision_id == first.requirement_draft_revision_id
    assert _message_count(service, conversation.conversation_id, message_type="user_text") == user_message_count
    assert _message_count(service, conversation.conversation_id, message_type="requirement_review") == 1
    assert _tool_call_count(service, conversation.conversation_id, tool_name="extract_requirements") == tool_call_count
    assert _requirement_snapshot_count(service, first.requirement_draft_revision_id) == 1
    assert extractor.received_job_titles == [None]


def test_submit_jd_replay_repairs_missing_review_message_when_link_exists(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
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
        "idempotency_key": "submit-jd-missing-review-1",
    }
    first = service.submit_jd(**request)
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            """
            DELETE FROM wts_requirement_transcript_snapshots
            WHERE draft_revision_id = ?
            """,
            (first.requirement_draft_revision_id,),
        )
        conn.execute(
            """
            DELETE FROM agent_transcript_messages
            WHERE conversation_id = ? AND message_type = 'requirement_review'
            """,
            (conversation.conversation_id,),
        )

    replay = service.submit_jd(**request)

    assert replay.requirement_draft_revision_id == first.requirement_draft_revision_id
    assert _message_count(service, conversation.conversation_id, message_type="requirement_review") == 1
    assert _requirement_snapshot_count(service, first.requirement_draft_revision_id) == 1


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

    second_amendment = service.amend_requirement_draft_from_text(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=amended.requirement_draft.draft_revision_id,
        base_revision_id=amended.requirement_draft.draft_revision_id,
        text="需要确认：平台治理经验",
        target_section_hint="must_have_capabilities",
        idempotency_key="lineage-review-1",
    )
    second_link = service.get_requirement_draft_job_request_link(second_amendment.requirement_draft.draft_revision_id)
    assert second_link.job_request_revision_id == original_job_request_id
    assert _extract_section_texts(second_amendment.requirement_draft, "must_have_capabilities").count("平台治理经验") == 1
    assert _extractor.received_jd_texts[-2:] == ["需要补充平台治理经验", "需要确认：平台治理经验"]


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


def test_confirm_creates_one_workflow_start_intent_for_same_draft(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-atomic-1")

    first = _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-1")
    second = _confirm(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-2",
    )

    assert first.workflow_start_intent_id == second.workflow_start_intent_id
    assert first.workflow_start_intent_id
    assert first.job_request_revision_id == submitted.job_request_revision_id
    assert first.requirement_draft_revision_id == submitted.requirement_draft_revision_id
    assert _workflow_start_intent_count_for_draft(service, submitted.requirement_draft_revision_id) == 1
    assert _outbox_count_for_aggregate(service, first.workflow_start_intent_id) == 1
    intent = _workflow_start_intent_row(service, first.workflow_start_intent_id)
    assert intent["approved_requirement_revision_id"]
    approved = service.tool_adapter.runtime_store.get_approved_requirement(intent["approved_requirement_revision_id"])
    assert approved.draft_revision_id == submitted.requirement_draft_revision_id
    assert intent["job_request_revision_id"] == submitted.job_request_revision_id
    assert intent["status"] == "pending"
    assert intent["deterministic_run_key"] == (
        f"wts:workspace_1:{conversation.conversation_id}:{submitted.requirement_draft_revision_id}"
    )


def test_confirm_idempotency_key_rejects_different_request_hash(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-conflict-1")
    _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-1")

    with pytest.raises(ConversationAgentError) as exc_info:
        service.confirm_requirements(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            draft_revision_id="different_draft",
            base_revision_id="different_draft",
            idempotency_key="confirm-1",
        )

    assert exc_info.value.reason_code == "idempotency_key_conflict"


def test_confirm_old_draft_with_fresh_key_after_newer_draft_is_stale(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-old-draft-1")
    original_draft_id = submitted.requirement_draft_revision_id
    original_item_id = submitted.requirement_draft.sections[0].items[0].item_id
    confirmed = _confirm(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-old-draft-1",
    )
    updated = service.update_requirement_draft(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=original_draft_id,
        base_revision_id=original_draft_id,
        operations=[{"op": "edit_text", "item_id": original_item_id, "text": "Python API 与平台工程负责人"}],
        idempotency_key="update-after-confirm-1",
    )

    replay = _confirm(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-old-draft-1",
    )
    assert replay.workflow_start_intent_id == confirmed.workflow_start_intent_id

    with pytest.raises(ConversationAgentError) as exc_info:
        service.confirm_requirements(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            draft_revision_id=original_draft_id,
            base_revision_id=original_draft_id,
            idempotency_key="confirm-old-draft-fresh-1",
        )

    assert exc_info.value.reason_code == "requirement_draft_stale"
    assert exc_info.value.payload["latestDraftRevisionId"] == updated.requirement_draft_revision_id
    assert _workflow_start_intent_count_for_draft(service, original_draft_id) == 1
    assert _outbox_count_for_aggregate(service, confirmed.workflow_start_intent_id) == 1
    assert _confirm_request_count(service) == 1


def test_confirm_recovers_intent_and_outbox_after_approved_requirement_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-crash-1")
    original_create = service.workflow_start_intent_store.create_or_get_confirmed_draft_intent

    def crash_after_runtime_confirm(**kwargs: object):
        del kwargs
        raise ConversationAgentError("simulated_crash_after_approval")

    service.workflow_start_intent_store.create_or_get_confirmed_draft_intent = crash_after_runtime_confirm
    with pytest.raises(ConversationAgentError) as crash:
        _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-crash-1")
    assert crash.value.reason_code == "simulated_crash_after_approval"
    service.workflow_start_intent_store.create_or_get_confirmed_draft_intent = original_create

    assert _approved_requirement_count_for_idempotency(
        service,
        conversation_id=conversation.conversation_id,
        idempotency_key="confirm-crash-1",
    ) == 1
    assert _workflow_start_intent_count_for_draft(service, submitted.requirement_draft_revision_id) == 0
    assert _outbox_count(service) == 0

    def fail_if_reconfirming(
        self: AgentToolAdapter,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        idempotency_key: str,
    ):
        del self, draft_revision_id, base_revision_id, idempotency_key
        raise AssertionError("retry should recover the approved requirement without re-confirming")

    monkeypatch.setattr(AgentToolAdapter, "confirm_requirements", fail_if_reconfirming)

    recovered = _confirm(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-crash-1",
    )

    assert recovered.workflow_start_intent_id
    assert _approved_requirement_count_for_idempotency(
        service,
        conversation_id=conversation.conversation_id,
        idempotency_key="confirm-crash-1",
    ) == 1
    assert _workflow_start_intent_count_for_draft(service, submitted.requirement_draft_revision_id) == 1
    assert _outbox_count_for_aggregate(service, recovered.workflow_start_intent_id) == 1
    intent = service.workflow_start_intent_store.get(recovered.workflow_start_intent_id)
    approved = service.tool_adapter.runtime_store.get_approved_requirement_by_idempotency(
        conversation_id=conversation.conversation_id,
        idempotency_key="confirm-crash-1",
    )
    assert approved is not None
    assert intent.approved_requirement_revision_id == approved.approved_requirement_revision_id


def test_confirm_recovery_same_key_after_newer_draft_creates_missing_intent_outbox(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-crash-newer-1")
    original_draft_id = submitted.requirement_draft_revision_id
    original_item_id = submitted.requirement_draft.sections[0].items[0].item_id

    _simulate_crash_after_runtime_approval(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-crash-newer-1",
    )
    updated = service.update_requirement_draft(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=original_draft_id,
        base_revision_id=original_draft_id,
        operations=[{"op": "edit_text", "item_id": original_item_id, "text": "Python API 与平台工程负责人"}],
        idempotency_key="update-after-crash-1",
    )

    with pytest.raises(ConversationAgentError) as stale:
        service.confirm_requirements(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            draft_revision_id=original_draft_id,
            base_revision_id=original_draft_id,
            idempotency_key="confirm-crash-newer-fresh-1",
        )
    assert stale.value.reason_code == "requirement_draft_stale"
    assert stale.value.payload["latestDraftRevisionId"] == updated.requirement_draft_revision_id
    assert _confirm_request_count(service) == 1
    assert _workflow_start_intent_count_for_draft(service, original_draft_id) == 0
    assert _outbox_count(service) == 0

    recovered = _confirm(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-crash-newer-1",
    )

    assert recovered.workflow_start_intent_id
    assert _confirm_request_count(service) == 1
    assert _approved_requirement_count_for_idempotency(
        service,
        conversation_id=conversation.conversation_id,
        idempotency_key="confirm-crash-newer-1",
    ) == 1
    assert _workflow_start_intent_count_for_draft(service, original_draft_id) == 1
    assert _outbox_count_for_aggregate(service, recovered.workflow_start_intent_id) == 1
    intent = service.workflow_start_intent_store.get(recovered.workflow_start_intent_id)
    approved = service.tool_adapter.runtime_store.get_approved_requirement_by_idempotency(
        conversation_id=conversation.conversation_id,
        idempotency_key="confirm-crash-newer-1",
    )
    assert approved is not None
    assert intent.approved_requirement_revision_id == approved.approved_requirement_revision_id


def test_confirm_idempotency_key_rejects_same_draft_changed_expected_revision(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-base-hash-1")
    _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-base-hash-1")

    with pytest.raises(ConversationAgentError) as exc_info:
        service.confirm_requirements(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            draft_revision_id=submitted.requirement_draft_revision_id,
            base_revision_id="stale-base-revision",
            idempotency_key="confirm-base-hash-1",
        )

    assert exc_info.value.reason_code == "idempotency_key_conflict"


def test_confirm_idempotency_key_rejects_same_draft_changed_source_selection(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-source-hash-1")
    _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-source-hash-1")
    _set_job_request_sources(service, submitted.job_request_revision_id, source_kinds_json='["liepin"]')

    with pytest.raises(ConversationAgentError) as exc_info:
        _confirm(
            service,
            conversation_id=conversation.conversation_id,
            submitted=submitted,
            idempotency_key="confirm-source-hash-1",
        )

    assert exc_info.value.reason_code == "idempotency_key_conflict"


def test_confirm_idempotency_key_rejects_same_draft_changed_job_request_body_identity(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-body-hash-1")
    _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-body-hash-1")
    _mutate_job_request_body_identity(service, submitted.job_request_revision_id)

    with pytest.raises(ConversationAgentError) as exc_info:
        _confirm(
            service,
            conversation_id=conversation.conversation_id,
            submitted=submitted,
            idempotency_key="confirm-body-hash-1",
        )

    assert exc_info.value.reason_code == "idempotency_key_conflict"


def test_confirm_recovery_rejects_changed_expected_revision_after_approved_requirement_crash(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-crash-base-1")

    _simulate_crash_after_runtime_approval(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-crash-base-1",
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        service.confirm_requirements(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            draft_revision_id=submitted.requirement_draft_revision_id,
            base_revision_id="changed-base-revision",
            idempotency_key="confirm-crash-base-1",
        )

    assert exc_info.value.reason_code == "idempotency_key_conflict"
    assert _workflow_start_intent_count_for_draft(service, submitted.requirement_draft_revision_id) == 0
    assert _outbox_count(service) == 0


def test_confirm_recovery_rejects_changed_job_request_hash_after_approved_requirement_crash(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-crash-body-1")

    _simulate_crash_after_runtime_approval(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-crash-body-1",
    )
    _mutate_job_request_body_identity(service, submitted.job_request_revision_id)

    with pytest.raises(ConversationAgentError) as exc_info:
        _confirm(
            service,
            conversation_id=conversation.conversation_id,
            submitted=submitted,
            idempotency_key="confirm-crash-body-1",
        )

    assert exc_info.value.reason_code == "idempotency_key_conflict"
    assert _workflow_start_intent_count_for_draft(service, submitted.requirement_draft_revision_id) == 0
    assert _outbox_count(service) == 0


def test_confirm_missing_effective_title_does_not_create_intent_or_outbox(tmp_path: Path) -> None:
    service, _extractor = _service(tmp_path)
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-title-1")
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            """
            UPDATE wts_job_request_revisions
            SET user_job_title = NULL, extracted_job_title = NULL
            WHERE job_request_revision_id = ?
            """,
            (submitted.job_request_revision_id,),
        )

    with pytest.raises(ConversationAgentError) as exc_info:
        _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-title-1")

    assert exc_info.value.reason_code == "job_title_missing"
    assert _workflow_start_intent_count_for_draft(service, submitted.requirement_draft_revision_id) == 0
    assert _outbox_count(service) == 0
    approved = service.tool_adapter.runtime_store.get_approved_requirement_by_idempotency(
        conversation_id=conversation.conversation_id,
        idempotency_key="confirm-title-1",
    )
    assert approved is None


def test_source_selection_rejects_missing_duplicate_and_disallowed_source_state() -> None:
    from seektalent_conversation_agent.source_selection import SourceSelectionError, resolve_runtime_source_selection

    with pytest.raises(SourceSelectionError) as missing:
        resolve_runtime_source_selection(
            source_kinds=None,
            workspace_source_policy_id=None,
            registered_runtime_source_ids={"cts", "liepin"},
        )
    assert missing.value.reason_code == "source_policy_missing"

    with pytest.raises(SourceSelectionError) as empty:
        resolve_runtime_source_selection(
            source_kinds=[],
            workspace_source_policy_id=None,
            registered_runtime_source_ids={"cts", "liepin"},
        )
    assert empty.value.reason_code == "source_selection_empty"

    with pytest.raises(SourceSelectionError) as duplicate:
        resolve_runtime_source_selection(
            source_kinds=["cts", "cts"],
            workspace_source_policy_id=None,
            registered_runtime_source_ids={"cts", "liepin"},
        )
    assert duplicate.value.reason_code == "duplicate_source_kind"

    with pytest.raises(SourceSelectionError) as disallowed:
        resolve_runtime_source_selection(
            source_kinds=["liepin"],
            workspace_source_policy_id=None,
            registered_runtime_source_ids={"cts"},
        )
    assert disallowed.value.reason_code == "source_policy_disallowed"


def test_source_selection_uses_source_registry_default_policy_and_custom_registered_sources() -> None:
    from seektalent.source_contracts import RegisteredSource, SourceBudget, SourceCapabilities, SourcePlan, SourceRegistry
    from seektalent_runtime_control.source_catalog import RuntimeSourcePolicyResolver
    from seektalent_conversation_agent.source_selection import RuntimeSourceSelectionResolver, SourceSelectionError

    budget = SourceBudget(card_target=3, detail_target=0, scan_limit=3)

    async def run_card_lane(request):  # type: ignore[no-untyped-def]
        raise AssertionError("selection should not execute source lanes")

    def source(source_id: str) -> RegisteredSource:
        return RegisteredSource(
            source_id=source_id,
            label=source_id,
            capabilities=SourceCapabilities(
                supports_card_search=True,
                supports_detail_fetch=False,
                supports_native_filters=False,
                supports_incremental_detail=False,
                requires_human_login=False,
                max_safe_concurrency=1,
                stable_external_id=True,
                stable_dedup_key=True,
            ),
            default_budget=budget,
            plan=lambda runtime_run_id, source_index, budget_overrides: SourcePlan(
                source_id=source_id,
                source_plan_id=f"{runtime_run_id}:source:{source_index}",
                runtime_run_id=runtime_run_id,
                label=source_id,
                budget=budget,
            ),
            run_card_lane=run_card_lane,
        )

    resolver = RuntimeSourceSelectionResolver(
        source_policy_resolver=RuntimeSourcePolicyResolver(
            SourceRegistry(
                [source("internal_referrals"), source("external_board")],
                default_source_ids=("internal_referrals",),
            )
        )
    )

    default_selection = resolver.resolve_runtime_source_selection(
        source_kinds=None,
        workspace_source_policy_id=None,
    )
    explicit_selection = resolver.resolve_runtime_source_selection(
        source_kinds=["external_board"],
        workspace_source_policy_id=None,
    )

    assert default_selection.runtime_source_ids == ("internal_referrals",)
    assert explicit_selection.runtime_source_ids == ("external_board",)
    with pytest.raises(SourceSelectionError) as exc_info:
        resolver.resolve_runtime_source_selection(source_kinds=["liepin"], workspace_source_policy_id=None)
    assert exc_info.value.reason_code == "source_policy_disallowed"


def test_workflow_start_resolves_source_kinds_to_runtime_source_ids(tmp_path: Path) -> None:
    workflow_executor = CapturingWorkflowExecutor()
    service, _extractor = _service_with_workflow_executor(
        tmp_path,
        workflow_executor=workflow_executor,
        registered_runtime_source_ids={"cts", "liepin"},
    )
    conversation = _conversation(service)
    submitted = _submit_jd(
        service,
        conversation_id=conversation.conversation_id,
        idempotency_key="submit-source-1",
        source_kinds=["liepin"],
    )
    confirmed = _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-source-1")
    outbox_id = _outbox_id_for_aggregate(service, confirmed.workflow_start_intent_id)

    intent = service.process_workflow_start_outbox_item(outbox_id)

    assert intent.status == "started"
    assert intent.runtime_run_id == "runtime_run_from_intent_1"
    assert len(workflow_executor.calls) == 1
    call = workflow_executor.calls[0]
    assert tuple(call["source_ids"]) == ("liepin",)
    assert not isinstance(call["approved_requirement"], str)
    assert call["approved_requirement"].approved_requirement_revision_id == intent.approved_requirement_revision_id
    assert call["run_intent_id"] == intent.deterministic_run_key
    assert call["start_idempotency_key"] == intent.deterministic_run_key
    assert service.outbox_store.get(outbox_id).status == "done"
    reopened = service.reopen_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )
    assert reopened.conversation_reopen_state.runtime_run_id == "runtime_run_from_intent_1"


def test_workflow_start_marks_intent_failed_when_source_disallowed(tmp_path: Path) -> None:
    workflow_executor = CapturingWorkflowExecutor()
    service, _extractor = _service_with_workflow_executor(
        tmp_path,
        workflow_executor=workflow_executor,
        registered_runtime_source_ids={"cts"},
    )
    conversation = _conversation(service)
    submitted = _submit_jd(
        service,
        conversation_id=conversation.conversation_id,
        idempotency_key="submit-disallowed-1",
        source_kinds=["liepin"],
    )
    confirmed = _confirm(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-disallowed-1",
    )
    outbox_id = _outbox_id_for_aggregate(service, confirmed.workflow_start_intent_id)

    intent = service.process_workflow_start_outbox_item(outbox_id)

    assert intent.status == "failed"
    assert intent.reason_code == "source_policy_disallowed"
    assert workflow_executor.calls == []
    assert service.outbox_store.get(outbox_id).status == "done"


def test_workflow_start_uses_default_source_policy_when_sources_are_not_explicit(tmp_path: Path) -> None:
    from seektalent.source_adapters.registry import build_default_source_registry
    from seektalent_conversation_agent.source_selection import RuntimeSourceSelectionResolver
    from seektalent_runtime_control.source_catalog import RuntimeSourcePolicyResolver

    workflow_executor = CapturingWorkflowExecutor()
    service, _extractor = _service_with_workflow_executor(
        tmp_path,
        workflow_executor=workflow_executor,
        source_selection_resolver=RuntimeSourceSelectionResolver(
            source_policy_resolver=RuntimeSourcePolicyResolver(
                build_default_source_registry(make_settings(workspace_root=str(tmp_path)))
            )
        ),
    )
    conversation = _conversation(service)
    submitted = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title=None,
        jd_text="需要 Python 平台负责人，负责 API 与平台工程。",
        notes="优先华东，接受远程协作",
        idempotency_key="submit-default-source-1",
    )
    confirmed = _confirm(
        service,
        conversation_id=conversation.conversation_id,
        submitted=submitted,
        idempotency_key="confirm-default-source-1",
    )
    outbox_id = _outbox_id_for_aggregate(service, confirmed.workflow_start_intent_id)

    intent = service.process_workflow_start_outbox_item(outbox_id)

    assert intent.status == "started"
    assert tuple(workflow_executor.calls[0]["source_ids"]) == ("liepin",)


def test_workflow_start_outbox_retry_after_started_intent_does_not_enqueue_twice(tmp_path: Path) -> None:
    workflow_executor = CapturingWorkflowExecutor()
    service, _extractor = _service_with_workflow_executor(
        tmp_path,
        workflow_executor=workflow_executor,
        registered_runtime_source_ids={"cts", "liepin"},
    )
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-retry-1")
    confirmed = _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-retry-1")
    outbox_id = _outbox_id_for_aggregate(service, confirmed.workflow_start_intent_id)

    first = service.process_workflow_start_outbox_item(outbox_id)
    _reset_outbox_to_pending(service, outbox_id)
    second = service.process_workflow_start_outbox_item(outbox_id)

    assert first.workflow_start_intent_id == second.workflow_start_intent_id
    assert second.status == "started"
    assert len(workflow_executor.calls) == 1
    assert service.outbox_store.get(outbox_id).status == "done"


def test_workflow_start_outbox_claim_blocks_duplicate_executor_entry_and_expires(tmp_path: Path) -> None:
    workflow_executor = CapturingWorkflowExecutor()
    service, _extractor = _service_with_workflow_executor(
        tmp_path,
        workflow_executor=workflow_executor,
        registered_runtime_source_ids={"cts", "liepin"},
    )
    conversation = _conversation(service)
    submitted = _submit_jd(service, conversation_id=conversation.conversation_id, idempotency_key="submit-claim-1")
    confirmed = _confirm(service, conversation_id=conversation.conversation_id, submitted=submitted, idempotency_key="confirm-claim-1")
    outbox_id = _outbox_id_for_aggregate(service, confirmed.workflow_start_intent_id)

    claimed = service.outbox_store.claim_for_processing(
        outbox_id,
        claimed_at="2026-06-09T00:00:10.000000Z",
        reclaim_before=None,
    )
    assert claimed is not None
    assert claimed.status == "in_progress"

    blocked = service.process_workflow_start_outbox_item(outbox_id)

    assert blocked.workflow_start_intent_id == confirmed.workflow_start_intent_id
    assert blocked.status == "pending"
    assert workflow_executor.calls == []

    _expire_outbox_claim(service, outbox_id)
    started = service.process_workflow_start_outbox_item(outbox_id)

    assert started.status == "started"
    assert len(workflow_executor.calls) == 1
    assert service.outbox_store.get(outbox_id).status == "done"


def test_fresh_conversation_store_initialize_creates_wts_tables_at_current_version(tmp_path: Path) -> None:
    db_path = tmp_path / "conversation_agent.sqlite3"

    ConversationStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = _tables(conn)
        _assert_wts_columns(conn)

    assert version == CONVERSATION_AGENT_SCHEMA_VERSION
    assert WTS_TABLES <= tables


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

    assert version == CONVERSATION_AGENT_SCHEMA_VERSION
    assert WTS_TABLES <= tables
    _assert_wts_columns(conn)


def test_conversation_store_migrates_v5_database_to_confirm_request_table(tmp_path: Path) -> None:
    db_path = tmp_path / "conversation_agent_v5.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE agent_conversations (
                conversation_id TEXT PRIMARY KEY
            );
            PRAGMA user_version = 5;
            """
        )

    ConversationStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = _tables(conn)

    assert version == CONVERSATION_AGENT_SCHEMA_VERSION
    assert "wts_confirm_requirement_requests" in tables
    _assert_wts_columns(conn)


def test_conversation_store_migrates_v6_database_to_workflow_outbox_index(tmp_path: Path) -> None:
    db_path = tmp_path / "conversation_agent_v6.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE agent_conversations (
                conversation_id TEXT PRIMARY KEY
            );
            CREATE TABLE wts_outbox (
                outbox_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            PRAGMA user_version = 6;
            """
        )

    ConversationStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(wts_outbox)")}

    assert version == CONVERSATION_AGENT_SCHEMA_VERSION
    assert "idx_wts_outbox_workflow_aggregate" in indexes


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


def _service_with_workflow_executor(
    tmp_path: Path,
    *,
    workflow_executor: CapturingWorkflowExecutor,
    registered_runtime_source_ids: set[str] | None = None,
    source_selection_resolver=None,
) -> tuple[ConversationAgentService, FakeRequirementExecutor]:
    from seektalent_conversation_agent.source_selection import RuntimeSourceSelectionResolver

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
            workflow_executor=workflow_executor,
        ),
        now=_clock(),
        conversation_id_factory=lambda: "agent_conv_1",
        message_id_factory=_sequence("agent_msg"),
        tool_call_id_factory=_sequence("agent_tool_call"),
        source_selection_resolver=source_selection_resolver
        or RuntimeSourceSelectionResolver(registered_runtime_source_ids=registered_runtime_source_ids or set()),
    )
    return service, executor


def _conversation(service: ConversationAgentService):
    return service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 岗位",
    )


def _submit_jd(
    service: ConversationAgentService,
    *,
    conversation_id: str,
    idempotency_key: str,
    source_kinds: list[str] | None = None,
):
    return service.submit_jd(
        conversation_id=conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title=None,
        jd_text="需要 Python 平台负责人，负责 API 与平台工程。",
        notes="优先华东，接受远程协作",
        source_kinds=source_kinds or ["cts", "liepin"],
        idempotency_key=idempotency_key,
    )


def _confirm(
    service: ConversationAgentService,
    *,
    conversation_id: str,
    submitted,
    idempotency_key: str,
):
    return service.confirm_requirements(
        conversation_id=conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=submitted.requirement_draft_revision_id,
        base_revision_id=submitted.requirement_draft_revision_id,
        idempotency_key=idempotency_key,
    )


def _extract_section_texts(draft, section_id: str) -> list[str]:
    return [item.text for item in draft.section(section_id).items if item.status != "deleted"]


def _workflow_start_intent_count_for_draft(service: ConversationAgentService, draft_revision_id: str) -> int:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM wts_workflow_start_intents
            WHERE draft_revision_id = ?
            """,
            (draft_revision_id,),
        ).fetchone()
    return int(row[0])


def _workflow_start_intent_row(service: ConversationAgentService, workflow_start_intent_id: str) -> sqlite3.Row:
    with sqlite3.connect(service.store.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM wts_workflow_start_intents
            WHERE workflow_start_intent_id = ?
            """,
            (workflow_start_intent_id,),
        ).fetchone()
    assert row is not None
    return row


def _outbox_count_for_aggregate(service: ConversationAgentService, aggregate_id: str) -> int:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM wts_outbox
            WHERE aggregate_id = ?
            """,
            (aggregate_id,),
        ).fetchone()
    return int(row[0])


def _outbox_count(service: ConversationAgentService) -> int:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM wts_outbox").fetchone()
    return int(row[0])


def _confirm_request_count(service: ConversationAgentService) -> int:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM wts_confirm_requirement_requests").fetchone()
    return int(row[0])


def _approved_requirement_count_for_idempotency(
    service: ConversationAgentService,
    *,
    conversation_id: str,
    idempotency_key: str,
) -> int:
    runtime_store = service.tool_adapter.runtime_store
    assert runtime_store is not None
    with sqlite3.connect(runtime_store.path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM runtime_approved_requirements
            WHERE agent_conversation_id = ? AND idempotency_key = ?
            """,
            (conversation_id, idempotency_key),
        ).fetchone()
    return int(row[0])


def _message_count(service: ConversationAgentService, conversation_id: str, *, message_type: str) -> int:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_transcript_messages
            WHERE conversation_id = ? AND message_type = ?
            """,
            (conversation_id, message_type),
        ).fetchone()
    return int(row[0])


def _tool_call_count(service: ConversationAgentService, conversation_id: str, *, tool_name: str) -> int:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_tool_calls
            WHERE conversation_id = ? AND tool_name = ?
            """,
            (conversation_id, tool_name),
        ).fetchone()
    return int(row[0])


def _requirement_snapshot_count(service: ConversationAgentService, draft_revision_id: str) -> int:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM wts_requirement_transcript_snapshots
            WHERE draft_revision_id = ?
            """,
            (draft_revision_id,),
        ).fetchone()
    return int(row[0])


def _outbox_id_for_aggregate(service: ConversationAgentService, aggregate_id: str) -> str:
    with sqlite3.connect(service.store.path) as conn:
        row = conn.execute(
            """
            SELECT outbox_id
            FROM wts_outbox
            WHERE aggregate_id = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (aggregate_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _reset_outbox_to_pending(service: ConversationAgentService, outbox_id: str) -> None:
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            """
            UPDATE wts_outbox
            SET status = 'pending'
            WHERE outbox_id = ?
            """,
            (outbox_id,),
        )


def _expire_outbox_claim(service: ConversationAgentService, outbox_id: str) -> None:
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            """
            UPDATE wts_outbox
            SET status = 'in_progress', updated_at = '2026-06-08T23:59:00.000000Z'
            WHERE outbox_id = ?
            """,
            (outbox_id,),
        )


def _set_job_request_sources(
    service: ConversationAgentService,
    job_request_revision_id: str,
    *,
    source_kinds_json: str,
) -> None:
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            """
            UPDATE wts_job_request_revisions
            SET source_kinds_json = ?, updated_at = '2026-06-09T00:00:00.000000Z'
            WHERE job_request_revision_id = ?
            """,
            (source_kinds_json, job_request_revision_id),
        )


def _mutate_job_request_body_identity(service: ConversationAgentService, job_request_revision_id: str) -> None:
    with sqlite3.connect(service.store.path) as conn, conn:
        conn.execute(
            """
            UPDATE wts_job_request_revisions
            SET jd_text = 'mutated canonical JD body',
                request_hash = 'mutated-job-request-request-hash',
                updated_at = '2026-06-09T00:00:00.000000Z'
            WHERE job_request_revision_id = ?
            """,
            (job_request_revision_id,),
        )


def _simulate_crash_after_runtime_approval(
    service: ConversationAgentService,
    *,
    conversation_id: str,
    submitted,
    idempotency_key: str,
) -> None:
    original_create = service.workflow_start_intent_store.create_or_get_confirmed_draft_intent

    def crash_after_runtime_confirm(**kwargs: object):
        del kwargs
        raise ConversationAgentError("simulated_crash_after_approval")

    service.workflow_start_intent_store.create_or_get_confirmed_draft_intent = crash_after_runtime_confirm
    try:
        with pytest.raises(ConversationAgentError) as crash:
            _confirm(service, conversation_id=conversation_id, submitted=submitted, idempotency_key=idempotency_key)
        assert crash.value.reason_code == "simulated_crash_after_approval"
    finally:
        service.workflow_start_intent_store.create_or_get_confirmed_draft_intent = original_create

    assert _approved_requirement_count_for_idempotency(
        service,
        conversation_id=conversation_id,
        idempotency_key=idempotency_key,
    ) == 1
    assert _workflow_start_intent_count_for_draft(service, submitted.requirement_draft_revision_id) == 0
    assert _outbox_count(service) == 0


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
    indexes = {
        table_name: {row[1] for row in conn.execute(f"PRAGMA index_list({table_name})")}
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
        "confirm_request_id",
        "workspace_id",
        "owner_user_id",
        "conversation_id",
        "draft_revision_id",
        "expected_draft_revision_id",
        "job_request_revision_id",
        "approved_requirement_revision_id",
        "idempotency_key",
        "request_hash",
        "status",
        "created_at",
        "updated_at",
    } <= columns["wts_confirm_requirement_requests"]
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
    assert "idx_wts_outbox_workflow_aggregate" in indexes["wts_outbox"]


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
