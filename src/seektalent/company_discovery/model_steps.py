from __future__ import annotations

from datetime import datetime
from time import perf_counter
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
from seektalent.prompting import LoadedPrompt
from seektalent.tracing import provider_usage_from_result


class CompanyDiscoveryModelSteps:
    def __init__(self, settings: AppSettings, prompts: dict[str, LoadedPrompt]) -> None:
        self.settings = settings
        missing = [
            name
            for name in [
                "company_discovery_plan",
                "company_discovery_extract",
                "company_discovery_reduce",
            ]
            if name not in prompts
        ]
        if missing:
            raise ValueError(f"Missing company discovery prompts: {', '.join(missing)}")
        self.prompts = prompts
        self.last_call_artifact: dict[str, object] | None = None

    async def plan_search_queries(self, discovery_input: CompanyDiscoveryInput) -> list[CompanySearchTask]:
        prompt_text = _plan_prompt(discovery_input)
        plan = cast(
            CompanySearchPlan,
            await self._run_step(
                "company_discovery_plan",
                CompanySearchPlan,
                user_payload={"DISCOVERY_INPUT": discovery_input.model_dump(mode="json")},
                user_prompt_text=prompt_text,
            ),
        )
        return plan.tasks[: self.settings.company_discovery_max_search_calls]

    async def extract_company_evidence(
        self,
        page_reads: list[PageReadResult],
        search_results: list[WebSearchResult],
    ) -> list[TargetCompanyCandidate]:
        prompt_text = _evidence_prompt(page_reads, search_results)
        extraction = cast(
            CompanyEvidenceExtraction,
            await self._run_step(
                "company_discovery_extract",
                CompanyEvidenceExtraction,
                user_payload={
                    "PAGE_READS": [item.model_dump(mode="json") for item in page_reads],
                    "SEARCH_RESULTS": [item.model_dump(mode="json") for item in search_results[:20]],
                },
                user_prompt_text=prompt_text,
            ),
        )
        return extraction.candidates

    async def reduce_company_plan(
        self,
        candidates: list[TargetCompanyCandidate],
        discovery_input: CompanyDiscoveryInput,
        *,
        stop_reason: str,
    ) -> TargetCompanyPlan:
        prompt_text = _reduce_prompt(candidates, discovery_input, stop_reason=stop_reason)
        return cast(
            TargetCompanyPlan,
            await self._run_step(
                "company_discovery_reduce",
                TargetCompanyPlan,
                user_payload={
                    "DISCOVERY_INPUT": discovery_input.model_dump(mode="json"),
                    "CANDIDATES": [item.model_dump(mode="json") for item in candidates],
                    "STOP_REASON": stop_reason,
                },
                user_prompt_text=prompt_text,
            ),
        )

    def _agent(self, prompt_name: str, output_type: type[Any]) -> Agent[None, Any]:
        model = build_model(self.settings.company_discovery_model)
        return cast(
            Agent[None, Any],
            Agent(
                model=model,
                output_type=build_output_spec(self.settings.company_discovery_model, model, output_type),
                system_prompt=self.prompts[prompt_name].content,
                model_settings=build_model_settings(
                    self.settings,
                    self.settings.company_discovery_model,
                    reasoning_effort=self.settings.company_discovery_reasoning_effort,
                ),
                retries=0,
                output_retries=2,
            ),
        )

    async def _run_step(
        self,
        prompt_name: str,
        output_type: type[Any],
        *,
        user_payload: dict[str, object],
        user_prompt_text: str,
    ) -> Any:
        self.last_call_artifact = None
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        started_clock = perf_counter()
        try:
            result = await self._agent(prompt_name, output_type).run(user_prompt_text)
        except Exception as exc:
            self.last_call_artifact = {
                "stage": prompt_name,
                "prompt_name": prompt_name,
                "model_id": self.settings.company_discovery_model,
                "user_payload": user_payload,
                "user_prompt_text": user_prompt_text,
                "started_at": started_at,
                "latency_ms": max(1, int((perf_counter() - started_clock) * 1000)),
                "status": "failed",
                "retries": 0,
                "output_retries": 2,
                "error_message": str(exc),
            }
            raise
        output = result.output
        structured_output = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
        self.last_call_artifact = {
            "stage": prompt_name,
            "prompt_name": prompt_name,
            "model_id": self.settings.company_discovery_model,
            "user_payload": user_payload,
            "user_prompt_text": user_prompt_text,
            "structured_output": structured_output,
            "started_at": started_at,
            "latency_ms": max(1, int((perf_counter() - started_clock) * 1000)),
            "status": "succeeded",
            "retries": 0,
            "output_retries": 2,
            "provider_usage": provider_usage_from_result(result),
        }
        return output


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
