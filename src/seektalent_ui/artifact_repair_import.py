from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_ui.workbench_store import WorkbenchRuntimeSourcingJobContext, WorkbenchStore


def import_legacy_runtime_completion_for_repair(
    *,
    store: WorkbenchStore,
    context: WorkbenchRuntimeSourcingJobContext,
    artifacts: object,
    repair_confirmed: bool = False,
    runtime_mode: str = "prod",
    allow_prod_repair: bool = False,
    runtime_control_store: RuntimeControlStore | None = None,
    created_at: str | None = None,
) -> None:
    _require_repair_import_allowed(
        repair_confirmed=repair_confirmed,
        runtime_mode=runtime_mode,
        allow_prod_repair=allow_prod_repair,
    )
    store._jobs.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)
    _record_repair_source_metadata(
        runtime_control_store=runtime_control_store,
        context=context,
        artifacts=artifacts,
        artifact_kind="legacy_runtime_completion",
        created_at=created_at,
    )


def import_legacy_runtime_checkpoint_for_repair(
    *,
    store: WorkbenchStore,
    context: WorkbenchRuntimeSourcingJobContext,
    artifacts: object,
    repair_confirmed: bool = False,
    runtime_mode: str = "prod",
    allow_prod_repair: bool = False,
    runtime_control_store: RuntimeControlStore | None = None,
    created_at: str | None = None,
) -> None:
    _require_repair_import_allowed(
        repair_confirmed=repair_confirmed,
        runtime_mode=runtime_mode,
        allow_prod_repair=allow_prod_repair,
    )
    store._jobs.refresh_runtime_candidate_index_with_artifacts(context=context, artifacts=artifacts)
    _record_repair_source_metadata(
        runtime_control_store=runtime_control_store,
        context=context,
        artifacts=artifacts,
        artifact_kind="legacy_runtime_checkpoint",
        created_at=created_at,
    )


def import_legacy_runtime_public_events_for_repair(
    *,
    store: WorkbenchStore,
    context: WorkbenchRuntimeSourcingJobContext,
    artifacts: object,
    repair_confirmed: bool = False,
    runtime_mode: str = "prod",
    allow_prod_repair: bool = False,
    runtime_control_store: RuntimeControlStore | None = None,
    created_at: str | None = None,
) -> int:
    _require_repair_import_allowed(
        repair_confirmed=repair_confirmed,
        runtime_mode=runtime_mode,
        allow_prod_repair=allow_prod_repair,
    )
    imported_count = store._events.reconcile_runtime_public_events_from_artifacts(context=context, artifacts=artifacts)
    _record_repair_source_metadata(
        runtime_control_store=runtime_control_store,
        context=context,
        artifacts=artifacts,
        artifact_kind="legacy_runtime_public_events",
        created_at=created_at,
        extra_metadata={"imported_count": imported_count},
    )
    return imported_count


def _require_repair_import_allowed(
    *,
    repair_confirmed: bool,
    runtime_mode: str,
    allow_prod_repair: bool,
) -> None:
    if not repair_confirmed:
        raise PermissionError("artifact_repair_import_requires_operator_confirmation")
    if runtime_mode == "prod" and not allow_prod_repair:
        raise PermissionError("artifact_repair_import_rejected_in_prod")


def _record_repair_source_metadata(
    *,
    runtime_control_store: RuntimeControlStore | None,
    context: WorkbenchRuntimeSourcingJobContext,
    artifacts: object,
    artifact_kind: str,
    created_at: str | None,
    extra_metadata: dict[str, object] | None = None,
) -> None:
    if runtime_control_store is None:
        return
    runtime_run_id = _runtime_run_id(context=context, artifacts=artifacts)
    if runtime_run_id is None:
        return
    safe_uri = _safe_artifact_uri(artifacts)
    metadata: dict[str, object] = {
        "source": "artifact_repair_import",
        "artifactKind": artifact_kind,
        "workbenchJobId": context.job.job_id,
        "workbenchSessionId": context.session.session_id,
        "runtimeRunId": runtime_run_id,
        "safeUri": safe_uri,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    runtime_control_store.record_artifact_ref(
        artifact_ref_id=_artifact_ref_id(runtime_run_id=runtime_run_id, artifact_kind=artifact_kind, safe_uri=safe_uri),
        runtime_run_id=runtime_run_id,
        artifact_kind=artifact_kind,
        safe_uri=safe_uri,
        visibility="debug_repair",
        metadata=metadata,
        created_at=created_at or _now(),
    )


def _runtime_run_id(*, context: WorkbenchRuntimeSourcingJobContext, artifacts: object) -> str | None:
    value = getattr(artifacts, "run_id", None) or context.job.runtime_run_id
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _safe_artifact_uri(artifacts: object) -> str:
    run_dir = getattr(artifacts, "run_dir", None)
    try:
        path = Path(run_dir) if run_dir is not None else None
    except TypeError:
        path = None
    if path is None:
        return "legacy-artifacts:unknown"
    return f"legacy-artifacts:{path}"


def _artifact_ref_id(*, runtime_run_id: str, artifact_kind: str, safe_uri: str) -> str:
    digest = sha256(f"{runtime_run_id}:{artifact_kind}:{safe_uri}".encode("utf-8")).hexdigest()[:24]
    return f"artifact_repair_{digest}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
