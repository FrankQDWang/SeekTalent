from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path

import pytest

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeRunRecord, RuntimeStageOutputInput
from seektalent_runtime_control.retention import RuntimeControlRetentionPolicy, RuntimeRetentionService
from seektalent_runtime_control.store import MAX_RUNTIME_CONTROL_JSON_BYTES, RuntimeControlStore


def test_large_stage_output_is_file_backed_and_resolved_transparently(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact", status="running")
    large_output = _large_safe_output()

    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large",
            runtime_run_id="runtime_run_stage_artifact",
            stage="sourcing",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output=large_output,
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:05.000000Z",
        )
    )

    assert saved.output == large_output
    assert saved.artifact_ref_id is not None
    assert saved.payload_size_bytes > MAX_RUNTIME_CONTROL_JSON_BYTES

    with sqlite3.connect(tmp_path / "runtime_control.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT output_json, artifact_ref_id
            FROM runtime_control_stage_outputs
            WHERE output_id = 'rtout_large'
            """
        ).fetchone()
        artifact = conn.execute(
            """
            SELECT artifact_kind, safe_uri, visibility, metadata_json
            FROM runtime_control_artifact_refs
            WHERE artifact_ref_id = ?
            """,
            (saved.artifact_ref_id,),
        ).fetchone()

    marker = json.loads(row["output_json"])
    assert len(row["output_json"].encode("utf-8")) <= MAX_RUNTIME_CONTROL_JSON_BYTES
    assert marker["artifactRefId"] == saved.artifact_ref_id
    assert "chunks" not in marker
    assert row["artifact_ref_id"] == saved.artifact_ref_id
    assert artifact is not None
    assert artifact["artifact_kind"] == "runtime_stage_output"
    assert artifact["safe_uri"].startswith("artifact://runtime-control/stage-output/")
    assert artifact["visibility"] == "internal"
    assert json.loads(artifact["metadata_json"])["outputId"] == "rtout_large"

    artifact_files = list(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))
    assert len(artifact_files) == 1
    assert json.loads(artifact_files[0].read_text(encoding="utf-8")) == large_output

    loaded = store.get_stage_output(
        runtime_run_id="runtime_run_stage_artifact",
        stage="sourcing",
        output_kind="candidate_batch",
        node_id=None,
        round_no=None,
        schema_version=None,
    )
    listed = store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact", stage="sourcing")

    assert loaded is not None
    assert loaded.output == large_output
    assert [item.output for item in listed] == [large_output]


def test_large_stage_output_rejects_external_artifact_ref_id(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_ref", status="running")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id="rtout_large_external_ref",
                runtime_run_id="runtime_run_stage_artifact_ref",
                stage="sourcing",
                node_id=None,
                round_no=None,
                output_kind="candidate_batch",
                schema_version="stage-output/v1",
                output=_large_safe_output(),
                source_event_id=None,
                source_checkpoint_id=None,
                artifact_ref_id="shared_ref",
                created_at="2026-06-17T00:00:05.000000Z",
            )
        )

    assert exc_info.value.reason_code == "runtime_stage_output_artifact_ref_external"
    assert list(tmp_path.rglob("shared_ref.json")) == []


def test_file_backed_stage_output_rejects_corrupt_artifact_payload(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_corrupt", status="running")
    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_corrupt",
            runtime_run_id="runtime_run_stage_artifact_corrupt",
            stage="sourcing",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:05.000000Z",
        )
    )
    artifact_path = next(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))
    artifact_path.write_text(json.dumps({"chunks": [{"candidateId": "wrong"}]}), encoding="utf-8")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.get_stage_output(
            runtime_run_id="runtime_run_stage_artifact_corrupt",
            stage="sourcing",
            output_kind="candidate_batch",
            node_id=None,
            round_no=None,
            schema_version=None,
        )

    assert exc_info.value.reason_code == "runtime_stage_output_artifact_hash_mismatch"


def test_failed_large_stage_output_save_removes_new_artifact_file(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_failed_save", status="running")
    store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_duplicate_file_cleanup",
            runtime_run_id="runtime_run_stage_artifact_failed_save",
            stage="sourcing",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:05.000000Z",
        )
    )
    artifact_files_before = sorted(tmp_path.rglob("*.json"))

    with pytest.raises(sqlite3.IntegrityError):
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id="rtout_duplicate_file_cleanup",
                runtime_run_id="runtime_run_stage_artifact_failed_save",
                stage="debug",
                node_id=None,
                round_no=None,
                output_kind="debug_trace",
                schema_version="debug_trace/v1",
                output=_large_subject_output(),
                source_event_id=None,
                source_checkpoint_id=None,
                artifact_ref_id=None,
                created_at="2026-06-17T00:00:06.000000Z",
            )
        )

    assert sorted(tmp_path.rglob("*.json")) == artifact_files_before


def test_failed_large_stage_output_save_removes_file_when_artifact_ref_recording_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import seektalent_runtime_control.store as store_module

    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_ref_failure", status="running")

    def fail_artifact_ref_recording(*args: object, **kwargs: object) -> None:
        raise sqlite3.DatabaseError("forced artifact ref failure")

    monkeypatch.setattr(store_module, "_record_stage_output_artifact_ref", fail_artifact_ref_recording)

    with pytest.raises(sqlite3.DatabaseError, match="forced artifact ref failure"):
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id="rtout_ref_failure_cleanup",
                runtime_run_id="runtime_run_stage_artifact_ref_failure",
                stage="debug",
                node_id=None,
                round_no=None,
                output_kind="debug_trace",
                schema_version="debug_trace/v1",
                output=_large_subject_output(),
                source_event_id=None,
                source_checkpoint_id=None,
                artifact_ref_id=None,
                created_at="2026-06-17T00:00:06.000000Z",
            )
        )

    assert list(tmp_path.rglob("rtartifact_stage_*.json")) == []
    assert store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_ref_failure") == []


def test_failed_large_stage_output_save_removes_file_when_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_save_commit_failure", status="running")
    original_connect = store._connect

    class CommitFailingConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn
            self._fail_next_commit = True

        def __getattr__(self, name: str) -> object:
            return getattr(self._conn, name)

        def commit(self) -> None:
            if self._fail_next_commit:
                self._fail_next_commit = False
                raise sqlite3.DatabaseError("forced stage output commit failure")
            self._conn.commit()

    @contextmanager
    def failing_connect():
        with original_connect() as conn:
            yield CommitFailingConnection(conn)

    monkeypatch.setattr(store, "_connect", failing_connect)

    with pytest.raises(sqlite3.DatabaseError, match="forced stage output commit failure"):
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id="rtout_commit_failure_cleanup",
                runtime_run_id="runtime_run_stage_artifact_save_commit_failure",
                stage="debug",
                node_id=None,
                round_no=None,
                output_kind="debug_trace",
                schema_version="debug_trace/v1",
                output=_large_subject_output(),
                source_event_id=None,
                source_checkpoint_id=None,
                artifact_ref_id=None,
                created_at="2026-06-17T00:00:06.000000Z",
            )
        )

    assert list(tmp_path.rglob("rtartifact_stage_*.json")) == []
    monkeypatch.setattr(store, "_connect", original_connect)
    assert store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_save_commit_failure") == []
    with sqlite3.connect(store.path) as conn:
        remaining_ref = conn.execute("SELECT 1 FROM runtime_control_artifact_refs").fetchone()
    assert remaining_ref is None


def test_retention_rollback_keeps_file_backed_stage_output_artifact(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_retention_rollback", status="completed")
    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_debug_rollback",
            runtime_run_id="runtime_run_stage_artifact_retention_rollback",
            stage="debug",
            node_id=None,
            round_no=None,
            output_kind="debug_trace",
            schema_version="debug_trace/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:05.000000Z",
        )
    )
    artifact_path = next(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_stage_output_retention_delete
            BEFORE DELETE ON runtime_control_stage_outputs
            BEGIN
                SELECT RAISE(ABORT, 'forced retention delete failure');
            END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="forced retention delete failure"):
        store.delete_terminal_stage_outputs(older_than="2026-06-01T00:00:00.000000Z", batch_size=100)

    assert artifact_path.exists()
    assert store.get_stage_output(
        runtime_run_id="runtime_run_stage_artifact_retention_rollback",
        stage="debug",
        output_kind="debug_trace",
        node_id=None,
        round_no=None,
        schema_version=None,
    ) is not None


def test_retention_commit_failure_restores_quarantined_stage_output_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_commit_failure", status="completed")
    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_debug_commit_failure",
            runtime_run_id="runtime_run_stage_artifact_commit_failure",
            stage="debug",
            node_id=None,
            round_no=None,
            output_kind="debug_trace",
            schema_version="debug_trace/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:05.000000Z",
        )
    )
    artifact_path = next(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))
    original_connect = store._connect

    class CommitFailingConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn
            self._fail_next_commit = True

        def __getattr__(self, name: str) -> object:
            return getattr(self._conn, name)

        def commit(self) -> None:
            if self._fail_next_commit:
                self._fail_next_commit = False
                raise sqlite3.DatabaseError("forced retention commit failure")
            self._conn.commit()

    @contextmanager
    def failing_connect():
        with original_connect() as conn:
            yield CommitFailingConnection(conn)

    monkeypatch.setattr(store, "_connect", failing_connect)

    with pytest.raises(sqlite3.DatabaseError, match="forced retention commit failure"):
        store.delete_terminal_stage_outputs(older_than="2026-06-01T00:00:00.000000Z", batch_size=100)

    assert artifact_path.exists()
    assert list(tmp_path.rglob(f"{artifact_path.name}.delete-*")) == []
    monkeypatch.setattr(store, "_connect", original_connect)
    assert (
        store.get_stage_output(
            runtime_run_id="runtime_run_stage_artifact_commit_failure",
            stage="debug",
            output_kind="debug_trace",
            node_id=None,
            round_no=None,
            schema_version=None,
        )
        is not None
    )


def test_retention_post_commit_partial_unlink_failure_records_pending_artifact_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_partial_unlink", status="completed")
    first = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_debug_partial_unlink_1",
            runtime_run_id="runtime_run_stage_artifact_partial_unlink",
            stage="debug",
            node_id=None,
            round_no=None,
            output_kind="debug_trace",
            schema_version="debug_trace/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:05.000000Z",
        )
    )
    second = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_debug_partial_unlink_2",
            runtime_run_id="runtime_run_stage_artifact_partial_unlink",
            stage="debug",
            node_id=None,
            round_no=None,
            output_kind="debug_trace_2",
            schema_version="debug_trace/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:06.000000Z",
        )
    )
    first_path = next(tmp_path.rglob(f"{first.artifact_ref_id}.json"))
    second_path = next(tmp_path.rglob(f"{second.artifact_ref_id}.json"))
    original_unlink = Path.unlink

    def fail_second_quarantine_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith(f"{second_path.name}.delete-"):
            raise OSError("forced second artifact delete failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_quarantine_unlink)

    with pytest.raises(OSError, match="forced second artifact delete failure"):
        store.delete_terminal_stage_outputs(older_than="2026-06-01T00:00:00.000000Z", batch_size=100)

    assert store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_partial_unlink") == []
    assert not first_path.exists()
    assert not second_path.exists()
    assert list(tmp_path.rglob(f"{first_path.name}.delete-*")) == []
    quarantined = list(tmp_path.rglob(f"{second_path.name}.delete-*"))
    assert len(quarantined) == 1
    with sqlite3.connect(store.path) as conn:
        remaining_refs = conn.execute(
            """
            SELECT artifact_ref_id
            FROM runtime_control_artifact_refs
            WHERE artifact_ref_id IN (?, ?)
            """,
            (first.artifact_ref_id, second.artifact_ref_id),
        ).fetchall()
    assert remaining_refs == []
    pending = _pending_artifact_deletions(store.path)
    assert pending == [
        {
            "artifact_ref_id": second.artifact_ref_id,
            "quarantine_path": str(quarantined[0]),
            "reason_code": "runtime_stage_output_retention",
            "status": "pending",
        }
    ]


def test_retention_deletes_file_backed_stage_output_artifacts(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_retention", status="completed")
    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_debug",
            runtime_run_id="runtime_run_stage_artifact_retention",
            stage="debug",
            node_id=None,
            round_no=None,
            output_kind="debug_trace",
            schema_version="debug_trace/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:05.000000Z",
        )
    )
    artifact_files = list(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))
    assert len(artifact_files) == 1
    artifact_path = artifact_files[0]

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        policy=RuntimeControlRetentionPolicy(
            terminal_run_min_age_days=7,
            developer_event_ttl_days=7,
            internal_event_ttl_days=7,
            checkpoint_ttl_days=7,
            lease_ttl_days=7,
            command_ttl_days=7,
            non_required_stage_output_ttl_days=7,
            batch_size=100,
        ),
    ).cleanup()

    assert result.deleted_stage_output_count == 1
    assert not artifact_path.exists()
    assert store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_retention") == []
    with sqlite3.connect(tmp_path / "runtime_control.sqlite3") as conn:
        remaining_ref = conn.execute(
            "SELECT 1 FROM runtime_control_artifact_refs WHERE artifact_ref_id = ?",
            (saved.artifact_ref_id,),
        ).fetchone()
    assert remaining_ref is None


def test_privacy_erasure_deletes_file_backed_stage_output_artifacts(tmp_path: Path) -> None:
    from seektalent.privacy_erasure import erase_candidate_subject

    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_erasure", status="completed")
    _insert_subject_candidate(
        store,
        runtime_run_id="runtime_run_stage_artifact_erasure",
        resume_id="resume_1",
        identity_id="identity_1",
    )
    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_subject",
            runtime_run_id="runtime_run_stage_artifact_erasure",
            stage="finalization",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output=_large_subject_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:05.000000Z",
        )
    )
    unrelated_same_run = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_unrelated_same_run",
            runtime_run_id="runtime_run_stage_artifact_erasure",
            stage="debug",
            node_id=None,
            round_no=None,
            output_kind="debug_trace",
            schema_version="debug_trace/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:06.000000Z",
        )
    )
    other_store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_erasure_other", status="completed")
    other_saved = other_store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_other_run",
            runtime_run_id="runtime_run_stage_artifact_erasure_other",
            stage="debug",
            node_id=None,
            round_no=None,
            output_kind="debug_trace",
            schema_version="debug_trace/v1",
            output=_large_safe_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:06.000000Z",
        )
    )
    artifact_path = next(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))
    unrelated_same_run_path = next(tmp_path.rglob(f"{unrelated_same_run.artifact_ref_id}.json"))
    other_run_path = next(tmp_path.rglob(f"{other_saved.artifact_ref_id}.json"))

    result = erase_candidate_subject(
        resume_id="resume_1",
        erased_at="2026-06-17T00:00:13.000000Z",
        runtime_control_path=store.path,
    )

    assert result.runtime_candidate_identity_count == 1
    assert result.runtime_candidate_evidence_count == 1
    assert not artifact_path.exists()
    assert unrelated_same_run_path.exists()
    assert other_run_path.exists()
    assert [item.output_id for item in store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_erasure")] == [
        unrelated_same_run.output_id
    ]
    assert [item.output_id for item in store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_erasure_other")] == [
        other_saved.output_id
    ]
    with sqlite3.connect(tmp_path / "runtime_control.sqlite3") as conn:
        remaining_ref = conn.execute(
            "SELECT 1 FROM runtime_control_artifact_refs WHERE artifact_ref_id = ?",
            (saved.artifact_ref_id,),
        ).fetchone()
    assert remaining_ref is None


def test_privacy_erasure_post_commit_file_delete_failure_records_pending_artifact_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from seektalent.privacy_erasure import erase_candidate_subject

    store = _store_with_run(
        tmp_path,
        runtime_run_id="runtime_run_stage_artifact_erasure_delete_failure",
        status="completed",
    )
    _insert_subject_candidate(
        store,
        runtime_run_id="runtime_run_stage_artifact_erasure_delete_failure",
        resume_id="resume_1",
        identity_id="identity_1",
    )
    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_subject_delete_failure",
            runtime_run_id="runtime_run_stage_artifact_erasure_delete_failure",
            stage="finalization",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output=_large_subject_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:05.000000Z",
        )
    )
    artifact_path = next(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))
    original_unlink = Path.unlink

    def fail_subject_artifact_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == artifact_path or path.name.startswith(f"{artifact_path.name}.delete-"):
            raise OSError("forced artifact delete failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_subject_artifact_unlink)

    with pytest.raises(OSError, match="forced artifact delete failure"):
        erase_candidate_subject(
            resume_id="resume_1",
            erased_at="2026-06-17T00:00:13.000000Z",
            runtime_control_path=store.path,
        )

    assert not artifact_path.exists()
    quarantined = list(tmp_path.rglob(f"{artifact_path.name}.delete-*"))
    assert len(quarantined) == 1
    assert store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_erasure_delete_failure") == []
    with sqlite3.connect(store.path) as conn:
        remaining_ref = conn.execute(
            "SELECT 1 FROM runtime_control_artifact_refs WHERE artifact_ref_id = ?",
            (saved.artifact_ref_id,),
        ).fetchone()
    assert remaining_ref is None
    assert _pending_artifact_deletions(store.path) == [
        {
            "artifact_ref_id": saved.artifact_ref_id,
            "quarantine_path": str(quarantined[0]),
            "reason_code": "privacy_erasure",
            "status": "pending",
        }
    ]


def test_privacy_erasure_deletes_file_backed_stage_output_for_evidence_only_subject(tmp_path: Path) -> None:
    from seektalent.privacy_erasure import erase_candidate_subject

    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_stage_artifact_evidence_only", status="completed")
    _insert_subject_evidence_only(
        store,
        runtime_run_id="runtime_run_stage_artifact_evidence_only",
        resume_id="resume_1",
        identity_id="identity_1",
    )
    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_large_subject_evidence_only",
            runtime_run_id="runtime_run_stage_artifact_evidence_only",
            stage="finalization",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output=_large_subject_output(),
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-05-01T00:00:05.000000Z",
        )
    )
    artifact_path = next(tmp_path.rglob(f"{saved.artifact_ref_id}.json"))

    result = erase_candidate_subject(
        resume_id="resume_1",
        erased_at="2026-06-17T00:00:13.000000Z",
        runtime_control_path=store.path,
    )

    assert result.runtime_candidate_identity_count == 0
    assert result.runtime_candidate_evidence_count == 1
    assert not artifact_path.exists()
    assert store.list_stage_outputs(runtime_run_id="runtime_run_stage_artifact_evidence_only") == []
    with sqlite3.connect(tmp_path / "runtime_control.sqlite3") as conn:
        remaining_ref = conn.execute(
            "SELECT 1 FROM runtime_control_artifact_refs WHERE artifact_ref_id = ?",
            (saved.artifact_ref_id,),
        ).fetchone()
    assert remaining_ref is None


def _store_with_run(tmp_path: Path, *, runtime_run_id: str, status: str) -> RuntimeControlStore:
    completed_at = "2026-05-01T00:00:00.000000Z" if status in {"cancelled", "completed", "failed"} else None
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id=f"reqapproved_{runtime_run_id}",
            status=status,
            current_stage="sourcing",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-05-01T00:00:00.000000Z",
            updated_at="2026-05-01T00:00:00.000000Z",
            completed_at=completed_at,
        )
    )
    return store


def _insert_subject_candidate(
    store: RuntimeControlStore,
    *,
    runtime_run_id: str,
    resume_id: str,
    identity_id: str,
) -> None:
    with store._connect() as conn, conn:
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_identities (
                runtime_run_id, identity_id, canonical_resume_id, merged_resume_ids_json,
                source_evidence_ids_json, display_name, title, company, location, summary,
                score, fit_bucket, source_round, payload_hash, updated_at
            )
            VALUES (?, ?, ?, '[]', '["evidence_1"]', 'Alice Chen', '', '', '', '', NULL, NULL, NULL, 'hash_1', ?)
            """,
            (runtime_run_id, identity_id, resume_id, "2026-05-01T00:00:04.000000Z"),
        )
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_evidence (
                runtime_run_id, evidence_id, identity_id, resume_id, source_kind,
                evidence_level, provider_candidate_key_hash, score, fit_bucket,
                payload_json, payload_hash, updated_at
            )
            VALUES (?, 'evidence_1', ?, ?, 'cts', 'summary', 'provider_hash_1', 91, 'strong', '{}', 'hash_2', ?)
            """,
            (runtime_run_id, identity_id, resume_id, "2026-05-01T00:00:04.000000Z"),
        )


def _insert_subject_evidence_only(
    store: RuntimeControlStore,
    *,
    runtime_run_id: str,
    resume_id: str,
    identity_id: str,
) -> None:
    with store._connect() as conn, conn:
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_evidence (
                runtime_run_id, evidence_id, identity_id, resume_id, source_kind,
                evidence_level, provider_candidate_key_hash, score, fit_bucket,
                payload_json, payload_hash, updated_at
            )
            VALUES (?, 'evidence_1', ?, ?, 'cts', 'summary', 'provider_hash_1', 91, 'strong', '{}', 'hash_2', ?)
            """,
            (runtime_run_id, identity_id, resume_id, "2026-05-01T00:00:04.000000Z"),
        )


def _pending_artifact_deletions(path: Path) -> list[dict[str, str]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT artifact_ref_id, quarantine_path, reason_code, status
            FROM runtime_control_artifact_deletions
            ORDER BY requested_at ASC, deletion_id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _large_safe_output() -> dict[str, object]:
    return {
        "chunks": [
            {
                "candidateId": f"candidate_{index}",
                "summary": "safe public scoring explanation " + ("x" * 360),
            }
            for index in range(80)
        ]
    }


def _large_subject_output() -> dict[str, object]:
    return {
        "chunks": [
            {
                "candidateId": "resume_1",
                "displayName": "Alice Chen",
                "summary": "safe candidate explanation " + ("x" * 360),
            }
            for _ in range(80)
        ]
    }
