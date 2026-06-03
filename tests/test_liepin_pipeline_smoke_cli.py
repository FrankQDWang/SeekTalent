from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import seektalent.providers.liepin.smoke_cli as smoke_cli
from seektalent.cli import main
from seektalent.models import (
    FinalCandidate,
    FinalResult,
    RuntimeFinalizationRevision,
    RuntimeSourceCoverageSummary,
    RuntimeSourceEvidence,
)
from seektalent.providers.liepin.store import LiepinStore
from seektalent.runtime.orchestrator import RunStageError
from tests.settings_factory import make_settings
from tests.test_liepin_cli import RecordingSmokeWorker, _approved_gate_and_connection


def test_liepin_smoke_pipeline_requires_job_title_and_jd(capsys, tmp_path: Path) -> None:
    db_path, gate_ref, connection_id, _provider_account_hash = _approved_gate_and_connection(tmp_path)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--pipeline",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "opencli",
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "validation failed: --pipeline requires --job-title" in captured.err


def _fake_pipeline_artifacts(
    *,
    coverage: RuntimeSourceCoverageSummary | None = None,
    detail_evidence: tuple[RuntimeSourceEvidence, ...] | None = None,
    final_candidates: tuple[FinalCandidate, ...] | None = None,
    stop_reason: str = "source_coverage_completed",
):
    run_id = "run_live_pipeline_1"
    run_dir = Path("/tmp/seektalent-artifacts/run_live_pipeline_1")
    if coverage is None:
        coverage = RuntimeSourceCoverageSummary(
            status="complete",
            selected_source_kinds=("liepin",),
            completed_source_kinds=("liepin",),
            blocked_source_kinds=(),
            failed_source_kinds=(),
            partial_source_kinds=(),
            empty_source_kinds=(),
            missing_source_kinds=(),
        )
    candidates = final_candidates
    if candidates is None:
        candidates = (
            FinalCandidate(
                resume_id="liepin-opencli-1",
                rank=1,
                final_score=86,
                fit_bucket="fit",
                match_summary="Strong Python data platform fit.",
                strengths=["Python data platform delivery"],
                weaknesses=[],
                matched_must_haves=["Python"],
                matched_preferences=[],
                risk_flags=[],
                why_selected="Matches the smoke JD.",
                source_round=1,
            ),
        )
    evidence = detail_evidence
    if evidence is None:
        evidence = (
            RuntimeSourceEvidence(
                evidence_id="evidence-liepin-detail-1",
                source="liepin",
                provider="liepin",
                source_plan_id=f"{run_id}:source:1:liepin",
                source_lane_run_id="liepin-lane-1",
                evidence_level="detail",
                candidate_resume_id="liepin-opencli-1",
                provider_candidate_key_hash="hash-liepin-opencli-1",
                collected_at="2026-06-03T12:00:00+08:00",
            ),
        )
    return SimpleNamespace(
        run_id=run_id,
        run_dir=run_dir,
        final_result=FinalResult(
            run_id=run_id,
            run_dir=str(run_dir),
            rounds_executed=1,
            stop_reason=stop_reason,
            candidates=list(candidates),
            summary="One candidate reached final shortlist.",
        ),
        source_coverage_summary=coverage,
        finalization_revision=RuntimeFinalizationRevision(
            revision=1,
            runtime_run_id=run_id,
            reason_code="source_coverage_completed",
            selected_source_kinds=("liepin",),
            candidate_identity_ids=("identity-liepin-opencli-1",),
            created_at="2026-06-03T12:00:00+08:00",
            coverage_summary=coverage,
        ),
        run_state=SimpleNamespace(
            source_evidence_by_resume_id={
                "liepin-opencli-1": list(evidence),
            },
        ),
    )


class FakePipelineRuntime:
    calls: list[dict[str, object]] = []
    last_settings = None

    def __init__(self, settings) -> None:
        self.settings = settings
        self.__class__.last_settings = settings

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return _fake_pipeline_artifacts()


def test_liepin_smoke_pipeline_report_uses_runtime_artifact_fields() -> None:
    report = smoke_cli._liepin_smoke_pipeline_report(_fake_pipeline_artifacts())

    assert report == {
        "runtime_run_id": "run_live_pipeline_1",
        "artifact_run_dir": "/tmp/seektalent-artifacts/run_live_pipeline_1",
        "stop_reason": "source_coverage_completed",
        "coverage_status": "completed",
        "detail_evidence_count": 1,
        "final_candidate_count": 1,
    }


