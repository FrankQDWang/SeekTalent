from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from experiments.openclaw_baseline import (
    OPENCLAW_AGENT_ID,
    OPENCLAW_GATEWAY_BASE_URL,
    OPENCLAW_MAX_ROUNDS,
    OPENCLAW_MODEL,
)
from experiments.openclaw_baseline.adapters import (
    candidate_rows,
    ranked_candidates_from_ids,
    seen_candidate_briefs,
    shortlist_briefs,
)
from experiments.openclaw_baseline.cts_tools import SearchCandidatesTool
from experiments.openclaw_baseline.judge_eval import evaluate_openclaw_run
from experiments.openclaw_baseline.wandb_logging import log_openclaw_to_wandb
from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationResult, TOP_K
from seektalent.prompting import PromptRegistry, json_block
from seektalent.tracing import RunTracer

ROUND_INSTRUCTIONS = """You are the OpenClaw generic baseline for a CTS resume benchmark.

Rules:
- Use the search_candidates tool for CTS access. Do not invent candidates.
- The only valid shortlist ids are resume_ids you have already seen in CURRENT_SHORTLIST or tool outputs.
- You must perform at least one CTS search in every round before returning a shortlist snapshot.
- Return only one JSON object with this schema:
  {"action":"continue|stop","summary":"...","stop_reason":"... or null","ranked_resume_ids":["resume-id", "..."]}
- ranked_resume_ids must be unique, ordered best-first, and contain at most 10 ids.
- Set action=stop only if the shortlist is already strong enough or more search is low value.
"""


class ShortlistSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    summary: str = Field(min_length=1)
    stop_reason: str | None = None
    ranked_resume_ids: list[str] = Field(min_length=1, max_length=TOP_K)


@dataclass(frozen=True)
class OpenClawRunResult:
    run_id: str
    run_dir: Path
    trace_log_path: Path
    rounds_executed: int
    stop_reason: str
    round_01_candidates: list[dict[str, object]]
    final_candidates: list[dict[str, object]]
    evaluation_result: EvaluationResult


class OpenClawResponsesClient:
    def __init__(
        self,
        *,
        base_url: str,
        agent_id: str = OPENCLAW_AGENT_ID,
        gateway_token: str | None = None,
        timeout_seconds: float = 90.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = _normalize_gateway_base_url(base_url)
        self.agent_id = agent_id
        self.gateway_token = gateway_token
        headers = {"x-openclaw-agent-id": agent_id}
        if gateway_token:
            headers["Authorization"] = f"Bearer {gateway_token}"
        self.client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    def create_response(self, payload: dict[str, object]) -> dict[str, object]:
        response = self.client.post("/v1/responses", json=payload)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.client.close()


def _message_input(text: str) -> list[dict[str, object]]:
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]


def _normalize_gateway_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1/responses"):
        return normalized[: -len("/v1/responses")]
    return normalized


def _round_prompt(
    *,
    job_title: str,
    jd: str,
    notes: str,
    round_no: int,
    current_shortlist_ids: list[str],
    tool_runner: SearchCandidatesTool,
) -> str:
    blocks = [
        json_block("ROUND_META", {"round_no": round_no, "max_rounds": OPENCLAW_MAX_ROUNDS, "top_k": TOP_K}),
        json_block("JOB", {"job_title": job_title, "jd": jd, "notes": notes}),
        json_block("CURRENT_SHORTLIST", {"candidates": shortlist_briefs(current_shortlist_ids, tool_runner.candidate_store)}),
        json_block("SEEN_CANDIDATES", {"candidates": seen_candidate_briefs(tool_runner.candidate_store)}),
        json_block(
            "TOOL_BUDGET",
            {
                "max_attempts_per_round": tool_runner.max_attempts,
                "max_pages_per_round": tool_runner.max_pages,
                "page_size_cap": TOP_K,
            },
        ),
    ]
    return "\n\n".join(blocks)


def _extract_function_calls(body: dict[str, object]) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    for item in body.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        call_id = str(item.get("call_id") or item.get("id") or "")
        name = str(item.get("name") or "")
        arguments = item.get("arguments")
        if not call_id or not name or arguments is None:
            continue
        calls.append({"call_id": call_id, "name": name, "arguments": str(arguments)})
    return calls


