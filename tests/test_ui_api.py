from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import httpx

from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.mock_data import load_mock_resume_corpus
from seektalent.models import FinalCandidate, FinalResult
from seektalent.normalization import normalize_resume
from seektalent.runtime import RunArtifacts
from seektalent_ui.server import RunRegistry, create_server
from tests.settings_factory import make_settings


@dataclass
class FakeRuntimeController:
    artifacts: RunArtifacts
    started: threading.Event
    release: threading.Event
    error_message: str | None = None
    seen_job_titles: list[str] = field(default_factory=list)
    seen_notes: list[str] = field(default_factory=list)


def _build_runtime_factory(controller: FakeRuntimeController):
    class FakeRuntime:
        def __init__(self, settings: AppSettings) -> None:
            del settings

        def run(self, *, job_title: str, jd: str, notes: str) -> RunArtifacts:
            assert job_title
            assert jd
            controller.seen_job_titles.append(job_title)
            controller.seen_notes.append(notes)
            controller.started.set()
            controller.release.wait(timeout=2)
            if controller.error_message is not None:
                raise RuntimeError(controller.error_message)
            return controller.artifacts

    return FakeRuntime


def _start_server(registry: RunRegistry):
    server = create_server("127.0.0.1", 0, registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    return server, thread, f"http://{host}:{port}"


def _wait_for_status(client: httpx.Client, url: str, expected: str, *, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(url)
        payload = response.json()
        if payload["status"] == expected:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for status={expected}")


def _build_controller(tmp_path: Path) -> FakeRuntimeController:
    candidate = load_mock_resume_corpus()[0]
    normalized = normalize_resume(candidate)
    trace_log_path = tmp_path / "trace.log"
    trace_log_path.write_text("", encoding="utf-8")
    artifacts = RunArtifacts(
        final_result=FinalResult(
            run_id="worker-run-1",
            run_dir=str(tmp_path),
            rounds_executed=3,
            stop_reason="reflection_stop",
            summary="Returned 1 candidates after 3 rounds.",
            candidates=[
                FinalCandidate(
                    resume_id=candidate.resume_id,
                    rank=1,
                    final_score=92,
                    fit_bucket="fit",
                    match_summary="Must 92/100, preferred 65/100, risk 8/100.",
                    strengths=["Matched must-have: python"],
                    weaknesses=[],
                    matched_must_haves=["python", "agent"],
                    matched_preferences=["resume"],
                    risk_flags=[],
                    why_selected="Direct Python agent experience with tracing and ranking.",
                    source_round=1,
                )
            ],
        ),
        final_markdown="",
        run_id="worker-run-1",
        run_dir=tmp_path,
        trace_log_path=trace_log_path,
        candidate_store={candidate.resume_id: candidate},
        normalized_store={candidate.resume_id: normalized},
        evaluation_result=EvaluationResult(
            run_id="worker-run-1",
            judge_model="openai-chat:deepseek-v3.2",
            jd_sha256="jd",
            round_01=EvaluationStageResult(
                stage="round_01",
                ndcg_at_10=0.5,
                precision_at_10=0.4,
                total_score=0.43,
                candidates=[],
            ),
            final=EvaluationStageResult(
                stage="final",
                ndcg_at_10=0.7,
                precision_at_10=0.6,
                total_score=0.63,
                candidates=[],
            ),
        ),
        terminal_stop_guidance=None,
    )
    return FakeRuntimeController(
        artifacts=artifacts,
        started=threading.Event(),
        release=threading.Event(),
    )


def test_ui_api_serves_run_lifecycle_and_candidate_detail(tmp_path: Path) -> None:
    controller = _build_controller(tmp_path)
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    registry = RunRegistry(settings, runtime_factory=_build_runtime_factory(controller))
    server, thread, base_url = _start_server(registry)

    try:
        with httpx.Client(base_url=base_url, timeout=2.0) as client:
            create_response = client.post(
                "/api/runs",
                json={"jobTitle": "Python Engineer", "jdText": "JD"},
            )
            assert create_response.status_code == 201
            payload = create_response.json()
            assert payload["status"] == "queued"
            run_id = payload["runId"]

            assert controller.started.wait(timeout=1)
            running_payload = _wait_for_status(client, f"/api/runs/{run_id}", "running")
            assert running_payload["finalShortlist"] == []

            detail_pending = client.get(f"/api/runs/{run_id}/candidates/mock-r001")
            assert detail_pending.status_code == 409

            controller.release.set()
            completed_payload = _wait_for_status(client, f"/api/runs/{run_id}", "completed")
            assert completed_payload["finalShortlist"][0]["candidateId"] == "mock-r001"

            detail_response = client.get(f"/api/runs/{run_id}/candidates/mock-r001")
            assert detail_response.status_code == 200
            detail_payload = detail_response.json()
            assert detail_payload["candidate"]["name"] == "Lin Qian"
            assert "snapshotId" not in detail_payload["resumeView"]
            assert "verdictHistory" not in detail_payload
            assert detail_payload["resumeView"]["projection"]["workYear"] == 8
            assert controller.seen_job_titles == ["Python Engineer"]
            assert controller.seen_notes == [""]

            not_found = client.get("/api/runs/missing-run")
            assert not_found.status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ui_api_marks_failed_runs(tmp_path: Path) -> None:
    controller = _build_controller(tmp_path)
    controller.error_message = "boom"
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    registry = RunRegistry(settings, runtime_factory=_build_runtime_factory(controller))
    server, thread, base_url = _start_server(registry)

    try:
        with httpx.Client(base_url=base_url, timeout=2.0) as client:
            create_response = client.post(
                "/api/runs",
                json={"jobTitle": "Python Engineer", "jdText": "JD", "sourcingPreferenceText": ""},
            )
            run_id = create_response.json()["runId"]
            controller.release.set()
            failed_payload = _wait_for_status(client, f"/api/runs/{run_id}", "failed")
            assert failed_payload["errorMessage"] == "boom"
            assert failed_payload["finalShortlist"] == []
            assert controller.seen_job_titles == ["Python Engineer"]
            assert controller.seen_notes == [""]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ui_api_rejects_missing_job_title(tmp_path: Path) -> None:
    controller = _build_controller(tmp_path)
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    registry = RunRegistry(settings, runtime_factory=_build_runtime_factory(controller))
    server, thread, base_url = _start_server(registry)

    try:
        with httpx.Client(base_url=base_url, timeout=2.0) as client:
            response = client.post("/api/runs", json={"jdText": "JD"})
            assert response.status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
