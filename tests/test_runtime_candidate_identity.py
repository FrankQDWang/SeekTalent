from __future__ import annotations

import pytest

from seektalent.models import (
    NormalizedResume,
    ResumeCandidate,
    RuntimeIdentitySignals,
    RuntimeSourceEvidence,
)
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
) -> NormalizedResume:
    return NormalizedResume(
        resume_id=resume_id,
        dedup_key=resume_id,
        candidate_name=name,
        headline=current_title,
        current_title=current_title,
        current_company=current_company,
        education_summary="南京邮电大学 硕士",
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


def test_canonical_resume_prefers_detail_then_freshness_completeness_and_provider_rank() -> None:
    candidates = {
        "card-old": _candidate("card-old"),
        "detail-old": _candidate("detail-old"),
        "detail-new": _candidate("detail-new"),
    }
    normalized = {
        "card-old": _normalized("card-old", completeness=100, score_source="card"),
        "detail-old": _normalized("detail-old", completeness=75, score_source="detail"),
        "detail-new": _normalized("detail-new", completeness=75, score_source="detail"),
    }
    evidence = [
        _evidence("card-evidence", resume_id="card-old", source="cts", level="card", provider_rank=1),
        _evidence(
            "detail-old-evidence",
            resume_id="detail-old",
            source="liepin",
            level="detail",
            provider_rank=3,
            collected_at="2026-05-14T00:00:00Z",
        ),
        _evidence(
            "detail-new-evidence",
            resume_id="detail-new",
            source="liepin",
            level="detail",
            provider_rank=2,
            collected_at="2026-05-15T00:00:00Z",
        ),
    ]

    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        resume_ids=("card-old", "detail-old", "detail-new"),
        candidates=candidates,
        normalized_store=normalized,
        evidence=evidence,
    )

    assert selection.canonical_resume_id == "detail-new"
    assert "detail_evidence" in selection.safe_reason_codes
