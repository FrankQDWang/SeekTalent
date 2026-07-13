from __future__ import annotations

import pytest

from seektalent.models import (
    NormalizedResume,
    ResumeCandidate,
    RuntimeIdentitySignals,
    RuntimeSourceEvidence,
    StructuredResumeEvidence,
    StructuredResumeTimelineItem,
)
import seektalent.runtime.source_lanes as source_lanes_module
from seektalent.runtime.source_lanes import (
    RuntimeCandidateIdentityIndex,
    choose_canonical_resume_for_identity,
)


def _signals(
    *,
    name: str | None = "王明",
    masked: bool = False,
    company: str | None = "海光集成电路",
    title: str | None = "高级主管工程师",
    school: tuple[str, ...] = ("南京邮电大学",),
    chronology: tuple[str, ...] = ("海光集成电路:2023-10:present",),
    provider_hash: str | None = None,
    contacts: tuple[str, ...] = (),
) -> RuntimeIdentitySignals:
    return RuntimeIdentitySignals(
        normalized_name=name,
        is_masked_name=masked,
        current_company_norm=company,
        current_title_norm=title,
        school_norms=school,
        work_chronology_fingerprints=chronology,
        provider_candidate_key_hash=provider_hash,
        protected_contact_hashes=contacts,
    )


def _candidate(resume_id: str, *, source_resume_id: str | None = None) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=source_resume_id or resume_id,
        snapshot_sha256=f"snapshot-{resume_id}",
        dedup_key=resume_id,
        search_text=f"{resume_id} senior engineer",
        raw={},
    )


def _normalized(
    resume_id: str,
    *,
    name: str = "王明",
    current_company: str = "海光集成电路",
    current_title: str = "高级主管工程师",
    completeness: int = 80,
    score_source: str = "card",
    current_duration: str = "",
    prior_work: tuple[tuple[str, str, str], ...] = (),
    project_duration: str = "",
    education_duration: str = "",
) -> NormalizedResume:
    work_experience = [
        StructuredResumeTimelineItem(
            company=current_company,
            title=current_title,
            duration=current_duration,
        ),
        *(
            StructuredResumeTimelineItem(company=company, title=title, duration=duration)
            for company, title, duration in prior_work
        ),
    ]
    return NormalizedResume(
        resume_id=resume_id,
        dedup_key=resume_id,
        candidate_name=name,
        headline=current_title,
        current_title=current_title,
        current_company=current_company,
        education_summary="南京邮电大学 硕士",
        structured_evidence=StructuredResumeEvidence(
            current_role={"company": current_company, "title": current_title},
            work_experience=work_experience,
            project_experience=[StructuredResumeTimelineItem(name="项目", duration=project_duration)]
            if project_duration
            else [],
            education_experience=[StructuredResumeTimelineItem(school="南京邮电大学", duration=education_duration)]
            if education_duration
            else [],
        ),
        completeness_score=completeness,
        score_evidence_source=score_source,
    )


def _evidence(
    evidence_id: str,
    *,
    resume_id: str,
    source: str,
    level: str = "card",
    provider_rank: int | None = None,
    collected_at: str = "2026-05-15T00:00:00Z",
) -> RuntimeSourceEvidence:
    return RuntimeSourceEvidence(
        evidence_id=evidence_id,
        source=source,
        provider=source,
        source_plan_id=f"plan-{source}",
        source_lane_run_id=f"lane-{source}",
        evidence_level=level,
        candidate_resume_id=resume_id,
        provider_candidate_key_hash=f"hash-{evidence_id}",
        provider_rank=provider_rank,
        collected_at=collected_at,
        safe_reason_codes=("source_detail_candidate" if level == "detail" else "source_card_candidate",),
    )


def test_identity_index_uses_same_provider_key_hash_for_stable_identity() -> None:
    left = RuntimeCandidateIdentityIndex()
    first = left.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(provider_hash="same-provider-hash"),
    )
    second = left.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(provider_hash="same-provider-hash"),
    )

    right = RuntimeCandidateIdentityIndex()
    second_first = right.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(provider_hash="same-provider-hash"),
    )
    first_second = right.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(provider_hash="same-provider-hash"),
    )

    assert first.identity_id == second.identity_id
    assert first.identity_id == second_first.identity_id == first_second.identity_id


