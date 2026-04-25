import asyncio
import json
from pathlib import Path

import httpx
import pytest

from seektalent.company_discovery.model_steps import CompanyDiscoveryModelSteps
from seektalent.company_discovery.bocha_provider import BochaWebSearchProvider
from seektalent.company_discovery.models import (
    CompanyDiscoveryInput,
    CompanyEvidence,
    CompanySearchTask,
    PageReadResult,
    SearchRerankResult,
    TargetCompanyCandidate,
    TargetCompanyPlan,
    WebSearchResult,
)
from seektalent.company_discovery.query_injection import inject_target_company_terms
from seektalent.company_discovery.scheduler import select_company_seed_terms
from seektalent.company_discovery.service import CompanyDiscoveryService
from seektalent.models import HardConstraintSlots, QueryTermCandidate, RequirementSheet, SentQueryRecord
from seektalent.prompting import LoadedPrompt
from seektalent.retrieval.query_plan import canonicalize_controller_query_terms
from tests.settings_factory import make_settings


def _anchor() -> QueryTermCandidate:
    return QueryTermCandidate(
        term="python",
        source="job_title",
        category="role_anchor",
        priority=1,
        evidence="title",
        first_added_round=0,
        retrieval_role="role_anchor",
        queryability="admitted",
        family="role.python",
    )


def _company_term(term: str, family: str, *, first_added_round: int = 2) -> QueryTermCandidate:
    return QueryTermCandidate.model_construct(
        term=term,
        source="company_discovery",
        category="company",
        priority=20,
        evidence="company evidence",
        first_added_round=first_added_round,
        retrieval_role="target_company",
        queryability="admitted",
        active=True,
        family=family,
    )


def test_company_discovery_models_create() -> None:
    evidence = CompanyEvidence(
        title="Volcengine hiring",
        url="https://example.com",
        snippet="ByteDance cloud platform",
        source_type="web",
    )
    candidate = TargetCompanyCandidate(
        name="火山引擎",
        aliases=["Volcengine"],
        source="web_inferred",
        intent="target",
        confidence=0.8,
        fit_axes=["cloud", "platform"],
        search_usage="company_filter",
        evidence=[evidence],
        rationale="Matches the target domain.",
    )
    plan = TargetCompanyPlan(
        explicit_targets=[candidate],
        inferred_targets=[],
        excluded_companies=["Tencent"],
        holdout_companies=[],
        rejected_companies=[],
        stop_reason="done",
    )

    assert plan.explicit_targets[0].evidence[0].url == "https://example.com"
    assert plan.explicit_targets[0].aliases == ["Volcengine"]
    assert plan.stop_reason == "done"


def test_inject_target_company_terms_appends_deduped_company_terms() -> None:
    plan = TargetCompanyPlan(
        explicit_targets=[
            TargetCompanyCandidate(
                name="火山引擎",
                aliases=["Volcengine"],
                source="explicit_jd",
                intent="target",
                confidence=0.9,
                fit_axes=["cloud"],
                search_usage="keyword_and_filter",
                evidence=[],
                rationale="JD named company.",
            ),
            TargetCompanyCandidate(
                name="火山引擎",
                aliases=[],
                source="candidate_backfill",
                intent="target",
                confidence=0.5,
                fit_axes=["cloud"],
                search_usage="keyword_term",
                evidence=[],
                rationale="Duplicate company.",
            ),
        ],
        inferred_targets=[
            TargetCompanyCandidate(
                name="阿里云",
                aliases=["Aliyun"],
                source="web_inferred",
                intent="similar_to_target",
                confidence=0.7,
                fit_axes=["cloud"],
                search_usage="company_filter",
                evidence=[
                    CompanyEvidence(title="Aliyun role", url="https://example.com/a", snippet="Alibaba Cloud", source_type="web"),
                ],
                rationale="Relevant adjacent company.",
            ),
            TargetCompanyCandidate(
                name="腾讯",
                aliases=[],
                source="web_inferred",
                intent="exclude",
                confidence=0.1,
                fit_axes=[],
                search_usage="exclude",
                evidence=[],
                rationale="Should not inject.",
            ),
        ],
        excluded_companies=[],
        holdout_companies=[],
        rejected_companies=[],
        stop_reason=None,
    )
    pool = [
        _anchor(),
        QueryTermCandidate.model_construct(
            term="aliyun",
            source="company_discovery",
            category="company",
            priority=25,
            evidence="existing pool company",
            first_added_round=1,
            retrieval_role="target_company",
            queryability="admitted",
            active=True,
            family="company.aliyun",
        ),
    ]

    injected = inject_target_company_terms(pool, plan, first_added_round=2)

    assert [item.term for item in injected] == ["python", "aliyun", "火山引擎"]
    assert injected[2].category == "company"
    assert injected[2].retrieval_role == "target_company"
    assert injected[2].source == "explicit_jd"
    assert injected[2].queryability == "admitted"
    assert injected[2].active is True
    assert injected[2].priority >= 20
    assert injected[2].family == "company.火山引擎"