def _extract_text(body: dict[str, object]) -> str:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    parts: list[str] = []
    for item in body.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
    return "\n".join(parts)


def _parse_snapshot(text: str) -> ShortlistSnapshot:
    return ShortlistSnapshot.model_validate(json.loads(text))


async def run_openclaw_round(
    *,
    responses_client: OpenClawResponsesClient,
    tool_runner: SearchCandidatesTool,
    tracer: RunTracer,
    prompt: str,
    round_no: int,
) -> ShortlistSnapshot:
    previous_response_id: str | None = None
    next_input: object = _message_input(prompt)
    repair_attempted = False
    for step in range(tool_runner.max_attempts + 3):
        request_payload: dict[str, object] = {
            "model": OPENCLAW_MODEL,
            "instructions": ROUND_INSTRUCTIONS,
            "input": next_input,
            "tools": [tool_runner.tool_spec()],
        }
        if previous_response_id is not None:
            request_payload["previous_response_id"] = previous_response_id
        started = perf_counter()
        body = responses_client.create_response(request_payload)
        latency_ms = max(1, int((perf_counter() - started) * 1000))
        tracer.append_jsonl(
            "openclaw_transcript.jsonl",
            {
                "round_no": round_no,
                "step": step + 1,
                "request": request_payload,
                "response": body,
                "latency_ms": latency_ms,
            },
        )
        previous_response_id = str(body.get("id") or "")
        calls = _extract_function_calls(body)
        if calls:
            outputs: list[dict[str, object]] = []
            for call in calls:
                if call["name"] != "search_candidates":
                    raise ValueError(f"Unsupported tool call: {call['name']}")
                result = await tool_runner.invoke_async(call["arguments"])
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
            next_input = outputs
            continue

        if tool_runner.calls_used == 0:
            raise ValueError(f"OpenClaw round {round_no} ended without a CTS search.")
        text = _extract_text(body).strip()
        if not text:
            raise ValueError(f"OpenClaw round {round_no} returned neither tool calls nor text.")
        try:
            return _parse_snapshot(text)
        except (json.JSONDecodeError, ValidationError) as exc:
            if repair_attempted:
                raise ValueError(f"OpenClaw round {round_no} returned invalid shortlist JSON: {text}") from exc
            repair_attempted = True
            next_input = _message_input(
                'Return only valid JSON with keys "action", "summary", "stop_reason", and "ranked_resume_ids".'
            )
    raise RuntimeError(f"OpenClaw round {round_no} exceeded the local turn step limit.")


def _run_summary(
    *,
    rounds_executed: int,
    stop_reason: str,
    round_01_ids: list[str],
    final_ids: list[str],
) -> str:
    lines = [
        "# OpenClaw Baseline Summary",
        "",
        f"- Rounds executed: `{rounds_executed}`",
        f"- Stop reason: `{stop_reason}`",
        f"- Round 1 shortlist: `{', '.join(round_01_ids)}`",
        f"- Final shortlist: `{', '.join(final_ids)}`",
    ]
    return "\n".join(lines)


