from __future__ import annotations

from collections import defaultdict

from seektalent_ui.models import (
    WorkbenchFinalTopCandidateEvidenceResponse,
    WorkbenchFinalTopCandidateResponse,
)
from seektalent_ui.candidate_identity import (
    public_identity_id,
    workbench_candidate_field_identity_keys,
    workbench_resume_freshness_key,
)
from seektalent_ui.workbench_store import WorkbenchCandidateEvidence, WorkbenchCandidateReviewItem


_EVIDENCE_RANK = {"card": 0, "detail": 1, "final": 2}


def project_final_top_candidates(items: list[WorkbenchCandidateReviewItem], *, limit: int = 10) -> list[WorkbenchFinalTopCandidateResponse]:
    groups: dict[str, list[WorkbenchCandidateReviewItem]] = defaultdict(list)
    key_to_group: dict[str, str] = {}
    for item in items:
        keys = _identity_keys(item)
        existing_group_ids = [key_to_group[key] for key in keys if key in key_to_group]
        group_id = min(existing_group_ids) if existing_group_ids else keys[0]
        for old_group_id in sorted(set(existing_group_ids)):
            if old_group_id == group_id:
                continue
            groups[group_id].extend(groups.pop(old_group_id, []))
            for key, mapped_group_id in list(key_to_group.items()):
                if mapped_group_id == old_group_id:
                    key_to_group[key] = group_id
        groups[group_id].append(item)
        for key in keys:
            key_to_group[key] = group_id

    ranked_items = [_project_group(identity_id, group) for identity_id, group in groups.items()]
    ranked_items.sort(
        key=lambda item: (
            item.aggregateScore if item.aggregateScore is not None else -1,
            _EVIDENCE_RANK[item.evidenceLevel],
            item.canonicalReviewItemId,
        ),
        reverse=True,
    )
    return [item.model_copy(update={"rank": index + 1}) for index, item in enumerate(ranked_items[:limit])]


def _identity_keys(item: WorkbenchCandidateReviewItem) -> tuple[str, ...]:
    keys: list[str] = []
    runtime_identity_ids = sorted({evidence.runtime_identity_id for evidence in item.evidence if evidence.runtime_identity_id})
    keys.extend(f"identity:{identity_id}" for identity_id in runtime_identity_ids)
    provider_hashes = sorted(
        {
            (evidence.source_kind, evidence.provider_candidate_key_hash)
            for evidence in item.evidence
            if evidence.provider_candidate_key_hash
        }
    )
    keys.extend(f"provider:{source_kind}:{provider_hash}" for source_kind, provider_hash in provider_hashes)
    keys.extend(
        workbench_candidate_field_identity_keys(
            display_name=item.display_name,
            title=item.title,
            company=item.company,
            location=item.location,
            summary=item.summary,
        )
    )
    return tuple(keys or [f"review:{item.review_item_id}"])


def _project_group(identity_id: str, group: list[WorkbenchCandidateReviewItem]) -> WorkbenchFinalTopCandidateResponse:
    canonical = max(
        group,
        key=_canonical_sort_key,
    )
    best_score_item = max(group, key=_score_sort_key)
    rank_score = best_score_item.aggregate_score
    evidence = [evidence for item in group for evidence in item.evidence]
    return WorkbenchFinalTopCandidateResponse(
        reviewItemId=canonical.review_item_id,
        runtimeIdentityId=public_identity_id(identity_id),
        canonicalReviewItemId=canonical.review_item_id,
        mergedReviewItemIds=sorted(item.review_item_id for item in group),
        rank=0,
        displayName=canonical.display_name,
        title=canonical.title,
        company=canonical.company,
        location=canonical.location,
        summary=canonical.summary,
        aggregateScore=rank_score,
        fitBucket=best_score_item.fit_bucket,
        whySelected=canonical.why_selected or canonical.summary,
        riskFlags=canonical.missing_risks,
        matchedMustHaves=canonical.matched_must_haves,
        matchedPreferences=canonical.matched_preferences,
        strengths=canonical.strengths,
        weaknesses=canonical.weaknesses,
        sourceRound=canonical.source_round,
        sourceBadges=_merged_source_badges(group),
        evidenceLevel=canonical.evidence_level,
        sourceEvidence=[_evidence_response(item) for item in evidence],
    )


def _canonical_sort_key(item: WorkbenchCandidateReviewItem) -> tuple[tuple[int, int, int], int, int, str]:
    freshness = workbench_resume_freshness_key(
        item.title,
        item.company,
        item.summary,
        " ".join(item.strengths),
        " ".join(item.missing_risks),
    )
    return (
        freshness,
        _EVIDENCE_RANK[item.evidence_level],
        item.aggregate_score if item.aggregate_score is not None else -1,
        item.updated_at,
    )


def _score_sort_key(item: WorkbenchCandidateReviewItem) -> tuple[int, int, str]:
    return (
        item.aggregate_score if item.aggregate_score is not None else -1,
        _EVIDENCE_RANK[item.evidence_level],
        item.updated_at,
    )


def _merged_source_badges(group: list[WorkbenchCandidateReviewItem]) -> list[str]:
    result: list[str] = []
    for item in group:
        for badge in item.source_badges:
            if badge not in result and badge != "Multiple sources":
                result.append(badge)
    source_kinds = {evidence.source_kind for item in group for evidence in item.evidence}
    if len(source_kinds) > 1:
        result.append("Multiple sources")
    return result


def _evidence_response(evidence: WorkbenchCandidateEvidence) -> WorkbenchFinalTopCandidateEvidenceResponse:
    return WorkbenchFinalTopCandidateEvidenceResponse(
        evidenceId=evidence.evidence_id,
        sourceRunId=evidence.source_run_id,
        sourceKind=evidence.source_kind,
        evidenceLevel=evidence.evidence_level,
        score=evidence.score,
        fitBucket=evidence.fit_bucket,
    )
