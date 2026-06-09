from __future__ import annotations

from pathlib import Path


def test_run_tracer_dev_mode_preserves_debug_artifact_writes(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="dev_full_local")
    path = tracer.write_json("debug.provider_payload", {"rawProviderPayload": "provider-secret", "summary": "kept"})
    tracer.close()

    assert path.exists()
    assert "provider-secret" in path.read_text(encoding="utf-8")


def test_run_tracer_compact_mode_redacts_sensitive_debug_payloads(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="prod_compact_local")
    path = tracer.write_json(
        "debug.provider_payload",
        {"rawProviderPayload": "provider-secret", "summary": "safe summary", "nested": {"cookies": "cookie"}},
    )
    tracer.close()

    payload = path.read_text(encoding="utf-8")
    assert "safe summary" in payload
    assert "provider-secret" not in payload
    assert "cookie" not in payload


def test_run_tracer_off_mode_does_not_create_run_artifact_directory_for_progress(tmp_path: Path) -> None:
    from seektalent.tracing import RunTracer

    tracer = RunTracer(tmp_path, output_mode="off_except_db")
    tracer.emit("runtime_progress", summary="ordinary progress", payload={"summary": "safe"})
    tracer.close()

    assert not any(tmp_path.rglob("run_manifest.json"))
    assert not any(tmp_path.rglob("runtime.trace_log"))
