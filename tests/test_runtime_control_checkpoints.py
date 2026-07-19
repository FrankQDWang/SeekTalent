from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest


def test_recoverable_checkpoint_uses_exact_pointer_without_older_fallback(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_older_valid",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=1,
            safe_boundary="after_round_controller",
            run_state={"round": 1},
            source_plan={"sourceIds": ["cts", "custom_source"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        executor_id="executor_1",
        attempt_no=lease.attempt_no,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                run_state_json, source_plan_json, pending_commands_json,
                artifact_manifest_ref, schema_version, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rtcheckpoint_newest_corrupt",
                "runtime_run_1",
                "round",
                2,
                "after_round_controller",
                "{not json",
                "{}",
                "[]",
                None,
                "runtime-control-checkpoint/v1",
                "2026-06-08T00:00:02.000000Z",
            ),
        )
        conn.execute(
            "UPDATE runtime_control_runs SET latest_checkpoint_id = ? WHERE runtime_run_id = ?",
            ("rtcheckpoint_newest_corrupt", "runtime_run_1"),
        )

    loaded = store.get_latest_recoverable_checkpoint(runtime_run_id="runtime_run_1")

    assert loaded == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_newest_corrupt",
        reason_code="runtime_checkpoint_corrupt",
    )


def test_checkpoint_write_requires_active_executor_and_updates_latest_pointer(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )

    checkpoint = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_1",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=2,
        safe_boundary="after_round_controller",
        run_state={"round": 2, "privateState": "persisted but not exposed as RunState"},
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref="artifact_manifest_1",
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:02.000000Z",
    )

    saved = store.write_checkpoint(checkpoint, executor_id="executor_1")

    assert saved.checkpoint_id == "rtcheckpoint_1"
    assert store.get_run("runtime_run_1").latest_checkpoint_id == "rtcheckpoint_1"
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_1") == checkpoint

    with pytest.raises(RuntimeControlError) as exc_info:
        store.write_checkpoint(
            checkpoint.model_copy(update={"checkpoint_id": "rtcheckpoint_2"}),
            executor_id="executor_stale",
        )

    assert exc_info.value.reason_code == "runtime_executor_stale"


def test_recoverable_checkpoint_null_pointer_returns_none(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)

    assert store.get_latest_recoverable_checkpoint(runtime_run_id="runtime_run_1") is None


