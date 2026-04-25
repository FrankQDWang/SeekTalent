from seektalent.models import (
    CTSQuery,
    AgeRequirement,
    CitySearchSummary,
    ConstraintProjectionResult,
    ControllerContext,
    DegreeRequirement,
    FinalizeContext,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    PreferenceSlots,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionContext,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RequirementDigest,
    RequirementSheet,
    RetrievalState,
    RoundRetrievalPlan,
    RoundState,
    RuntimeConstraint,
    RunState,
    ScoredCandidate,
    ScoringPolicy,
    SearchControllerDecision,
    SearchAttempt,
    SearchObservation,
    SentQueryRecord,
    StopGuidance,
)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["Python", "LLM application"],
        preferred_capabilities=["Retrieval"],
        exclusion_signals=["Pure frontend"],
        hard_constraints=HardConstraintSlots(
            locations=["上海市"],
            degree_requirement=DegreeRequirement(
                canonical_degree="本科及以上",
                raw_text="本科及以上",
                pinned=True,
            ),
            age_requirement=AgeRequirement(max_age=35, raw_text="35岁以下"),
            company_names=["OpenAI"],
        ),
        preferences=PreferenceSlots(
            preferred_domains=["招聘"],
            preferred_query_terms=["resume matching"],
        ),
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
        ],
        scoring_rationale="Core scoring is Python plus LLM application depth.",
    )


def _scored_candidate(resume_id: str) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=92,
        must_have_match_score=90,
        preferred_match_score=70,
        risk_score=10,
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
        source_round=1,
    )


def test_v02_run_state_models_can_be_composed() -> None:
    requirement_sheet = _requirement_sheet()
    scoring_policy = ScoringPolicy(
        role_title=requirement_sheet.role_title,
        role_summary=requirement_sheet.role_summary,
        must_have_capabilities=requirement_sheet.must_have_capabilities,
        preferred_capabilities=requirement_sheet.preferred_capabilities,
        exclusion_signals=requirement_sheet.exclusion_signals,
        hard_constraints=requirement_sheet.hard_constraints,
        preferences=requirement_sheet.preferences,
        scoring_rationale=requirement_sheet.scoring_rationale,
    )
    sent_query = SentQueryRecord(
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
        scoring_policy=scoring_policy,
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
            sent_query_history=[sent_query],
        ),
        seen_resume_ids=["r-1"],
        top_pool_ids=["r-1"],
    )

    assert run_state.requirement_sheet.hard_constraints.locations == ["上海市"]
    assert run_state.retrieval_state.query_term_pool[0].active is True
    assert run_state.retrieval_state.sent_query_history[0].keyword_query == 'python "resume matching"'
    assert run_state.model_dump(mode="json")["scoring_policy"]["role_title"] == "Senior Python Engineer"


