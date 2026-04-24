from __future__ import annotations

from pathlib import Path

from seektalent.controller.react_controller import render_controller_prompt
from seektalent.evaluation import render_judge_prompt
from seektalent.finalize.finalizer import render_finalize_prompt
from seektalent.models import (
    AgeRequirement,
    ControllerContext,
    CitySearchSummary,
    DegreeRequirement,
    ExperienceRequirement,
    GenderRequirement,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    NormalizedExperience,
    NormalizedResume,
    PreferenceSlots,
    QueryTermCandidate,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    ReflectionContext,
    ReflectionSummaryView,
    RequirementSheet,
    ResumeCandidate,
    RoundRetrievalPlan,
    RuntimeConstraint,
    ScoredCandidate,
    ScoringContext,
    ScoringFailure,
    ScoringPolicy,
    SearchAttempt,
    SearchObservation,
    SearchObservationView,
    SentQueryRecord,
    StopGuidance,
    TopPoolEntryView,
)
from seektalent.reflection.critic import render_reflection_prompt
from seektalent.requirements.extractor import render_requirements_prompt
from seektalent.scoring.scorer import render_scoring_prompt


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_term="python",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "retrieval"],
        preferred_capabilities=["RAG"],
        hard_constraints=HardConstraintSlots(locations=["上海市"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
                retrieval_role="role_anchor",
                queryability="admitted",
                family="role.python",
            ),
            QueryTermCandidate(
                term="retrieval",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="core_skill",
                queryability="admitted",
                family="skill.retrieval",
            ),
            QueryTermCandidate(
                term="internal roadmap",
                source="notes",
                category="expansion",
                priority=9,
                evidence="Notes",
                first_added_round=0,
                active=False,
                retrieval_role="score_only",
                queryability="blocked",
                family="blocked.internal",
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )


def _sent_query() -> SentQueryRecord:
    return SentQueryRecord(
        round_no=1,
        batch_no=1,
        requested_count=10,
        query_terms=["python", "retrieval"],
        keyword_query='python "retrieval"',
        source_plan_version=1,
        rationale="Initial recall.",
    )


def _scored_candidate(resume_id: str = "resume-1") -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=86,
        must_have_match_score=90,
        preferred_match_score=75,
        risk_score=20,
        risk_flags=["short tenure"],
        reasoning_summary="Strong Python fit with retrieval experience.",
        evidence=["python", "retrieval"],
        confidence="high",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["RAG"],
        negative_signals=[],
        strengths=["Strong Python"],
        weaknesses=["Short tenure"],
        source_round=1,
    )


def test_requirements_prompt_is_readable_text_not_full_input_truth_json() -> None:
    prompt = render_requirements_prompt(
        InputTruth(
            job_title="Senior Python Engineer",
            jd="Build Python retrieval systems.",
            notes="Shanghai preferred.",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        )
    )

    assert "TASK" in prompt
    assert "JOB TITLE" in prompt
    assert "JOB DESCRIPTION" in prompt
    assert "SOURCING NOTES" in prompt
    assert "Senior Python Engineer" in prompt
    assert "Build Python retrieval systems." in prompt
    assert "INPUT_TRUTH" not in prompt
    assert '"job_title_sha256"' not in prompt


