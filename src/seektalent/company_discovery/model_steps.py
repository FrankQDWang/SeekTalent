from __future__ import annotations

from typing import Any, cast

from pydantic_ai import Agent

from seektalent.company_discovery.models import (
    CompanyDiscoveryInput,
    CompanyEvidenceExtraction,
    CompanySearchPlan,
    CompanySearchTask,
    PageReadResult,
    TargetCompanyCandidate,
    TargetCompanyPlan,
    WebSearchResult,
)
from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec


class CompanyDiscoveryModelSteps:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    async def plan_search_queries(self, discovery_input: CompanyDiscoveryInput) -> list[CompanySearchTask]:
        result = await self._agent(
            CompanySearchPlan,
            "Generate bounded web search tasks for finding evidence-backed source companies.",
        ).run(_plan_prompt(discovery_input))
        plan = cast(CompanySearchPlan, result.output)
        return plan.tasks[: self.settings.company_discovery_max_search_calls]

    async def extract_company_evidence(
        self,
        page_reads: list[PageReadResult],
        search_results: list[WebSearchResult],
    ) -> list[TargetCompanyCandidate]:
        result = await self._agent(
            CompanyEvidenceExtraction,
            "Extract target company candidates only when the provided evidence supports them.",
        ).run(_evidence_prompt(page_reads, search_results))
        extraction = cast(CompanyEvidenceExtraction, result.output)
        return extraction.candidates

    async def reduce_company_plan(
        self,
        candidates: list[TargetCompanyCandidate],
        discovery_input: CompanyDiscoveryInput,
        *,
        stop_reason: str,
    ) -> TargetCompanyPlan:
        result = await self._agent(
            TargetCompanyPlan,
            "Merge aliases, remove duplicates, and return a concise target company plan.",
        ).run(_reduce_prompt(candidates, discovery_input, stop_reason=stop_reason))
        return cast(TargetCompanyPlan, result.output)

    def _agent(self, output_type: type[Any], system_prompt: str) -> Agent[None, Any]:
        model = build_model(self.settings.company_discovery_model)
        return cast(
            Agent[None, Any],
            Agent(
                model=model,
                output_type=build_output_spec(self.settings.company_discovery_model, model, output_type),
                system_prompt=system_prompt,
                model_settings=build_model_settings(
                    self.settings,
                    self.settings.company_discovery_model,
                    reasoning_effort=self.settings.company_discovery_reasoning_effort,
                ),
                retries=0,
                output_retries=2,
            ),
        )


def _plan_prompt(discovery_input: CompanyDiscoveryInput) -> str:
    return "\n\n".join(
        [
            "TASK\nCreate 1-4 web search tasks for discovering recruiting source companies.",
            "Do not output company conclusions. Only output search tasks.",
            f"INPUT\n{discovery_input.model_dump_json()}",
        ]
    )


def _evidence_prompt(page_reads: list[PageReadResult], search_results: list[WebSearchResult]) -> str:
    return "\n\n".join(
        [
            "TASK\nExtract possible target/source companies only from explicit page or search evidence.",
            "Rules: do not invent companies; reject generic technology users; prefer companies with similar teams, "
            "products, industry, role function, or tech stack. Use source='web_inferred'. Use source_type='web' "
            "inside evidence. Use search_usage='keyword_term' unless evidence says a hard company filter is required.",
            f"PAGES\n{[item.model_dump(mode='json') for item in page_reads]}",
            f"SEARCH_RESULTS\n{[item.model_dump(mode='json') for item in search_results[:20]]}",
        ]
    )


def _reduce_prompt(
    candidates: list[TargetCompanyCandidate],
    discovery_input: CompanyDiscoveryInput,
    *,
    stop_reason: str,
) -> str:
    return "\n\n".join(
        [
            "TASK\nReturn a TargetCompanyPlan using inferred_targets for accepted web-discovered companies.",
            "Merge aliases and duplicates. Put weak or unsupported names into holdout_companies/rejected_companies "
            "as strings. Keep only evidence-backed companies.",
            f"INPUT\n{discovery_input.model_dump_json()}",
            f"CANDIDATES\n{[item.model_dump(mode='json') for item in candidates]}",
            f"STOP_REASON\n{stop_reason}",
        ]
    )
