from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter, sleep
from typing import Callable, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from experiments.baseline_evaluation import evaluate_baseline_run
from experiments.baseline_wandb import log_baseline_failure_to_wandb, log_baseline_to_wandb
from experiments.claude_code_baseline import (
    CLAUDE_CODE_MAX_ROUNDS,
    CLAUDE_CODE_MODEL_ALIAS,
    CLAUDE_CODE_VERSION,
)
from experiments.claude_code_baseline.adapters import candidate_rows, ranked_candidates_from_ids
from experiments.claude_code_baseline.router import (
    free_local_port,
    isolated_process_env,
    write_claude_settings,
    write_router_config,
)
from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationResult, TOP_K
from seektalent.models import ResumeCandidate
from seektalent.prompting import PromptRegistry, json_block
from seektalent.tracing import RunTracer

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class ClaudeShortlist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    stop_reason: str | None = None
    ranked_resume_ids: list[str] = Field(min_length=1, max_length=TOP_K)


@dataclass(frozen=True)
class ClaudeCodeRunResult:
    run_id: str
    run_dir: Path
    trace_log_path: Path
    rounds_executed: int
    stop_reason: str
    round_01_candidates: list[dict[str, object]]
    final_candidates: list[dict[str, object]]
    evaluation_result: EvaluationResult


