from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.models import QueryTermCandidate, RequirementSheet
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.requirements import ReviewResolutionOperation


def test_next_round_requirement_amendments_accumulate_unless_explicitly_replaced(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=FakeRequirementNormalizer(),
        amendment_id_factory=_sequence("reqamend_1", "reqamend_2", "reqamend_3"),
        approved_requirement_id_factory=_sequence("reqapproved_2", "reqapproved_3", "reqapproved_4"),
        now=_clock("2026-06-08T00:00:01.000000Z"),
    )

    first = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Add Kafka.",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-1",
    )
    second = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Reject frequent job hopping.",
        target_section_hint="exclusion_signals",
        idempotency_key="amend-2",
    )
    replacement = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Replace Kafka with distributed systems.",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-3",
        replace_amendment_id=first.amendment_id,
    )

    assert first.target_round_no == 3
    assert second.target_round_no == 3
    assert store.get_requirement_amendment(first.amendment_id).status == "superseded"
    assert store.get_requirement_amendment(second.amendment_id).status == "pending_target_round"
    assert replacement.status == "pending_target_round"
    assert replacement.supersedes_amendment_id == first.amendment_id

    pending = store.list_runtime_requirement_amendments(
        runtime_run_id="runtime_run_1",
        target_round_no=3,
        statuses={"pending_target_round"},
    )
    assert [amendment.amendment_id for amendment in pending] == [second.amendment_id, replacement.amendment_id]


def test_next_round_requirement_retargets_locked_round_and_activates_at_boundary(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.models import RuntimeControlEventInput

    store = _store_with_approved_run(tmp_path)
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.append_executor_event(
        RuntimeControlEventInput(
            event_id="rtevt_locked_round_3",
            runtime_run_id="runtime_run_1",
            event_type="runtime_round_input_locked",
            stage="round",
            round_no=3,
            source_id=None,
            status="completed",
            summary="round 3 input locked",
            payload={},
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        executor_id="executor_1",
        run_status="running",
    )
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=FakeRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_1",
        approved_requirement_id_factory=lambda: "reqapproved_2",
        now=_clock("2026-06-08T00:00:02.000000Z", "2026-06-08T00:00:03.000000Z"),
    )

    amendment = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Add Kafka.",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-1",
    )

    assert amendment.target_round_no == 4

    activated = service.apply_next_round_requirements_at_boundary(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        round_no=4,
    )

    assert [item.amendment_id for item in activated] == [amendment.amendment_id]
    assert store.get_requirement_amendment(amendment.amendment_id).status == "applied"
    assert store.get_run("runtime_run_1").approved_requirement_revision_id == "reqapproved_2"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=1, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_next_round_requirement_submitted",
        "runtime_next_round_requirement_applied",
        "runtime_requirement_revision_activated",
    ]


def test_next_round_requirement_can_replace_full_requirement_sheet_with_query_terms_and_scoring(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=FullSheetRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_full_sheet",
        approved_requirement_id_factory=lambda: "reqapproved_full_sheet",
        now=_clock("2026-06-08T00:00:01.000000Z"),
    )

    result = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Must have ClickHouse production experience and prioritize real-time data warehouse keywords.",
        target_section_hint=None,
        idempotency_key="amend-full-sheet",
    )

    approved = store.get_approved_requirement(result.approved_requirement_revision_id)
    assert approved.draft_revision_id is None
    assert approved.base_approved_requirement_revision_id == "reqapproved_1"
    assert approved.source_amendment_id == result.amendment_id
    assert approved.requirement_sheet.must_have_capabilities == ["Python", "ClickHouse production experience"]
    assert [term.term for term in approved.requirement_sheet.initial_query_term_pool] == [
        "ClickHouse real-time data warehouse"
    ]
    assert approved.requirement_sheet.scoring_rationale == (
        "Prioritize Python search systems and ClickHouse real-time analytics experience."
    )


