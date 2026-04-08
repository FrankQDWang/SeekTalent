from __future__ import annotations

import asyncio

from seektalent.clients.cts_client import CTSClient, MockCTSClient
from seektalent.config import AppSettings
from seektalent.models import ChildFrontierNodeStub, HardConstraints, RuntimeOnlyConstraints, SearchExecutionPlan_t


def _plan() -> SearchExecutionPlan_t:
    return SearchExecutionPlan_t(
        query_terms=["python", "agent workflow"],
        projected_filters=HardConstraints(
            locations=["上海"],
            company_names=["Hewa Talent Cloud"],
            degree_requirement="本科及以上",
            school_type_requirement=["211"],
            min_years=3,
            max_years=8,
        ),
        runtime_only_constraints=RuntimeOnlyConstraints(),
        target_new_candidate_count=2,
        semantic_hash="hash-1",
        source_card_ids=["card-1"],
        child_frontier_node_stub=ChildFrontierNodeStub(
            frontier_node_id="node-1",
            parent_frontier_node_id="root",
            selected_operator_name="bootstrap",
        ),
        derived_position="Python Agent Engineer",
        derived_work_content="retrieval ranking workflow",
    )


def test_cts_client_build_request_payload_uses_phase1_contract() -> None:
    client = CTSClient(AppSettings(_env_file=None, mock_cts=True))
    payload, notes = client.build_request_payload(_plan())

    assert payload["keyword"] == 'python "agent workflow"'
    assert payload["page"] == 1
    assert payload["pageSize"] == 2
    assert payload["location"] == "上海"
    assert payload["company"] == "Hewa Talent Cloud"
    assert payload["position"] == "Python Agent Engineer"
    assert payload["workContent"] == "retrieval ranking workflow"
    assert payload["degree"] == 2
    assert payload["schoolType"] == 2
    assert payload["workExperienceRange"] == 4
    assert any("degree_requirement mapped to CTS code 2" in note for note in notes)


def test_mock_cts_client_returns_retrieved_candidates() -> None:
    client = MockCTSClient(AppSettings(_env_file=None, mock_cts=True))
    plan = _plan()
    plan.derived_work_content = "retrieval ranking"
    result = asyncio.run(client.search(plan, trace_id="trace-1"))

    assert result.request_payload["position"] == "Python Agent Engineer"
    assert result.raw_candidate_count == len(result.candidates)
    assert result.candidates
    assert all(candidate.candidate_id.startswith("mock-r") for candidate in result.candidates)
    assert result.candidates[0].now_location == "上海"
