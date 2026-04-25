from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any, Awaitable, TypeVar

from seektalent.company_discovery.bocha_provider import BochaWebSearchProvider
from seektalent.company_discovery.model_steps import CompanyDiscoveryModelSteps
from seektalent.company_discovery.models import (
    CompanyDiscoveryInput,
    CompanyDiscoveryResult,
    CompanySearchTask,
    PageReadResult,
    SearchRerankResult,
    TargetCompanyCandidate,
    TargetCompanyPlan,
    WebSearchResult,
)
from seektalent.company_discovery.page_reader import PageReader
from seektalent.config import AppSettings
from seektalent.models import RequirementSheet
from seektalent.prompting import LoadedPrompt

T = TypeVar("T")


class CompanyDiscoveryService:
    def __init__(
        self,
        settings: AppSettings,
        *,
        search_provider: Any | None = None,
        page_reader: Any | None = None,
        model_steps: Any | None = None,
        prompts: dict[str, LoadedPrompt] | None = None,
    ) -> None:
        self.settings = settings
        self.search_provider = search_provider or BochaWebSearchProvider(settings)
        self.page_reader = page_reader or PageReader()
        self.last_call_artifacts: list[dict[str, object]] = []
        if model_steps is not None:
            self.model_steps = model_steps
        else:
            if prompts is None:
                raise ValueError("CompanyDiscoveryService requires prompts when model_steps is not provided.")
            self.model_steps = CompanyDiscoveryModelSteps(settings, prompts)

    async def discover_web(
        self,
        *,
        requirement_sheet: RequirementSheet,
        round_no: int,
        trigger_reason: str,
    ) -> CompanyDiscoveryResult:
        del round_no
        self.last_call_artifacts = []
        if not self.settings.bocha_api_key:
            raise ValueError("SEEKTALENT_BOCHA_API_KEY is required when company web discovery runs.")

        deadline = perf_counter() + self.settings.company_discovery_timeout_seconds
        discovery_input = build_discovery_input(requirement_sheet)
        search_tasks = await self._run_model_step(self.model_steps.plan_search_queries(discovery_input), deadline)
        search_results = await self._search(search_tasks, deadline)
        if not search_results:
            return _empty_result(
                trigger_reason=trigger_reason,
                discovery_input=discovery_input,
                stop_reason="no_accepted_companies",
            )
        reranked_results, page_reads = await self._read_pages(discovery_input, search_results, deadline)
        candidates = await self._run_model_step(
            self.model_steps.extract_company_evidence(page_reads, search_results),
            deadline,
        )
        plan = await self._build_plan(candidates, discovery_input, deadline)

        return _result(
            plan=plan,
            search_tasks=search_tasks,
            search_results=search_results,
            reranked_results=reranked_results,
            page_reads=page_reads,
            evidence_candidates=candidates,
            trigger_reason=trigger_reason,
            discovery_input=discovery_input,
        )

    async def _search(self, tasks: list[CompanySearchTask], deadline: float) -> list[WebSearchResult]:
        results: list[WebSearchResult] = []
        for task in tasks[: self.settings.company_discovery_max_search_calls]:
            results.extend(
                await self._with_deadline(
                    self.search_provider.search(
                        task.query,
                        count=self.settings.company_discovery_max_results_per_query,
                    ),
                    deadline,
                )
            )
        return _dedupe_results(results)

    async def _read_pages(
        self,
        discovery_input: CompanyDiscoveryInput,
        search_results: list[WebSearchResult],
        deadline: float,
    ) -> tuple[list[SearchRerankResult], list[PageReadResult]]:
        if not search_results or self.settings.company_discovery_max_open_pages == 0:
            return [], []
        reranked = await self._with_deadline(
            self.search_provider.rerank(
                _rerank_query(discovery_input),
                search_results,
                top_n=self.settings.company_discovery_max_open_pages,
            ),
            deadline,
        )
        page_reads: list[PageReadResult] = []
        for item in reranked[: self.settings.company_discovery_max_open_pages]:
            page_reads.append(await self._with_deadline(self.page_reader.read(item.url, timeout_s=4), deadline))
        return reranked, page_reads

    async def _build_plan(
        self,
        candidates: list[TargetCompanyCandidate],
        discovery_input: CompanyDiscoveryInput,
        deadline: float,
    ) -> TargetCompanyPlan:
        accepted = _accepted_candidates(candidates, self.settings.company_discovery_min_confidence)
        if not accepted:
            return TargetCompanyPlan(web_discovery_attempted=True, stop_reason="no_accepted_companies")
        plan = await self._run_model_step(
            self.model_steps.reduce_company_plan(accepted, discovery_input, stop_reason="completed"),
            deadline,
        )
        return _normalize_plan(plan, accepted, self.settings.company_discovery_accepted_company_limit)

    async def _with_deadline(self, awaitable: Awaitable[T], deadline: float) -> T:
        remaining = deadline - perf_counter()
        if remaining <= 0:
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            raise TimeoutError
        return await asyncio.wait_for(awaitable, timeout=remaining)

    async def _run_model_step(self, awaitable: Awaitable[T], deadline: float) -> T:
        if hasattr(self.model_steps, "last_call_artifact"):
            self.model_steps.last_call_artifact = None
        try:
            return await self._with_deadline(awaitable, deadline)
        finally:
            artifact = getattr(self.model_steps, "last_call_artifact", None)
            if artifact is not None:
                self.last_call_artifacts.append(dict(artifact))