def test_identity_index_merges_later_protected_contact_hash_and_preserves_alias() -> None:
    index = RuntimeCandidateIdentityIndex()
    cts_identity = index.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(provider_hash="cts-provider", contacts=()),
    )
    liepin_identity = index.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(name="李雷", company="量子科技", title="算法工程师", provider_hash="liepin-provider", contacts=()),
    )

    assert cts_identity.identity_id != liepin_identity.identity_id

    merged = index.upsert_candidate(
        resume_id="liepin-detail-1",
        evidence_id="evidence-liepin-detail",
        signals=_signals(
            name="李雷",
            company="量子科技",
            title="算法工程师",
            provider_hash="liepin-provider",
            contacts=("contact-hash-1",),
        ),
    )
    merged_again = index.upsert_candidate(
        resume_id="cts-detail-1",
        evidence_id="evidence-cts-detail",
        signals=_signals(provider_hash="cts-provider", contacts=("contact-hash-1",)),
    )

    assert merged.identity_id == merged_again.identity_id
    assert set(index.aliases_for(merged.identity_id)) >= {cts_identity.identity_id, liepin_identity.identity_id}


def test_identity_index_auto_merges_visible_name_with_strong_profile_corroborration() -> None:
    index = RuntimeCandidateIdentityIndex()
    cts_identity = index.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=("Tsinghua University",),
            chronology=("acme robotics:senior ai engineer:2024-present",),
            provider_hash="cts-provider",
        ),
    )
    liepin_identity = index.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="AI Engineer",
            school=("Tsinghua University",),
            chronology=("acme robotics:ai engineer:2024-present",),
            provider_hash="liepin-provider",
        ),
    )

    assert liepin_identity.identity_id == cts_identity.identity_id
    assert index.conflicts() == ()


def test_identity_index_records_medium_confidence_conflict_without_merge() -> None:
    index = RuntimeCandidateIdentityIndex()
    first = index.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=("Tsinghua University",),
            chronology=(),
            provider_hash="cts-provider",
        ),
    )
    second = index.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=(),
            chronology=(),
            provider_hash="liepin-provider",
        ),
    )

    assert second.identity_id != first.identity_id
    conflicts = index.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].match_score == 75
    assert set(conflicts[0].resume_ids) == {"cts-1", "liepin-1"}


def test_identity_index_removes_medium_conflict_after_later_strong_merge() -> None:
    index = RuntimeCandidateIdentityIndex()
    first = index.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=("Tsinghua University",),
            chronology=(),
            provider_hash="cts-provider",
        ),
    )
    second = index.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=(),
            chronology=(),
            provider_hash="liepin-provider",
        ),
    )

    assert second.identity_id != first.identity_id
    assert len(index.conflicts()) == 1

    index.upsert_candidate(
        resume_id="cts-detail-1",
        evidence_id="evidence-cts-detail",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=("Tsinghua University",),
            chronology=(),
            provider_hash="cts-provider",
            contacts=("contact-hash-1",),
        ),
    )
    index.upsert_candidate(
        resume_id="liepin-detail-1",
        evidence_id="evidence-liepin-detail",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=(),
            chronology=(),
            provider_hash="liepin-provider",
            contacts=("contact-hash-1",),
        ),
    )

    assert len(index.identities()) == 1
    assert index.conflicts() == ()


@pytest.mark.parametrize("masked_name", ["王**", "*明", "王某", "王女士", "W**", "Wang**", "候选人123", "匿名", "-", ""])
def test_masked_name_plus_company_and_title_does_not_auto_merge(masked_name: str) -> None:
    index = RuntimeCandidateIdentityIndex()
    visible = index.upsert_candidate(
        resume_id="cts-visible",
        evidence_id="evidence-cts",
        signals=_signals(name="王明", masked=False, provider_hash="cts-provider"),
    )
    masked = index.upsert_candidate(
        resume_id=f"liepin-{masked_name or 'blank'}",
        evidence_id=f"evidence-{masked_name or 'blank'}",
        signals=_signals(name=masked_name or None, masked=True, provider_hash="liepin-provider"),
    )

    assert visible.identity_id != masked.identity_id


