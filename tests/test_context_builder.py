import pytest

from seektalent.models import (
    CTSQuery,
    CitySearchSummary,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    NormalizedResume,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RequirementSheet,
    RetrievalState,
    RoundRetrievalPlan,
    RoundState,
    RunState,
    ScoredCandidate,
    ScoringPolicy,
    SearchControllerDecision,
    SearchAttempt,
    SearchObservation,
    SentQueryRecord,
)
from seektalent.runtime.context_builder import (
    build_controller_context,
    build_finalize_context,
    build_reflection_context,
    build_scoring_context,
)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "retrieval"],
        preferred_capabilities=["resume matching"],
        hard_constraints=HardConstraintSlots(locations=["上海市"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="resume matching",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="trace",
                source="jd",
                category="tooling",
                priority=3,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )


def _scored_candidate(
    resume_id: str,
    *,
    round_no: int,
    overall_score: int = 92,
    must_have_match_score: int = 90,
    risk_score: int = 10,
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=overall_score,
        must_have_match_score=must_have_match_score,
        preferred_match_score=70,
        risk_score=risk_score,
        risk_flags=[],
        reasoning_summary="Strong Python and retrieval background.",
        evidence=["Python", "Retrieval"],
        confidence="high",
        matched_must_haves=["Python"],
        missing_must_haves=[],
        matched_preferences=["Retrieval"],
        negative_signals=[],
        strengths=["Directly relevant backend work."],
        weaknesses=[],
        source_round=round_no,
    )


def _run_state_for_stop_gate(
    *,
    candidates: list[ScoredCandidate],
    completed_rounds: int,
    include_untried_family: bool,
    include_anchor_only_broaden: bool = False,
) -> RunState:
    requirement_sheet = _requirement_sheet()
    sent_query_history = [
        SentQueryRecord(
            round_no=1,
            city="上海市",
            phase="balanced",
            batch_no=1,
            requested_count=10,
            query_terms=["python", "resume matching"],
            keyword_query='python "resume matching"',
            source_plan_version=1,
            rationale="Round 1 query budget.",
        )
    ]
    if not include_untried_family:
        sent_query_history.append(
            SentQueryRecord(
                round_no=2,
                city="上海市",
                phase="balanced",
                batch_no=1,
                requested_count=10,
                query_terms=["python", "trace"],
                keyword_query='python "trace"',
                source_plan_version=1,
                rationale="Round 2 query budget.",
            )
        )
    if include_anchor_only_broaden:
        sent_query_history.append(
            SentQueryRecord(
                round_no=3,
                city="上海市",
                phase="balanced",
                batch_no=1,
                requested_count=10,
                query_terms=["python"],
                keyword_query="python",
                source_plan_version=1,
                rationale="Runtime broaden: anchor-only search.",
            )
        )
    return RunState(
        input_truth=InputTruth(
            job_title="Senior Python Engineer",
            jd="JD text",
            notes="Notes text",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
            sent_query_history=sent_query_history,
        ),
        scorecards_by_resume_id={candidate.resume_id: candidate for candidate in candidates},
        top_pool_ids=[candidate.resume_id for candidate in candidates],
        round_history=[
            RoundState(
                round_no=round_no,
                controller_decision=SearchControllerDecision(
                    thought_summary="Search.",
                    action="search_cts",
                    decision_rationale="Continue.",
                    proposed_query_terms=["python", "resume matching"],
                    proposed_filter_plan=ProposedFilterPlan(),
                ),
                retrieval_plan=RoundRetrievalPlan(
                    plan_version=1,
                    round_no=round_no,
                    query_terms=["python", "resume matching"],
                    keyword_query='python "resume matching"',
                    projected_cts_filters={},
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
                    rationale="Round",
                ),
                search_observation=SearchObservation(
                    round_no=round_no,
                    requested_count=10,
                    raw_candidate_count=10,
                    unique_new_count=1,
                    shortage_count=0,
                    fetch_attempt_count=1,
                ),
            )
            for round_no in range(1, completed_rounds + 1)
        ],
    )


def test_context_builder_projects_contexts_from_run_state() -> None:
    requirement_sheet = _requirement_sheet()
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Senior Python Engineer",
            jd="JD text",
            notes="Notes text",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
            sent_query_history=[
                SentQueryRecord(
                    round_no=1,
                    city="上海市",
                    phase="balanced",
                    batch_no=1,
                    requested_count=10,
                    query_terms=["python", "resume matching"],
                    keyword_query='python "resume matching"',
                    source_plan_version=1,
                    rationale="Round 1 query budget.",
                )
            ],
        ),
        scorecards_by_resume_id={
            "r-1": _scored_candidate("r-1", round_no=1),
            "r-2": _scored_candidate("r-2", round_no=1),
        },
        top_pool_ids=["r-1"],
        round_history=[
            RoundState(
                round_no=1,
                controller_decision=SearchControllerDecision(
                    thought_summary="Search round 1.",
                    action="search_cts",
                    decision_rationale="Need initial recall.",
                    proposed_query_terms=["python", "resume matching"],
                    proposed_filter_plan=ProposedFilterPlan(),
                ),
                retrieval_plan=RoundRetrievalPlan(
                    plan_version=1,
                    round_no=1,
                    query_terms=["python", "resume matching"],
                    keyword_query='python "resume matching"',
                    projected_cts_filters={},
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
                    rationale="Round 1",
                ),
                cts_queries=[
                    CTSQuery(
                        query_terms=["python", "resume matching"],
                        keyword_query='python "resume matching"',
                        native_filters={"location": ["上海市"]},
                        rationale="Round 1",
                    )
                ],
                search_observation=SearchObservation(
                    round_no=1,
                    requested_count=10,
                    raw_candidate_count=5,
                    unique_new_count=3,
                    shortage_count=2,
                    fetch_attempt_count=1,
                    new_resume_ids=["r-1", "r-2", "r-3"],
                    city_search_summaries=[
                        CitySearchSummary(
                            city="上海市",
                            phase="balanced",
                            batch_no=1,
                            requested_count=10,
                            unique_new_count=3,
                            shortage_count=7,
                            start_page=1,
                            next_page=2,
                            fetch_attempt_count=1,
                            exhausted_reason="cts_exhausted",
                        )
                    ],
                ),
                search_attempts=[
                    SearchAttempt(
                        city="上海市",
                        phase="balanced",
                        batch_no=1,
                        attempt_no=1,
                        requested_page=1,
                        requested_page_size=10,
                        raw_candidate_count=5,
                        batch_duplicate_count=0,
                        batch_unique_new_count=3,
                        cumulative_unique_new_count=3,
                        continue_refill=False,
                    )
                ],
                top_pool_ids=["r-1"],
                dropped_candidate_ids=["r-2"],
                reflection_advice=ReflectionAdvice(
                    keyword_advice=ReflectionKeywordAdvice(suggested_activate_terms=["trace"]),
                    filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
                    reflection_summary="Continue with one additional domain term next round.",
                ),
            )
        ],
    )

    controller_context = build_controller_context(
        run_state=run_state,
        round_no=2,
        min_rounds=1,
        max_rounds=3,
        target_new=5,
    )
    final_round_context = build_controller_context(
        run_state=run_state,
        round_no=3,
        min_rounds=1,
        max_rounds=3,
        target_new=5,
    )
    scoring_context = build_scoring_context(
        run_state=run_state,
        round_no=2,
        normalized_resume=NormalizedResume(
            resume_id="r-1",
            dedup_key="r-1",
            candidate_name="Lin Qian",
            headline="Senior Python Engineer",
            current_title="Senior Python Engineer",
            current_company="Hewa Talent Cloud",
            locations=["上海市"],
            education_summary="",
            skills=["python"],
            industry_tags=[],
            language_tags=[],
            recent_experiences=[],
            key_achievements=[],
            raw_text_excerpt="",
            completeness_score=80,
            missing_fields=[],
            normalization_notes=[],
        ),
    )
    reflection_context = build_reflection_context(
        run_state=run_state,
        round_state=run_state.round_history[0],
    )
    finalize_context = build_finalize_context(
        run_state=run_state,
        rounds_executed=1,
        stop_reason="reflection_stop",
        run_id="run-1",
        run_dir="/tmp/run-1",
    )

    assert controller_context.shortage_history == [2]
    assert controller_context.retrieval_rounds_completed == 1
    assert controller_context.rounds_remaining_after_current == 1
    assert controller_context.budget_used_ratio == pytest.approx(2 / 3)
    assert controller_context.near_budget_limit is False
    assert controller_context.stop_guidance.can_stop is False
    assert controller_context.stop_guidance.top_pool_strength == "weak"
    assert controller_context.stop_guidance.tried_families == ["role.python", "domain.resumematching"]
    assert controller_context.stop_guidance.untried_admitted_families == ["framework.trace"]
    assert controller_context.stop_guidance.productive_round_count == 1
    assert controller_context.stop_guidance.zero_gain_round_count == 0
    assert controller_context.is_final_allowed_round is False
    assert controller_context.latest_search_observation is not None
    assert controller_context.latest_search_observation.shortage_count == 2
    assert controller_context.latest_search_observation.city_search_summaries[0].city == "上海市"
    assert controller_context.previous_reflection is not None
    assert controller_context.latest_reflection_keyword_advice is not None
    assert scoring_context.scoring_policy.role_title == "Senior Python Engineer"
    assert scoring_context.runtime_only_constraints == []
    assert reflection_context.top_candidates[0].resume_id == "r-1"
    assert reflection_context.dropped_candidates[0].resume_id == "r-2"
    assert finalize_context.top_candidates[0].resume_id == "r-1"
    assert final_round_context.is_final_allowed_round is True
    assert final_round_context.retrieval_rounds_completed == 1
    assert final_round_context.rounds_remaining_after_current == 0
    assert final_round_context.near_budget_limit is True
    assert final_round_context.stop_guidance.can_stop is True
    assert "80% stop threshold starts at round 3" in final_round_context.budget_reminder
    assert list(final_round_context.model_dump(mode="json"))[-1] == "budget_reminder"


