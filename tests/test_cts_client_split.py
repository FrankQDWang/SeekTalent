from __future__ import annotations

import ast
from pathlib import Path

import pytest

from seektalent.clients.cts_models import Candidate, CandidateSearchResponse, EducationItem, WorkExperienceItem
from seektalent.models import CTSQuery


ROOT = Path(__file__).resolve().parents[1]
CLIENTS_ROOT = ROOT / "src" / "seektalent" / "clients"
CTS_CLIENT_PATH = CLIENTS_ROOT / "cts_client.py"
CTS_REQUEST_PATH = CLIENTS_ROOT / "cts_request.py"
CTS_RESPONSE_PATH = CLIENTS_ROOT / "cts_response.py"
CTS_MOCK_CLIENT_PATH = CLIENTS_ROOT / "cts_mock_client.py"


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_names(tree: ast.Module) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _function_names(tree: ast.Module) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_cts_client_delegates_request_response_and_mock_responsibilities() -> None:
    assert CTS_REQUEST_PATH.is_file()
    assert CTS_RESPONSE_PATH.is_file()
    assert CTS_MOCK_CLIENT_PATH.is_file()

    tree = _module_tree(CTS_CLIENT_PATH)
    modules = _imported_modules(tree)

    assert "seektalent.clients.cts_request" in modules
    assert "seektalent.clients.cts_response" in modules
    assert "seektalent.clients.cts_contracts" in modules
    assert not {
        "seektalent.evaluation",
        "seektalent.locations",
        "seektalent.mock_data",
    } & modules
    assert "MockCTSClient" not in _class_names(tree)
    assert not {
        "build_request_payload",
        "_fallback_resume_seed",
        "_extract_resume_id",
        "_normalize_candidate",
        "_candidate_field_text",
        "_matches_filter",
        "_retrieval_score",
    } & _function_names(tree)


def test_cts_client_does_not_keep_legacy_contract_reexports() -> None:
    import seektalent.clients.cts_client as cts_client

    assert cts_client.__all__ == ["CTSClient"]
    assert not {
        "BaseCTSClient",
        "CTSClientProtocol",
        "CTSFetchResult",
        "MockCTSClient",
        "cts_contracts",
    } & set(vars(cts_client))


def test_cts_request_builder_preserves_payload_contract() -> None:
    assert CTS_REQUEST_PATH.is_file()
    from seektalent.clients.cts_request import build_cts_request_payload

    query = CTSQuery(
        query_role="exploit",
        query_terms=["python"],
        keyword_query="python OR backend",
        native_filters={"location": ["上海", "北京"], "degree": 1},
        page=2,
        page_size=5,
        rationale="unit test",
        adapter_notes=["runtime location dispatch: 上海"],
    )

    payload, notes = build_cts_request_payload(query)

    assert payload == {
        "keyword": "python OR backend",
        "location": ["上海", "北京"],
        "degree": 1,
        "page": 2,
        "pageSize": 5,
    }
    assert notes == [
        "Dedup stays in runtime; CTS request does not receive seen ids.",
        "The project never forwards the full JD to CTS.",
        "runtime location dispatch: 上海",
    ]

    bad_list_filter = query.model_copy(update={"native_filters": {"company": ["Acme"]}})
    with pytest.raises(ValueError, match="must not be a list"):
        build_cts_request_payload(bad_list_filter)

    bad_native_filter = query.model_copy(update={"native_filters": {"unsupported": "x"}})
    with pytest.raises(ValueError, match="Unsupported native filter"):
        build_cts_request_payload(bad_native_filter)


def test_cts_response_mapper_preserves_resume_candidate_semantics() -> None:
    assert CTS_RESPONSE_PATH.is_file()
    from seektalent.clients.cts_response import normalize_cts_candidate

    candidate = Candidate(
        resumeId="resume-1",
        candidateName="Candidate A",
        expectedJobCategory="Backend Engineer",
        expectedIndustry="AI",
        expectedLocation="上海",
        nowLocation="Shanghai",
        age=31,
        gender="M",
        workYear=8,
        educationList=[
            EducationItem(school="Fudan", speciality="CS", degree="BS"),
        ],
        workExperienceList=[
            WorkExperienceItem(company="Acme", title="Engineer", summary="Built matching systems"),
        ],
        projectNameAll=["Search"],
        workSummariesAll=["Python backend"],
    )

    normalized = normalize_cts_candidate(candidate, round_no=3)

    assert normalized.resume_id == "resume-1"
    assert normalized.source_resume_id == "resume-1"
    assert normalized.used_fallback_id is False
    assert normalized.source_round == 3
    assert normalized.now_location == "Shanghai"
    assert normalized.expected_location == "上海"
    assert normalized.raw["provider"] == "cts"
    assert normalized.raw["source"] == "cts"
    assert "Backend Engineer" in normalized.search_text
    assert "Built matching systems" in normalized.search_text


def test_cts_response_mapper_handles_empty_success_response_data() -> None:
    from seektalent.clients.cts_response import normalize_cts_response_candidates

    response = CandidateSearchResponse(code=0, status="ok", message="no data", data=None)

    assert normalize_cts_response_candidates(response, round_no=1) == []


@pytest.mark.parametrize(
    ("body", "reason_code"),
    [
        ({"code": 10001, "status": "error", "message": "tenant credential invalid", "data": None}, "cts_auth_failed"),
        ({"code": 429, "status": "error", "message": "rate limit exceeded", "data": None}, "cts_rate_limited"),
        ({"code": 40001, "status": "error", "message": "invalid filter parameter", "data": None}, "cts_business_error"),
        ({"code": 0, "status": "failed", "message": "business failed", "data": None}, "cts_status_error"),
        ({"code": 0, "status": "ok", "message": "missing data schema"}, "cts_response_schema_invalid"),
    ],
)
def test_cts_response_parser_rejects_business_errors_and_malformed_payloads(
    body: dict[str, object],
    reason_code: str,
) -> None:
    from seektalent.clients.cts_response import CTSResponseError, parse_cts_search_response_body

    with pytest.raises(CTSResponseError) as exc_info:
        parse_cts_search_response_body(body)

    assert exc_info.value.reason_code == reason_code