def test_select_company_seed_terms_picks_first_untried_company() -> None:
    pool = [
        _anchor(),
        _company_term("火山引擎", "company.volcengine"),
        _company_term("阿里云", "company.aliyun"),
    ]

    sent_history = [
        SentQueryRecord(
            round_no=1,
            query_role="exploit",
            batch_no=1,
            requested_count=10,
            query_terms=["python", "火山引擎"],
            keyword_query="python 火山引擎",
            source_plan_version=1,
            rationale="sent",
        )
    ]

    selected = select_company_seed_terms(pool, sent_history, forced_families=set(), max_terms=2)

    assert [item.term for item in selected] == ["python", "阿里云"]


def test_select_company_seed_terms_uses_primary_role_anchor() -> None:
    pool = [
        _anchor().model_copy(update={"retrieval_role": "primary_role_anchor"}),
        _company_term("火山引擎", "company.volcengine"),
    ]

    selected = select_company_seed_terms(pool, [], forced_families=set(), max_terms=2)

    assert [item.term for item in selected] == ["python", "火山引擎"]


def test_query_plan_accepts_company_terms_without_counting_company_families_as_compiler_duplicates() -> None:
    pool = [
        _anchor(),
        _company_term("火山引擎", "company.volcengine"),
        _company_term("阿里云", "company.volcengine", first_added_round=3),
    ]

    assert canonicalize_controller_query_terms(
        ["python", "火山引擎", "阿里云"],
        round_no=2,
        title_anchor_terms=["python"],
        query_term_pool=pool,
    ) == ["python", "火山引擎", "阿里云"]


def test_query_plan_still_rejects_repeated_non_company_families() -> None:
    pool = [
        _anchor(),
        QueryTermCandidate(
            term="python data",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="domain.python",
        ),
        QueryTermCandidate(
            term="python infra",
            source="jd",
            category="domain",
            priority=3,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="domain.python",
        ),
    ]

    with pytest.raises(ValueError, match="repeat compiler families"):
        canonicalize_controller_query_terms(
            ["python", "python data", "python infra"],
            round_no=2,
            title_anchor_terms=["python"],
            query_term_pool=pool,
        )


