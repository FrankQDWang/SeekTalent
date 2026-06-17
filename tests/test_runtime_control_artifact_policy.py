from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _runtime_public_event_payload() -> dict[str, object]:
    return {
        "schemaVersion": "runtime_public_event_v1",
        "runtimeRunId": "run-public-mirror",
        "eventId": "run-public-mirror:1:source_result:cts",
        "eventSeq": 1,
        "stage": "source_result",
        "roundNo": 1,
        "sourceKind": "cts",
        "status": "completed",
        "counts": {"roundReturned": 1, "roundIdentities": 1},
        "safeReasonCode": None,
        "createdAt": "2026-05-09T00:01:02+00:00",
    }


def _sensitive_payload() -> dict[str, object]:
    return {
        "prompt": "raw prompt text",
        "messages": [{"role": "user", "content": "private prompt message"}],
        "cookies": "session-cookie",
        "authHeaders": {"Authorization": "Bearer secret"},
        "rawProviderPayload": {"html": "<private provider page>"},
        "rawResumeText": "candidate private resume",
        "structuredOutput": {"candidate": "private structured output"},
        "summary": "safe summary",
        "nested": {"requestHeaders": {"cookie": "nested cookie"}, "safe": "kept"},
    }


def test_prod_mode_writes_no_debug_trace_or_public_event_artifact(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="prod")
    debug_path = tracer.write_json("debug.provider_payload", _sensitive_payload())
    public_path = tracer.append_runtime_public_event_mirror(_runtime_public_event_payload())
    tracer.emit("runtime_progress", summary="ordinary progress", payload=_sensitive_payload())
    tracer.close()

    assert debug_path == tracer.run_dir / "debug_provider_payload.json"
    assert public_path == tracer.run_dir / "runtime" / "public_events.jsonl"
    assert not any(tmp_path.rglob("run_manifest.json"))
    assert not any(tmp_path.rglob("runtime.trace_log"))
    assert not any(tmp_path.rglob("public_events.jsonl"))


def test_prod_mode_skips_corpus_raw_provider_payload_capture(tmp_path: Path) -> None:
    from seektalent.config import AppSettings
    from seektalent.corpus.runtime import ProviderReturnedCandidate
    from seektalent.runtime.orchestrator import WorkflowRuntime
    from seektalent.tracing import RunTracer

    settings = AppSettings(
        _env_file=None,
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        corpus_db_path=str(tmp_path / "corpus.sqlite3"),
    )
    runtime = WorkflowRuntime(settings)
    tracer = RunTracer(settings.artifacts_path, output_mode=settings.runtime_artifact_output_mode)
    runtime._active_corpus_session = tracer.store.create_root(
        kind="corpus",
        display_name=f"corpus ingest for {tracer.run_id}",
        producer="CorpusRuntime",
    )

    runtime._record_corpus_provider_results(
        tracer=tracer,
        returned_candidates=[
            ProviderReturnedCandidate(
                candidate=SimpleNamespace(
                    raw={"resume_id": "resume-prod-1", "provider_candidate_id": "provider-prod-1"},
                    search_text="private provider resume text",
                ),
                stage_id="retrieval",
                round_no=1,
                query_instance_id="query-prod-1",
                query_fingerprint="fingerprint-prod-1",
                provider_name="cts",
                provider_request_id="request-prod-1",
                provider_rank=1,
                provider_page_no=1,
                provider_fetch_no=1,
                attempt_no=1,
            )
        ],
    )
    rows = runtime.corpus_store.rows_for_tenant("resume_documents", "default", "default")
    runtime.corpus_store.close()
    tracer.close()

    assert rows == []
    assert not any(settings.artifacts_path.rglob("raw_payloads"))


def test_dev_mode_writes_compact_redacted_diagnostics_only(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="dev")
    path = tracer.write_json("debug.provider_payload", _sensitive_payload())
    public_path = tracer.append_runtime_public_event_mirror(_runtime_public_event_payload())
    tracer.close()

    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert path.exists()
    assert public_path.exists()
    assert payload["summary"] == "safe summary"
    assert payload["nested"]["safe"] == "kept"
    assert payload["_redactedSensitiveFieldCount"] == 7
    assert payload["nested"]["_redactedSensitiveFieldCount"] == 1
    assert "raw prompt text" not in serialized
    assert "session-cookie" not in serialized
    assert "Bearer secret" not in serialized
    assert "private provider page" not in serialized
    assert "private resume" not in serialized
    assert "private structured output" not in serialized


