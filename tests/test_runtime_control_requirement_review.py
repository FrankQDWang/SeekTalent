from __future__ import annotations

import pytest

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.requirements import (
    DraftOperation,
    RequirementAmendment,
    RequirementAmendmentSummary,
    RequirementDraftItem,
    ReviewItem,
    ReviewResolutionOperation,
)
from seektalent_runtime_control.service import _apply_review_resolution, _now
from tests.test_runtime_control_requirements import runtime_service


def _draft_with_review_item(tmp_path, *, candidate_section: str = "must_have_capabilities"):  # type: ignore[no-untyped-def]
    service = runtime_service(tmp_path)
    draft = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API",
        notes=None,
        source_ids=[],
        idempotency_key="extract",
    )
    amendment_id = "reqamend_review"
    review_item = ReviewItem(
        review_item_id="review_kafka",
        raw_text="Kafka 要求怎么归类",
        candidate_text="Kafka 生产环境实战",
        candidate_section=candidate_section,
        reason_code="requirement_amendment_ambiguous",
    )
    amended = draft.model_copy(
        deep=True,
        update={
            "draft_revision_id": "reqdraft_review",
            "base_revision_id": draft.draft_revision_id,
            "status": "needs_review",
            "created_at": _now(),
            "amendment": RequirementAmendmentSummary(amendment_id=amendment_id, status="needs_review"),
            "unresolved_review_item_count": 1,
            "can_confirm": False,
        },
    )
    target = amended.section(candidate_section)
    target.items.append(
        RequirementDraftItem(
            item_id="reqitem_review_kafka",
            selected=True,
            enabled=True,
            editable=True,
            text="Kafka 生产环境实战",
            value="Kafka 生产环境实战",
            source="extracted_amendment",
            status="needs_review",
            review_item_id=review_item.review_item_id,
            amendment_id=amendment_id,
            sort_order=(len(target.items) + 1) * 10,
            allowed_actions=["select", "edit", "delete"],
        )
    )
    service.store.save_requirement_amendment(
        RequirementAmendment(
            amendment_id=amendment_id,
            agent_conversation_id=draft.conversation_id,
            base_draft_revision_id=draft.draft_revision_id,
            result_draft_revision_id=amended.draft_revision_id,
            input_text=review_item.raw_text,
            target_section_hint=None,
            status="needs_review",
            normalized_patch={"reviewItems": [review_item.model_dump(mode="json")]},
            rejected_fragments=[],
            review_items=[review_item],
            idempotency_key="amend-ambiguous",
            created_at=amended.created_at,
        )
    )
    service.store.save_requirement_draft(
        amended,
        extracted_requirement_sheet_json=service.store.get_extracted_requirement_sheet_json(draft.draft_revision_id),
        idempotency_key="amend-ambiguous",
    )
    return service, amended


def test_review_required_amendment_blocks_confirm_until_resolved(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, amended = _draft_with_review_item(tmp_path)

    assert amended.status == "needs_review"
    assert amended.unresolved_review_item_count == 1

    with pytest.raises(RuntimeControlError) as exc_info:
        service.confirm_requirements(
            draft_revision_id=amended.draft_revision_id,
            base_revision_id=amended.draft_revision_id,
            idempotency_key="confirm-blocked",
        )
    assert exc_info.value.reason_code == "requirement_review_unresolved"

    resolved = service.resolve_requirement_review(
        draft_revision_id=amended.draft_revision_id,
        base_revision_id=amended.draft_revision_id,
        amendment_id=amended.amendment.amendment_id,
        operations=[
            ReviewResolutionOperation(
                op="accept_candidate",
                review_item_id="review_kafka",
                target_section="must_have_capabilities",
                text="Kafka 生产环境实战",
            )
        ],
        idempotency_key="resolve-1",
    )

    assert resolved.status == "draft_ready"
    assert resolved.unresolved_review_item_count == 0
    assert any(item.text == "Kafka 生产环境实战" for item in resolved.section("must_have_capabilities").items)


def test_review_items_use_target_section_order(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, amended = _draft_with_review_item(
        tmp_path,
        candidate_section="preferred_capabilities",
    )

    preferred = amended.section("preferred_capabilities")

    assert [item.sort_order for item in preferred.items] == [10, 20]
    assert preferred.items[-1].review_item_id == "review_kafka"


def test_resolve_requirement_review_rejects_stale_base(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, amended = _draft_with_review_item(tmp_path)
    must_item = amended.section("must_have_capabilities").items[0]
    latest = service.update_requirement_draft(
        draft_revision_id=amended.draft_revision_id,
        base_revision_id=amended.draft_revision_id,
        operations=[DraftOperation(op="set_enabled", item_id=must_item.item_id, enabled=False)],
        idempotency_key="edit-after-review",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        service.resolve_requirement_review(
            draft_revision_id=amended.draft_revision_id,
            base_revision_id=amended.draft_revision_id,
            amendment_id=amended.amendment.amendment_id,
            operations=[],
            idempotency_key="resolve-stale",
        )

    assert exc_info.value.reason_code == "requirement_draft_stale"
    assert exc_info.value.payload["latestDraftRevisionId"] == latest.draft_revision_id


def test_resolve_requirement_review_missing_amendment_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, amended = _draft_with_review_item(tmp_path)

    with pytest.raises(RuntimeControlError) as exc_info:
        service.resolve_requirement_review(
            draft_revision_id=amended.draft_revision_id,
            base_revision_id=amended.draft_revision_id,
            amendment_id="missing_amendment",
            operations=[],
            idempotency_key="resolve-missing-amendment",
        )

    assert exc_info.value.reason_code == "requirement_draft_not_found"


def test_review_resolution_sorts_target_section_after_append(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, amended = _draft_with_review_item(tmp_path)
    preferred = amended.section("preferred_capabilities")
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

    _apply_review_resolution(
        amended,
        ReviewResolutionOperation(
            op="accept_candidate",
            review_item_id="review_kafka",
            target_section="preferred_capabilities",
        ),
        amendment_id=amended.amendment.amendment_id,
    )

    assert [item.sort_order for item in preferred.items] == [10, 30, 30]