def test_controller_prompt_contains_decision_brief_and_exact_data() -> None:
    sheet = _requirement_sheet()
    prompt = render_controller_prompt(
        ControllerContext(
            full_jd="JD text",
            full_notes="Notes text",
            requirement_sheet=sheet,
            round_no=2,
            min_rounds=1,
            max_rounds=4,
            retrieval_rounds_completed=1,
            rounds_remaining_after_current=2,
            budget_used_ratio=0.5,
            near_budget_limit=False,
            is_final_allowed_round=False,
            target_new=10,
            stop_guidance=StopGuidance(
                can_stop=False,
                reason="Need more candidates.",
                untried_admitted_families=["skill.retrieval"],
                top_pool_strength="weak",
            ),
            query_term_pool=sheet.initial_query_term_pool,
            current_top_pool=[
                TopPoolEntryView(
                    resume_id="resume-1",
                    fit_bucket="fit",
                    overall_score=86,
                    must_have_match_score=90,
                    risk_score=20,
                    matched_must_haves=["python"],
                    risk_flags=["short tenure"],
                    reasoning_summary="Strong Python fit.",
                )
            ],
            latest_search_observation=SearchObservationView(
                unique_new_count=2,
                shortage_count=8,
                fetch_attempt_count=1,
                city_search_summaries=[
                    CitySearchSummary(
                        city="上海市",
                        phase="priority",
                        batch_no=1,
                        requested_count=10,
                        unique_new_count=2,
                        shortage_count=8,
                        start_page=1,
                        next_page=2,
                        fetch_attempt_count=1,
                    )
                ],
            ),
            previous_reflection=ReflectionSummaryView(
                decision="continue",
                reflection_summary="Activate retrieval and continue.",
            ),
            latest_reflection_keyword_advice=ReflectionKeywordAdvice(
                suggested_activate_terms=["retrieval"],
                suggested_drop_terms=["internal roadmap"],
            ),
            latest_reflection_filter_advice=ReflectionFilterAdvice(
                suggested_drop_filter_fields=["position"]
            ),
            shortage_history=[8],
        )
    )

    assert "TASK" in prompt
    assert "DECISION STATE" in prompt
    assert "TERM BANK" in prompt
    assert "CURRENT TOP POOL" in prompt
    assert "PREVIOUS REFLECTION" in prompt
    assert "REFLECTION ADVICE" in prompt
    assert "suggested_activate_terms" in prompt
    assert "retrieval" in prompt
    assert "suggested_drop_filter_fields" in prompt
    assert "position" in prompt
    assert "STRUCTURED CONSTRAINTS" in prompt
    assert "locations" in prompt
    assert "quality_gate_status" in prompt
    assert "上海市" in prompt
    assert "CitySearchSummary(" not in prompt
    assert "EXACT DATA" in prompt
    assert "Need more candidates." in prompt
    assert "retrieval" in prompt
    assert "Activate retrieval and continue." in prompt
    assert '"action_options"' in prompt
    assert '"admitted_terms"' in prompt
    assert "CONTROLLER_CONTEXT" not in prompt


def test_controller_prompt_says_few_shot_terms_are_not_reusable() -> None:
    prompt = Path("src/seektalent/prompts/controller.md").read_text(encoding="utf-8")

    assert "few-shot terms are examples only" in prompt
    assert "must not be reused unless they are in the current active admitted term bank" in prompt
    assert "thought_summary should stay short within schema budget" in prompt
    assert "decision_rationale should be a concise audit summary within schema budget" in prompt