def test_dev_mode_redacts_common_secret_key_forms_and_token_values(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="dev")
    path = tracer.write_json(
        "debug.provider_payload",
        {
            "safe": "kept",
            "accessToken": "access-token-secret",
            "refreshToken": "refresh-token-secret",
            "sessionToken": "session-token-secret",
            "password": "password-secret",
            "secret": "raw-secret",
            "nested": {
                "safeNote": "Authorization: Bearer provider-token-value",
                "safeApiLine": "api_key=provider-api-key",
            },
        },
    )
    tracer.close()

    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["safe"] == "kept"
    assert payload["_redactedSensitiveFieldCount"] == 5
    assert payload["nested"]["safeNote"] == "[redacted]"
    assert payload["nested"]["safeApiLine"] == "[redacted]"
    assert "access-token-secret" not in serialized
    assert "refresh-token-secret" not in serialized
    assert "session-token-secret" not in serialized
    assert "password-secret" not in serialized
    assert "raw-secret" not in serialized
    assert "provider-token-value" not in serialized
    assert "provider-api-key" not in serialized


def test_debug_full_local_mode_writes_full_diagnostics_only_by_explicit_request(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="debug_full_local")
    path = tracer.write_json("debug.provider_payload", _sensitive_payload())
    public_path = tracer.append_runtime_public_event_mirror(_runtime_public_event_payload())
    tracer.close()

    serialized = path.read_text(encoding="utf-8")
    assert path.exists()
    assert public_path.exists()
    assert "raw prompt text" in serialized
    assert "session-cookie" in serialized
    assert "Bearer secret" in serialized
    assert "private provider page" in serialized
    assert "candidate private resume" in serialized
    assert "private structured output" in serialized


@pytest.mark.parametrize("mode", ["dev_full_local", "prod_compact_local", "off_except_db", "off"])
def test_old_artifact_mode_names_fail_fast(mode: str) -> None:
    from seektalent_runtime_control.artifact_policy import normalize_artifact_output_mode

    with pytest.raises(ValueError, match="runtime_artifact_output_mode_unsupported"):
        normalize_artifact_output_mode(mode)


def test_unknown_artifact_mode_fails_fast() -> None:
    from seektalent_runtime_control.artifact_policy import normalize_artifact_output_mode

    with pytest.raises(ValueError, match="runtime_artifact_output_mode_unsupported"):
        normalize_artifact_output_mode("verbose_local")


def test_app_settings_resolves_runtime_mode_defaults_and_rejects_legacy_artifact_modes(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from seektalent.config import AppSettings

    prod = AppSettings(_env_file=None, runtime_mode="prod", workspace_root=str(tmp_path / "prod"))
    dev = AppSettings(_env_file=None, runtime_mode="dev", workspace_root=str(tmp_path / "dev"))

    assert prod.runtime_artifact_output_mode == "prod"
    assert dev.runtime_artifact_output_mode == "dev"
    with pytest.raises(ValidationError, match="runtime_artifact_output_mode_unsupported"):
        AppSettings(
            _env_file=None,
            runtime_mode="prod",
            workspace_root=str(tmp_path / "legacy"),
            runtime_artifact_output_mode="prod_compact_local",
        )


def test_injected_api_run_tracer_default_uses_supported_dev_mode(tmp_path: Path) -> None:
    from seektalent.api import _InjectedSessionRunTracer

    tracer = _InjectedSessionRunTracer(tmp_path)
    tracer.emit("runtime_progress", summary="ok", payload={"summary": "safe"})
    tracer.close()

    assert (tracer.run_dir / "manifests" / "run_manifest.json").exists()


def test_run_artifact_manifest_carries_retention_metadata(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="dev")
    tracer.write_json("debug.provider_payload", {"summary": "safe"})
    tracer.close()

    manifest = json.loads((tracer.run_dir / "manifests" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["retention_ttl_class"] == "dev_debug"
    assert manifest["max_bytes"] == 5_000_000
    assert manifest["delete_eligible"] is True
    assert manifest["safety_class"] == "artifact_debug"
    assert manifest["support_bundle_only"] is False