def test_name_only_match_stays_separate_without_corroborration() -> None:
    index = RuntimeCandidateIdentityIndex()
    first = index.upsert_candidate(
        resume_id="resume-1",
        evidence_id="evidence-1",
        signals=_signals(name="王明", company=None, title=None, school=(), chronology=(), provider_hash="provider-1"),
    )
    second = index.upsert_candidate(
        resume_id="resume-2",
        evidence_id="evidence-2",
        signals=_signals(name="王明", company=None, title=None, school=(), chronology=(), provider_hash="provider-2"),
    )

    assert first.identity_id != second.identity_id


def test_identity_index_scores_only_candidate_fuzzy_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    index = RuntimeCandidateIdentityIndex()
    for item_number in range(50):
        index.upsert_candidate(
            resume_id=f"resume-{item_number}",
            evidence_id=f"evidence-{item_number}",
            signals=_signals(name=f"person-{item_number}", provider_hash=f"provider-{item_number}"),
        )

    calls: list[tuple[str | None, str | None]] = []
    original_match_score = source_lanes_module._identity_match_score

    def counting_match_score(left: RuntimeIdentitySignals, right: RuntimeIdentitySignals) -> int:
        calls.append((left.normalized_name, right.normalized_name))
        return original_match_score(left, right)

    monkeypatch.setattr(source_lanes_module, "_identity_match_score", counting_match_score)

    merged = index.upsert_candidate(
        resume_id="resume-new",
        evidence_id="evidence-new",
        signals=_signals(name="person-20", provider_hash="provider-new"),
    )

    assert index.identity_for_resume_id("resume-20") == merged.identity_id
    assert calls == [("person-20", "person-20")]


def test_canonical_resume_ignores_collected_at_when_older_content_arrives_later() -> None:
    candidates = {
        "older": _candidate("older"),
        "newer": _candidate("newer"),
    }
    normalized = {
        "older": _normalized("older", current_duration="2020-01 - 2022-12"),
        "newer": _normalized("newer", current_duration="2023-01 - 2025-06"),
    }
    evidence = [
        _evidence(
            "older-evidence",
            resume_id="older",
            source="cts",
            level="detail",
            collected_at="2026-07-01T00:00:00Z",
        ),
        _evidence(
            "newer-evidence",
            resume_id="newer",
            source="liepin",
            level="card",
            collected_at="2026-01-01T00:00:00Z",
        ),
    ]

    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("older", "newer"),
        candidates=candidates,
        normalized_store=normalized,
        evidence=evidence,
    )

    assert selection.canonical_resume_id == "newer"
    assert selection.equivalent_latest_resume_ids == ("newer",)
    assert selection.display_source_evidence_ids == ("newer-evidence",)
    assert selection.conflicting_resume_ids == ()
    assert selection.content_version_key
    assert "structured_work_newer" in selection.safe_reason_codes
    assert "structured_work_newer" in selection.to_public_payload()["safe_reason_codes"]


def test_canonical_resume_prefers_later_structured_work_chronology() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("2024", "2025"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("2024", "2025")},
        normalized_store={
            "2024": _normalized("2024", current_duration="2021-03 to 2024-08"),
            "2025": _normalized("2025", current_duration="2021-03 to 2025-02"),
        },
        evidence=[
            _evidence("e-2024", resume_id="2024", source="cts"),
            _evidence("e-2025", resume_id="2025", source="liepin"),
        ],
    )

    assert selection.canonical_resume_id == "2025"


def test_project_and_education_dates_do_not_make_work_content_newer() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("work-newer", "non-work-later"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("work-newer", "non-work-later")},
        normalized_store={
            "work-newer": _normalized("work-newer", current_duration="2021-01 - 2025-03"),
            "non-work-later": _normalized(
                "non-work-later",
                current_duration="2021-01 - 2024-12",
                project_duration="2025-12",
                education_duration="2026-06",
            ),
        },
        evidence=[],
    )

    assert selection.canonical_resume_id == "work-newer"


def test_consistent_same_latest_work_state_is_equivalent_despite_completeness() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("sparse", "complete"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("sparse", "complete")},
        normalized_store={
            "sparse": _normalized("sparse", current_duration="2023-10 - present", completeness=35),
            "complete": _normalized(
                "complete",
                current_duration="2023-10 - 至今",
                prior_work=(("旧公司", "工程师", "2020-01 - 2023-09"),),
                completeness=95,
            ),
        },
        evidence=[
            _evidence("e-sparse", resume_id="sparse", source="cts"),
            _evidence("e-complete", resume_id="complete", source="liepin"),
        ],
    )

    assert set(selection.equivalent_latest_resume_ids) == {"sparse", "complete"}
    assert selection.canonical_resume_id == "complete"
    assert set(selection.display_source_evidence_ids) == {"e-sparse", "e-complete"}
    assert selection.selected_evidence_id == "e-complete"
    assert selection.conflicting_resume_ids == ()
    assert "equivalent_latest_content" in selection.safe_reason_codes


