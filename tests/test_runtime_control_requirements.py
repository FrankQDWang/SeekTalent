from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.models import HardConstraintSlots, QueryTermCandidate, RequirementSheet
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.requirements import DraftOperation
from seektalent_runtime_control.service import RuntimeControlService, _apply_operation
from seektalent_runtime_control.store import RuntimeControlStore


class RequirementExecutor:
    def __init__(self) -> None:
        self.normalized_texts: list[str] = []

    def extract_requirements(self, *, job_title: str, jd_text: str, notes: str | None) -> RequirementSheet:
        return requirement_sheet(job_title=job_title)

    def normalize_requirement_text(self, *, text: str, target_section_hint: str | None, current_draft) -> dict[str, object]:
        self.normalized_texts.append(text)
        return {
            "additions": [
                {
                    "sectionId": target_section_hint or "must_have_capabilities",
                    "text": "Kafka 生产环境实战",
                    "value": "Kafka 生产环境实战",
                    "source": "runtime_normalized",
                }
            ],
            "reviewItems": [],
            "rejectedFragments": [],
        }


def runtime_service(tmp_path: Path, executor: RequirementExecutor | None = None) -> RuntimeControlService:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    return RuntimeControlService(store=store, executor=executor or RequirementExecutor())


def requirement_sheet(*, job_title: str = "Python 后端工程师") -> RequirementSheet:
    return RequirementSheet(
        job_title=job_title,
        title_anchor_terms=["Python 后端"],
        title_anchor_rationale="JD explicitly names backend role.",
        role_summary="Build Python backend services.",
        must_have_capabilities=["Python API 开发", "分布式系统经验"],
        preferred_capabilities=["Kafka 经验"],
        exclusion_signals=["频繁跳槽且无稳定项目"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Python 后端",
                source="jd",
                category="role_anchor",
                priority=100,
                evidence="JD title",
                first_added_round=0,
            )
        ],
        scoring_rationale="Score against confirmed backend requirements.",
    )


def test_extract_requirements_persists_editable_default_selected_draft(tmp_path: Path) -> None:
    service = runtime_service(tmp_path)

    draft = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API 和 Kafka",
        notes=None,
        source_ids=["internal_referrals"],
        idempotency_key="agent_conv_1:extract:1",
    )

    assert draft.status == "draft_ready"
    assert {section.section_id for section in draft.sections} == {
        "must_have_capabilities",
        "preferred_capabilities",
        "hard_constraints",
        "exclusion_signals",
        "initial_query_term_pool",
    }
    assert all(item.selected for section in draft.sections for item in section.items)
    assert service.get_requirement_draft(conversation_id="agent_conv_1").draft_revision_id == draft.draft_revision_id

    replay = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API 和 Kafka",
        notes=None,
        source_ids=["internal_referrals"],
        idempotency_key="agent_conv_1:extract:1",
    )
    assert replay.draft_revision_id == draft.draft_revision_id


def test_update_requirement_draft_creates_revision_and_rejects_stale_base(tmp_path: Path) -> None:
    service = runtime_service(tmp_path)
    draft = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API 和 Kafka",
        notes=None,
        source_ids=[],
        idempotency_key="extract",
    )
    must_item = draft.section("must_have_capabilities").items[0]
    preferred_item = draft.section("preferred_capabilities").items[0]

    updated = service.update_requirement_draft(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        operations=[
            DraftOperation(op="set_selected", item_id=must_item.item_id, selected=False),
            DraftOperation(op="edit_text", item_id=preferred_item.item_id, text="Kafka 生产环境实战"),
            DraftOperation(op="move_item", item_id=preferred_item.item_id, target_section="must_have_capabilities"),
        ],
        idempotency_key="edit-1",
    )

    assert updated.draft_revision_id != draft.draft_revision_id
    assert updated.section("must_have_capabilities").find_item(must_item.item_id).selected is False
    assert any(item.text == "Kafka 生产环境实战" for item in updated.section("must_have_capabilities").items)

    with pytest.raises(RuntimeControlError) as exc_info:
        service.update_requirement_draft(
            draft_revision_id=draft.draft_revision_id,
            base_revision_id=draft.draft_revision_id,
            operations=[DraftOperation(op="delete_item", item_id=must_item.item_id)],
            idempotency_key="edit-stale",
        )

    assert exc_info.value.reason_code == "requirement_draft_stale"
    assert exc_info.value.payload["latestDraftRevisionId"] == updated.draft_revision_id


def test_update_requirement_draft_rejects_missing_item_operation(tmp_path: Path) -> None:
    service = runtime_service(tmp_path)
    draft = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API 和 Kafka",
        notes=None,
        source_ids=[],
        idempotency_key="extract",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        service.update_requirement_draft(
            draft_revision_id=draft.draft_revision_id,
            base_revision_id=draft.draft_revision_id,
            operations=[DraftOperation(op="edit_text", item_id="missing_item", text="Kafka 生产环境实战")],
            idempotency_key="edit-invalid-item",
        )

    assert exc_info.value.reason_code == "requirement_draft_invalid"


def test_move_item_sorts_target_section_after_append(tmp_path: Path) -> None:
    service = runtime_service(tmp_path)
    draft = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API 和 Kafka",
        notes=None,
        source_ids=[],
        idempotency_key="extract",
    )
    must_item = draft.section("must_have_capabilities").items[0]
    preferred = draft.section("preferred_capabilities")
    preferred.items[0].sort_order = 30
    preferred.items.append(
        preferred.items[0].model_copy(
            deep=True,
            update={
                "item_id": "reqitem_lower_order",
                "text": "低排序需求",
                "value": "低排序需求",
                "sort_order": 10,
            },
        )
    )

    _apply_operation(
        draft,
        DraftOperation(op="move_item", item_id=must_item.item_id, target_section="preferred_capabilities"),
    )

    assert [item.sort_order for item in preferred.items] == [10, 30, 30]


def test_confirm_requirements_uses_only_selected_resolved_items(tmp_path: Path) -> None:
    service = runtime_service(tmp_path)
    draft = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API 和 Kafka",
        notes=None,
        source_ids=[],
        idempotency_key="extract",
    )
    first_must = draft.section("must_have_capabilities").items[0]
    updated = service.update_requirement_draft(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        operations=[DraftOperation(op="set_selected", item_id=first_must.item_id, selected=False)],
        idempotency_key="edit-1",
    )

    approved = service.confirm_requirements(
        draft_revision_id=updated.draft_revision_id,
        base_revision_id=updated.draft_revision_id,
        idempotency_key="confirm-1",
    )

    assert approved.status == "confirmed"
    assert approved.draft_revision_id == updated.draft_revision_id
    assert approved.base_approved_requirement_revision_id is None
    assert approved.source_amendment_id is None
    assert approved.requirement_sheet.must_have_capabilities == ["分布式系统经验"]
    assert first_must.item_id in approved.deselected_item_ids

    replay = service.confirm_requirements(
        draft_revision_id=updated.draft_revision_id,
        base_revision_id=updated.draft_revision_id,
        idempotency_key="confirm-1",
    )
    assert replay.approved_requirement_revision_id == approved.approved_requirement_revision_id