def test_bocha_provider_reranks_search_results() -> None:
    seen_requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "results": [
                        {"index": 2, "bocha@rerankScore": 0.97},
                        {"index": 0, "relevance_score": 0.83},
                        {"index": 1, "score": 0.79},
                        {"index": 10, "score": 0.1},
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BochaWebSearchProvider(make_settings(bocha_api_key="bocha-key"), http_client=client)
    results = [
        WebSearchResult(rank=1, title="Alpha", url="https://example.com/a", snippet="Snippet A", summary="Summary A"),
        WebSearchResult(rank=2, title="Beta", url="https://example.com/b", snippet="Snippet B", summary="Summary B"),
        WebSearchResult(rank=3, title="Gamma", url="https://example.com/c", snippet="Snippet C", summary="Summary C"),
    ]

    try:
        reranked = asyncio.run(provider.rerank("python engineer", results, top_n=5))
    finally:
        asyncio.run(client.aclose())

    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.bochaai.com/v1/rerank"
    assert request.headers["authorization"] == "Bearer bocha-key"
    assert json.loads(request.content.decode("utf-8")) == {
        "model": "gte-rerank",
        "query": "python engineer",
        "documents": [
            "Alpha\nhttps://example.com/a\nSnippet A\nSummary A",
            "Beta\nhttps://example.com/b\nSnippet B\nSummary B",
            "Gamma\nhttps://example.com/c\nSnippet C\nSummary C",
        ],
        "top_n": 3,
        "return_documents": True,
    }
    assert [item.model_dump() for item in reranked] == [
        {"rank": 1, "source_index": 2, "score": 0.97, "title": "Gamma", "url": "https://example.com/c"},
        {"rank": 2, "source_index": 0, "score": 0.83, "title": "Alpha", "url": "https://example.com/a"},
        {"rank": 3, "source_index": 1, "score": 0.79, "title": "Beta", "url": "https://example.com/b"},
    ]


def test_bocha_provider_rejects_malformed_search_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"data": {"unexpected": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BochaWebSearchProvider(make_settings(bocha_api_key="bocha-key"), http_client=client)

    try:
        with pytest.raises(ValueError, match="malformed"):
            asyncio.run(provider.search("minimax 大模型", count=10))
    finally:
        asyncio.run(client.aclose())


def test_bocha_provider_rejects_malformed_rerank_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"data": {"unexpected": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BochaWebSearchProvider(make_settings(bocha_api_key="bocha-key"), http_client=client)
    results = [WebSearchResult(rank=1, title="Alpha", url="https://example.com/a")]

    try:
        with pytest.raises(ValueError, match="malformed"):
            asyncio.run(provider.rerank("python engineer", results, top_n=1))
    finally:
        asyncio.run(client.aclose())


class StubSearchProvider:
    async def search(self, query: str, *, count: int) -> list[WebSearchResult]:
        del query, count
        return [
            WebSearchResult(
                rank=1,
                title="AI infrastructure companies",
                url="https://example.com/companies",
                snippet="火山引擎 has AI platform teams.",
            )
        ]

    async def rerank(
        self,
        query: str,
        results: list[WebSearchResult],
        *,
        top_n: int,
    ) -> list[SearchRerankResult]:
        del query, top_n
        return [SearchRerankResult(rank=1, source_index=0, score=0.9, title=results[0].title, url=results[0].url)]


class StubPageReader:
    async def read(self, url: str, *, timeout_s: float) -> PageReadResult:
        del timeout_s
        return PageReadResult(url=url, title="companies", text="火山引擎 provides AI platform services.")


class StubCompanyModelSteps:
    async def plan_search_queries(self, discovery_input: CompanyDiscoveryInput) -> list[CompanySearchTask]:
        del discovery_input
        return [
            CompanySearchTask(
                query_id="q1",
                query="AI platform source companies",
                intent="market_map",
                rationale="Find source companies.",
            )
        ]

    async def extract_company_evidence(
        self,
        page_reads: list[PageReadResult],
        search_results: list[WebSearchResult],
    ) -> list[TargetCompanyCandidate]:
        return [
            TargetCompanyCandidate(
                name="火山引擎",
                aliases=["Volcengine"],
                source="web_inferred",
                intent="target",
                confidence=0.91,
                fit_axes=["ai_platform"],
                search_usage="keyword_term",
                evidence=[
                    CompanyEvidence(
                        title=page_reads[0].title,
                        url=search_results[0].url,
                        snippet="AI platform source company.",
                        source_type="web",
                    )
                ],
                rationale="Evidence-backed company.",
            )
        ]

    async def reduce_company_plan(
        self,
        candidates: list[TargetCompanyCandidate],
        discovery_input: CompanyDiscoveryInput,
        *,
        stop_reason: str,
    ) -> TargetCompanyPlan:
        del discovery_input
        return TargetCompanyPlan(
            inferred_targets=candidates,
            web_discovery_attempted=True,
            stop_reason=stop_reason,
        )


class FailingCompanyModelSteps:
    def __init__(self) -> None:
        self.last_call_artifact: dict[str, object] | None = None

    async def plan_search_queries(self, discovery_input: CompanyDiscoveryInput) -> list[CompanySearchTask]:
        self.last_call_artifact = {
            "stage": "company_discovery_plan",
            "prompt_name": "company_discovery_plan",
            "model_id": "openai-chat:qwen3.5-flash",
            "user_payload": {"DISCOVERY_INPUT": discovery_input.model_dump(mode="json")},
            "user_prompt_text": "plan discovery prompt",
            "started_at": "2026-01-01T00:00:00+00:00",
            "latency_ms": 5,
            "status": "failed",
            "retries": 0,
            "output_retries": 2,
            "error_message": "plan failed",
        }
        raise RuntimeError("plan failed")


def test_company_discovery_service_returns_evidence_backed_plan() -> None:
    import asyncio

    settings = make_settings(mock_cts=True, bocha_api_key="bocha-key")
    service = CompanyDiscoveryService(
        settings,
        search_provider=StubSearchProvider(),
        page_reader=StubPageReader(),
        model_steps=StubCompanyModelSteps(),
    )
    requirement_sheet = RequirementSheet(
        role_title="AI Platform Engineer",
        title_anchor_terms=["AI Platform"],
        title_anchor_rationale="Title maps directly to the AI platform role anchor.",
        role_summary="Build AI platform systems.",
        must_have_capabilities=["LLM serving", "Kubernetes"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[_anchor()],
        scoring_rationale="Score platform fit.",
    )

    result = asyncio.run(
        service.discover_web(
            requirement_sheet=requirement_sheet,
            round_no=2,
            trigger_reason="low recall",
        )
    )

    assert result.plan.inferred_targets[0].name == "火山引擎"
    assert result.search_result_count == 1
    assert result.opened_page_count == 1
    assert result.plan.web_discovery_attempted is True


def test_company_discovery_service_retains_failed_step_artifact() -> None:
    settings = make_settings(mock_cts=True, bocha_api_key="bocha-key")
    service = CompanyDiscoveryService(
        settings,
        search_provider=StubSearchProvider(),
        page_reader=StubPageReader(),
        model_steps=FailingCompanyModelSteps(),
    )
    requirement_sheet = RequirementSheet(
        role_title="AI Platform Engineer",
        title_anchor_terms=["AI Platform"],
        title_anchor_rationale="Title maps directly to the AI platform role anchor.",
        role_summary="Build AI platform systems.",
        must_have_capabilities=["LLM serving", "Kubernetes"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[_anchor()],
        scoring_rationale="Score platform fit.",
    )

    with pytest.raises(RuntimeError, match="plan failed"):
        asyncio.run(
            service.discover_web(
                requirement_sheet=requirement_sheet,
                round_no=2,
                trigger_reason="low recall",
            )
        )

    assert service.last_call_artifacts == [
        {
            "stage": "company_discovery_plan",
            "prompt_name": "company_discovery_plan",
            "model_id": "openai-chat:qwen3.5-flash",
            "user_payload": {
                "DISCOVERY_INPUT": {
                    "role_title": "AI Platform Engineer",
                    "title_anchor_term": "AI Platform",
                    "must_have_capabilities": ["LLM serving", "Kubernetes"],
                    "preferred_domains": [],
                    "preferred_backgrounds": [],
                    "locations": ["上海"],
                    "exclusions": [],
                }
            },
            "user_prompt_text": "plan discovery prompt",
            "started_at": "2026-01-01T00:00:00+00:00",
            "latency_ms": 5,
            "status": "failed",
            "retries": 0,
            "output_retries": 2,
            "error_message": "plan failed",
        }
    ]


def test_company_discovery_model_steps_store_named_prompts() -> None:
    prompts = {
        "company_discovery_plan": LoadedPrompt(
            name="company_discovery_plan",
            path=Path("company_discovery_plan.md"),
            content="plan prompt",
            sha256="h1",
        ),
        "company_discovery_extract": LoadedPrompt(
            name="company_discovery_extract",
            path=Path("company_discovery_extract.md"),
            content="extract prompt",
            sha256="h2",
        ),
        "company_discovery_reduce": LoadedPrompt(
            name="company_discovery_reduce",
            path=Path("company_discovery_reduce.md"),
            content="reduce prompt",
            sha256="h3",
        ),
    }

    steps = CompanyDiscoveryModelSteps(make_settings(), prompts)

    assert steps.prompts == prompts


def test_company_discovery_model_steps_fail_fast_when_prompt_is_missing() -> None:
    with pytest.raises(ValueError, match="company_discovery_extract"):
        CompanyDiscoveryModelSteps(
            make_settings(),
            {
                "company_discovery_plan": LoadedPrompt(
                    name="company_discovery_plan",
                    path=Path("company_discovery_plan.md"),
                    content="plan prompt",
                    sha256="h1",
                ),
                "company_discovery_reduce": LoadedPrompt(
                    name="company_discovery_reduce",
                    path=Path("company_discovery_reduce.md"),
                    content="reduce prompt",
                    sha256="h3",
                ),
            },
        )


def test_company_discovery_model_steps_use_named_prompt_content(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __class_getitem__(cls, item):  # noqa: ANN001, N805
            del item
            return cls

        def __init__(self, **kwargs):  # noqa: ANN003
            captured["system_prompt"] = kwargs["system_prompt"]

    monkeypatch.setattr("seektalent.company_discovery.model_steps.Agent", FakeAgent)
    monkeypatch.setattr("seektalent.company_discovery.model_steps.build_model", lambda model_id: object())
    monkeypatch.setattr("seektalent.company_discovery.model_steps.build_output_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr("seektalent.company_discovery.model_steps.build_model_settings", lambda *args, **kwargs: {})

    prompts = {
        "company_discovery_plan": LoadedPrompt(
            name="company_discovery_plan",
            path=Path("company_discovery_plan.md"),
            content="plan system prompt",
            sha256="h1",
        ),
        "company_discovery_extract": LoadedPrompt(
            name="company_discovery_extract",
            path=Path("company_discovery_extract.md"),
            content="extract system prompt",
            sha256="h2",
        ),
        "company_discovery_reduce": LoadedPrompt(
            name="company_discovery_reduce",
            path=Path("company_discovery_reduce.md"),
            content="reduce system prompt",
            sha256="h3",
        ),
    }

    steps = CompanyDiscoveryModelSteps(make_settings(), prompts)

    steps._agent("company_discovery_extract", CompanyDiscoveryInput)

    assert captured["system_prompt"] == "extract system prompt"
