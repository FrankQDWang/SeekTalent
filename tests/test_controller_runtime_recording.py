from types import SimpleNamespace

from seektalent.models import StopControllerDecision
from seektalent.runtime import controller_runtime


def _decision() -> StopControllerDecision:
    return StopControllerDecision(
        thought_summary="done",
        action="stop",
        decision_rationale="done",
        stop_reason="done",
    )


def _call(recorder, *, monkeypatch):
    calls = []
    monkeypatch.setattr(controller_runtime, "_record_controller_success", lambda **kwargs: calls.append(kwargs))
    tracer = SimpleNamespace(write_json=lambda *args: writes.append(args))
    writes = []
    recorder(
        settings=object(),
        controller=object(),
        controller_decision=_decision(),
        controller_stage_state={"artifacts": ["context", "call", "decision"]},
        round_no=2,
        tracer=tracer,
        progress_callback=None,
        build_llm_call_snapshot=lambda **_: None,
        write_aux_llm_call_artifact=lambda **_: None,
        emit_llm_event=lambda **_: None,
        emit_progress=lambda *_args, **_kwargs: None,
    )
    return calls, writes


def test_finalize_records_decision_and_links_it_from_shared_call_recorder(monkeypatch) -> None:
    calls, writes = _call(controller_runtime.finalize_controller_stage, monkeypatch=monkeypatch)

    assert writes[0][0] == "round.02.controller.controller_decision"
    assert calls[0]["output_artifact_refs"] == ["round.02.controller.controller_decision"]
    assert calls[0]["event_artifact_paths"] == ["context", "call", "decision"]


def test_evidence_only_uses_shared_call_recorder_without_persisting_replacement(monkeypatch) -> None:
    calls, writes = _call(controller_runtime.record_controller_call_evidence, monkeypatch=monkeypatch)

    assert writes == []
    assert calls[0]["output_artifact_refs"] == []
    assert calls[0]["event_artifact_paths"] == ["context", "call"]