@pytest.mark.parametrize(
    ("checkpoint_id", "row_run_id", "expected_reason"),
    [
        ("rtcheckpoint_missing", None, "runtime_checkpoint_missing"),
        ("rtcheckpoint_wrong_run", "runtime_run_other", "runtime_checkpoint_run_mismatch"),
    ],
)
def test_recoverable_checkpoint_fails_for_missing_or_wrong_run_pointer(
    tmp_path: Path,
    checkpoint_id: str,
    row_run_id: str | None,
    expected_reason: str,
) -> None:
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    with sqlite3.connect(db_path) as conn:
        if row_run_id is not None:
            _insert_checkpoint_row(
                conn,
                checkpoint_id=checkpoint_id,
                runtime_run_id=row_run_id,
                created_at="2026-06-08T00:00:01.000000Z",
            )
        conn.execute(
            "UPDATE runtime_control_runs SET latest_checkpoint_id = ? WHERE runtime_run_id = ?",
            (checkpoint_id, "runtime_run_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(checkpoint_id=checkpoint_id, reason_code=expected_reason)


def test_recoverable_checkpoint_ignores_newer_unreferenced_row(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    pointer = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_pointer",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        run_state={"round": 1},
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:01.000000Z",
    )
    store.write_checkpoint(pointer, executor_id="executor_1", attempt_no=lease.attempt_no)
    with sqlite3.connect(db_path) as conn:
        _insert_checkpoint_row(
            conn,
            checkpoint_id="rtcheckpoint_stray_newer",
            runtime_run_id="runtime_run_1",
            created_at="2026-06-08T00:00:09.000000Z",
        )

    assert store.get_latest_recoverable_checkpoint(runtime_run_id="runtime_run_1") == pointer


@pytest.mark.parametrize(
    ("safe_boundary", "schema_version", "expected_reason"),
    [
        ("after_round_controller", "runtime-control-checkpoint/v999", "runtime_checkpoint_schema_unsupported"),
        ("after_source", "runtime-control-checkpoint/v1", "runtime_checkpoint_safe_boundary_unregistered"),
        ("after_scoring", "runtime-control-checkpoint/v1", "runtime_checkpoint_safe_boundary_unregistered"),
        ("future_boundary", "runtime-control-checkpoint/v1", "runtime_checkpoint_safe_boundary_unregistered"),
    ],
)
def test_recoverable_checkpoint_rejects_unsupported_or_unregistered_boundary(
    tmp_path: Path,
    safe_boundary: str,
    schema_version: str,
    expected_reason: str,
) -> None:
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    with sqlite3.connect(db_path) as conn:
        _insert_checkpoint_row(
            conn,
            checkpoint_id="rtcheckpoint_invalid",
            runtime_run_id="runtime_run_1",
            safe_boundary=safe_boundary,
            schema_version=schema_version,
            created_at="2026-06-08T00:00:01.000000Z",
        )
        conn.execute(
            "UPDATE runtime_control_runs SET latest_checkpoint_id = ? WHERE runtime_run_id = ?",
            ("rtcheckpoint_invalid", "runtime_run_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_invalid",
        reason_code=expected_reason,
    )


@pytest.mark.parametrize(
    ("safe_boundary", "round_no", "run_state"),
    [
        ("runtime_candidate_checkpoint", None, {}),
        ("after_round_controller", 2, {"round": 2}),
    ],
)
def test_each_registered_safe_boundary_runs_its_validator(
    tmp_path: Path,
    safe_boundary: str,
    round_no: int | None,
    run_state: dict[str, object],
) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    checkpoint = RuntimeCheckpoint(
        checkpoint_id=f"rtcheckpoint_{safe_boundary}",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=round_no,
        safe_boundary=safe_boundary,
        run_state=run_state,
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:01.000000Z",
    )
    store.write_checkpoint(checkpoint, executor_id="executor_1", attempt_no=lease.attempt_no)

    assert store.get_latest_recoverable_checkpoint(runtime_run_id="runtime_run_1") == checkpoint


def test_registered_boundary_fails_when_its_committed_truth_is_invalid(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_invalid_boundary",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="after_round_controller",
            run_state={"round": 1},
            source_plan={"sourceIds": ["cts", "custom_source"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        executor_id="executor_1",
        attempt_no=lease.attempt_no,
    )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_invalid_boundary",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_before_source_dispatch_fails_closed_without_positive_dispatch_evidence(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_pending_dispatch",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="before_source_dispatch",
            run_state={"round": 2},
            source_plan={"sourceIds": ["cts", "custom_source"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        executor_id="executor_1",
        attempt_no=lease.attempt_no,
    )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_pending_dispatch",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


@pytest.mark.parametrize(
    ("column", "corrupt_json"),
    [
        ("run_state_json", "[]"),
        ("source_plan_json", "[]"),
        ("pending_commands_json", "{}"),
        ("pending_commands_json", "[42]"),
    ],
)
def test_recoverable_checkpoint_rejects_shape_corrupt_payloads_without_normalizing(
    tmp_path: Path,
    column: str,
    corrupt_json: str,
) -> None:
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    with sqlite3.connect(db_path) as conn:
        _insert_checkpoint_row(
            conn,
            checkpoint_id="rtcheckpoint_shape_corrupt",
            runtime_run_id="runtime_run_1",
            created_at="2026-06-08T00:00:01.000000Z",
        )
        conn.execute(
            f"UPDATE runtime_control_checkpoints SET {column} = ? WHERE checkpoint_id = ?",
            (corrupt_json, "rtcheckpoint_shape_corrupt"),
        )
        conn.execute(
            "UPDATE runtime_control_runs SET latest_checkpoint_id = ? WHERE runtime_run_id = ?",
            ("rtcheckpoint_shape_corrupt", "runtime_run_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_shape_corrupt",
        reason_code="runtime_checkpoint_corrupt",
    )


def test_diagnostic_checkpoint_read_keeps_legacy_shape_normalization(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    with sqlite3.connect(db_path) as conn:
        _insert_checkpoint_row(
            conn,
            checkpoint_id="rtcheckpoint_diagnostic_shape",
            runtime_run_id="runtime_run_1",
            created_at="2026-06-08T00:00:01.000000Z",
        )
        conn.execute(
            "UPDATE runtime_control_checkpoints SET pending_commands_json = ? WHERE checkpoint_id = ?",
            ("[42]", "rtcheckpoint_diagnostic_shape"),
        )

    checkpoint = store.get_latest_checkpoint(runtime_run_id="runtime_run_1")

    assert checkpoint is not None
    assert checkpoint.checkpoint_id == "rtcheckpoint_diagnostic_shape"
    assert checkpoint.pending_commands == []


@pytest.mark.parametrize(
    ("source_ids_json", "checkpoint_source_ids"),
    [
        ('["cts", 42]', ["cts"]),
        ("{}", []),
    ],
)
def test_recoverable_checkpoint_rejects_invalid_committed_run_source_scope_shape(
    tmp_path: Path,
    source_ids_json: str,
    checkpoint_source_ids: list[str],
) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_invalid_run_sources",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="after_round_controller",
            run_state={"round": 2},
            source_plan={"sourceIds": checkpoint_source_ids},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        executor_id="executor_1",
        attempt_no=lease.attempt_no,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE runtime_control_runs SET source_ids_json = ? WHERE runtime_run_id = ?",
            (source_ids_json, "runtime_run_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_invalid_run_sources",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_runtime_candidate_boundary_validates_same_sqlite_candidate_truth(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    checkpoint = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_candidate_truth",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=None,
        safe_boundary="runtime_candidate_checkpoint",
        run_state={
            "candidate_identities": {"identity_1": {"resume_ids": ["resume_1"]}},
            "candidate_identity_by_resume_id": {"resume_1": "identity_1"},
            "canonical_resume_by_identity_id": {
                "identity_1": {"canonical_resume_id": "resume_1"}
            },
            "candidate_store": {"resume_1": {}},
            "normalized_store": {"resume_1": {}},
        },
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:01.000000Z",
    )
    store.write_checkpoint(checkpoint, executor_id="executor_1", attempt_no=lease.attempt_no)
    assert store.get_latest_recoverable_checkpoint(runtime_run_id="runtime_run_1") == checkpoint

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_candidate_identities
            SET payload_hash = 'tampered'
            WHERE runtime_run_id = ? AND identity_id = ?
            """,
            ("runtime_run_1", "identity_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_candidate_truth",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_runtime_candidate_boundary_rejects_business_column_tampering_with_unchanged_hash(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    checkpoint = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_candidate_columns",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=None,
        safe_boundary="runtime_candidate_checkpoint",
        run_state={
            "candidate_identities": {"identity_1": {"resume_ids": ["resume_1"]}},
            "candidate_identity_by_resume_id": {"resume_1": "identity_1"},
            "canonical_resume_by_identity_id": {
                "identity_1": {"canonical_resume_id": "resume_1"}
            },
            "candidate_store": {"resume_1": {}},
            "normalized_store": {"resume_1": {}},
        },
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:01.000000Z",
    )
    store.write_checkpoint(checkpoint, executor_id="executor_1", attempt_no=lease.attempt_no)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_candidate_identities
            SET canonical_resume_id = 'resume_tampered'
            WHERE runtime_run_id = ? AND identity_id = ?
            """,
            ("runtime_run_1", "identity_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_candidate_columns",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_runtime_candidate_boundary_rejects_lossy_persisted_json_shape(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    checkpoint = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_candidate_json_shape",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=None,
        safe_boundary="runtime_candidate_checkpoint",
        run_state={
            "candidate_identities": {"identity_1": {"resume_ids": ["resume_1"]}},
            "candidate_identity_by_resume_id": {"resume_1": "identity_1"},
            "canonical_resume_by_identity_id": {
                "identity_1": {"canonical_resume_id": "resume_1"}
            },
            "candidate_store": {"resume_1": {}},
            "normalized_store": {"resume_1": {}},
        },
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:01.000000Z",
    )
    store.write_checkpoint(checkpoint, executor_id="executor_1", attempt_no=lease.attempt_no)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_candidate_identities
            SET conflicting_resume_ids_json = '{}'
            WHERE runtime_run_id = ? AND identity_id = ?
            """,
            ("runtime_run_1", "identity_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_candidate_json_shape",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_runtime_candidate_boundary_fail_closes_invalid_persisted_revision_key(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    checkpoint = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_invalid_revision_key",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=None,
        safe_boundary="runtime_candidate_checkpoint",
        run_state={
            "finalization_revisions": [
                {
                    "revision": 1,
                    "reason_code": "runtime_finalized",
                    "candidate_identity_ids": [],
                    "coverage_summary": {},
                }
            ]
        },
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:01.000000Z",
    )
    store.write_checkpoint(checkpoint, executor_id="executor_1", attempt_no=lease.attempt_no)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_candidate_finalization_revisions
            SET revision = 'bad'
            WHERE runtime_run_id = ? AND revision = 1
            """,
            ("runtime_run_1",),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_invalid_revision_key",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_runtime_candidate_boundary_rejects_stale_persisted_truth_from_older_checkpoint(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_candidate_nonempty",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=None,
            safe_boundary="runtime_candidate_checkpoint",
            run_state={
                "candidate_identities": {"identity_1": {"resume_ids": ["resume_1"]}},
                "candidate_identity_by_resume_id": {"resume_1": "identity_1"},
                "canonical_resume_by_identity_id": {
                    "identity_1": {"canonical_resume_id": "resume_1"}
                },
                "candidate_store": {"resume_1": {}},
                "normalized_store": {"resume_1": {}},
            },
            source_plan={"sourceIds": ["cts", "custom_source"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        executor_id="executor_1",
        attempt_no=lease.attempt_no,
    )
    latest = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_candidate_empty",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=None,
        safe_boundary="runtime_candidate_checkpoint",
        run_state={},
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:02.000000Z",
    )
    store.write_checkpoint(latest, executor_id="executor_1", attempt_no=lease.attempt_no)

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_candidate_empty",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_after_round_controller_validates_same_sqlite_candidate_truth(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    checkpoint = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_after_round_truth",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=2,
        safe_boundary="after_round_controller",
        run_state={
            "round": 2,
            "candidate_identities": {"identity_1": {"resume_ids": ["resume_1"]}},
            "candidate_identity_by_resume_id": {"resume_1": "identity_1"},
            "canonical_resume_by_identity_id": {
                "identity_1": {"canonical_resume_id": "resume_1"}
            },
            "candidate_store": {"resume_1": {}},
            "normalized_store": {"resume_1": {}},
        },
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:01.000000Z",
    )
    store.write_checkpoint(checkpoint, executor_id="executor_1", attempt_no=lease.attempt_no)
    assert store.get_latest_recoverable_checkpoint(runtime_run_id="runtime_run_1") == checkpoint

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_candidate_identities
            SET payload_hash = 'tampered'
            WHERE runtime_run_id = ? AND identity_id = ?
            """,
            ("runtime_run_1", "identity_1"),
        )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id="rtcheckpoint_after_round_truth",
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def _insert_checkpoint_row(
    conn: sqlite3.Connection,
    *,
    checkpoint_id: str,
    runtime_run_id: str,
    safe_boundary: str = "after_round_controller",
    schema_version: str = "runtime-control-checkpoint/v1",
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO runtime_control_checkpoints (
            checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
            run_state_json, source_plan_json, pending_commands_json,
            artifact_manifest_ref, schema_version, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            checkpoint_id,
            runtime_run_id,
            "round",
            1,
            safe_boundary,
            '{"round": 1}',
            '{"sourceIds": ["cts", "custom_source"]}',
            "[]",
            None,
            schema_version,
            created_at,
        ),
    )


def _create_run(store) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status="running",
            current_stage="round",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts", "custom_source"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00.000000Z",
            updated_at="2026-06-08T00:00:00.000000Z",
            completed_at=None,
        )
    )