def test_stop_guidance_blocks_usable_low_quality_pool_before_budget_threshold() -> None:
    candidates = [
        _scored_candidate(f"strong-{index}", round_no=1)
        for index in range(2)
    ] + [
        _scored_candidate(f"weak-{index}", round_no=1, overall_score=65, must_have_match_score=60, risk_score=40)
        for index in range(8)
    ]
    context = build_controller_context(
        run_state=_run_state_for_stop_gate(
            candidates=candidates,
            completed_rounds=3,
            include_untried_family=True,
        ),
        round_no=4,
        min_rounds=3,
        max_rounds=10,
        target_new=10,
    )

    assert context.near_budget_limit is False
    assert context.stop_guidance.can_stop is False
    assert context.stop_guidance.top_pool_strength == "usable"
    assert context.stop_guidance.fit_count == 10
    assert context.stop_guidance.strong_fit_count == 2
    assert context.stop_guidance.quality_gate_status == "continue_low_quality"
    assert "strong-fit candidates" in context.stop_guidance.reason


def test_stop_guidance_allows_low_quality_pool_at_budget_threshold() -> None:
    candidates = [
        _scored_candidate(f"strong-{index}", round_no=1)
        for index in range(2)
    ] + [
        _scored_candidate(f"weak-{index}", round_no=1, overall_score=65, must_have_match_score=60, risk_score=40)
        for index in range(8)
    ]
    context = build_controller_context(
        run_state=_run_state_for_stop_gate(
            candidates=candidates,
            completed_rounds=7,
            include_untried_family=True,
        ),
        round_no=8,
        min_rounds=3,
        max_rounds=10,
        target_new=10,
    )

    assert context.near_budget_limit is True
    assert context.is_final_allowed_round is False
    assert context.stop_guidance.can_stop is True
    assert context.stop_guidance.quality_gate_status == "budget_stop_allowed"
    assert "8/10 near-budget stop threshold" in context.stop_guidance.reason


