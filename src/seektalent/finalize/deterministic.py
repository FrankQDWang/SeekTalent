from __future__ import annotations

from seektalent.models import FinalCandidate, FinalResult, FinalizeContext, RuntimeEvidenceLevel, ScoredCandidate


def build_deterministic_final_result(finalize_context: FinalizeContext) -> FinalResult:
    candidates = [
        FinalCandidate(
            resume_id=candidate.resume_id,
            rank=rank,
            final_score=candidate.overall_score,
            fit_bucket=candidate.fit_bucket,
            source_provider=candidate.source_provider,
            evidence_level=_evidence_level(candidate),
            detail_open_status=_detail_open_status(candidate),
            score_evidence_source=candidate.score_evidence_source,
            card_scorecard_ref=candidate.card_scorecard_ref,
            detail_scorecard_ref=candidate.detail_scorecard_ref,
            detail_open_reason=candidate.detail_open_reason,
            detail_open_policy_version=candidate.detail_open_policy_version,
            match_summary=_match_summary(candidate),
            strengths=candidate.strengths,
            weaknesses=candidate.weaknesses,
            matched_must_haves=candidate.matched_must_haves,
            matched_preferences=candidate.matched_preferences,
            risk_flags=candidate.risk_flags,
            why_selected=_why_selected(candidate),
            source_round=candidate.source_round,
        )
        for rank, candidate in enumerate(finalize_context.top_candidates, start=1)
    ]
    return FinalResult(
        run_id=finalize_context.run_id,
        run_dir=finalize_context.run_dir,
        rounds_executed=finalize_context.rounds_executed,
        stop_reason=finalize_context.stop_reason,
        candidates=candidates,
        summary=_summary(finalize_context),
    )


def _summary(context: FinalizeContext) -> str:
    count = len(context.top_candidates)
    return f"Selected {count} final candidate{'s' if count != 1 else ''} by deterministic runtime ranking."


def _match_summary(candidate: ScoredCandidate) -> str:
    if candidate.reasoning_summary.strip():
        return candidate.reasoning_summary.strip()
    return f"{candidate.resume_id} scored {candidate.overall_score} with fit bucket {candidate.fit_bucket}."


def _why_selected(candidate: ScoredCandidate) -> str:
    parts: list[str] = [f"Ranked by runtime score {candidate.overall_score}."]
    if candidate.matched_must_haves:
        parts.append("Matched must-haves: " + ", ".join(candidate.matched_must_haves[:4]) + ".")
    if candidate.risk_flags:
        parts.append("Risk flags: " + ", ".join(candidate.risk_flags[:3]) + ".")
    return " ".join(parts)


def _evidence_level(candidate: ScoredCandidate) -> RuntimeEvidenceLevel:
    if candidate.detail_scorecard_ref or candidate.score_evidence_source == "detail_enriched":
        return "detail"
    return "card"


def _detail_open_status(candidate: ScoredCandidate) -> str:
    if candidate.detail_scorecard_ref or candidate.score_evidence_source == "detail_enriched":
        return "opened"
    if candidate.source_provider == "cts":
        return "not_supported"
    return "not_opened"