def build_discovery_input(requirement_sheet: RequirementSheet) -> CompanyDiscoveryInput:
    return CompanyDiscoveryInput(
        role_title=requirement_sheet.role_title,
        title_anchor_term=requirement_sheet.title_anchor_term,
        must_have_capabilities=requirement_sheet.must_have_capabilities[:6],
        preferred_domains=requirement_sheet.preferences.preferred_domains[:4],
        preferred_backgrounds=requirement_sheet.preferences.preferred_backgrounds[:4],
        locations=requirement_sheet.hard_constraints.locations[:6],
        exclusions=requirement_sheet.exclusion_signals[:6],
    )


def _rerank_query(discovery_input: CompanyDiscoveryInput) -> str:
    return " ".join(
        [
            discovery_input.role_title,
            discovery_input.title_anchor_term,
            *discovery_input.must_have_capabilities[:4],
            "source companies",
        ]
    )


def _dedupe_results(results: list[WebSearchResult]) -> list[WebSearchResult]:
    seen: set[str] = set()
    output: list[WebSearchResult] = []
    for result in results:
        key = result.url.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output


def _accepted_candidates(candidates: list[TargetCompanyCandidate], min_confidence: float) -> list[TargetCompanyCandidate]:
    accepted = [
        item
        for item in candidates
        if item.confidence >= min_confidence and item.intent != "exclude" and item.search_usage not in {"exclude", "holdout"}
    ]
    return sorted(accepted, key=lambda item: (-item.confidence, -len(item.evidence), item.name.casefold()))


def _normalize_plan(
    plan: TargetCompanyPlan,
    accepted: list[TargetCompanyCandidate],
    limit: int,
) -> TargetCompanyPlan:
    inferred = _accepted_candidates(plan.inferred_targets, 0) or accepted
    inferred = inferred[:limit]
    return plan.model_copy(
        update={
            "explicit_targets": [],
            "inferred_targets": inferred,
            "web_discovery_attempted": True,
            "stop_reason": "completed" if inferred else "no_accepted_companies",
        }
    )


def _empty_result(
    *,
    trigger_reason: str,
    discovery_input: CompanyDiscoveryInput | None = None,
    stop_reason: str,
) -> CompanyDiscoveryResult:
    plan = TargetCompanyPlan(web_discovery_attempted=True, stop_reason=stop_reason)
    return CompanyDiscoveryResult(plan=plan, discovery_input=discovery_input, trigger_reason=trigger_reason)


def _result(
    *,
    plan: TargetCompanyPlan,
    search_tasks: list[CompanySearchTask],
    search_results: list[WebSearchResult],
    reranked_results: list[SearchRerankResult],
    page_reads: list[PageReadResult],
    evidence_candidates: list[TargetCompanyCandidate],
    trigger_reason: str,
    discovery_input: CompanyDiscoveryInput,
) -> CompanyDiscoveryResult:
    return CompanyDiscoveryResult(
        plan=plan,
        discovery_input=discovery_input,
        search_tasks=search_tasks,
        search_results=search_results,
        reranked_results=reranked_results,
        page_reads=page_reads,
        evidence_candidates=evidence_candidates,
        search_result_count=len(search_results),
        opened_page_count=len(page_reads),
        trigger_reason=trigger_reason,
    )