def test_same_latest_marker_with_conflicting_current_role_is_version_conflict() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("alpha", "beta"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("alpha", "beta")},
        normalized_store={
            "alpha": _normalized(
                "alpha",
                current_company="甲公司",
                current_title="架构师",
                current_duration="2024-01 - present",
                completeness=30,
            ),
            "beta": _normalized(
                "beta",
                current_company="乙公司",
                current_title="总监",
                current_duration="2024-01 - present",
                completeness=90,
            ),
        },
        evidence=[
            _evidence("e-alpha", resume_id="alpha", source="cts"),
            _evidence("e-beta", resume_id="beta", source="liepin"),
        ],
    )

    assert len(selection.equivalent_latest_resume_ids) == 1
    assert selection.canonical_resume_id == "beta"
    assert len(selection.display_source_evidence_ids) == 1
    assert set(selection.conflicting_resume_ids) | set(selection.equivalent_latest_resume_ids) == {"alpha", "beta"}
    assert "resume_version_conflict" in selection.safe_reason_codes


def test_sparse_newer_work_content_beats_more_complete_older_content() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("sparse-new", "complete-old"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("sparse-new", "complete-old")},
        normalized_store={
            "sparse-new": _normalized("sparse-new", current_duration="2025-04", completeness=20),
            "complete-old": _normalized(
                "complete-old",
                current_duration="2024-12",
                prior_work=(("旧公司", "工程师", "2018-01 - 2024-11"),),
                completeness=100,
            ),
        },
        evidence=[],
    )

    assert selection.canonical_resume_id == "sparse-new"


def test_unknown_work_freshness_is_deterministic_and_not_semantically_equivalent() -> None:
    inputs = {
        "alpha": _normalized("alpha", current_company="甲公司", current_title="工程师"),
        "beta": _normalized("beta", current_company="乙公司", current_title="工程师"),
    }

    forward = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("alpha", "beta"),
        candidates={resume_id: _candidate(resume_id) for resume_id in inputs},
        normalized_store=inputs,
        evidence=[],
    )
    reverse = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("beta", "alpha"),
        candidates={resume_id: _candidate(resume_id) for resume_id in inputs},
        normalized_store=inputs,
        evidence=[],
    )

    assert forward.canonical_resume_id == reverse.canonical_resume_id
    assert forward.content_version_key == reverse.content_version_key
    assert len(forward.equivalent_latest_resume_ids) == 1
    assert forward.conflicting_resume_ids == ()
    assert len(forward.incomparable_resume_ids) == 1
    assert forward.to_public_payload()["incomparable_resume_ids"] == list(forward.incomparable_resume_ids)
    assert "content_freshness_unknown" in forward.safe_reason_codes


def test_unknown_work_freshness_ignores_completeness_and_input_order() -> None:
    equal_completeness = {
        "alpha": _normalized("alpha", current_company="甲公司", current_title="工程师", completeness=50),
        "beta": _normalized("beta", current_company="乙公司", current_title="总监", completeness=50),
    }
    baseline = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("alpha", "beta"),
        candidates={resume_id: _candidate(resume_id) for resume_id in equal_completeness},
        normalized_store=equal_completeness,
        evidence=[],
    )
    expected_id = baseline.canonical_resume_id
    other_id = next(resume_id for resume_id in equal_completeness if resume_id != expected_id)
    unequal_completeness = {
        expected_id: equal_completeness[expected_id].model_copy(update={"completeness_score": 10}),
        other_id: equal_completeness[other_id].model_copy(update={"completeness_score": 100}),
    }

    forward = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=(expected_id, other_id),
        candidates={resume_id: _candidate(resume_id) for resume_id in unequal_completeness},
        normalized_store=unequal_completeness,
        evidence=[],
    )
    reverse = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=(other_id, expected_id),
        candidates={resume_id: _candidate(resume_id) for resume_id in unequal_completeness},
        normalized_store=unequal_completeness,
        evidence=[],
    )

    assert forward.canonical_resume_id == expected_id
    assert reverse.canonical_resume_id == expected_id
    assert forward.content_version_key == baseline.content_version_key
    assert forward.conflicting_resume_ids == ()
    assert forward.incomparable_resume_ids == (other_id,)