async def run_openclaw_baseline(
    *,
    job_title: str,
    jd: str,
    notes: str,
    settings: AppSettings,
    gateway_base_url: str | None = None,
    gateway_token: str | None = None,
    agent_id: str = OPENCLAW_AGENT_ID,
    responses_client: OpenClawResponsesClient | None = None,
) -> OpenClawRunResult:
    tracer = RunTracer(settings.runs_path)
    tracer.write_json(
        "run_config.json",
        {
            "gateway_base_url": gateway_base_url or OPENCLAW_GATEWAY_BASE_URL,
            "agent_id": agent_id,
            "backing_model": settings.controller_model,
            "judge_model": settings.effective_judge_model,
            "max_rounds": OPENCLAW_MAX_ROUNDS,
            "search_max_pages_per_round": settings.search_max_pages_per_round,
            "search_max_attempts_per_round": settings.search_max_attempts_per_round,
            "mock_cts": settings.mock_cts,
        },
    )
    tracer.emit("run_started", summary="Starting OpenClaw baseline run.")
    owned_client = responses_client is None
    if responses_client is None:
        responses_client = OpenClawResponsesClient(
            base_url=gateway_base_url or OPENCLAW_GATEWAY_BASE_URL,
            agent_id=agent_id,
            gateway_token=gateway_token,
        )
    tool_runner = SearchCandidatesTool(settings=settings, tracer=tracer)
    prompt_registry = PromptRegistry(settings.prompt_dir)
    judge_prompt = prompt_registry.load("judge")
    round_01_candidates = None
    current_shortlist_ids: list[str] = []
    stop_reason = "max_rounds_reached"
    rounds_executed = 0
    try:
        for round_no in range(1, OPENCLAW_MAX_ROUNDS + 1):
            previous_shortlist_ids = list(current_shortlist_ids)
            tool_runner.start_round(round_no)
            tracer.emit("round_started", round_no=round_no, summary="Starting OpenClaw round.")
            snapshot = await run_openclaw_round(
                responses_client=responses_client,
                tool_runner=tool_runner,
                tracer=tracer,
                prompt=_round_prompt(
                    job_title=job_title,
                    jd=jd,
                    notes=notes,
                    round_no=round_no,
                    current_shortlist_ids=current_shortlist_ids,
                    tool_runner=tool_runner,
                ),
                round_no=round_no,
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/shortlist_snapshot.json",
                snapshot.model_dump(mode="json"),
            )
            ranked = ranked_candidates_from_ids(snapshot.ranked_resume_ids, tool_runner.candidate_store)
            current_shortlist_ids = [candidate.resume_id for candidate in ranked]
            if round_01_candidates is None:
                round_01_candidates = list(ranked)
            rounds_executed = round_no
            tracer.emit(
                "round_completed",
                round_no=round_no,
                status="succeeded",
                summary=snapshot.summary,
                artifact_paths=[f"rounds/round_{round_no:02d}/shortlist_snapshot.json"],
                payload={
                    "round_new_resume_ids": list(tool_runner.round_new_resume_ids),
                    "shortlist_ids": current_shortlist_ids,
                },
            )
            if snapshot.action == "stop":
                stop_reason = snapshot.stop_reason or "openclaw_stop"
                break
            if round_no == OPENCLAW_MAX_ROUNDS:
                stop_reason = "max_rounds_reached"
                break
            if not tool_runner.round_new_resume_ids and current_shortlist_ids == previous_shortlist_ids:
                stop_reason = "no_progress_repeated_results"
                break
        if round_01_candidates is None:
            raise ValueError("OpenClaw did not produce a valid round_01 shortlist.")
        final_candidates = ranked_candidates_from_ids(current_shortlist_ids, tool_runner.candidate_store)
        tracer.write_json("round_01_candidates.json", candidate_rows(round_01_candidates))
        tracer.write_json("final_candidates.json", candidate_rows(final_candidates))
        tracer.write_text(
            "run_summary.md",
            _run_summary(
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                round_01_ids=[candidate.resume_id for candidate in round_01_candidates],
                final_ids=[candidate.resume_id for candidate in final_candidates],
            ),
        )
        evaluation_artifacts = await evaluate_openclaw_run(
            settings=settings,
            prompt=judge_prompt,
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            jd=jd,
            notes=notes,
            round_01_candidates=round_01_candidates,
            final_candidates=final_candidates,
            rounds_executed=rounds_executed,
        )
        tracer.emit(
            "evaluation_completed",
            status="succeeded",
            summary=(
                f"round_01 total={evaluation_artifacts.result.round_01.total_score:.4f}; "
                f"final total={evaluation_artifacts.result.final.total_score:.4f}"
            ),
            artifact_paths=[str(evaluation_artifacts.path.relative_to(tracer.run_dir))],
        )
        log_openclaw_to_wandb(
            settings=settings,
            artifact_root=tracer.run_dir,
            evaluation=evaluation_artifacts.result,
            rounds_executed=rounds_executed,
        )
        tracer.emit("run_finished", status="succeeded", stop_reason=stop_reason, summary="OpenClaw baseline finished.")
        return OpenClawRunResult(
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            trace_log_path=tracer.trace_log_path,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            round_01_candidates=candidate_rows(round_01_candidates),
            final_candidates=candidate_rows(final_candidates),
            evaluation_result=evaluation_artifacts.result,
        )
    except Exception as exc:
        tracer.emit("run_failed", status="failed", summary=str(exc), error_message=str(exc))
        raise
    finally:
        if owned_client:
            responses_client.close()
        tracer.close()
