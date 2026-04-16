from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from experiments.openclaw_baseline.adapters import candidate_brief
from seektalent.clients.cts_client import CTSClient, CTSClientProtocol, MockCTSClient
from seektalent.config import AppSettings
from seektalent.evaluation import TOP_K
from seektalent.models import CTSQuery, ConstraintValue, ResumeCandidate, unique_strings
from seektalent.retrieval import serialize_keyword_query
from seektalent.tracing import RunTracer


class SearchCandidatesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_terms: list[str] = Field(min_length=1, max_length=3)
    native_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=TOP_K, ge=1, le=TOP_K)


@dataclass
class SearchCandidatesTool:
    settings: AppSettings
    tracer: RunTracer
    candidate_store: dict[str, ResumeCandidate] = field(default_factory=dict)
    client: CTSClientProtocol | None = None
    round_no: int = 0
    calls_used: int = 0
    round_new_resume_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = MockCTSClient(self.settings) if self.settings.mock_cts else CTSClient(self.settings)

    @property
    def max_attempts(self) -> int:
        return self.settings.search_max_attempts_per_round

    @property
    def max_pages(self) -> int:
        return self.settings.search_max_pages_per_round

    def start_round(self, round_no: int) -> None:
        self.round_no = round_no
        self.calls_used = 0
        self.round_new_resume_ids = []

    def tool_spec(self) -> dict[str, object]:
        return {
            "type": "function",
            "name": "search_candidates",
            "description": (
                "Search CTS candidates. Use only concise query terms and CTS-native filters. "
                "The round has hard page and attempt limits."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "native_filters": {
                        "type": "object",
                        "default": {},
                        "additionalProperties": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "integer"},
                                {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            ]
                        },
                    },
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": TOP_K, "default": TOP_K},
                },
                "required": ["query_terms"],
            },
        }

    async def invoke_async(self, raw_arguments: str | dict[str, object]) -> dict[str, object]:
        args = SearchCandidatesArgs.model_validate(
            json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        )
        self.calls_used += 1
        if self.calls_used > self.max_attempts or args.page > self.max_pages:
            payload = {
                "status": "budget_exhausted",
                "round_no": self.round_no,
                "attempt_no": self.calls_used,
                "max_attempts": self.max_attempts,
                "max_pages": self.max_pages,
                "requested_page": args.page,
            }
            self.tracer.append_jsonl("tool_calls.jsonl", payload)
            return payload

        query_terms = unique_strings(args.query_terms)
        query = CTSQuery(
            query_role="exploit",
            query_terms=query_terms,
            keyword_query=serialize_keyword_query(query_terms),
            native_filters=args.native_filters,
            page=args.page,
            page_size=args.page_size,
            rationale=f"OpenClaw baseline round {self.round_no}.",
        )
        started = perf_counter()
        result = await self.client.search(
            query,
            round_no=self.round_no,
            trace_id=f"openclaw-r{self.round_no:02d}-a{self.calls_used:02d}",
        )
        new_resume_ids: list[str] = []
        for candidate in result.candidates:
            if candidate.resume_id not in self.candidate_store:
                new_resume_ids.append(candidate.resume_id)
                self.round_new_resume_ids.append(candidate.resume_id)
            self.candidate_store[candidate.resume_id] = candidate
        payload = {
            "status": "ok",
            "round_no": self.round_no,
            "attempt_no": self.calls_used,
            "latency_ms": max(1, int((perf_counter() - started) * 1000)),
            "query_terms": query_terms,
            "native_filters": args.native_filters,
            "page": args.page,
            "page_size": args.page_size,
            "raw_candidate_count": result.raw_candidate_count,
            "new_resume_ids": new_resume_ids,
            "candidates": [candidate_brief(candidate) for candidate in result.candidates],
            "adapter_notes": result.adapter_notes,
            "response_message": result.response_message,
        }
        self.tracer.append_jsonl("tool_calls.jsonl", payload)
        return payload