def test_next_round_requirement_rejects_patch_with_full_sheet_and_additions(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=ConflictingPatchRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_conflict",
        approved_requirement_id_factory=lambda: "reqapproved_conflict",
        now=_clock("2026-06-08T00:00:01.000000Z"),
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        service.submit_next_round_requirement(
            runtime_run_id="runtime_run_1",
            text="Add Kafka and replace the full sheet.",
            target_section_hint="must_have_capabilities",
            idempotency_key="amend-conflict",
        )

    assert exc_info.value.reason_code == "requirement_sheet_patch_conflict"


def test_next_round_requirement_needs_review_does_not_create_approved_revision(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=ReviewRequiredRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_review",
        approved_requirement_id_factory=lambda: "reqapproved_should_not_exist",
        now=_clock("2026-06-08T00:00:01.000000Z"),
    )

    result = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Kafka 要求怎么归类",
        target_section_hint=None,
        idempotency_key="amend-review",
    )

    assert result.status == "needs_review"
    assert result.review_required is True
    assert result.review_items is not None
    assert len(result.review_items) == 1
    review_item = result.review_items[0]
    assert result.review_items[0].review_item_id == "review_kafka"
    assert review_item.raw_text == "Kafka 要求怎么归类"
    assert review_item.candidate_text == "Kafka 生产环境实战"
    assert review_item.candidate_section == "must_have_capabilities"
    assert review_item.reason_code == "requirement_amendment_ambiguous"
    assert result.approved_requirement_revision_id is None
    amendment = store.get_requirement_amendment(result.amendment_id)
    assert amendment.status == "needs_review"
    assert amendment.result_approved_requirement_revision_id is None

    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_next_round_requirement_submitted",
        "runtime_next_round_requirement_needs_review",
    ]
    needs_review_event = next(
        event for event in events if event.event_type == "runtime_next_round_requirement_needs_review"
    )
    review_items = needs_review_event.payload["reviewItems"]
    assert isinstance(review_items, list) and len(review_items) == 1
    assert review_items[0]["reviewItemId"] == "review_kafka"
    assert review_items[0]["candidateText"] == "Kafka 生产环境实战"


def test_resolved_next_round_requirement_review_retargets_after_round_lock(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.models import RuntimeControlEventInput

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=ReviewRequiredRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_review",
        approved_requirement_id_factory=lambda: "reqapproved_resolved",
        now=_clock(
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
            "2026-06-08T00:00:03.000000Z",
        ),
    )
    pending = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Kafka 要求怎么归类",
        target_section_hint=None,
        idempotency_key="amend-review",
    )
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:01.500000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.append_executor_event(
        RuntimeControlEventInput(
            event_id="rtevt_locked_round_3",
            runtime_run_id="runtime_run_1",
            event_type="runtime_round_input_locked",
            stage="round",
            round_no=pending.target_round_no,
            source_id=None,
            status="completed",
            summary="round 3 input locked",
            payload={},
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:02.500000Z",
        ),
        executor_id="executor_1",
        run_status="running",
    )

    resolved = service.resolve_next_round_requirement_review(
        runtime_run_id="runtime_run_1",
        amendment_id=pending.amendment_id,
        base_approved_requirement_revision_id="reqapproved_1",
        operations=[
            ReviewResolutionOperation(
                op="accept_candidate",
                review_item_id="review_kafka",
                target_section="must_have_capabilities",
                text="Kafka 生产环境实战",
            )
        ],
        idempotency_key="resolve-review",
    )

    assert resolved.status == "pending_target_round"
    assert resolved.target_round_no == 4
    assert resolved.approved_requirement_revision_id == "reqapproved_resolved"
    resolved_events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=20).events
    normalized_event = next(
        event for event in resolved_events if event.event_type == "runtime_next_round_requirement_normalized"
    )
    assert normalized_event.payload["amendmentId"] == "reqamend_review"
    assert normalized_event.payload["approvedRequirementRevisionId"] == "reqapproved_resolved"
    assert normalized_event.payload["targetRoundNo"] == 4
    approved = store.get_approved_requirement("reqapproved_resolved")
    assert approved.base_approved_requirement_revision_id == "reqapproved_1"
    assert approved.source_amendment_id == pending.amendment_id
    assert approved.requirement_sheet.must_have_capabilities == ["Python", "Kafka 生产环境实战"]
    amendment = store.get_requirement_amendment(pending.amendment_id)
    assert amendment.status == "pending_target_round"
    resolved_patch = amendment.resolved_patch
    assert resolved_patch is not None
    assert resolved_patch["rejectedFragments"] == []
    additions = resolved_patch["additions"]
    assert isinstance(additions, list) and len(additions) == 1
    assert additions[0] == {
        "sectionId": "must_have_capabilities",
        "text": "Kafka 生产环境实战",
        "source": "user_review_resolution",
        "reviewItemId": "review_kafka",
    }