def test_stop_guidance_requires_broaden_when_low_quality_pool_has_no_active_families_before_budget() -> None:
    candidates = [
        _scored_candidate(f"strong-{index}", round_no=1)
        for index in range(2)
    ] + [
        _scored_candidate(f"weak-{index}", round_no=1, overall_score=65, must_have_match_score=60, risk_score=40)
        for index in range(8)
    ]
    context = build_controller_context(
        run_state=_run_state_for_stop_gate(
            candidates=candidates,
            completed_rounds=3,
            include_untried_family=False,
        ),
        round_no=4,
        min_rounds=3,
        max_rounds=10,
        target_new=10,
    )

    assert context.stop_guidance.can_stop is False
    assert context.stop_guidance.untried_admitted_families == []
    assert context.stop_guidance.quality_gate_status == "broaden_required"
    assert context.stop_guidance.broadening_attempted is False


def test_stop_guidance_allows_low_quality_exhausted_after_anchor_only_broaden() -> None:
    candidates = [
        _scored_candidate(f"strong-{index}", round_no=1)
        for index in range(2)
    ] + [
        _scored_candidate(f"weak-{index}", round_no=1, overall_score=65, must_have_match_score=60, risk_score=40)
        for index in range(8)
    ]
    context = build_controller_context(
        run_state=_run_state_for_stop_gate(
            candidates=candidates,
            completed_rounds=4,
            include_untried_family=False,
            include_anchor_only_broaden=True,
        ),
        round_no=5,
        min_rounds=3,
        max_rounds=10,
        target_new=10,
    )

    assert context.stop_guidance.can_stop is True
    assert context.stop_guidance.untried_admitted_families == []
    assert context.stop_guidance.quality_gate_status == "low_quality_exhausted"
    assert context.stop_guidance.broadening_attempted is True


