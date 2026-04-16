from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from experiments.claude_code_baseline import CLAUDE_CODE_MAX_ROUNDS
from experiments.claude_code_baseline.adapters import candidate_brief
from seektalent.clients.cts_client import CTSClient, CTSClientProtocol, MockCTSClient
from seektalent.config import AppSettings, load_process_env
from seektalent.evaluation import TOP_K, persist_raw_resume_snapshot
from seektalent.models import CTSQuery, ConstraintValue, ResumeCandidate, unique_strings
from seektalent.retrieval import serialize_keyword_query


class SearchCandidatesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_terms: list[str] = Field(min_length=1, max_length=3)
    native_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=TOP_K, ge=1, le=TOP_K)


def tool_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query_terms": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
            "native_filters": {
                "type": "object",
                "default": {},
                "additionalProperties": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "integer"},
                        {"type": "array", "items": {"type": "string"}},
                    ]
                },
            },
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": TOP_K, "default": TOP_K},
        },
        "required": ["query_terms"],
    }


class CTSToolSession:
    def __init__(self, *, settings: AppSettings, run_dir: Path, client: CTSClientProtocol | None = None) -> None:
        self.settings = settings
        self.run_dir = run_dir
        self.client = client or (MockCTSClient(settings) if settings.mock_cts else CTSClient(settings))
        self.total_calls = 0
        self.first_search_resume_ids: list[str] = []
        self.candidate_store: dict[str, ResumeCandidate] = {}

    def _write_json(self, name: str, payload: object) -> None:
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_jsonl(self, name: str, payload: object) -> None:
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _persist_state(self, *, fatal_error: str | None = None) -> None:
        self._write_json(
            "cts_state.json",
            {
                "total_calls": self.total_calls,
                "first_search_resume_ids": self.first_search_resume_ids,
                "candidate_ids": list(self.candidate_store.keys()),
                "fatal_error": fatal_error,
            },
        )
        self._write_json(
            "candidates.json",
            {
                resume_id: candidate.model_dump(mode="json")
                for resume_id, candidate in self.candidate_store.items()
            },
        )

    async def search_candidates(self, raw_arguments: dict[str, object]) -> dict[str, object]:
        args = SearchCandidatesArgs.model_validate(raw_arguments)
        next_call = self.total_calls + 1
        if next_call > CLAUDE_CODE_MAX_ROUNDS or args.page > self.settings.search_max_pages_per_round:
            message = "Claude Code CTS budget exhausted."
            payload = {
                "status": "budget_exhausted",
                "attempt_no": next_call,
                "max_total_attempts": CLAUDE_CODE_MAX_ROUNDS,
                "max_pages": self.settings.search_max_pages_per_round,
                "requested_page": args.page,
            }
            self._append_jsonl("tool_calls.jsonl", payload)
            self._persist_state(fatal_error=message)
            return {**payload, "error_message": message}

        self.total_calls = next_call
        query_terms = unique_strings(args.query_terms)
        query = CTSQuery(
            query_role="exploit",
            query_terms=query_terms,
            keyword_query=serialize_keyword_query(query_terms),
            native_filters=args.native_filters,
            page=args.page,
            page_size=args.page_size,
            rationale=f"Claude Code baseline CTS round {self.total_calls}.",
        )
        started = perf_counter()
        try:
            result = await self.client.search(
                query,
                round_no=self.total_calls,
                trace_id=f"claude-code-cts{self.total_calls:02d}",
            )
        except Exception as exc:
            self._append_jsonl(
                "tool_calls.jsonl",
                {
                    "status": "failed",
                    "attempt_no": self.total_calls,
                    "query_terms": query_terms,
                    "native_filters": args.native_filters,
                    "page": args.page,
                    "page_size": args.page_size,
                    "error_message": str(exc),
                },
            )
            self._persist_state(fatal_error=str(exc))
            raise

        if not self.first_search_resume_ids:
            self.first_search_resume_ids = [candidate.resume_id for candidate in result.candidates[:TOP_K]]
        new_resume_ids: list[str] = []
        for candidate in result.candidates:
            if candidate.resume_id not in self.candidate_store:
                new_resume_ids.append(candidate.resume_id)
            self.candidate_store[candidate.resume_id] = candidate
            persist_raw_resume_snapshot(run_dir=self.run_dir, candidate=candidate)

        payload = {
            "status": "ok",
            "attempt_no": self.total_calls,
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
        self._append_jsonl("tool_calls.jsonl", payload)
        self._persist_state()
        return payload


def _response(message_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


async def handle_message(session: CTSToolSession, message: dict[str, Any]) -> dict[str, object] | None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        return _response(
            message_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "seektalent-cts", "version": "claude_code"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _response(message_id, {})
    if method == "tools/list":
        return _response(
            message_id,
            {
                "tools": [
                    {
                        "name": "search_candidates",
                        "description": "Search CTS candidates using concise query terms and CTS-native filters.",
                        "inputSchema": tool_schema(),
                    }
                ]
            },
        )
    if method == "tools/call":
        params = message.get("params") or {}
        if not isinstance(params, dict) or params.get("name") != "search_candidates":
            return _error(message_id, -32602, "Unsupported tool call.")
        try:
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object.")
            result = await session.search_candidates(arguments)
            return _response(
                message_id,
                {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False},
            )
        except Exception as exc:
            return _response(
                message_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
    if message_id is None:
        return None
    return _error(message_id, -32601, f"Unknown method: {method}")


async def serve(session: CTSToolSession) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = await handle_message(session, message)
        except Exception as exc:
            response = _error(None, -32603, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    load_process_env(args.env_file)
    settings = AppSettings(_env_file=args.env_file)
    if not settings.mock_cts:
        settings.require_cts_credentials()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(serve(CTSToolSession(settings=settings, run_dir=run_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
