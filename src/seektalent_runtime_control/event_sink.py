from __future__ import annotations

from hashlib import sha256
import json
from typing import Protocol

from seektalent.progress import ProgressEvent
from seektalent_runtime_control.events import normalize_progress_event, public_event_payload
from seektalent_runtime_control.models import RuntimeControlEvent, RuntimeControlEventInput, RuntimeStageOutputInput
from seektalent_runtime_control.store import RuntimeControlStore


RUNTIME_PUBLIC_STAGE_OUTPUT_SCHEMA_VERSION = "runtime-public-stage-output/v2"
_PUBLIC_STAGE_OUTPUT_BOUNDARIES = {"round_query", "source_result", "merge", "scoring", "feedback", "finalization"}


class RuntimeEventSink(Protocol):
    def append_progress(
        self,
        progress: ProgressEvent,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        now: str,
    ) -> RuntimeControlEvent: ...

    def append_control_event(
        self,
        event: RuntimeControlEventInput,
        *,
        executor_id: str,
        attempt_no: int | None = None,
    ) -> RuntimeControlEvent: ...


class RuntimeControlEventSink:
    def __init__(self, store: RuntimeControlStore) -> None:
        self.store = store

    def append_progress(
        self,
        progress: ProgressEvent,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        now: str,
    ) -> RuntimeControlEvent:
        event_input = normalize_progress_event(progress, runtime_run_id=runtime_run_id, now=now)
        return self.append_control_event(event_input, executor_id=executor_id, attempt_no=attempt_no)

    def append_control_event(
        self,
        event: RuntimeControlEventInput,
        *,
        executor_id: str,
        attempt_no: int | None = None,
    ) -> RuntimeControlEvent:
        event = self.store.append_executor_event(
            event,
            executor_id=executor_id,
            attempt_no=attempt_no,
            run_status="running",
        )
        self._save_public_stage_output(event, executor_id=executor_id, attempt_no=attempt_no)
        return event

    def _save_public_stage_output(
        self,
        event: RuntimeControlEvent,
        *,
        executor_id: str,
        attempt_no: int | None,
    ) -> None:
        payload = public_event_payload(event)
        if payload is None:
            return
        stage = str(payload["stage"])
        if stage not in _PUBLIC_STAGE_OUTPUT_BOUNDARIES:
            return
        source_kind = payload["sourceKind"] if isinstance(payload["sourceKind"], str) else None
        output_kind = f"runtime_public_{stage}"
        output = _stage_output_payload(payload)
        self.store.save_stage_output(
            RuntimeStageOutputInput(
                output_id=_stage_output_id(
                    runtime_run_id=event.runtime_run_id,
                    stage=stage,
                    round_no=event.round_no,
                    node_id=source_kind,
                    output_kind=output_kind,
                    schema_version=RUNTIME_PUBLIC_STAGE_OUTPUT_SCHEMA_VERSION,
                ),
                runtime_run_id=event.runtime_run_id,
                stage=stage,
                node_id=source_kind,
                round_no=event.round_no,
                output_kind=output_kind,
                schema_version=RUNTIME_PUBLIC_STAGE_OUTPUT_SCHEMA_VERSION,
                output=output,
                source_event_id=event.event_id,
                source_checkpoint_id=None,
                artifact_ref_id=None,
                created_at=event.created_at,
            ),
            executor_id=executor_id,
            attempt_no=attempt_no,
        )


def _stage_output_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": RUNTIME_PUBLIC_STAGE_OUTPUT_SCHEMA_VERSION,
        "publicEventSchemaVersion": payload["schemaVersion"],
        "stage": payload["stage"],
        "roundNo": payload["roundNo"],
        "sourceKind": payload["sourceKind"],
        "status": payload["status"],
        "counts": payload["counts"],
        "details": payload["details"],
        "safeReasonCode": payload["safeReasonCode"],
    }


def _stage_output_id(
    *,
    runtime_run_id: str,
    stage: str,
    round_no: int | None,
    node_id: str | None,
    output_kind: str,
    schema_version: str,
) -> str:
    payload = json.dumps(
        {
            "runtimeRunId": runtime_run_id,
            "stage": stage,
            "roundNo": round_no,
            "nodeId": node_id,
            "outputKind": output_kind,
            "schemaVersion": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"rtout_{sha256(payload.encode('utf-8')).hexdigest()[:32]}"
