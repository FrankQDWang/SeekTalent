from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from cv_match.agent.finalize_agent import FinalizeAgent
from cv_match.agent.reflection_agent import ReflectionAgent
from cv_match.agent.scoring_agent import ScoringAgent
from cv_match.agent.strategy_agent import StrategyAgent
from cv_match.clients.cts_client import CTSClient, CTSClientProtocol, MockCTSClient
from cv_match.config import AppSettings
from cv_match.models import (
    CTSQuery,
    FinalResult,
    NormalizedResume,
    ResumeCandidate,
    RoundResult,
    RunContextSnapshot,
    ScoredCandidate,
    ScoringContext,
    scored_candidate_sort_key,
    unique_strings,
)
from cv_match.normalization import ResumeNormalizer
from cv_match.prompting import PromptRegistry
from cv_match.tracing import RunTracer


@dataclass
class RunArtifacts:
    final_result: FinalResult
    final_markdown: str
    run_id: str
    run_dir: Path
    trace_log_path: Path


class MatchRunner:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.prompts = PromptRegistry(settings.prompt_dir)
        prompt_map = self.prompts.load_many(
            ["strategy_extraction", "scoring", "reflection", "finalize"]
        )
        self.strategy_agent = StrategyAgent(settings, prompt_map["strategy_extraction"])
        self.scoring_agent = ScoringAgent(settings, prompt_map["scoring"])
        self.reflection_agent = ReflectionAgent(settings, prompt_map["reflection"])
        self.finalize_agent = FinalizeAgent(settings, prompt_map["finalize"])
        self.resume_normalizer = ResumeNormalizer()
        self.cts_client: CTSClientProtocol = MockCTSClient(settings) if settings.mock_cts else CTSClient(settings)

    def run(self, *, jd: str, notes: str) -> RunArtifacts:
        tracer = RunTracer(self.settings.runs_path)
        candidate_store: dict[str, ResumeCandidate] = {}
        round_results: list[RoundResult] = []
        seen_resume_ids: set[str] = set()
        top_scored: list[ScoredCandidate] = []
        stop_reason = "max_rounds_reached"
        consecutive_shortage_rounds = 0
        run_config = {
            "settings": self.settings.model_dump(mode="json"),
            "llm_backend_mode": self.settings.llm_backend_mode,
            "selected_openapi_file": str(self.settings.spec_file),
            "prompt_hashes": self.prompts.prompt_hashes(),
            "prompt_files": self.prompts.prompt_files(),
        }
        tracer.write_json("run_config.json", run_config)
        input_snapshot = {
            "jd_chars": len(jd),
            "notes_chars": len(notes),
            "jd_sha256": hashlib.sha256(jd.encode("utf-8")).hexdigest(),
            "notes_sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
            "jd_preview": self._preview_text(jd),
            "notes_preview": self._preview_text(notes),
        }
        tracer.write_json("input_snapshot.json", input_snapshot)
        tracer.emit(
            "run_started",
            summary="Starting deterministic resume matching run.",
            payload={
                "mock_cts": self.settings.mock_cts,
                "llm_backend_mode": self.settings.llm_backend_mode,
            },
        )
        tracer.emit(
            "user_input_captured",
            summary=(
                f"Captured sanitized input snapshot; jd_chars={len(jd)}, notes_chars={len(notes)}. "
                f"JD preview: {input_snapshot['jd_preview']}"
            ),
            payload=input_snapshot,
        )
        strategy = self.strategy_agent.extract(jd=jd, notes=notes)
        tracer.emit(
            "strategy_extracted",
            model=self.settings.strategy_model,
            summary=strategy.search_rationale,
            payload=strategy.model_dump(mode="json"),
        )

        for round_no in range(1, self.settings.max_rounds + 1):
            round_stop_reason = None
            target_new = 10 if round_no == 1 else 5
            query = self._build_query(strategy=strategy, seen_ids=sorted(seen_resume_ids), target_new=target_new)
            tracer.emit(
                "cts_query_built",
                round_no=round_no,
                summary=(
                    f"Built query with {len(query.keywords)} keywords, "
                    f"{len(query.hard_filters)} hard filters, "
                    f"{len(query.soft_filters)} soft filters, page_size={query.page_size}."
                ),
                payload=query.model_dump(mode="json"),
            )
            tracer.emit(
                "tool_called",
                round_no=round_no,
                tool_name="cts.search_candidates",
                summary=query.keyword_query,
            )
            try:
                search_result = self.cts_client.search(
                    query,
                    round_no=round_no,
                    trace_id=f"{tracer.run_id}-r{round_no}",
                )
            except Exception as exc:  # noqa: BLE001
                tracer.emit(
                    "tool_failed",
                    round_no=round_no,
                    tool_name="cts.search_candidates",
                    summary=str(exc),
                )
                tracer.close()
                raise
            tracer.emit(
                "tool_succeeded",
                round_no=round_no,
                tool_name="cts.search_candidates",
                latency_ms=search_result.latency_ms,
                summary=(
                    f"{search_result.response_message}; "
                    f"raw_candidate_count={search_result.raw_candidate_count}; total={search_result.total}"
                ),
                payload={
                    "request_payload": search_result.request_payload,
                    "adapter_notes": search_result.adapter_notes,
                    "raw_candidate_count": search_result.raw_candidate_count,
                    "total": search_result.total,
                },
            )
            new_candidates, duplicate_count = self._dedup_new_candidates(
                candidates=search_result.candidates,
                seen_ids=seen_resume_ids,
            )
            for candidate in new_candidates:
                candidate_store[candidate.resume_id] = candidate
            shortage_count = max(0, target_new - len(new_candidates))
            consecutive_shortage_rounds = consecutive_shortage_rounds + 1 if shortage_count else 0
            tracer.emit(
                "dedup_applied",
                round_no=round_no,
                summary=f"Removed {duplicate_count} duplicates; shortage={shortage_count}.",
                payload={
                    "seen_resume_ids_before_round": sorted(seen_resume_ids),
                    "new_resume_ids": [candidate.resume_id for candidate in new_candidates],
                },
            )
            seen_resume_ids.update(candidate.resume_id for candidate in new_candidates)
            scoring_pool = self._build_scoring_pool(
                round_no=round_no,
                top_scored=top_scored,
                new_candidates=new_candidates,
                candidate_store=candidate_store,
            )
            scoring_context = ScoringContext(
                round_no=round_no,
                must_have_keywords=strategy.must_have_keywords,
                preferred_keywords=strategy.preferred_keywords,
                negative_keywords=strategy.negative_keywords,
                hard_filters=strategy.hard_filters,
                soft_filters=strategy.soft_filters,
                scoring_rationale=strategy.search_rationale,
            )
            normalized_scoring_pool = self._normalize_scoring_pool(
                round_no=round_no,
                scoring_pool=scoring_pool,
                tracer=tracer,
            )
            tracer.emit(
                "scoring_fanout_started",
                round_no=round_no,
                summary=(
                    f"Scoring {len(normalized_scoring_pool)} resumes with max_concurrency="
                    f"{self.settings.scoring_max_concurrency}."
                ),
            )
            scored_candidates, scoring_failures = self.scoring_agent.score_candidates_parallel(
                candidates=normalized_scoring_pool,
                context=scoring_context,
                tracer=tracer,
            )
            retried_successes = sum(1 for item in scored_candidates if item.retry_count > 0)
            tracer.emit(
                "scoring_fanin_completed",
                round_no=round_no,
                summary=(
                    f"Scored {len(scored_candidates)} resumes; "
                    f"retried_successes={retried_successes}; failures={len(scoring_failures)}."
                ),
                payload={
                    "successful_scores": len(scored_candidates) - retried_successes,
                    "retried_successes": retried_successes,
                    "final_failures": len(scoring_failures),
                },
            )
            ranked = sorted(scored_candidates, key=scored_candidate_sort_key)
            top_scored = ranked[:5]
            tracer.emit(
                "top5_updated",
                round_no=round_no,
                summary=", ".join(candidate.resume_id for candidate in top_scored) or "No scored resumes.",
                payload={"top5_ids": [candidate.resume_id for candidate in top_scored]},
            )
            dropped = [candidate for candidate in ranked if candidate.resume_id not in {item.resume_id for item in top_scored}]
            reflection = None
            if self.settings.enable_reflection:
                tracer.emit(
                    "reflection_started",
                    round_no=round_no,
                    model=self.settings.reflection_model,
                    summary="Starting round reflection.",
                )
                reflection = self.reflection_agent.reflect(
                    round_no=round_no,
                    strategy=strategy,
                    new_candidate_summaries=[candidate.compact_summary() for candidate in new_candidates],
                    scored_candidates=ranked,
                    top_candidates=top_scored,
                    dropped_candidates=dropped,
                    shortage_count=shortage_count,
                    scoring_failure_count=len(scoring_failures),
                )
                strategy, changes = self._apply_reflection(strategy=strategy, reflection=reflection)
                reflection = reflection.model_copy(update={"strategy_changes": changes})
                effective_stop_reason = None
                if round_no >= self.settings.min_rounds and reflection.decision == "stop":
                    stop_reason = reflection.stop_reason or "reflection_stop"
                    round_stop_reason = stop_reason
                    effective_stop_reason = round_stop_reason
                tracer.emit(
                    "reflection_decision",
                    round_no=round_no,
                    model=self.settings.reflection_model,
                    stop_reason=effective_stop_reason,
                    summary=reflection.reflection_summary,
                    payload=reflection.model_dump(mode="json"),
                )

            if (
                round_no >= self.settings.min_rounds
                and consecutive_shortage_rounds >= 2
                and not (self.settings.enable_reflection and reflection is not None and reflection.decision == "stop")
            ):
                stop_reason = "insufficient_new_candidates"
                round_stop_reason = stop_reason

            snapshot = RunContextSnapshot(
                run_id=tracer.run_id,
                run_dir=str(tracer.run_dir),
                round_no=round_no,
                seen_resume_ids=sorted(seen_resume_ids),
                current_top_ids=[candidate.resume_id for candidate in top_scored],
                strategy=strategy,
                prompt_hashes=self.prompts.prompt_hashes(),
                model_settings={
                    "strategy_model": self.settings.strategy_model,
                    "scoring_model": self.settings.scoring_model,
                    "finalize_model": self.settings.finalize_model,
                    "reflection_model": self.settings.reflection_model,
                    "reasoning_effort": self.settings.reasoning_effort,
                    "llm_backend_mode": self.settings.llm_backend_mode,
                },
                reflection_enabled=self.settings.enable_reflection,
            )
            round_results.append(
                RoundResult(
                    round_no=round_no,
                    query=query,
                    new_candidates=new_candidates,
                    normalized_resumes=normalized_scoring_pool,
                    scoring_pool_resume_ids=[candidate.resume_id for candidate in normalized_scoring_pool],
                    scored_candidates=ranked,
                    scoring_failures=scoring_failures,
                    top_candidates=top_scored,
                    reflection=reflection,
                    shortage_count=shortage_count,
                    duplicate_count=duplicate_count,
                    stop_reason=round_stop_reason,
                    context_snapshot=snapshot,
                )
            )
            if round_no >= self.settings.min_rounds:
                if self.settings.enable_reflection and reflection is not None and reflection.decision == "stop":
                    break
                if consecutive_shortage_rounds >= 2:
                    break

        final_result = self.finalize_agent.finalize(
            run_id=tracer.run_id,
            run_dir=str(tracer.run_dir),
            rounds_executed=len(round_results),
            stop_reason=stop_reason,
            ranked_candidates=top_scored,
        )
        final_markdown = self._render_final_markdown(final_result)
        tracer.write_json("round_summaries.json", [item.model_dump(mode="json") for item in round_results])
        tracer.write_json("final_candidates.json", final_result.model_dump(mode="json"))
        tracer.emit(
            "final_answer_created",
            summary=f"Prepared final shortlist with {len(final_result.candidates)} candidates.",
        )
        tracer.write_text("final_answer.md", final_markdown)
        tracer.emit(
            "run_finished",
            stop_reason=stop_reason,
            summary=f"Run completed after {len(round_results)} rounds.",
        )
        tracer.close()
        return RunArtifacts(
            final_result=final_result,
            final_markdown=final_markdown,
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            trace_log_path=tracer.trace_log_path,
        )

    def _preview_text(self, text: str, limit: int = 180) -> str:
        collapsed = re.sub(r"\s+", " ", text).strip()
        if len(collapsed) <= limit:
            return collapsed
        return f"{collapsed[:limit].rstrip()}..."

    def _build_query(self, *, strategy: object, seen_ids: list[str], target_new: int) -> CTSQuery:
        query_keywords = unique_strings(strategy.retrieval_keywords)
        return CTSQuery(
            keywords=query_keywords,
            keyword_query=" ".join(query_keywords),
            hard_filters=strategy.hard_filters,
            soft_filters=strategy.soft_filters,
            exclude_ids=seen_ids,
            page=1,
            page_size=target_new,
            rationale=strategy.search_rationale,
        )

    def _dedup_new_candidates(
        self,
        *,
        candidates: list[ResumeCandidate],
        seen_ids: set[str],
    ) -> tuple[list[ResumeCandidate], int]:
        deduped: list[ResumeCandidate] = []
        local_seen = set(seen_ids)
        duplicates = 0
        for candidate in candidates:
            if candidate.resume_id in local_seen:
                duplicates += 1
                continue
            local_seen.add(candidate.resume_id)
            deduped.append(candidate)
        return deduped, duplicates

    def _build_scoring_pool(
        self,
        *,
        round_no: int,
        top_scored: list[ScoredCandidate],
        new_candidates: list[ResumeCandidate],
        candidate_store: dict[str, ResumeCandidate],
    ) -> list[ResumeCandidate]:
        if round_no == 1:
            return list(new_candidates)
        pool: list[ResumeCandidate] = []
        used_ids: set[str] = set()
        for scored in top_scored:
            candidate = candidate_store.get(scored.resume_id)
            if candidate is None or candidate.resume_id in used_ids:
                continue
            pool.append(candidate)
            used_ids.add(candidate.resume_id)
        for candidate in new_candidates:
            if candidate.resume_id in used_ids:
                continue
            pool.append(candidate)
            used_ids.add(candidate.resume_id)
        return pool

    def _normalize_scoring_pool(
        self,
        *,
        round_no: int,
        scoring_pool: list[ResumeCandidate],
        tracer: RunTracer,
    ) -> list[NormalizedResume]:
        normalized_pool: list[NormalizedResume] = []
        for candidate in scoring_pool:
            tracer.emit(
                "resume_normalization_started",
                round_no=round_no,
                resume_id=candidate.resume_id,
                summary=candidate.compact_summary(),
            )
            normalized = self.resume_normalizer.normalize(candidate)
            if (
                normalized.completeness_score < 70
                or normalized.used_fallback_id
                or normalized.missing_fields
                or normalized.normalization_notes
            ):
                tracer.emit(
                    "resume_normalization_warning",
                    round_no=round_no,
                    resume_id=normalized.resume_id,
                    summary=(
                        f"completeness={normalized.completeness_score}, "
                        f"missing={len(normalized.missing_fields)}, "
                        f"fallback_id={normalized.used_fallback_id}"
                    ),
                    payload={
                        "completeness_score": normalized.completeness_score,
                        "missing_fields_count": len(normalized.missing_fields),
                        "used_fallback_id": normalized.used_fallback_id,
                        "missing_fields": normalized.missing_fields,
                        "normalization_notes": normalized.normalization_notes,
                    },
                )
            tracer.emit(
                "resume_normalized",
                round_no=round_no,
                resume_id=normalized.resume_id,
                summary=normalized.compact_summary(),
                payload={
                    "completeness_score": normalized.completeness_score,
                    "missing_fields_count": len(normalized.missing_fields),
                    "used_fallback_id": normalized.used_fallback_id,
                },
            )
            normalized_pool.append(normalized)
        return normalized_pool

    def _apply_reflection(self, *, strategy: object, reflection: object) -> tuple[object, list[str]]:
        next_hard_filters = strategy.hard_filters
        if reflection.adjust_hard_filters:
            next_hard_filters = reflection.adjust_hard_filters
        next_soft_filters = strategy.soft_filters
        if reflection.adjust_soft_filters:
            next_soft_filters = reflection.adjust_soft_filters
        next_strategy = strategy.model_copy(
            update={
                "preferred_keywords": unique_strings(strategy.preferred_keywords + reflection.adjust_keywords),
                "negative_keywords": unique_strings(strategy.negative_keywords + reflection.adjust_negative_keywords),
                "hard_filters": next_hard_filters,
                "soft_filters": next_soft_filters,
                "strategy_version": strategy.strategy_version + 1,
            }
        ).normalized()
        changes: list[str] = []
        added_keywords = [item for item in next_strategy.preferred_keywords if item not in strategy.preferred_keywords]
        added_negatives = [item for item in next_strategy.negative_keywords if item not in strategy.negative_keywords]
        if added_keywords:
            changes.append(f"Added retrieval keywords: {', '.join(added_keywords)}.")
        if added_negatives:
            changes.append(f"Added negative keywords: {', '.join(added_negatives)}.")
        if next_strategy.hard_filters != strategy.hard_filters:
            changes.append("Updated hard filters.")
        if next_strategy.soft_filters != strategy.soft_filters:
            changes.append("Updated soft filters.")
        if not changes:
            changes.append("No strategy changes.")
        return next_strategy, changes

    def _render_final_markdown(self, final_result: FinalResult) -> str:
        lines = [
            f"# Final Shortlist",
            "",
            f"- Run ID: `{final_result.run_id}`",
            f"- Rounds: `{final_result.rounds_executed}`",
            f"- Stop reason: `{final_result.stop_reason}`",
            "",
            final_result.summary,
            "",
        ]
        for candidate in final_result.candidates:
            lines.extend(
                [
                    f"## Rank {candidate.rank}: `{candidate.resume_id}`",
                    "",
                    f"- Score: `{candidate.final_score}`",
                    f"- Fit bucket: `{candidate.fit_bucket}`",
                    f"- Source round: `{candidate.source_round}`",
                    f"- Match summary: {candidate.match_summary}",
                    f"- Must-have hits: {', '.join(candidate.matched_must_haves) or 'None'}",
                    f"- Preference hits: {', '.join(candidate.matched_preferences) or 'None'}",
                    f"- Risk flags: {', '.join(candidate.risk_flags) or 'None'}",
                    f"- Why selected: {candidate.why_selected}",
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"