def run_process(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def wait_for_port(port: int, *, timeout_seconds: float = 15.0) -> None:
    deadline = perf_counter() + timeout_seconds
    while perf_counter() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        sleep(0.25)
    raise RuntimeError(f"CCR did not listen on 127.0.0.1:{port} within {timeout_seconds:g}s.")


def _write_mcp_config(*, path: Path, settings: AppSettings, env_file: str | Path, run_dir: Path) -> Path:
    project_root = settings.project_root
    pythonpath = os.pathsep.join([str(project_root), str(project_root / "src")])
    config = {
        "mcpServers": {
            "seektalent_cts": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "experiments.claude_code_baseline.cts_mcp",
                    "--env-file",
                    str(Path(env_file).resolve()),
                    "--run-dir",
                    str(run_dir),
                ],
                "env": {"PYTHONPATH": pythonpath},
            }
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _build_prompt(*, job_title: str, jd: str, notes: str, settings: AppSettings) -> str:
    instructions = """You are the Claude Code generic baseline for a CTS resume benchmark.

Rules:
- Use only the MCP tool search_candidates for CTS access. Do not invent candidates.
- One accepted search_candidates call equals one benchmark round. You have at most 10 accepted CTS calls.
- The final ranked_resume_ids must be unique and must come from candidates returned by search_candidates.
- query_terms must contain 1-3 concise terms. page must stay within the configured page budget.
- Prefer a strong top-10 shortlist over exhaustive exploration.
- Return only the final JSON object required by the provided JSON schema."""
    blocks = [
        instructions,
        json_block("JOB", {"job_title": job_title, "jd": jd, "notes": notes}),
        json_block(
            "TOOL_BUDGET",
            {
                "max_total_cts_calls": CLAUDE_CODE_MAX_ROUNDS,
                "max_pages_per_round": settings.search_max_pages_per_round,
                "max_attempts_per_round": settings.search_max_attempts_per_round,
                "page_size_cap": TOP_K,
            },
        ),
    ]
    return "\n\n".join(blocks)


def _extract_json_text(stdout: str) -> str:
    stripped = stdout.strip()
    if not stripped:
        raise ValueError("Claude Code returned empty stdout.")
    try:
        body = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(body, dict):
        result = body.get("result")
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
    return stripped


def parse_shortlist(stdout: str) -> ClaudeShortlist:
    text = _extract_json_text(stdout)
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    shortlist = ClaudeShortlist.model_validate(json.loads(text))
    if len(shortlist.ranked_resume_ids) != len(set(shortlist.ranked_resume_ids)):
        raise ValueError("Claude Code final ranked_resume_ids contains duplicates.")
    return shortlist


def _load_cts_state(run_dir: Path) -> dict[str, object]:
    path = run_dir / "cts_state.json"
    if not path.exists():
        return {"total_calls": 0, "first_search_resume_ids": [], "fatal_error": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_candidate_store(run_dir: Path) -> dict[str, ResumeCandidate]:
    path = run_dir / "candidates.json"
    if not path.exists():
        return {}
    body = json.loads(path.read_text(encoding="utf-8"))
    return {
        resume_id: ResumeCandidate.model_validate(payload)
        for resume_id, payload in body.items()
    }


def _rounds_executed(run_dir: Path) -> int:
    return int(_load_cts_state(run_dir).get("total_calls") or 0)


def _start_router(
    *,
    env: dict[str, str],
    cwd: Path,
    port: int,
    tracer: RunTracer,
    process_runner: ProcessRunner,
) -> None:
    completed = process_runner(["ccr", "start"], env=env, cwd=str(cwd), timeout=30)
    tracer.write_json(
        "ccr_start.json",
        {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
    )
    if completed.returncode != 0:
        raise RuntimeError(f"CCR start failed: {completed.stderr or completed.stdout}")
    wait_for_port(port)


def _stop_router(*, env: dict[str, str], cwd: Path, tracer: RunTracer, process_runner: ProcessRunner) -> None:
    try:
        completed = process_runner(["ccr", "stop"], env=env, cwd=str(cwd), timeout=30)
        tracer.write_json(
            "ccr_stop.json",
            {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
        )
    except Exception as exc:  # noqa: BLE001
        tracer.write_json("ccr_stop.json", {"returncode": None, "error_message": str(exc)})


def _claude_command(*, settings_path: Path, mcp_config_path: Path, prompt: str) -> list[str]:
    return [
        "claude",
        "--bare",
        "--print",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--settings",
        str(settings_path),
        "--mcp-config",
        str(mcp_config_path),
        "--strict-mcp-config",
        "--json-schema",
        json.dumps(ClaudeShortlist.model_json_schema(), ensure_ascii=False),
        "--model",
        CLAUDE_CODE_MODEL_ALIAS,
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        "",
        prompt,
    ]


def _run_summary(
    *,
    rounds_executed: int,
    stop_reason: str,
    round_01_ids: list[str],
    final_ids: list[str],
) -> str:
    return "\n".join(
        [
            "# Claude Code Baseline Summary",
            "",
            f"- Rounds executed: `{rounds_executed}`",
            f"- Stop reason: `{stop_reason}`",
            f"- Round 1 shortlist: `{', '.join(round_01_ids)}`",
            f"- Final shortlist: `{', '.join(final_ids)}`",
        ]
    )


async def run_claude_code_baseline(
    *,
    job_title: str,
    jd: str,
    notes: str,
    settings: AppSettings,
    env_file: str | Path = ".env",
    process_runner: ProcessRunner = run_process,
    manage_router: bool = True,
    timeout_seconds: int = 900,
) -> ClaudeCodeRunResult:
    tracer = RunTracer(settings.artifacts_path)
    prompt_registry = PromptRegistry(settings.prompt_dir)
    judge_prompt = prompt_registry.load("judge")
    port = free_local_port()
    router_token = f"sk-{uuid.uuid4().hex}"
    run_home = tracer.run_dir / "home"
    process_env = isolated_process_env(env_file=env_file, home_dir=run_home)
    process_env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    process_env["ANTHROPIC_AUTH_TOKEN"] = router_token
    process_env["ANTHROPIC_API_KEY"] = router_token
    process_env["NO_PROXY"] = "127.0.0.1,localhost"
    process_env["DISABLE_TELEMETRY"] = "1"
    process_env["DISABLE_COST_WARNINGS"] = "1"
    write_router_config(home_dir=run_home, settings=settings, env_file=env_file, port=port, api_key=router_token)
    settings_path = write_claude_settings(path=tracer.run_dir / "claude_settings.json", port=port, api_key=router_token)
    mcp_config_path = _write_mcp_config(
        path=tracer.run_dir / "mcp_config.json",
        settings=settings,
        env_file=env_file,
        run_dir=tracer.run_dir,
    )
    tracer.write_json(
        "run_config.json",
        {
            "backing_model": settings.controller_model_id,
            "judge_model": settings.judge_model_id,
            "max_rounds": CLAUDE_CODE_MAX_ROUNDS,
            "search_max_pages_per_round": settings.search_max_pages_per_round,
            "search_max_attempts_per_round": settings.search_max_attempts_per_round,
            "mock_cts": settings.mock_cts,
            "isolated_home": str(run_home),
            "ccr_port": port,
        },
    )
    tracer.emit("run_started", summary="Starting Claude Code baseline run.")
    try:
        if manage_router:
            _start_router(
                env=process_env,
                cwd=settings.project_root,
                port=port,
                tracer=tracer,
                process_runner=process_runner,
            )
        command = _claude_command(
            settings_path=settings_path,
            mcp_config_path=mcp_config_path,
            prompt=_build_prompt(job_title=job_title, jd=jd, notes=notes, settings=settings),
        )
        started = perf_counter()
        completed = process_runner(command, env=process_env, cwd=str(settings.project_root), timeout=timeout_seconds)
        latency_ms = max(1, int((perf_counter() - started) * 1000))
        tracer.append_jsonl(
            "claude_code_transcript.jsonl",
            {
                "command": [item if item != router_token else "<redacted>" for item in command],
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "latency_ms": latency_ms,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Claude Code exited with {completed.returncode}: {completed.stderr or completed.stdout}")

        state = _load_cts_state(tracer.run_dir)
        fatal_error = state.get("fatal_error")
        if fatal_error:
            raise RuntimeError(str(fatal_error))
        rounds_executed = int(state.get("total_calls") or 0)
        if rounds_executed < 1:
            raise ValueError("Claude Code did not perform a successful CTS search.")

        shortlist = parse_shortlist(completed.stdout)
        candidate_store = _load_candidate_store(tracer.run_dir)
        missing_ids = [resume_id for resume_id in shortlist.ranked_resume_ids if resume_id not in candidate_store]
        if missing_ids:
            raise ValueError(f"Claude Code returned unseen resume ids: {missing_ids}")

        round_01_candidates = ranked_candidates_from_ids(
            list(state.get("first_search_resume_ids") or []),
            candidate_store,
        )
        final_candidates = ranked_candidates_from_ids(shortlist.ranked_resume_ids, candidate_store)
        stop_reason = shortlist.stop_reason or "claude_code_stop"
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
        evaluation_artifacts = await evaluate_baseline_run(
            settings=settings,
            prompt=judge_prompt,
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            jd=jd,
            notes=notes,
            round_01_candidates=round_01_candidates,
            final_candidates=final_candidates,
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
        log_baseline_to_wandb(
            settings=settings,
            artifact_root=tracer.run_dir,
            evaluation=evaluation_artifacts.result,
            rounds_executed=rounds_executed,
            version=CLAUDE_CODE_VERSION,
            artifact_prefix="claude-code",
            backing_model=settings.controller_model_id,
        )
        tracer.emit("run_finished", status="succeeded", stop_reason=stop_reason, summary="Claude Code baseline finished.")
        return ClaudeCodeRunResult(
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            trace_log_path=tracer.trace_log_path,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            round_01_candidates=candidate_rows(round_01_candidates),
            final_candidates=candidate_rows(final_candidates),
            evaluation_result=evaluation_artifacts.result,
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        message = f"Claude Code returned invalid final JSON: {exc}"
        tracer.write_json("failure.json", {"error_type": type(exc).__name__, "error_message": message})
        log_baseline_failure_to_wandb(
            settings=settings,
            run_id=tracer.run_id,
            jd=jd,
            rounds_executed=_rounds_executed(tracer.run_dir),
            error_message=message,
            version=CLAUDE_CODE_VERSION,
            backing_model=settings.controller_model_id,
            failure_metric_prefix="claude_code",
        )
        tracer.emit("run_failed", status="failed", summary=message, error_message=message)
        raise ValueError(message) from exc
    except Exception as exc:
        tracer.write_json("failure.json", {"error_type": type(exc).__name__, "error_message": str(exc)})
        log_baseline_failure_to_wandb(
            settings=settings,
            run_id=tracer.run_id,
            jd=jd,
            rounds_executed=_rounds_executed(tracer.run_dir),
            error_message=str(exc),
            version=CLAUDE_CODE_VERSION,
            backing_model=settings.controller_model_id,
            failure_metric_prefix="claude_code",
        )
        tracer.emit("run_failed", status="failed", summary=str(exc), error_message=str(exc))
        raise
    finally:
        if manage_router:
            _stop_router(env=process_env, cwd=settings.project_root, tracer=tracer, process_runner=process_runner)
        tracer.close()