def test_liepin_smoke_pipeline_runs_runtime_liepin_only(monkeypatch, capsys, tmp_path: Path) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    jd_file = tmp_path / "jd.txt"
    jd_file.write_text("Build Python data platforms.", encoding="utf-8")
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)
    FakePipelineRuntime.calls = []

    monkeypatch.setattr(
        smoke_cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_browser_action_backend="opencli",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(smoke_cli, "build_liepin_worker_client", lambda settings: worker, raising=False)
    monkeypatch.setattr(smoke_cli, "WorkflowRuntime", FakePipelineRuntime, raising=False)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--pipeline",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "opencli",
            "--job-title",
            "Data Platform Engineer",
            "--jd-file",
            str(jd_file),
            "--min-final-candidates",
            "1",
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert "session: ready" in captured.out
    assert "pipeline: completed" in captured.out
    assert "final_candidate_count: 1" in captured.out
    assert FakePipelineRuntime.calls[0]["source_kinds"] == ("liepin",)
    assert FakePipelineRuntime.calls[0]["liepin_context"]["connection_id"] == connection_id
    assert Path(FakePipelineRuntime.last_settings.liepin_connector_db_path) == db_path
    session = LiepinStore(db_path).get_session_metadata(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
    )
    assert session is not None
    assert session["provider_account_hash"] == provider_account_hash
    assert session["session_store_key_id"] == "local-development"
    assert session["encrypted_state_sha256"]
    assert session["session_updated_at"]
    assert provider_account_hash not in captured.out


def test_liepin_smoke_pipeline_accepts_detail_backed_degraded_finalization(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    jd_file = tmp_path / "jd.txt"
    jd_file.write_text("Build Python data platforms.", encoding="utf-8")
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)

    class RuntimeWithDegradedFinalization(FakePipelineRuntime):
        def run(self, **kwargs):
            self.calls.append(kwargs)
            return _fake_pipeline_artifacts(
                coverage=RuntimeSourceCoverageSummary(
                    status="degraded",
                    selected_source_kinds=("liepin",),
                    completed_source_kinds=(),
                    blocked_source_kinds=(),
                    failed_source_kinds=(),
                    partial_source_kinds=("liepin",),
                    empty_source_kinds=(),
                    missing_source_kinds=(),
                    finalization_scope="available_sources_only",
                ),
                stop_reason="source_lanes_degraded",
            )

    monkeypatch.setattr(
        smoke_cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_browser_action_backend="opencli",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(smoke_cli, "build_liepin_worker_client", lambda settings: worker, raising=False)
    monkeypatch.setattr(smoke_cli, "WorkflowRuntime", RuntimeWithDegradedFinalization, raising=False)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--pipeline",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "opencli",
            "--job-title",
            "Data Platform Engineer",
            "--jd-file",
            str(jd_file),
            "--min-final-candidates",
            "1",
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert "pipeline: completed" in captured.out
    assert "source_coverage: degraded" in captured.out
    assert "final_candidate_count: 1" in captured.out
    assert provider_account_hash not in captured.out


def test_liepin_smoke_pipeline_reports_runtime_source_lane_safe_reason(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    jd_file = tmp_path / "jd.txt"
    jd_file.write_text("Build Python data platforms.", encoding="utf-8")
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)

    class RuntimeWithBlockedSourceLane(FakePipelineRuntime):
        def run(self, **kwargs):
            self.calls.append(kwargs)
            raise RunStageError("source_lanes", "liepin_opencli_detail_not_opened")

    monkeypatch.setattr(
        smoke_cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_browser_action_backend="opencli",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(smoke_cli, "build_liepin_worker_client", lambda settings: worker, raising=False)
    monkeypatch.setattr(smoke_cli, "WorkflowRuntime", RuntimeWithBlockedSourceLane, raising=False)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--pipeline",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "opencli",
            "--job-title",
            "Data Platform Engineer",
            "--jd-file",
            str(jd_file),
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "validation failed: pipeline blocked: source_browser_timeout" in captured.err
    assert "unexpected_failure" not in captured.err
    assert provider_account_hash not in captured.err


def test_liepin_smoke_pipeline_rejects_missing_detail_evidence(monkeypatch, capsys, tmp_path: Path) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    jd_file = tmp_path / "jd.txt"
    jd_file.write_text("Build Python data platforms.", encoding="utf-8")
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)

    class RuntimeWithoutDetailEvidence(FakePipelineRuntime):
        def run(self, **kwargs):
            artifacts = super().run(**kwargs)
            artifacts.run_state.source_evidence_by_resume_id = {}
            return artifacts

    monkeypatch.setattr(
        smoke_cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_browser_action_backend="opencli",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(smoke_cli, "build_liepin_worker_client", lambda settings: worker, raising=False)
    monkeypatch.setattr(smoke_cli, "WorkflowRuntime", RuntimeWithoutDetailEvidence, raising=False)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--pipeline",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "opencli",
            "--job-title",
            "Data Platform Engineer",
            "--jd-file",
            str(jd_file),
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "pipeline detail evidence missing" in captured.err
    assert provider_account_hash not in captured.err


def test_liepin_smoke_pipeline_rejects_empty_final_shortlist(monkeypatch, capsys, tmp_path: Path) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    jd_file = tmp_path / "jd.txt"
    jd_file.write_text("Build Python data platforms.", encoding="utf-8")
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)

    class RuntimeWithoutFinalCandidates(FakePipelineRuntime):
        def run(self, **kwargs):
            artifacts = super().run(**kwargs)
            artifacts.final_result.candidates = []
            return artifacts

    monkeypatch.setattr(
        smoke_cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_browser_action_backend="opencli",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(smoke_cli, "build_liepin_worker_client", lambda settings: worker, raising=False)
    monkeypatch.setattr(smoke_cli, "WorkflowRuntime", RuntimeWithoutFinalCandidates, raising=False)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--pipeline",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "opencli",
            "--job-title",
            "Data Platform Engineer",
            "--jd-file",
            str(jd_file),
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "pipeline final shortlist empty" in captured.err
    assert provider_account_hash not in captured.err

