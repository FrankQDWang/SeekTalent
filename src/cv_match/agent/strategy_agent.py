from __future__ import annotations

import asyncio
import re

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import FilterCondition, KeywordAttribution, SearchStrategy, unique_strings
from cv_match.prompting import LoadedPrompt, json_block

KEYWORD_PATTERNS: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "agent": ("agent", "智能体"),
    "pydantic ai": ("pydantic ai", "pydantic-ai"),
    "retrieval": ("retrieval", "检索", "召回"),
    "reflection": ("reflection", "反思", "self-reflection"),
    "trace": ("trace", "tracing", "可观察性", "日志"),
    "openapi": ("openapi",),
    "resume matching": ("resume", "简历", "候选人"),
    "parallel scoring": ("并发", "fan-out", "fan-in", "parallel"),
    "cli": ("cli", "命令行"),
    "ranking": ("ranking", "评分", "打分", "rerank"),
}
NEGATIVE_PATTERNS = {
    "frontend": ("frontend", "react", "前端"),
    "sales": ("sales", "销售"),
    "research": ("research", "算法研究", "paper", "论文"),
}
LOCATION_NAMES = ("上海", "北京", "深圳", "杭州", "remote", "远程")


class StrategyAgent:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.use_mock_backend = settings.llm_backend_mode != "openai-responses"
        self.agent: Agent[None, SearchStrategy] | None = None
        if not self.use_mock_backend:
            self.agent = Agent(
                model=OpenAIResponsesModel(settings.strategy_model),
                output_type=SearchStrategy,
                system_prompt=prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )

    def extract(self, *, jd: str, notes: str) -> SearchStrategy:
        if self.use_mock_backend:
            return self._extract_mock(jd=jd, notes=notes).normalized()
        return asyncio.run(self._extract_live(jd=jd, notes=notes)).normalized()

    async def _extract_live(self, *, jd: str, notes: str) -> SearchStrategy:
        assert self.agent is not None
        prompt = "\n\n".join(
            [
                json_block("JD", {"jd": jd}),
                json_block("NOTES", {"notes": notes}),
            ]
        )
        result = await self.agent.run(prompt)
        return result.output

    def _extract_mock(self, *, jd: str, notes: str) -> SearchStrategy:
        jd_lower = jd.casefold()
        notes_lower = notes.casefold()
        must_have: list[str] = []
        preferred: list[str] = []
        negative: list[str] = []
        attributions: list[KeywordAttribution] = []
        for canonical, aliases in KEYWORD_PATTERNS.items():
            in_jd = any(alias.casefold() in jd_lower for alias in aliases)
            in_notes = any(alias.casefold() in notes_lower for alias in aliases)
            if not (in_jd or in_notes):
                continue
            bucket = "must_have" if in_jd else "preferred"
            if bucket == "must_have":
                must_have.append(canonical)
            else:
                preferred.append(canonical)
            attributions.append(
                KeywordAttribution(
                    keyword=canonical,
                    source="jd" if in_jd else "notes",
                    bucket=bucket,
                    reason="Matched explicit keyword in input text.",
                )
            )
        for canonical, aliases in NEGATIVE_PATTERNS.items():
            if any(alias.casefold() in notes_lower for alias in aliases):
                negative.append(canonical)
                attributions.append(
                    KeywordAttribution(
                        keyword=canonical,
                        source="notes",
                        bucket="negative",
                        reason="Matched explicit exclusion wording in notes.",
                    )
                )
        hard_filters: list[FilterCondition] = []
        soft_filters: list[FilterCondition] = []
        for location in LOCATION_NAMES:
            if location.casefold() not in (jd_lower + "\n" + notes_lower):
                continue
            if re.search(rf"(必须|only|限定).{{0,6}}{re.escape(location)}", jd + "\n" + notes, re.IGNORECASE):
                hard_filters.append(
                    FilterCondition(
                        field="location",
                        value=location,
                        source="notes" if location.casefold() in notes_lower else "jd",
                        rationale="Location was expressed as mandatory.",
                        strictness="hard",
                    )
                )
            elif re.search(rf"(优先|prefer).{{0,6}}{re.escape(location)}", jd + "\n" + notes, re.IGNORECASE):
                soft_filters.append(
                    FilterCondition(
                        field="location",
                        value=location,
                        source="notes" if location.casefold() in notes_lower else "jd",
                        rationale="Location was expressed as a preference.",
                        strictness="soft",
                    )
                )
        if not must_have:
            fallback = re.findall(r"[A-Za-z][A-Za-z0-9\-\+\.]{2,}", jd)
            must_have = unique_strings(fallback[:4]) or ["python", "agent"]
        search_rationale = (
            f"Use {len(must_have)} must-have keywords, {len(preferred)} preferred keywords, "
            f"and {len(negative)} exclusions to build a CTS-safe structured query."
        )
        return SearchStrategy(
            must_have_keywords=must_have,
            preferred_keywords=preferred,
            negative_keywords=negative,
            hard_filters=hard_filters,
            soft_filters=soft_filters,
            keyword_attributions=attributions,
            search_rationale=search_rationale,
        )
