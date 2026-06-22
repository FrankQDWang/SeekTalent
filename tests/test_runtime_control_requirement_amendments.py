from __future__ import annotations

from seektalent.models import ExperienceRequirement, HardConstraintSlots

from tests.test_runtime_control_requirements import RequirementExecutor, runtime_service


class ScalarHardConstraintRequirementExecutor(RequirementExecutor):
    def extract_requirements(self, *, job_title: str, jd_text: str, notes: str | None):  # type: ignore[no-untyped-def]
        sheet = super().extract_requirements(job_title=job_title, jd_text=jd_text, notes=notes)
        if "5 年以上" not in jd_text:
            return sheet
        return sheet.model_copy(
            update={
                "hard_constraints": HardConstraintSlots(
                    locations=sheet.hard_constraints.locations,
                    experience_requirement=ExperienceRequirement(min_years=5, raw_text="5 年以上"),
                )
            }
        )


def test_free_form_amendment_extracts_supplement_and_creates_revision(tmp_path) -> None:  # type: ignore[no-untyped-def]
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

    assert executor.extracted_texts == ["需要 Python API", "另外希望有 Kafka 实战"]
    assert amended.draft_revision_id != draft.draft_revision_id
    added = amended.section("preferred_capabilities").items[-1]
    assert added.text == "Kafka 生产环境实战"
    assert added.selected is True
    assert added.source == "extracted_amendment"
    assert amended.amendment is not None
    assert amended.amendment.status == "applied"
    amendment = service.store.get_requirement_amendment(amended.amendment.amendment_id)
    assert amendment.normalized_patch["extractedSupplement"]["preferred_capabilities"] == ["Kafka 生产环境实战"]

    replay = service.amend_requirement_draft_from_text(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        text="另外希望有 Kafka 实战",
        target_section_hint="preferred_capabilities",
        idempotency_key="amend-1",
    )
    assert replay.draft_revision_id == amended.draft_revision_id


def test_free_form_amendment_deduplicates_already_present_extracted_items(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service = runtime_service(tmp_path)
    draft = service.extract_requirements(
        conversation_id="agent_conv_invalid_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API",
        notes=None,
        source_ids=[],
        idempotency_key="extract-invalid-1",
    )

    amended = service.amend_requirement_draft_from_text(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        text="没有新增结构化需求",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-invalid-1",
    )

    assert amended.amendment.added_item_ids == []
    assert [item.text for item in amended.section("preferred_capabilities").items] == ["Kafka 经验"]


def test_free_form_amendment_persists_merged_sheet_for_confirm(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service = runtime_service(tmp_path)
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
        text="另外希望有 Kafka 实战",
        target_section_hint="preferred_capabilities",
        idempotency_key="amend-invalid-2",
    )
    approved = service.confirm_requirements(
        draft_revision_id=amended.draft_revision_id,
        base_revision_id=amended.draft_revision_id,
        idempotency_key="confirm-amended",
    )

    assert approved.requirement_sheet.preferred_capabilities == ["Kafka 经验", "Kafka 生产环境实战"]
    assert [term.term for term in approved.requirement_sheet.initial_query_term_pool] == [
        "Python 后端",
        "Kafka 生产经验",
    ]


def test_free_form_amendment_confirm_preserves_scalar_hard_constraints(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service = runtime_service(tmp_path, executor=ScalarHardConstraintRequirementExecutor())
    draft = service.extract_requirements(
        conversation_id="agent_conv_scalar_1",
        job_title="Python 后端工程师",
        jd_text="需要 Python API",
        notes=None,
        source_ids=[],
        idempotency_key="extract-scalar-1",
    )

    amended = service.amend_requirement_draft_from_text(
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        text="另外要求 5 年以上后端经验",
        target_section_hint="hard_constraints",
        idempotency_key="amend-scalar-1",
    )
    approved = service.confirm_requirements(
        draft_revision_id=amended.draft_revision_id,
        base_revision_id=amended.draft_revision_id,
        idempotency_key="confirm-amended-scalar-1",
    )

    assert approved.requirement_sheet.hard_constraints.locations == ["上海"]
    assert approved.requirement_sheet.hard_constraints.experience_requirement is not None
    assert approved.requirement_sheet.hard_constraints.experience_requirement.min_years == 5
