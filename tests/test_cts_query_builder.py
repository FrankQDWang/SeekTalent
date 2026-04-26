from seektalent.models import CTSQuery
from seektalent.providers.cts.query_builder import CTSQueryBuildInput, build_cts_query


def test_build_cts_query_without_city_keeps_base_filters() -> None:
    result = build_cts_query(
        CTSQueryBuildInput(
            query_role="exploit",
            query_terms=["python", "retrieval"],
            keyword_query="python retrieval",
            base_filters={"age": 3, "position": "backend"},
            adapter_notes=["projection: age mapped to CTS code 3"],
            page=2,
            page_size=5,
            rationale="builder test",
        )
    )

    assert isinstance(result, CTSQuery)
    assert result.native_filters == {"age": 3, "position": "backend"}
    assert result.page == 2
    assert result.page_size == 5
    assert result.adapter_notes == ["projection: age mapped to CTS code 3"]


def test_build_cts_query_with_city_injects_location_and_note() -> None:
    result = build_cts_query(
        CTSQueryBuildInput(
            query_role="exploit",
            query_terms=["python"],
            keyword_query="python",
            base_filters={"age": 3},
            adapter_notes=["projection: age mapped to CTS code 3"],
            page=1,
            page_size=10,
            rationale="builder test",
            city="上海",
        )
    )

    assert result.native_filters == {"age": 3, "location": ["上海"]}
    assert result.adapter_notes == [
        "projection: age mapped to CTS code 3",
        "runtime location dispatch: 上海",
    ]