def test_reflection_prompt_contains_round_review_and_candidate_ids() -> None:
    sheet = _requirement_sheet()
    runtime_term_pool = [
        *sheet.initial_query_term_pool,
        QueryTermCandidate(
            term="Graph Search",
            source="reflection",
            category="tooling",
            priority=3,
            evidence="Runtime term",
            first_added_round=2,
            retrieval_role="framework_tool",
            queryability="admitted",
            family="skill.graph_search",
        ),
        QueryTermCandidate(
            term="Vector Search",
            source="reflection",
            category="tooling",
            priority=4,
            evidence="Runtime term",
            first_added_round=2,
            retrieval_role="framework_tool",
            queryability="admitted",
            family="skill.vector_search",
        ),
    ]
    prompt = render_reflection_prompt(
        ReflectionContext(
            round_no=2,
            full_jd="JD text",
            full_notes="Notes text",
            requirement_sheet=sheet,
            current_retrieval_plan=RoundRetrievalPlan(
                plan_version=2,
                round_no=2,
                query_terms=["python", "retrieval"],
                keyword_query='python "retrieval"',
                projected_cts_filters={"position": "backend"},
                runtime_only_constraints=[],
                location_execution_plan=LocationExecutionPlan(
                    mode="single",
                    allowed_locations=["上海市"],
                    preferred_locations=[],
                    priority_order=[],
                    balanced_order=["上海市"],
                    rotation_offset=0,
                    target_new=10,
                ),
                target_new=10,
                rationale="Search Python retrieval.",
            ),
            search_observation=SearchObservation(
                round_no=2,
                requested_count=10,
                raw_candidate_count=3,
                unique_new_count=2,
                shortage_count=8,
                fetch_attempt_count=1,
                new_resume_ids=["resume-1", "resume-2"],
            ),
            search_attempts=[
                SearchAttempt(
                    attempt_no=1,
                    requested_page=1,
                    requested_page_size=10,
                    raw_candidate_count=3,
                    batch_duplicate_count=1,
                    batch_unique_new_count=2,
                    cumulative_unique_new_count=2,
                    continue_refill=False,
                )
            ],
            top_candidates=[_scored_candidate("resume-1")],
            dropped_candidates=[_scored_candidate("resume-2")],
            scoring_failures=[
                ScoringFailure(
                    resume_id="resume-3",
                    branch_id="branch-1",
                    round_no=2,
                    attempts=1,
                    error_message="schema parse failed",
                )
            ],
            sent_query_history=[
                _sent_query(),
                SentQueryRecord(
                    round_no=2,
                    batch_no=1,
                    requested_count=10,
                    query_terms=[" graph   search "],
                    keyword_query='"graph search"',
                    source_plan_version=2,
                    rationale="Runtime term recall.",
                ),
            ],
            query_term_pool=runtime_term_pool,
        )
    )

    assert "TASK" in prompt
    assert "reflection_rationale" in prompt
    assert "ROUND RESULT" in prompt
    assert "CURRENT QUERY" in prompt
    assert "REQUIREMENTS" in prompt
    assert "JD text" in prompt
    assert "Notes text" in prompt
    assert "Senior Python Engineer" in prompt
    assert "TERM BANK" in prompt
    assert "skill.retrieval" in prompt
    assert "skill.vector_search" in prompt
    assert "Vector Search" in prompt
    assert "UNTRIED ADMITTED TERMS\nVector Search" in prompt
    assert "| Graph Search | skill.graph_search | framework_tool | admitted | True | 3 | reflection | yes |" in prompt
    assert "active" in prompt
    assert "TOP CANDIDATES" in prompt
    assert "DROPPED CANDIDATES" in prompt
    assert "UNTRIED ADMITTED TERMS" in prompt
    assert "EXACT DATA" in prompt
    assert "raw=3" in prompt
    assert "new=2" in prompt
    assert "resume-1" in prompt
    assert "resume-2" in prompt
    assert "schema parse failed" in prompt
    assert "REFLECTION_CONTEXT" not in prompt


def test_reflection_prompt_mentions_rationale_schema_budget() -> None:
    prompt = Path("src/seektalent/prompts/reflection.md").read_text(encoding="utf-8")

    assert "reflection_rationale should be a concise audit summary within schema budget" in prompt
    assert "not a step-by-step reasoning transcript" in prompt