def test_resolved_next_round_requirement_review_rejects_when_amendment_not_found(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=ReviewRequiredRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_review",
        approved_requirement_id_factory=lambda: "reqapproved_resolved",
        now=_clock("2026-06-08T00:00:01.000000Z"),
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        service.resolve_next_round_requirement_review(
            runtime_run_id="runtime_run_1",
            amendment_id="missing_amendment",
            base_approved_requirement_revision_id="reqapproved_1",
            operations=[
                ReviewResolutionOperation(
                    op="accept_candidate",
                    review_item_id="review_kafka",
                    target_section="must_have_capabilities",
                    text="Kafka 生产环境实战",
                )
            ],
            idempotency_key="resolve-review",
        )

    assert exc_info.value.reason_code == "requirement_draft_not_found"


def test_resolved_next_round_requirement_review_rejects_when_amendment_stale(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    review_service = RuntimeCommandService(
        store=store,
        requirement_normalizer=ReviewRequiredRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_review",
        approved_requirement_id_factory=_sequence("reqapproved_resolved", "reqapproved_stale"),
        now=_clock("2026-06-08T00:00:01.000000Z", "2026-06-08T00:00:02.000000Z"),
    )
    pending = review_service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Kafka 要求怎么归类",
        target_section_hint=None,
        idempotency_key="amend-review",
    )

    approved_submitter = RuntimeCommandService(
        store=store,
        requirement_normalizer=FakeRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_advance",
        approved_requirement_id_factory=lambda: "reqapproved_new_base",
        now=_clock("2026-06-08T00:00:03.000000Z"),
    )
    approved_submitter.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Add GoLang.",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-advance",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        review_service.resolve_next_round_requirement_review(
            runtime_run_id="runtime_run_1",
            amendment_id=pending.amendment_id,
            base_approved_requirement_revision_id="reqapproved_new_base",
            operations=[
                ReviewResolutionOperation(
                    op="accept_candidate",
                    review_item_id="review_kafka",
                    target_section="must_have_capabilities",
                    text="Kafka 生产环境实战",
                )
            ],
            idempotency_key="resolve-review-stale",
        )

    assert exc_info.value.reason_code == "requirement_amendment_stale"


def test_resolved_next_round_requirement_review_returns_pending_amendment_when_not_needs_review(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=FakeRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_existing",
        approved_requirement_id_factory=lambda: "reqapproved_existing",
        now=_clock("2026-06-08T00:00:01.000000Z"),
    )
    pending = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Add Kafka.",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-existing",
    )

    resolved = service.resolve_next_round_requirement_review(
        runtime_run_id="runtime_run_1",
        amendment_id=pending.amendment_id,
        base_approved_requirement_revision_id="reqapproved_1",
        operations=[
            ReviewResolutionOperation(
                op="accept_candidate",
                review_item_id="should_be_ignored",
                target_section="must_have_capabilities",
                text="Should ignore",
            )
        ],
        idempotency_key="resolve-review-existing",
    )

    assert resolved.status == "pending_target_round"
    assert resolved.amendment_id == pending.amendment_id
    assert resolved.target_round_no == pending.target_round_no
    assert store.get_requirement_amendment(pending.amendment_id).status == "pending_target_round"


def test_resolved_next_round_requirement_review_rejects_when_no_future_round_exists(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        requirement_normalizer=ReviewRequiredRequirementNormalizer(),
        amendment_id_factory=lambda: "reqamend_review",
        approved_requirement_id_factory=lambda: "reqapproved_resolved",
        now=_clock("2026-06-08T00:00:01.000000Z", "2026-06-08T00:00:02.000000Z"),
    )
    pending = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="Kafka 要求怎么归类",
        target_section_hint=None,
        idempotency_key="amend-review",
    )
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="completed",
        updated_at="2026-06-08T00:00:02.000000Z",
        completed_at="2026-06-08T00:00:02.000000Z",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        service.resolve_next_round_requirement_review(
            runtime_run_id="runtime_run_1",
            amendment_id=pending.amendment_id,
            base_approved_requirement_revision_id="reqapproved_1",
            operations=[
                ReviewResolutionOperation(
                    op="accept_candidate",
                    review_item_id="review_kafka",
                    target_section="must_have_capabilities",
                    text="Kafka 生产环境实战",
                )
            ],
            idempotency_key="resolve-review",
        )

    assert exc_info.value.reason_code == "runtime_no_future_round_available"


