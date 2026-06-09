from __future__ import annotations

import pytest

from seektalent_runtime_control.errors import RuntimeControlError
from tests.test_runtime_control_requirements import RequirementExecutor, runtime_service


class BlankTextAdditionExecutor(RequirementExecutor):
    def normalize_requirement_text(self, *, text: str, target_section_hint: str | None, current_draft) -> dict[str, object]:
        return {
            "additions": [{"sectionId": target_section_hint or "must_have_capabilities", "text": " "}],
            "reviewItems": [],
            "rejectedFragments": [],
        }


class MalformedPayloadExecutor(RequirementExecutor):
    def normalize_requirement_text(self, *, text: str, target_section_hint: str | None, current_draft) -> dict[str, object]:
        return {
            "additions": {"not": "a list"},
            "reviewItems": [],
            "rejectedFragments": [
                "not a dict",
                {1: "non-string key"},
            ],
        }


def test_free_form_amendment_normalizes_through_executor_and_creates_revision(tmp_path) -> None:  # type: ignore[no-untyped-def]
    executor = RequirementExecutor()
    service = runtime_service(tmp_path, executor=executor)
    draft = service.extract_requirements(
        conversation_id="agent_conv_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API",
        notes=None,
        source_ids=[],
        idempotency_key="extract",
    )

    amended = service.amend_requirement_draft_from_text(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        text="另外希望有 Kafka 实战",
        target_section_hint="preferred_capabilities",
        idempotency_key="amend-1",
    )

    assert executor.normalized_texts == ["另外希望有 Kafka 实战"]
    assert amended.draft_revision_id != draft.draft_revision_id
    added = amended.section("preferred_capabilities").items[-1]
    assert added.text == "Kafka 生产环境实战"
    assert added.selected is True
    assert added.source == "runtime_normalized"
    assert amended.amendment is not None
    assert amended.amendment.status == "applied"

    replay = service.amend_requirement_draft_from_text(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        text="另外希望有 Kafka 实战",
        target_section_hint="preferred_capabilities",
        idempotency_key="amend-1",
    )
    assert replay.draft_revision_id == amended.draft_revision_id


def test_free_form_amendment_rejects_blank_text_addition(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service = runtime_service(tmp_path, executor=BlankTextAdditionExecutor())
    draft = service.extract_requirements(
        conversation_id="agent_conv_invalid_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API",
        notes=None,
        source_ids=[],
        idempotency_key="extract-invalid-1",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        service.amend_requirement_draft_from_text(
            draft_revision_id=draft.draft_revision_id,
            base_revision_id=draft.draft_revision_id,
            text="添加一个空需求",
            target_section_hint="must_have_capabilities",
            idempotency_key="amend-invalid-1",
        )

    assert exc_info.value.reason_code == "requirement_draft_invalid"


def test_free_form_amendment_ignores_malformed_list_payloads(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service = runtime_service(tmp_path, executor=MalformedPayloadExecutor())
    draft = service.extract_requirements(
        conversation_id="agent_conv_invalid_2",
        job_title="Python 后端工程师",
        jd_text="需要 Python API",
        notes=None,
        source_ids=[],
        idempotency_key="extract-invalid-2",
    )

    amended = service.amend_requirement_draft_from_text(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        text="添加一个 malformed patch",
        target_section_hint="preferred_capabilities",
        idempotency_key="amend-invalid-2",
    )

    assert amended.status == "draft_ready"
    assert amended.amendment.status == "applied"
    assert amended.amendment.added_item_ids == []
    assert [item.text for item in amended.section("preferred_capabilities").items] == ["Kafka 经验"]