def test_scoring_prompt_contains_policy_resume_card_and_exact_resume_id() -> None:
    prompt = render_scoring_prompt(
        ScoringContext(
            round_no=2,
            scoring_policy=ScoringPolicy(
                role_title="Senior Python Engineer",
                role_summary="Build resume matching workflows.",
                must_have_capabilities=["python"],
                preferred_capabilities=["RAG"],
                exclusion_signals=["no backend"],
                hard_constraints=HardConstraintSlots(
                    locations=["上海市"],
                    school_names=["复旦大学"],
                    degree_requirement=DegreeRequirement(canonical_degree="本科及以上", raw_text="本科及以上"),
                    experience_requirement=ExperienceRequirement(min_years=3, max_years=5, raw_text="3-5年"),
                    gender_requirement=GenderRequirement(canonical_gender="男", raw_text="男性优先"),
                    age_requirement=AgeRequirement(max_age=35, raw_text="35岁以下"),
                    company_names=["阿里巴巴"],
                ),
                preferences=PreferenceSlots(
                    preferred_companies=["字节跳动"],
                    preferred_domains=["AI"],
                    preferred_backgrounds=["大厂"],
                    preferred_query_terms=["RAG"],
                ),
                scoring_rationale="Score Python fit first.",
            ),
            normalized_resume=NormalizedResume(
                resume_id="resume-1",
                dedup_key="resume-1",
                candidate_name="Alice",
                current_title="Python Engineer",
                current_company="Example Co",
                years_of_experience=5,
                locations=["上海市"],
                education_summary="本科",
                skills=["python", "rag"],
                recent_experiences=[
                    NormalizedExperience(
                        title="Backend Engineer",
                        company="Example Co",
                        duration="2020-2024",
                        summary="Built retrieval APIs.",
                    )
                ],
                raw_text_excerpt="Python retrieval trace",
                completeness_score=90,
                source_round=2,
            ),
            requirement_sheet_sha256="requirement-sheet-hash",
            runtime_only_constraints=[
                RuntimeConstraint(
                    field="age_requirement",
                    normalized_value=["max=35"],
                    source="notes",
                    rationale="Age not projected to CTS.",
                    blocking=False,
                )
            ],
        )
    )

    assert "TASK" in prompt
    assert "SCORING POLICY" in prompt
    assert "RESUME CARD" in prompt
    assert "RECENT EXPERIENCE" in prompt
    assert "EXACT DATA" in prompt
    assert "Senior Python Engineer" in prompt
    assert "Hard constraints" in prompt
    assert "本科及以上" in prompt
    assert "3-5年" in prompt
    assert "35岁以下" in prompt
    assert "阿里巴巴" in prompt
    assert "Preferences" in prompt
    assert "字节跳动" in prompt
    assert "Runtime-only constraints" in prompt
    assert "age_requirement" in prompt
    assert "Python retrieval trace" in prompt
    assert '"resume_id": "resume-1"' in prompt
    assert "SCORING_CONTEXT" not in prompt


def test_finalizer_prompt_contains_ranked_list_and_exact_order() -> None:
    prompt = render_finalize_prompt(
        run_id="run-1",
        run_dir="/tmp/run-1",
        rounds_executed=2,
        stop_reason="controller_stop",
        ranked_candidates=[_scored_candidate("resume-1"), _scored_candidate("resume-2")],
    )

    assert "TASK" in prompt
    assert "FINALIZATION STATE" in prompt
    assert "RANKED CANDIDATES" in prompt
    assert "EXACT DATA" in prompt
    assert "1. resume-1" in prompt
    assert "2. resume-2" in prompt
    assert "matched_must_haves" in prompt
    assert "matched_preferences" in prompt
    assert "strengths" in prompt
    assert "weaknesses" in prompt
    assert "risk_flags" in prompt
    assert "short tenure" in prompt
    assert '"candidate_order"' in prompt
    assert '"stop_reason": "controller_stop"' in prompt
    assert "FINALIZATION_CONTEXT" not in prompt


def test_judge_prompt_uses_readable_resume_snapshot_without_raw_dump() -> None:
    prompt = render_judge_prompt(
        jd="Build Python retrieval systems.",
        notes="Shanghai preferred.",
        candidate=ResumeCandidate(
            resume_id="resume-1",
            source_resume_id="source-1",
            snapshot_sha256="snapshot-1",
            dedup_key="resume-1",
            source_round=1,
            now_location="上海市",
            work_year=5,
            expected_job_category="Python Engineer",
            education_summaries=["本科"],
            work_experience_summaries=["Built retrieval APIs."],
            project_names=["RAG Platform"],
            work_summaries=["Python backend retrieval work."],
            search_text="Python retrieval engineer",
            raw={"irrelevant_secret": "do-not-dump"},
        ),
        snapshot_hash="snapshot-1",
    )

    assert "TASK" in prompt
    assert "JOB DESCRIPTION" in prompt
    assert "NOTES" in prompt
    assert "RESUME SNAPSHOT" in prompt
    assert "EXACT DATA" in prompt
    assert "Python retrieval engineer" in prompt
    assert '"resume_id": "resume-1"' in prompt
    assert "do-not-dump" not in prompt
    assert "RESUME_SNAPSHOT" not in prompt