def test_v02_context_and_round_models_capture_round_truth() -> None:
    requirement_sheet = _requirement_sheet()
    digest = RequirementDigest(
        role_title=requirement_sheet.role_title,
        role_summary=requirement_sheet.role_summary,
        top_must_have_capabilities=["Python", "LLM application"],
        top_preferences=["Retrieval"],
        hard_constraint_summary=["location=上海市", "degree=本科及以上"],
    )
    projection = ConstraintProjectionResult(
        cts_native_filters={},
        runtime_only_constraints=[
            RuntimeConstraint(
                field="age_requirement",
                normalized_value=35,
                source="notes",
                rationale="Age stays runtime-only before enum mapping lands.",
                blocking=True,
            )
        ],
        adapter_notes=["age requirement not projected to CTS"],
    )
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python", "resume matching"],
        keyword_query='python "resume matching"',
        projected_cts_filters=projection.cts_native_filters,
        runtime_only_constraints=projection.runtime_only_constraints,
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
        rationale="Round 1 uses two anchor terms.",
    )
    controller_decision = SearchControllerDecision(
        thought_summary="Search round 1 with two anchor terms.",
        action="search_cts",
        decision_rationale="Need initial recall.",
        proposed_query_terms=["python", "resume matching"],
        proposed_filter_plan=ProposedFilterPlan(),
    )
    cts_query = CTSQuery(
        query_terms=["python", "resume matching"],
        keyword_query='python "resume matching"',
        native_filters={"location": ["上海市"]},
        rationale="Round 1 CTS query.",
    )
    search_observation = SearchObservation(
        round_no=1,
        requested_count=10,
        raw_candidate_count=8,
        unique_new_count=6,
        shortage_count=4,
        fetch_attempt_count=2,
        new_resume_ids=["r-1"],
        new_candidate_summaries=["Python Engineer | 上海 | 6y"],
        city_search_summaries=[
            CitySearchSummary(
                city="上海市",
                phase="balanced",
                batch_no=1,
                requested_count=10,
                unique_new_count=6,
                shortage_count=4,
                start_page=1,
                next_page=3,
                fetch_attempt_count=2,
                exhausted_reason="cts_exhausted",
            )
        ],
    )
    reflection_advice = ReflectionAdvice(
        keyword_advice=ReflectionKeywordAdvice(suggested_activate_terms=["LLM application"]),
        filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
        suggest_stop=False,
        reflection_summary="Continue with one additional domain term next round.",
    )
    round_state = RoundState(
        round_no=1,
        controller_decision=controller_decision,
        retrieval_plan=retrieval_plan,
        cts_queries=[cts_query],
        search_observation=search_observation,
        top_pool_ids=["r-1"],
        reflection_advice=reflection_advice,
    )
    controller_context = ControllerContext(
        full_jd="JD text",
        full_notes="Notes text",
        requirement_sheet=requirement_sheet,
        round_no=2,
        min_rounds=3,
        max_rounds=5,
        retrieval_rounds_completed=1,
        rounds_remaining_after_current=3,
        budget_used_ratio=0.4,
        near_budget_limit=False,
        is_final_allowed_round=False,
        target_new=5,
        stop_guidance=StopGuidance(
            can_stop=False,
            reason="1 retrieval rounds completed; min_rounds is 3.",
            continue_reasons=["1 retrieval rounds completed; min_rounds is 3."],
            tried_families=["role.python", "domain.resumematching"],
            top_pool_strength="weak",
        ),
        requirement_digest=digest,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        latest_reflection_keyword_advice=reflection_advice.keyword_advice,
        latest_reflection_filter_advice=reflection_advice.filter_advice,
        shortage_history=[4],
    )
    reflection_context = ReflectionContext(
        round_no=1,
        full_jd="JD text",
        full_notes="Notes text",
        requirement_sheet=requirement_sheet,
        current_retrieval_plan=retrieval_plan,
        search_observation=search_observation,
        search_attempts=[
            SearchAttempt(
                city="上海市",
                phase="balanced",
                batch_no=1,
                attempt_no=1,
                requested_page=1,
                requested_page_size=10,
                raw_candidate_count=8,
                batch_duplicate_count=0,
                batch_unique_new_count=6,
                cumulative_unique_new_count=6,
                continue_refill=False,
                exhausted_reason="cts_exhausted",
            )
        ],
        top_candidates=[_scored_candidate("r-1")],
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
    )
    finalize_context = FinalizeContext(
        run_id="run-1",
        run_dir="runs/run-1",
        rounds_executed=1,
        stop_reason="max_rounds_reached",
        top_candidates=[_scored_candidate("r-1")],
        requirement_digest=digest,
        sent_query_history=reflection_context.sent_query_history,
    )

    assert round_state.retrieval_plan.query_terms == ["python", "resume matching"]
    assert controller_context.latest_reflection_keyword_advice is not None
    assert controller_context.rounds_remaining_after_current == 3
    assert controller_context.stop_guidance.can_stop is False
    assert reflection_context.sent_query_history[0].source_plan_version == 1
    assert finalize_context.top_candidates[0].resume_id == "r-1"
    assert round_state.search_observation is not None
    assert round_state.search_observation.city_search_summaries[0].city == "上海市"