def test_current_markers_share_one_freshness_layer_even_when_start_dates_differ() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("short-current", "long-current"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("short-current", "long-current")},
        normalized_store={
            "short-current": _normalized(
                "short-current", current_duration="2024-06 - present", completeness=30
            ),
            "long-current": _normalized(
                "long-current", current_duration="2020-01 - 至今", completeness=90
            ),
        },
        evidence=[],
    )

    assert set(selection.equivalent_latest_resume_ids) == {"short-current", "long-current"}
    assert selection.canonical_resume_id == "long-current"


def test_partially_overlapping_work_periods_with_different_roles_are_conflicting() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("alpha", "beta"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("alpha", "beta")},
        normalized_store={
            "alpha": _normalized(
                "alpha",
                current_duration="2024-01 - present",
                prior_work=(("甲公司", "工程师", "2020-01 - 2022-12"),),
                completeness=40,
            ),
            "beta": _normalized(
                "beta",
                current_duration="2024-01 - present",
                prior_work=(("乙公司", "总监", "2021-06 - 2022-12"),),
                completeness=90,
            ),
        },
        evidence=[],
    )

    assert selection.canonical_resume_id == "beta"
    assert selection.conflicting_resume_ids == ("alpha",)
    assert selection.incomparable_resume_ids == ()
    assert "resume_version_conflict" in selection.safe_reason_codes


def test_english_current_markers_require_word_boundaries() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("unknown", "dated"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("unknown", "dated")},
        normalized_store={
            "unknown": _normalized("unknown", current_duration="unknown"),
            "dated": _normalized("dated", current_duration="2025-01"),
        },
        evidence=[],
    )

    assert selection.canonical_resume_id == "dated"
    assert selection.conflicting_resume_ids == ()
    assert selection.incomparable_resume_ids == ("unknown",)


def test_negated_chinese_employment_phrase_is_not_a_current_marker() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("not-current", "current"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("not-current", "current")},
        normalized_store={
            "not-current": _normalized("not-current", current_duration="2025-01 - 不在职"),
            "current": _normalized("current", current_duration="2024-01 - 至今"),
        },
        evidence=[],
    )

    assert selection.canonical_resume_id == "current"


@pytest.mark.parametrize(
    "negated_duration",
    ("目前不在职", "现在不在职", "not current", "not presently employed"),
)
def test_explicitly_negated_employment_status_overrides_current_markers(negated_duration: str) -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("negated", "current"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("negated", "current")},
        normalized_store={
            "negated": _normalized("negated", current_duration=f"2025-01 - {negated_duration}"),
            "current": _normalized("current", current_duration="2024-01 - present"),
        },
        evidence=[],
    )

    assert selection.canonical_resume_id == "current"


def test_equivalent_latest_resumes_are_pairwise_materially_consistent() -> None:
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("sparse", "engineer", "director"),
        candidates={resume_id: _candidate(resume_id) for resume_id in ("sparse", "engineer", "director")},
        normalized_store={
            "sparse": _normalized(
                "sparse",
                current_company="acme",
                current_title="",
                current_duration="2024-01 - present",
                completeness=100,
            ),
            "engineer": _normalized(
                "engineer",
                current_company="acme",
                current_title="engineer",
                current_duration="2024-01 - present",
                completeness=60,
            ),
            "director": _normalized(
                "director",
                current_company="acme",
                current_title="director",
                current_duration="2024-01 - present",
                completeness=50,
            ),
        },
        evidence=[
            _evidence("e-sparse", resume_id="sparse", source="cts"),
            _evidence("e-engineer", resume_id="engineer", source="liepin"),
            _evidence("e-director", resume_id="director", source="liepin"),
        ],
    )

    assert selection.canonical_resume_id == "sparse"
    assert selection.equivalent_latest_resume_ids == ("engineer", "sparse")
    assert selection.conflicting_resume_ids == ("director",)
    assert selection.incomparable_resume_ids == ()
    assert set(selection.display_source_evidence_ids) == {"e-sparse", "e-engineer"}
    assert "e-director" not in selection.display_source_evidence_ids