def test_default_next_round_normalizer_treats_user_text_as_requirement_signal(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_approved_run(tmp_path)
    service = RuntimeCommandService(
        store=store,
        amendment_id_factory=lambda: "reqamend_default",
        approved_requirement_id_factory=lambda: "reqapproved_default",
        now=_clock("2026-06-08T00:00:01.000000Z"),
    )

    result = service.submit_next_round_requirement(
        runtime_run_id="runtime_run_1",
        text="ClickHouse production experience",
        target_section_hint="preferred_capabilities",
        idempotency_key="amend-default",
    )

    approved = store.get_approved_requirement(result.approved_requirement_revision_id)
    assert approved.requirement_sheet.preferred_capabilities == ["ClickHouse production experience"]
    assert [term.term for term in approved.requirement_sheet.initial_query_term_pool] == [
        "ClickHouse production experience"
    ]
    assert "ClickHouse production experience" in approved.requirement_sheet.scoring_rationale


class FakeRequirementNormalizer:
    def normalize_next_round_requirement_text(self, *, text: str, target_section_hint: str | None, current_requirement):
        return {
            "additions": [
                {
                    "sectionId": target_section_hint or "must_have_capabilities",
                    "text": text,
                }
            ],
            "reviewItems": [],
            "rejectedFragments": [],
        }


class FullSheetRequirementNormalizer:
    def normalize_next_round_requirement_text(self, *, text: str, target_section_hint: str | None, current_requirement):
        del text, target_section_hint
        sheet = current_requirement.requirement_sheet.model_copy(
            update={
                "must_have_capabilities": [
                    *current_requirement.requirement_sheet.must_have_capabilities,
                    "ClickHouse production experience",
                ],
                "initial_query_term_pool": [
                    QueryTermCandidate(
                        term="ClickHouse real-time data warehouse",
                        source="notes",
                        category="domain",
                        priority=95,
                        evidence="User added this runtime requirement.",
                        first_added_round=3,
                    )
                ],
                "scoring_rationale": (
                    "Prioritize Python search systems and ClickHouse real-time analytics experience."
                ),
            }
        )
        return {
            "requirementSheet": sheet.model_dump(mode="json"),
            "rejectedFragments": [],
            "reviewItems": [],
        }


class ConflictingPatchRequirementNormalizer:
    def normalize_next_round_requirement_text(self, *, text: str, target_section_hint: str | None, current_requirement):
        sheet = current_requirement.requirement_sheet.model_copy(
            update={"must_have_capabilities": ["Python", "Kafka"]}
        )
        return {
            "requirementSheet": sheet.model_dump(mode="json"),
            "additions": [{"sectionId": target_section_hint or "must_have_capabilities", "text": text}],
            "rejectedFragments": [],
            "reviewItems": [],
        }


class ReviewRequiredRequirementNormalizer:
    def normalize_next_round_requirement_text(self, *, text: str, target_section_hint: str | None, current_requirement):
        del target_section_hint, current_requirement
        return {
            "additions": [],
            "reviewItems": [
                {
                    "reviewItemId": "review_kafka",
                    "rawText": text,
                    "candidateText": "Kafka 生产环境实战",
                    "candidateSection": "must_have_capabilities",
                    "reasonCode": "requirement_amendment_ambiguous",
                }
            ],
            "rejectedFragments": [],
        }


def _store_with_approved_run(tmp_path: Path):
    from seektalent_runtime_control.models import RuntimeRunRecord
    from seektalent_runtime_control.requirements import ApprovedRequirementRevision
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    approved = ApprovedRequirementRevision(
        approved_requirement_revision_id="reqapproved_1",
        draft_revision_id="reqdraft_1",
        agent_conversation_id="agent_conv_1",
        requirement_sheet=_requirement_sheet(),
        selected_item_ids=[],
        deselected_item_ids=[],
        created_at="2026-06-08T00:00:00.000000Z",
    )
    store.save_approved_requirement(approved, idempotency_key="approved-1")
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status="running",
            current_stage="round",
            current_round=2,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00.000000Z",
            updated_at="2026-06-08T00:00:00.000000Z",
            completed_at=None,
        )
    )
    return store


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="Senior Python Engineer",
        title_anchor_terms=["Python Engineer"],
        title_anchor_rationale="Title is explicit.",
        role_summary="Build search systems.",
        must_have_capabilities=["Python"],
        preferred_capabilities=[],
        exclusion_signals=[],
        scoring_rationale="Relevant experience.",
    )


def _clock(*values: str):
    iterator = iter(values)
    last = values[-1]

    def now() -> str:
        nonlocal last
        last = next(iterator, last)
        return last

    return now


def _sequence(*values: str):
    iterator = iter(values)
    last = values[-1]

    def next_value() -> str:
        nonlocal last
        last = next(iterator, last)
        return last

    return next_value