def test_stop_guidance_allows_strong_pool_before_budget_threshold() -> None:
    candidates = [
        _scored_candidate(f"strong-{index}", round_no=1)
        for index in range(5)
    ] + [
        _scored_candidate(f"weak-{index}", round_no=1, overall_score=65, must_have_match_score=60, risk_score=40)
        for index in range(5)
    ]
    context = build_controller_context(
        run_state=_run_state_for_stop_gate(
            candidates=candidates,
            completed_rounds=3,
            include_untried_family=True,
        ),
        round_no=4,
        min_rounds=3,
        max_rounds=10,
        target_new=10,
    )

    assert context.stop_guidance.can_stop is True
    assert context.stop_guidance.top_pool_strength == "strong"
    assert context.stop_guidance.strong_fit_count == 5
    assert context.stop_guidance.quality_gate_status == "pass"


def test_stop_guidance_excludes_secondary_title_anchor_from_untried_families() -> None:
    requirement_sheet = RequirementSheet(
        role_title="Backend Platform Engineer",
        title_anchor_terms=["Backend", "Platform"],
        title_anchor_rationale="Title contributes both backend and platform anchors.",
        role_summary="Build backend platform services.",
        must_have_capabilities=["Python"],
        hard_constraints=HardConstraintSlots(locations=["上海市"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Backend",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.backend",
            ),
            QueryTermCandidate(
                term="Platform",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.platform",
            ),
            QueryTermCandidate(
                term="Python",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="core_skill",
                queryability="admitted",
                family="skill.python",
            ),
        ],
        scoring_rationale="Prefer backend platform resumes with Python signal.",
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Backend Platform Engineer",
            jd="Build backend platform services.",
            notes="Prefer Python signal.",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
            sent_query_history=[
                SentQueryRecord(
                    round_no=1,
                    city="上海市",
                    phase="balanced",
                    batch_no=1,
                    requested_count=10,
                    query_terms=["Backend", "Platform"],
                    keyword_query="Backend Platform",
                    source_plan_version=1,
                    rationale="Round 1 query budget.",
                )
            ],
        ),
        scorecards_by_resume_id={f"fit-{index}": _scored_candidate(f"fit-{index}", round_no=1) for index in range(2)},
        top_pool_ids=["fit-0", "fit-1"],
        round_history=[
            RoundState(
                round_no=1,
                controller_decision=SearchControllerDecision(
                    thought_summary="Round 1 search.",
                    action="search_cts",
                    decision_rationale="Start with both title anchors.",
                    proposed_query_terms=["Backend", "Platform"],
                    proposed_filter_plan=ProposedFilterPlan(),
                ),
                retrieval_plan=RoundRetrievalPlan(
                    plan_version=1,
                    round_no=1,
                    query_terms=["Backend", "Platform"],
                    keyword_query="Backend Platform",
                    projected_cts_filters={},
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
                    rationale="Round 1",
                ),
                search_observation=SearchObservation(
                    round_no=1,
                    requested_count=10,
                    raw_candidate_count=4,
                    unique_new_count=2,
                    shortage_count=6,
                    fetch_attempt_count=1,
                ),
            )
        ],
    )

    context = build_controller_context(
        run_state=run_state,
        round_no=2,
        min_rounds=1,
        max_rounds=5,
        target_new=10,
    )

    assert context.stop_guidance.untried_admitted_families == ["skill.python"]
