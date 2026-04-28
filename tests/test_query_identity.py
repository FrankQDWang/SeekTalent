from seektalent.models import CanonicalQuerySpec, ConstraintValue
from seektalent.retrieval.query_identity import (
    build_job_intent_fingerprint,
    build_query_fingerprint,
    build_query_instance_id,
)


def _spec(*, optional_terms: list[str], provider_filters: dict[str, ConstraintValue]) -> CanonicalQuerySpec:
    return CanonicalQuerySpec(
        lane_type="generic_explore",
        anchors=["python"],
        expansion_terms=["resume matching"],
        promoted_prf_expression=None,
        generic_explore_terms=["trace"],
        required_terms=["python"],
        optional_terms=optional_terms,
        excluded_terms=[],
        location_key="shanghai",
        provider_filters=provider_filters,
        boolean_template="required_plus_optional",
        rendered_provider_query='python "resume matching" trace',
        provider_name="cts",
        source_plan_version="2",
    )


def test_query_fingerprint_is_stable_across_runs() -> None:
    spec = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"city": "上海", "experience_years": 5},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    first = build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )
    second = build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )

    assert first == second


def test_query_fingerprint_canonicalizes_unordered_fields() -> None:
    first = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"experience_years": 5, "city": "上海"},
    )
    second = _spec(
        optional_terms=["trace", "resume matching"],
        provider_filters={"city": "上海", "experience_years": 5},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    assert build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=first,
        policy_version="typed-second-lane-v1",
    ) == build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=second,
        policy_version="typed-second-lane-v1",
    )


def test_query_fingerprint_rejects_lane_type_mismatch() -> None:
    spec = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"city": "上海", "experience_years": 5},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    try:
        build_query_fingerprint(
            job_intent_fingerprint=job_fingerprint,
            lane_type="exploit",
            canonical_query_spec=spec,
            policy_version="typed-second-lane-v1",
        )
    except ValueError as exc:
        assert "lane_type" in str(exc)
    else:
        raise AssertionError("expected build_query_fingerprint to reject mismatched lane_type")


def test_query_fingerprint_canonicalizes_list_valued_provider_filters() -> None:
    first = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"cities": ["  New York ", "san Francisco"], "experience_years": 5},
    )
    second = _spec(
        optional_terms=["trace", "resume matching"],
        provider_filters={"experience_years": 5, "cities": ["SAN FRANCISCO", "new york"]},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    assert build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=first,
        policy_version="typed-second-lane-v1",
    ) == build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=second,
        policy_version="typed-second-lane-v1",
    )


def test_query_fingerprint_canonicalizes_case_only_provider_query_variants() -> None:
    first = CanonicalQuerySpec(
        lane_type="generic_explore",
        anchors=["Python"],
        expansion_terms=["Resume Matching"],
        promoted_prf_expression=None,
        generic_explore_terms=["Trace"],
        required_terms=["Python"],
        optional_terms=["Resume Matching", "Trace"],
        excluded_terms=[],
        location_key="shanghai",
        provider_filters={"city": "上海"},
        boolean_template="required_plus_optional",
        rendered_provider_query='Python "Resume Matching" Trace',
        provider_name="cts",
        source_plan_version="2",
    )
    second = CanonicalQuerySpec(
        lane_type="generic_explore",
        anchors=["python"],
        expansion_terms=["resume matching"],
        promoted_prf_expression=None,
        generic_explore_terms=["trace"],
        required_terms=["python"],
        optional_terms=["resume matching", "trace"],
        excluded_terms=[],
        location_key="shanghai",
        provider_filters={"city": "上海"},
        boolean_template="required_plus_optional",
        rendered_provider_query='python "resume matching" trace',
        provider_name="cts",
        source_plan_version="2",
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    assert build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=first,
        policy_version="typed-second-lane-v1",
    ) == build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=second,
        policy_version="typed-second-lane-v1",
    )


def test_job_intent_fingerprint_canonicalizes_nested_hard_filters() -> None:
    first = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={
            "experience_requirement": {
                "raw_text": "  5+ YEARS IN SEARCH  ",
                "min_years": 5,
                "summary": " Search experience ",
            }
        },
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )
    second = build_job_intent_fingerprint(
        role_title="python engineer",
        must_haves=["Python", "Resume Matching"],
        preferred_terms=["TRACE"],
        hard_filters={
            "experience_requirement": {
                "raw_text": "5+ years in search",
                "min_years": 5,
                "summary": "search experience",
            }
        },
        location_preferences=["SHANGHAI"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    assert first == second


def test_query_instance_id_changes_by_run_but_not_fingerprint() -> None:
    spec = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"city": "上海", "experience_years": 5},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )
    query_fingerprint = build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )

    first = build_query_instance_id(
        run_id="run-a",
        round_no=2,
        lane_type="generic_explore",
        query_fingerprint=query_fingerprint,
        source_plan_version="2",
    )
    second = build_query_instance_id(
        run_id="run-b",
        round_no=2,
        lane_type="generic_explore",
        query_fingerprint=query_fingerprint,
        source_plan_version="2",
    )

    assert first != second
    assert query_fingerprint
