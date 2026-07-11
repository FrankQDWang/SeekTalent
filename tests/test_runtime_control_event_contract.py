from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import sqlite3

import pytest
from pydantic import ValidationError


def test_v1_database_migrates_to_run_intent_ownership_without_dropping_rows(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    _create_v1_runtime_db(db_path)

    store = RuntimeControlStore(db_path)
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(runtime_control_runs)").fetchall()}
        runnable_index_columns = [
            row["name"] for row in conn.execute("PRAGMA index_info(idx_runtime_runs_status_created)").fetchall()
        ]
        migrated = conn.execute(
            "SELECT runtime_run_id, run_intent_id, start_idempotency_key, run_kind FROM runtime_control_runs"
        ).fetchone()

    assert version == RUNTIME_CONTROL_SCHEMA_VERSION == 5
    assert migrated["runtime_run_id"] == "runtime_run_v1"
    assert migrated["run_intent_id"] == "runtime_run_v1"
    assert migrated["start_idempotency_key"] == "runtime_run_v1"
    assert migrated["run_kind"] == "primary"
    assert "idx_runtime_runs_run_intent" in indexes
    assert "idx_runtime_runs_approved_requirement_created" in indexes
    assert runnable_index_columns == ["status", "created_at", "runtime_run_id"]

    # The v1 UNIQUE(approved_requirement_revision_id) ownership must be gone.
    store.create_run(
        _run(
            runtime_run_id="runtime_run_v2",
            run_intent_id="intent_v2",
            start_idempotency_key="start_v2",
            approved_requirement_revision_id="reqapproved_v1",
        )
    )
    assert store.get_run_by_run_intent_id("runtime_run_v1").runtime_run_id == "runtime_run_v1"
    assert store.get_run_by_run_intent_id("intent_v2").runtime_run_id == "runtime_run_v2"


def test_v4_database_migrates_requirement_amendment_provenance_to_v5(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore

    db_path = tmp_path / "runtime_control_v4.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE runtime_requirement_amendments (
              amendment_id TEXT PRIMARY KEY,
              agent_conversation_id TEXT NOT NULL,
              runtime_run_id TEXT,
              base_draft_revision_id TEXT,
              result_draft_revision_id TEXT,
              base_approved_requirement_revision_id TEXT,
              result_approved_requirement_revision_id TEXT,
              target_round_no INTEGER,
              effective_boundary TEXT,
              applied_event_id TEXT,
              input_text TEXT NOT NULL,
              target_section_hint TEXT,
              status TEXT NOT NULL,
              normalized_patch_json TEXT NOT NULL,
              rejected_fragments_json TEXT NOT NULL,
              review_items_json TEXT NOT NULL,
              resolved_patch_json TEXT,
              superseded_by_amendment_id TEXT,
              resolved_at TEXT,
              idempotency_key TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(agent_conversation_id, idempotency_key),
              UNIQUE(runtime_run_id, idempotency_key)
            );
            INSERT INTO runtime_requirement_amendments (
              amendment_id, agent_conversation_id, runtime_run_id, input_text,
              status, normalized_patch_json, rejected_fragments_json, review_items_json,
              idempotency_key, created_at
            ) VALUES (
              'reqamend_v4', 'agent_conv_v4', 'runtime_run_v4', 'Add Kafka',
              'pending_target_round', '{}', '[]', '[]',
              'idem_v4', '2026-06-17T00:00:00.000000Z'
            );
            PRAGMA user_version = 4;
            """
        )

    RuntimeControlStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runtime_requirement_amendments)")}
        provenance = conn.execute(
            """
            SELECT provenance_json
            FROM runtime_requirement_amendments
            WHERE amendment_id = 'reqamend_v4'
            """
        ).fetchone()["provenance_json"]

    assert version == RUNTIME_CONTROL_SCHEMA_VERSION == 5
    assert "provenance_json" in columns
    assert provenance == "{}"


def test_runs_are_owned_by_run_intent_not_requirement_revision(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    first = store.create_run(
        _run(
            runtime_run_id="runtime_run_first",
            run_intent_id="intent_first",
            start_idempotency_key="start_first",
            approved_requirement_revision_id="reqapproved_shared",
        )
    )
    second = store.create_run(
        _run(
            runtime_run_id="runtime_run_second",
            run_intent_id="intent_second",
            start_idempotency_key="start_second",
            approved_requirement_revision_id="reqapproved_shared",
        )
    )

    assert first.approved_requirement_revision_id == second.approved_requirement_revision_id
    assert store.get_run_by_run_intent_id("intent_first").runtime_run_id == "runtime_run_first"
    assert store.get_run_by_start_idempotency_key("start_second").runtime_run_id == "runtime_run_second"
    assert store.get_run_by_approved_requirement_revision("reqapproved_shared").runtime_run_id in {
        "runtime_run_first",
        "runtime_run_second",
    }

    duplicate = store.create_run(
        _run(
            runtime_run_id="runtime_run_replayed",
            run_intent_id="intent_first",
            start_idempotency_key="start_first_replayed",
            approved_requirement_revision_id="reqapproved_fork",
        )
    )
    assert duplicate.runtime_run_id == "runtime_run_first"
    assert store.get_run_by_run_intent_id("intent_first").runtime_run_id == "runtime_run_first"

    start_key_replay = store.create_run(
        _run(
            runtime_run_id="runtime_run_start_replayed",
            run_intent_id="intent_start_replayed",
            start_idempotency_key="start_second",
            approved_requirement_revision_id="reqapproved_other",
        )
    )
    assert start_key_replay.runtime_run_id == "runtime_run_second"


def test_run_kind_is_limited_to_primary_rerun_or_fork_at_model_and_db_boundaries(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    for run_kind in ("primary", "rerun", "fork"):
        run = _run(runtime_run_id=f"runtime_run_{run_kind}", run_kind=run_kind)
        assert run.run_kind == run_kind

    with pytest.raises(ValidationError):
        _run(runtime_run_id="runtime_run_invalid_kind", run_kind="retry")

    db_path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(db_path).initialize()
    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO runtime_control_runs (
              runtime_run_id, run_intent_id, start_idempotency_key, run_kind,
              approved_requirement_revision_id, status, current_stage, latest_event_seq,
              source_ids_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "runtime_run_bad_db_kind",
                "intent_bad_db_kind",
                "start_bad_db_kind",
                "retry",
                "reqapproved_bad_db_kind",
                "queued",
                "queued",
                0,
                "[]",
                "2026-06-17T00:00:00.000000Z",
                "2026-06-17T00:00:00.000000Z",
            ),
        )


def test_runtime_events_are_ordered_idempotent_metadata_rich_and_public_filterable(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_events"))

    first = store.append_event(
        _event(
            event_id="rtevt_first",
            runtime_run_id="runtime_run_events",
            idempotency_key="event-key-1",
            visibility="public",
            payload={"message": "public progress"},
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )
    replay = store.append_event(
        _event(
            event_id="rtevt_replay",
            runtime_run_id="runtime_run_events",
            idempotency_key="event-key-1",
            visibility="public",
            payload={"message": "public progress"},
            created_at="2026-06-17T00:00:02.000000Z",
        )
    )
    internal = store.append_event(
        _event(
            event_id="rtevt_internal",
            runtime_run_id="runtime_run_events",
            idempotency_key="event-key-2",
            visibility="internal",
            payload={"artifactRefId": "artifact_hidden"},
            created_at="2026-06-17T00:00:03.000000Z",
        )
    )

    assert first.event_seq == 1
    assert replay.event_id == "rtevt_first"
    assert replay.event_seq == 1
    assert internal.event_seq == 2
    assert store.get_run("runtime_run_events").latest_event_seq == 2

    stored = store.get_event(runtime_run_id="runtime_run_events", event_id="rtevt_first")
    assert stored.schema_version == "runtime-control-event/v1"
    assert stored.visibility == "public"
    assert stored.payload_kind == "compact"
    assert stored.payload_size_bytes > 0
    assert stored.projection_attempt_count == 0
    assert stored.last_projection_error_code is None
    assert stored.projected_at is None

    page = store.list_events(runtime_run_id="runtime_run_events", after_seq=0, limit=10)
    assert [event.event_id for event in page.events] == ["rtevt_first", "rtevt_internal"]
    assert [event.event_seq for event in page.events] == [1, 2]

    public_page = store.list_public_events(runtime_run_id="runtime_run_events", after_seq=0, limit=10)
    assert [event.event_id for event in public_page.events] == ["rtevt_first"]
    assert public_page.events[0].payload == {"message": "public progress"}


def test_public_runtime_progress_normalizes_to_public_runtime_control_event() -> None:
    from seektalent.progress import ProgressEvent
    from seektalent.runtime.public_events import make_runtime_public_event
    from seektalent_runtime_control.events import (
        PUBLIC_RUNTIME_EVENT_SCHEMA_VERSION,
        RUNTIME_CONTROL_EVENT_SCHEMA_VERSION,
        normalize_progress_event,
        public_event_payload,
    )

    public_payload = dict(
        make_runtime_public_event(
            runtime_run_id="workflow_run_public",
            stage="source_result",
            event_seq=12,
            round_no=1,
            source_kind="cts",
            status="completed",
            counts={
                "roundReturned": 5,
                "roundIdentities": 4,
                "ignoredUnsafeCount": 99,
            },
            details={"reflectionSummary": "CTS returned a compact public batch."},
            created_at="2026-06-17T00:00:04.000000Z",
        )
    )
    public_payload.update(
        {
            "prompt": "do not persist",
            "provider": "raw provider",
            "resume": {"text": "private resume"},
            "rawStructuredOutput": {"unsafe": True},
        }
    )

    event = normalize_progress_event(
        ProgressEvent(
            type="runtime_public_event",
            message="CTS completed",
            timestamp="2026-06-17T00:00:04+00:00",
            round_no=1,
            payload=public_payload,
        ),
        runtime_run_id="runtime_run_control",
        now="2026-06-17T00:00:05.000000Z",
    )

    assert PUBLIC_RUNTIME_EVENT_SCHEMA_VERSION == "runtime_public_event_v1"
    assert RUNTIME_CONTROL_EVENT_SCHEMA_VERSION == "runtime-control-event/v1"
    assert event.runtime_run_id == "runtime_run_control"
    assert event.event_type == "runtime_round_source_result"
    assert event.stage == "source_result"
    assert event.round_no == 1
    assert event.source_id == "cts"
    assert event.status == "completed"
    assert event.visibility == "public"
    assert event.schema_version == RUNTIME_CONTROL_EVENT_SCHEMA_VERSION
    assert event.payload_kind == "compact"
    assert event.idempotency_key == "runtime_run_control:1:source_result:cts"
    assert event.created_at == "2026-06-17T00:00:05.000000Z"

    projected = public_event_payload(event)
    assert projected is not None
    assert projected["schemaVersion"] == PUBLIC_RUNTIME_EVENT_SCHEMA_VERSION
    assert projected["runtimeRunId"] == "runtime_run_control"
    assert projected["eventId"] == "runtime_run_control:1:source_result:cts"
    assert projected["counts"] == {"roundReturned": 5, "roundIdentities": 4}
    assert projected["details"] == {"reflectionSummary": "CTS returned a compact public batch."}

    serialized = str(event.model_dump(mode="json"))
    assert "runtime_runtime_public_event" not in serialized
    assert "ignoredUnsafeCount" not in serialized
    assert "do not persist" not in serialized
    assert "raw provider" not in serialized
    assert "private resume" not in serialized
    assert "rawStructuredOutput" not in serialized


@pytest.mark.parametrize("message", ["https://example.invalid/private/raw-identity", "Bearer private-token"])
def test_public_runtime_progress_redacts_unsafe_progress_message(message: str) -> None:
    from seektalent.progress import ProgressEvent
    from seektalent.runtime.public_events import make_runtime_public_event
    from seektalent_runtime_control.events import normalize_progress_event

    event = normalize_progress_event(
        ProgressEvent(
            type="runtime_public_event",
            message=message,
            timestamp="2026-07-11T00:00:00Z",
            round_no=1,
            payload=make_runtime_public_event(
                runtime_run_id="runtime-run-1",
                stage="source_result",
                event_seq=1,
                round_no=1,
                source_kind="cts",
            ),
        ),
        runtime_run_id="runtime-run-1",
        now="2026-07-11T00:00:01Z",
    )

    assert event.summary == "第 1 轮CTS检索结果已更新。"
    assert message not in repr(event.model_dump(mode="json"))


def test_public_runtime_progress_uses_canonical_summary_for_safe_looking_runtime_message() -> None:
    from seektalent.progress import ProgressEvent
    from seektalent.runtime.public_events import make_runtime_public_event
    from seektalent_runtime_control.events import normalize_progress_event

    raw_message = "OpenCLI CDP target 98b37a browser session failed"
    event = normalize_progress_event(
        ProgressEvent(
            type="runtime_public_event",
            message=raw_message,
            timestamp="2026-07-11T00:00:00Z",
            round_no=1,
            payload=make_runtime_public_event(
                runtime_run_id="runtime-run-1",
                stage="source_result",
                event_seq=1,
                round_no=1,
                source_kind="liepin",
                status="blocked",
            ),
        ),
        runtime_run_id="runtime-run-1",
        now="2026-07-11T00:00:01Z",
    )

    assert event.summary == "第 1 轮猎聘检索受阻：猎聘检索受阻，请稍后重试。"
    assert raw_message not in repr(event.model_dump(mode="json"))


def test_non_public_runtime_progress_is_developer_compact_and_redacted() -> None:
    from seektalent.progress import ProgressEvent
    from seektalent_runtime_control.events import normalize_progress_event, public_event_payload

    first = normalize_progress_event(
        ProgressEvent(
            type="search_started",
            message="query started with Cookie secret",
            timestamp="2026-06-17T00:00:04+00:00",
            round_no=2,
            payload={
                "stage": "search",
                "safe": "visible",
                "prompt": "private prompt",
                "provider": {"name": "raw provider"},
                "authorization": "Bearer hidden",
                "candidateResume": "private resume",
                "rawStructuredOutput": {"unsafe": True},
            },
        ),
        runtime_run_id="runtime_run_internal",
        now="2026-06-17T00:00:05.000000Z",
    )
    replay = normalize_progress_event(
        ProgressEvent(
            type="search_started",
            message="query started with Cookie secret",
            timestamp="2026-06-17T00:00:04+00:00",
            round_no=2,
            payload={
                "stage": "search",
                "safe": "visible",
                "prompt": "private prompt",
                "provider": {"name": "raw provider"},
                "authorization": "Bearer hidden",
                "candidateResume": "private resume",
                "rawStructuredOutput": {"unsafe": True},
            },
        ),
        runtime_run_id="runtime_run_internal",
        now="2026-06-17T00:00:06.000000Z",
    )

    assert first.event_type == "runtime_search_started"
    assert first.stage == "search"
    assert first.round_no == 2
    assert first.visibility == "developer"
    assert first.payload_kind == "compact"
    assert first.payload["safe"] == "visible"
    assert first.payload["progressType"] == "search_started"
    assert first.idempotency_key == replay.idempotency_key
    assert public_event_payload(first) is None

    serialized = str(first.model_dump(mode="json"))
    assert "visible" in serialized
    assert "Cookie" not in serialized
    assert "secret" not in serialized
    assert "private prompt" not in serialized
    assert "raw provider" not in serialized
    assert "Bearer" not in serialized
    assert "private resume" not in serialized
    assert "rawStructuredOutput" not in serialized


def test_runtime_event_sink_exposes_progress_and_control_event_boundaries(tmp_path: Path) -> None:
    from seektalent.progress import ProgressEvent
    from seektalent_runtime_control.event_sink import RuntimeControlEventSink

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_sink", status="starting"))
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_sink",
        executor_id="executor_sink",
        acquired_at="2026-06-17T00:00:01.000000Z",
        lease_expires_at="2026-06-17T00:01:01.000000Z",
    )
    sink = RuntimeControlEventSink(store)

    progress_event = sink.append_progress(
        ProgressEvent(
            type="search_started",
            message="search started",
            timestamp="2026-06-17T00:00:02+00:00",
            round_no=1,
            payload={"stage": "search", "sourceKind": "cts"},
        ),
        runtime_run_id="runtime_run_sink",
        executor_id="executor_sink",
        now="2026-06-17T00:00:02.000000Z",
    )
    control_event = sink.append_control_event(
        _event(
            event_id="rtevt_sink_control",
            runtime_run_id="runtime_run_sink",
            idempotency_key="sink-control",
            visibility="developer",
            created_at="2026-06-17T00:00:03.000000Z",
        ),
        executor_id="executor_sink",
    )

    assert progress_event.event_type == "runtime_search_started"
    assert control_event.event_id == "rtevt_sink_control"
    assert store.get_run("runtime_run_sink").latest_event_seq == 2


def test_stage_outputs_are_canonical_and_db_idempotent_for_absent_node_and_round(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_stage_outputs"))
    event = store.append_event(
        _event(
            event_id="rtevt_stage_output_source",
            runtime_run_id="runtime_run_stage_outputs",
            idempotency_key="event-key-stage-output",
        )
    )

    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_first",
            runtime_run_id="runtime_run_stage_outputs",
            stage="sourcing",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output={"candidateIds": ["cand_1"]},
            source_event_id=event.event_id,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:05.000000Z",
        )
    )
    duplicate = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_duplicate",
            runtime_run_id="runtime_run_stage_outputs",
            stage="sourcing",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output={"candidateIds": ["cand_1"]},
            source_event_id=event.event_id,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:06.000000Z",
        )
    )

    assert duplicate.output_id == saved.output_id
    assert saved.node_key == ""
    assert saved.round_key == -1
    assert saved.payload_hash
    assert saved.payload_size_bytes > 0

    loaded = store.get_stage_output(
        runtime_run_id="runtime_run_stage_outputs",
        stage="sourcing",
        output_kind="candidate_batch",
        node_id=None,
        round_no=None,
        schema_version=None,
    )
    assert loaded == saved
    assert loaded.output == {"candidateIds": ["cand_1"]}

    outputs = store.list_stage_outputs(runtime_run_id="runtime_run_stage_outputs", stage="sourcing")
    assert [output.output_id for output in outputs] == ["rtout_first"]

    with sqlite3.connect(tmp_path / "runtime_control.sqlite3") as conn:
        row = conn.execute(
            """
            SELECT node_id, node_key, round_no, round_key
            FROM runtime_control_stage_outputs
            WHERE output_id = ?
            """,
            (saved.output_id,),
        ).fetchone()
        indexes = {
            index_row[1] for index_row in conn.execute("PRAGMA index_list(runtime_control_stage_outputs)").fetchall()
        }
    assert row == (None, "", None, -1)
    assert "idx_runtime_stage_outputs_run_stage_round_kind" in indexes


def test_stage_outputs_support_round_filter_latest_schema_and_terminal_deletion(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_stage_output_filters", status="running"))
    store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_round_1",
            runtime_run_id="runtime_run_stage_output_filters",
            stage="ranking",
            node_id="ranker",
            round_no=1,
            output_kind="candidate_scores",
            schema_version="stage-output/v1",
            output={"scores": [1]},
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )
    latest = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_round_2",
            runtime_run_id="runtime_run_stage_output_filters",
            stage="ranking",
            node_id="ranker",
            round_no=2,
            output_kind="candidate_scores",
            schema_version="stage-output/v2",
            output={"scores": [2]},
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:02.000000Z",
        )
    )
    store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_final_shortlist",
            runtime_run_id="runtime_run_stage_output_filters",
            stage="finalization",
            output_kind="final_shortlist",
            schema_version="final_shortlist/v1",
            output={"candidateIds": ["candidate_1"]},
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:02.500000Z",
        )
    )

    loaded_latest = store.get_stage_output(
        runtime_run_id="runtime_run_stage_output_filters",
        stage="ranking",
        output_kind="candidate_scores",
        node_id="ranker",
        round_no=2,
        schema_version=None,
    )
    assert loaded_latest == latest

    round_one_outputs = store.list_stage_outputs(
        runtime_run_id="runtime_run_stage_output_filters",
        stage="ranking",
        round_no=1,
    )
    assert [output.output_id for output in round_one_outputs] == ["rtout_round_1"]

    store.update_run_status(
        runtime_run_id="runtime_run_stage_output_filters",
        status="completed",
        updated_at="2026-06-17T00:00:03.000000Z",
        completed_at="2026-06-17T00:00:03.000000Z",
    )
    deleted = store.delete_terminal_stage_outputs(older_than="2026-06-17T00:00:03.000000Z", batch_size=100)
    assert deleted == 2
    assert [
        output.output_id for output in store.list_stage_outputs(runtime_run_id="runtime_run_stage_output_filters")
    ] == ["rtout_final_shortlist"]


@pytest.mark.parametrize(
    "sensitive_output",
    [
        {"rawResumeText": "private resume"},
        {"resumeText": "private resume"},
        {"candidateResume": {"text": "private resume"}},
        {"provider": {"raw": "provider payload"}},
    ],
)
def test_stage_output_rejects_sensitive_payload(
    tmp_path: Path,
    sensitive_output: dict[str, object],
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_stage_output_privacy", status="running"))

    with pytest.raises(RuntimeControlError) as exc_info:
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id="rtout_sensitive",
                runtime_run_id="runtime_run_stage_output_privacy",
                stage="ranking",
                output_kind="candidate_scores",
                schema_version="stage-output/v1",
                output=sensitive_output,
                created_at="2026-06-17T00:00:01.000000Z",
            )
        )

    assert exc_info.value.reason_code == "runtime_stage_output_sensitive_payload"


def test_stage_output_allowlists_public_output(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_stage_output_privacy", status="running"))

    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_public",
            runtime_run_id="runtime_run_stage_output_privacy",
            stage="source_result",
            node_id="cts",
            round_no=1,
            output_kind="runtime_public_source_result",
            schema_version="runtime-public-stage-output/v2",
            output={
                "schemaVersion": "runtime-public-stage-output/v2",
                "publicEventSchemaVersion": "runtime_public_event_v1",
                "stage": "source_result",
                "roundNo": 1,
                "sourceKind": "cts",
                "status": "completed",
                "counts": {"roundReturned": 5, "notAllowed": 99},
                "details": {"reflectionSummary": "safe", "debugField": "drop"},
                "safeReasonCode": None,
                "extraDebug": "drop",
            },
            created_at="2026-06-17T00:00:02.000000Z",
        )
    )

    assert saved.output["counts"] == {"roundReturned": 5}
    assert saved.output["details"] == {"reflectionSummary": "safe"}
    assert "extraDebug" not in saved.output


def test_public_stage_output_rejects_sensitive_query_group_keys(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_public_sensitive", status="running"))

    with pytest.raises(RuntimeControlError) as exc_info:
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id="rtout_public_sensitive_feedback",
                runtime_run_id="runtime_run_public_sensitive",
                stage="feedback",
                round_no=1,
                output_kind="runtime_public_feedback",
                schema_version="runtime-public-stage-output/v2",
                output={
                    "schemaVersion": "runtime-public-stage-output/v2",
                    "publicEventSchemaVersion": "runtime_public_event_v1",
                    "stage": "feedback",
                    "roundNo": 1,
                    "sourceKind": None,
                    "status": "completed",
                    "counts": {},
                    "details": {
                        "queryGroups": [
                            {
                                "queryInstanceId": "query-1",
                                "termGroupKey": "group-1",
                                "queryRole": "exploit",
                                "laneType": "exploit",
                                "queryTerms": ["AI agent"],
                                "keywordQuery": "AI agent platform engineer",
                                "lifecycle": "executed",
                                "executionStatus": "completed",
                                "attempted": True,
                                "rawCandidateCount": 1,
                                "uniqueCandidateCount": 1,
                                "duplicateCandidateCount": 0,
                                "executions": [],
                                "providerPayload": {"secret": "reject the whole output"},
                            }
                        ]
                    },
                    "safeReasonCode": None,
                },
                source_event_id=None,
                source_checkpoint_id=None,
                artifact_ref_id=None,
                created_at="2026-06-22T00:00:00.000000Z",
            )
        )

    assert exc_info.value.reason_code == "runtime_stage_output_sensitive_payload"


def test_public_stage_output_keeps_query_groups_and_drops_unknown_public_details(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_public_query_packages", status="running"))

    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_public_feedback",
            runtime_run_id="runtime_run_public_query_packages",
            stage="feedback",
            round_no=1,
            output_kind="runtime_public_feedback",
            schema_version="runtime-public-stage-output/v2",
            output={
                "schemaVersion": "runtime-public-stage-output/v2",
                "publicEventSchemaVersion": "runtime_public_event_v1",
                "stage": "feedback",
                "roundNo": 1,
                "sourceKind": None,
                "status": "completed",
                "counts": {"feedbackCandidateCount": 2},
                "details": {
                    "queryGroups": [
                        {
                            "queryInstanceId": "query-1",
                            "termGroupKey": "group-1",
                            "queryRole": "exploit",
                            "laneType": "exploit",
                            "queryTerms": ["AI agent"],
                            "keywordQuery": "AI agent platform engineer",
                            "lifecycle": "executed",
                            "executionStatus": "completed",
                            "attempted": True,
                            "rawCandidateCount": 1,
                            "uniqueCandidateCount": 1,
                            "duplicateCandidateCount": 0,
                            "executions": [],
                            "debugNote": "drop this non-sensitive unknown key",
                        }
                    ],
                    "suggestedActivateTerms": ["platform"],
                    "suggestedKeepFilterFields": ["location"],
                    "suggestedDropTerms": [{"bad": "dict must be dropped"}],
                    "nonPublicDetail": "drop this field",
                },
                "safeReasonCode": None,
            },
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-22T00:00:01.000000Z",
        )
    )

    assert saved.output["details"]["queryGroups"] == [
        {
            "queryInstanceId": "query-1",
            "termGroupKey": "group-1",
            "queryRole": "exploit",
            "laneType": "exploit",
            "queryTerms": ["AI agent"],
            "keywordQuery": "AI agent platform engineer",
            "lifecycle": "executed",
            "executionStatus": "completed",
            "attempted": True,
            "rawCandidateCount": 1,
            "uniqueCandidateCount": 1,
            "duplicateCandidateCount": 0,
            "executions": [],
        }
    ]
    assert saved.output["details"]["suggestedKeepFilterFields"] == ["location"]
    assert saved.output["details"]["suggestedDropTerms"] == []
    assert "nonPublicDetail" not in saved.output["details"]


def test_public_stage_output_v2_keeps_only_safe_logical_query_group_fields() -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    output = sanitize_stage_output_payload(
        output_kind="runtime_public_feedback",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "feedback",
            "roundNo": 2,
            "sourceKind": None,
            "status": "completed",
            "counts": {},
            "details": {
                "keywordQuery": "legacy keyword must not survive v2",
                "queryTerms": ["legacy"],
                "plannedQueries": [{"sourceKind": "cts", "keywordQuery": "legacy"}],
                "executedQueries": [{"sourceKind": "liepin", "keywordQuery": "legacy"}],
                "queryGroups": [
                    {
                        "queryInstanceId": "explore-2",
                        "termGroupKey": "group-2",
                        "queryRole": "explore",
                        "laneType": "generic_explore",
                        "queryTerms": ["Platform", "Rust"],
                        "keywordQuery": "Platform Rust",
                        "lifecycle": "executed",
                        "executionStatus": "completed",
                        "attempted": True,
                        "rawCandidateCount": 4,
                        "uniqueCandidateCount": 2,
                        "duplicateCandidateCount": 2,
                        "executions": [
                            {
                                "sourceKind": "liepin",
                                "status": "completed",
                                "rawCandidateCount": 4,
                                "uniqueCandidateCount": 2,
                                "duplicateCandidateCount": 2,
                                "safeReasonCode": "blocked_backend_unavailable",
                                "providerUrl": "https://h.liepin.com/private",
                            },
                            {
                                "sourceKind": "https://provider.example/private/raw-identity",
                                "status": "completed",
                                "rawCandidateCount": 1,
                                "uniqueCandidateCount": 1,
                                "duplicateCandidateCount": 0,
                            },
                            {
                                "sourceKind": "cts",
                                "status": "failed",
                                "rawCandidateCount": 0,
                                "uniqueCandidateCount": 0,
                                "duplicateCandidateCount": 0,
                                "safeReasonCode": "Bearer private-token",
                            },
                        ],
                        "requestedCount": 10,
                        "exhaustedReason": "provider private detail",
                        "queryFingerprint": "private",
                        "providerUrl": "https://h.liepin.com/private",
                    }
                ],
            },
            "safeReasonCode": None,
        },
        stage="feedback",
        round_no=2,
        node_id=None,
    )

    assert set(output["details"]) == {"queryGroups"}
    [group] = output["details"]["queryGroups"]
    assert group == {
        "queryInstanceId": "explore-2",
        "termGroupKey": "group-2",
        "queryRole": "explore",
        "laneType": "generic_explore",
        "queryTerms": ["Platform", "Rust"],
        "keywordQuery": "Platform Rust",
        "lifecycle": "executed",
        "executionStatus": "completed",
        "attempted": True,
        "rawCandidateCount": 4,
        "uniqueCandidateCount": 2,
        "duplicateCandidateCount": 2,
        "executions": [
            {
                "sourceKind": "liepin",
                "status": "completed",
                "rawCandidateCount": 4,
                "uniqueCandidateCount": 2,
                "duplicateCandidateCount": 2,
                "safeReasonCode": "source_browser_backend_unavailable",
            },
            {
                "sourceKind": "cts",
                "status": "failed",
                "rawCandidateCount": 0,
                "uniqueCandidateCount": 0,
                "duplicateCandidateCount": 0,
            },
        ],
    }


def test_public_stage_output_v2_drops_sensitive_detail_text_and_invalid_status() -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    provider_url = "https://provider.example/private/raw-identity"
    private_token = "private-token"
    output = sanitize_stage_output_payload(
        output_kind="runtime_public_feedback",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "feedback",
            "roundNo": 1,
            "sourceKind": None,
            "status": f"Authorization=Bearer {private_token}",
            "counts": {},
            "details": {
                "resumeQualityComment": "Safe quality note.",
                "reflectionSummary": f"Authorization=Bearer {private_token}",
                "suggestedActivateTerms": [
                    "safe term",
                    provider_url,
                    f"Authorization=Bearer {private_token}",
                ],
            },
            "safeReasonCode": None,
        },
        stage="feedback",
        round_no=1,
        node_id=None,
    )

    assert output["status"] == "completed"
    assert output["details"] == {
        "resumeQualityComment": "Safe quality note.",
        "suggestedActivateTerms": ["safe term"],
    }
    assert provider_url not in repr(output)
    assert private_token not in repr(output)


@pytest.mark.parametrize(
    "source_kind",
    [
        "https://provider.example/private/raw-identity",
        "note: Authorization: Bearer private-token",
        "debug secret=private-token",
        "api-key=private-token",
        "api key=private-token",
        "API Key: private-token",
        "X-API-Key: private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_public_stage_output_v2_drops_unsafe_source_kind(source_kind: str) -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    output = sanitize_stage_output_payload(
        output_kind="runtime_public_source_result",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": source_kind,
            "status": "completed",
            "counts": {},
            "details": {},
            "safeReasonCode": None,
        },
        stage="source_result",
        round_no=1,
        node_id=source_kind,
    )

    assert output["sourceKind"] is None
    assert source_kind not in repr(output)


@pytest.mark.parametrize(
    "field",
    ["queryInstanceId", "termGroupKey", "queryRole", "laneType", "keywordQuery"],
)
def test_public_stage_output_v2_drops_sensitive_required_query_text(field: str) -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    secret = "https://provider.example/private/raw-identity"
    group = _stage_query_group(lifecycle="executed")
    group[field] = secret
    output = sanitize_stage_output_payload(
        output_kind="runtime_public_feedback",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "feedback",
            "roundNo": 1,
            "sourceKind": None,
            "status": "completed",
            "counts": {},
            "details": {"queryGroups": [group]},
            "safeReasonCode": None,
        },
        stage="feedback",
        round_no=1,
        node_id=None,
    )

    assert "queryGroups" not in output["details"]
    assert secret not in repr(output)


def test_public_stage_output_v2_filters_sensitive_query_terms() -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    secret = "https://provider.example/private/raw-identity"
    group = _stage_query_group(lifecycle="executed")
    group["queryTerms"] = ["safe term", secret, "Authorization=Bearer private-token"]
    output = sanitize_stage_output_payload(
        output_kind="runtime_public_feedback",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "feedback",
            "roundNo": 1,
            "sourceKind": None,
            "status": "completed",
            "counts": {},
            "details": {"queryGroups": [group]},
            "safeReasonCode": None,
        },
        stage="feedback",
        round_no=1,
        node_id=None,
    )

    [sanitized] = output["details"]["queryGroups"]
    assert sanitized["queryTerms"] == ["safe term"]
    assert secret not in repr(output)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "note: Authorization: Bearer private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_public_stage_output_v2_drops_shared_unsafe_query_text(unsafe_text: str) -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    group = _stage_query_group(lifecycle="executed")
    group["queryTerms"] = ["safe term", unsafe_text]
    output = sanitize_stage_output_payload(
        output_kind="runtime_public_feedback",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "feedback",
            "roundNo": 1,
            "sourceKind": None,
            "status": "completed",
            "counts": {},
            "details": {"queryGroups": [group]},
            "safeReasonCode": None,
        },
        stage="feedback",
        round_no=1,
        node_id=None,
    )

    [sanitized] = output["details"]["queryGroups"]
    assert sanitized["queryTerms"] == ["safe term"]
    assert unsafe_text not in repr(output)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "note: Authorization: Bearer private-token",
        "debug secret=private-token",
        "api-key=private-token",
        "api key=private-token",
        "API Key: private-token",
        "X-API-Key: private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_public_stage_output_v2_drops_shared_unsafe_text_from_all_public_query_fields(unsafe_text: str) -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    group = _stage_query_group(lifecycle="executed")
    group["queryTerms"] = ["safe term", unsafe_text]
    group["executions"] = [
        {"sourceKind": "cts", "status": "completed"},
        {"sourceKind": unsafe_text, "status": "completed"},
    ]
    unsafe_keyword_group = _stage_query_group(lifecycle="executed")
    unsafe_keyword_group["queryInstanceId"] = "query-unsafe-keyword"
    unsafe_keyword_group["keywordQuery"] = unsafe_text

    output = sanitize_stage_output_payload(
        output_kind="runtime_public_feedback",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "feedback",
            "roundNo": 1,
            "sourceKind": "internal_referrals",
            "status": "completed",
            "counts": {},
            "details": {
                "queryGroups": [group, unsafe_keyword_group],
                "resumeQualityComment": unsafe_text,
                "reflectionSummary": unsafe_text,
                "suggestedActivateTerms": ["safe detail", unsafe_text],
            },
            "safeReasonCode": None,
        },
        stage="feedback",
        round_no=1,
        node_id="internal_referrals",
    )

    [sanitized] = output["details"]["queryGroups"]
    assert sanitized["queryInstanceId"] == "query-1"
    assert sanitized["termGroupKey"] == "group-1"
    assert sanitized["queryTerms"] == ["safe term"]
    assert sanitized["keywordQuery"] == "AI agent"
    assert sanitized["executions"] == [
        {
            "sourceKind": "cts",
            "status": "completed",
            "rawCandidateCount": 0,
            "uniqueCandidateCount": 0,
            "duplicateCandidateCount": 0,
        }
    ]
    assert output["sourceKind"] == "internal_referrals"
    assert output["details"] == {
        "queryGroups": [sanitized],
        "suggestedActivateTerms": ["safe detail"],
    }
    assert unsafe_text not in repr(output)


@pytest.mark.parametrize(
    ("stage", "lifecycle", "expected_group_count"),
    [
        ("round_query", "planned", 1),
        ("feedback", "executed", 1),
        ("round_query", "executed", 0),
        ("feedback", "planned", 0),
        ("source_result", "planned", 0),
    ],
)
def test_public_stage_output_v2_enforces_query_group_stage_lifecycle(
    stage: str,
    lifecycle: str,
    expected_group_count: int,
) -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    output = sanitize_stage_output_payload(
        output_kind=f"runtime_public_{stage}",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": stage,
            "roundNo": 1,
            "sourceKind": None,
            "status": "completed",
            "counts": {},
            "details": {"queryGroups": [_stage_query_group(lifecycle=lifecycle)]},
            "safeReasonCode": None,
        },
        stage=stage,
        round_no=1,
        node_id=None,
    )

    assert len(output.get("details", {}).get("queryGroups", [])) == expected_group_count


@pytest.mark.parametrize(
    ("reason_code", "expected_reason_code"),
    [
        ("blocked_backend_unavailable", "source_browser_backend_unavailable"),
        ("Bearer private-token", None),
        ("unknown_private_reason", None),
    ],
)
def test_public_stage_output_v2_maps_or_drops_top_level_safe_reason_code(
    reason_code: str,
    expected_reason_code: str | None,
) -> None:
    from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload

    output = sanitize_stage_output_payload(
        output_kind="runtime_public_source_result",
        schema_version="runtime-public-stage-output/v2",
        output={
            "schemaVersion": "runtime-public-stage-output/v2",
            "publicEventSchemaVersion": "runtime_public_event_v1",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": None,
            "status": "blocked",
            "counts": {},
            "details": {},
            "safeReasonCode": reason_code,
        },
        stage="source_result",
        round_no=1,
        node_id=None,
    )

    assert output.get("safeReasonCode") == expected_reason_code
    assert "Bearer private-token" not in repr(output)
    assert "unknown_private_reason" not in repr(output)


def test_stage_output_reason_mapping_matches_runtime_public_event_contract() -> None:
    from seektalent.runtime import public_events as runtime_public_events
    from seektalent_runtime_control import stage_outputs
    from seektalent_workbench_v2 import runtime_display

    assert stage_outputs._PUBLIC_SOURCE_REASON_CODES == runtime_public_events.PUBLIC_SOURCE_REASON_CODES
    assert stage_outputs._PUBLIC_REASON_MAP == runtime_public_events._PUBLIC_REASON_MAP
    assert runtime_display._PUBLIC_SOURCE_REASON_CODES == runtime_public_events.PUBLIC_SOURCE_REASON_CODES
    assert runtime_display._PUBLIC_REASON_MAP == runtime_public_events._PUBLIC_REASON_MAP

    reason_codes = [
        None,
        "unknown_private_reason",
        *sorted(runtime_public_events.PUBLIC_SOURCE_REASON_CODES),
        *sorted(runtime_public_events._PUBLIC_REASON_MAP),
    ]
    assert [stage_outputs._safe_reason_code(reason) for reason in reason_codes] == [
        runtime_public_events.public_source_reason_code(reason) for reason in reason_codes
    ]
    assert [runtime_display.safe_runtime_progress_reason_code(reason) for reason in reason_codes] == [
        runtime_public_events.public_source_reason_code(reason) for reason in reason_codes
    ]
    assert [
        runtime_display.normalize_runtime_progress_payload({"stage": "source_result", "safeReasonCode": reason}).get(
            "safeReasonCode"
        )
        for reason in reason_codes
    ] == [runtime_public_events.public_source_reason_code(reason) for reason in reason_codes]


def _stage_query_group(*, lifecycle: str) -> dict[str, object]:
    group: dict[str, object] = {
        "queryInstanceId": "query-1",
        "termGroupKey": "group-1",
        "queryRole": "exploit",
        "laneType": "exploit",
        "queryTerms": ["AI agent"],
        "keywordQuery": "AI agent",
        "lifecycle": lifecycle,
        "executionStatus": None,
        "attempted": False,
        "rawCandidateCount": 0,
        "uniqueCandidateCount": 0,
        "duplicateCandidateCount": 0,
        "executions": [],
    }
    if lifecycle == "executed":
        group.update(executionStatus="completed", attempted=True)
    return group


@pytest.mark.parametrize(
    ("stage", "round_no", "output_kind", "payload_stage", "payload_round_no", "expected_reason"),
    [
        ("feedback", None, "runtime_public_feedback", "feedback", None, "runtime_public_round_required"),
        (
            "finalization",
            1,
            "runtime_public_finalization",
            "finalization",
            1,
            "runtime_public_finalization_run_level_required",
        ),
        ("round_query", 1, "runtime_public_round_query", "feedback", 1, "runtime_stage_output_metadata_mismatch"),
    ],
)
def test_public_stage_output_rejects_metadata_and_round_hierarchy_mismatch(
    tmp_path: Path,
    stage: str,
    round_no: int | None,
    output_kind: str,
    payload_stage: str,
    payload_round_no: int | None,
    expected_reason: str,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_public_metadata", status="running"))

    with pytest.raises(RuntimeControlError) as exc_info:
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id=f"rtout_public_{stage}",
                runtime_run_id="runtime_run_public_metadata",
                stage=stage,
                round_no=round_no,
                output_kind=output_kind,
                schema_version="runtime-public-stage-output/v2",
                output={
                    "schemaVersion": "runtime-public-stage-output/v2",
                    "publicEventSchemaVersion": "runtime_public_event_v1",
                    "stage": payload_stage,
                    "roundNo": payload_round_no,
                    "sourceKind": None,
                    "status": "completed",
                    "counts": {},
                    "details": {},
                    "safeReasonCode": None,
                },
                source_event_id=None,
                source_checkpoint_id=None,
                artifact_ref_id=None,
                created_at="2026-06-22T00:00:02.000000Z",
            )
        )

    assert exc_info.value.reason_code == expected_reason


def test_stage_output_duplicate_key_conflicts_when_payload_hash_changes(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_stage_output_conflict", status="running"))
    first = RuntimeStageOutputInput(
        output_id="rtout_conflict_1",
        runtime_run_id="runtime_run_stage_output_conflict",
        stage="ranking",
        node_id="ranker",
        round_no=1,
        output_kind="candidate_scores",
        schema_version="stage-output/v1",
        output={"scores": [1]},
        created_at="2026-06-17T00:00:01.000000Z",
    )
    store.save_stage_output(first)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.save_stage_output(
            first.model_copy(
                update={
                    "output_id": "rtout_conflict_2",
                    "output": {"scores": [2]},
                    "created_at": "2026-06-17T00:00:02.000000Z",
                }
            )
        )

    assert exc_info.value.reason_code == "runtime_stage_output_conflict"


def test_stage_output_identity_rejects_empty_node_id_and_negative_round_at_model_and_db_boundaries(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.models import RuntimeStageOutputInput
    from seektalent_runtime_control.store import RuntimeControlStore

    with pytest.raises(ValidationError):
        RuntimeStageOutputInput(
            output_id="rtout_empty_node",
            runtime_run_id="runtime_run_stage_identity",
            stage="ranking",
            node_id="",
            round_no=None,
            output_kind="candidate_scores",
            schema_version="stage-output/v1",
            output={},
            created_at="2026-06-17T00:00:01.000000Z",
        )
    with pytest.raises(ValidationError):
        RuntimeStageOutputInput(
            output_id="rtout_negative_round",
            runtime_run_id="runtime_run_stage_identity",
            stage="ranking",
            node_id=None,
            round_no=-1,
            output_kind="candidate_scores",
            schema_version="stage-output/v1",
            output={},
            created_at="2026-06-17T00:00:01.000000Z",
        )

    db_path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(db_path).initialize()
    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO runtime_control_stage_outputs (
              output_id, runtime_run_id, stage, node_id, node_key, round_no, round_key,
              output_kind, schema_version, output_json, payload_hash, payload_size_bytes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rtout_bad_identity",
                "runtime_run_stage_identity",
                "ranking",
                "",
                "",
                -1,
                -1,
                "candidate_scores",
                "stage-output/v1",
                "{}",
                "hash",
                2,
                "2026-06-17T00:00:01.000000Z",
            ),
        )


def test_payload_guards_reject_oversized_event_and_artifact_large_stage_output(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeStageOutputInput

    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_payload_guards"))
    oversized = {"text": "x" * (16 * 1024)}

    with pytest.raises(RuntimeControlError) as event_exc:
        store.append_event(
            _event(
                event_id="rtevt_oversized",
                runtime_run_id="runtime_run_payload_guards",
                idempotency_key="event-key-oversized",
                payload=oversized,
            )
        )
    assert event_exc.value.reason_code == "runtime_event_payload_too_large"

    saved = store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtout_oversized",
            runtime_run_id="runtime_run_payload_guards",
            stage="sourcing",
            node_id=None,
            round_no=None,
            output_kind="candidate_batch",
            schema_version="stage-output/v1",
            output=oversized,
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at="2026-06-17T00:00:06.000000Z",
        )
    )
    assert saved.artifact_ref_id is not None
    assert saved.output == oversized


def test_claim_next_runnable_run_updates_run_lease_snapshot_and_claim_event(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_claim_1", status="queued"))
    store.create_run(_run(runtime_run_id="runtime_run_claim_2", run_intent_id="intent_2", status="running"))

    claim = store.claim_next_runnable_run(
        executor_id="executor_claim",
        claimed_at="2026-06-17T00:00:10.000000Z",
        lease_expires_at="2026-06-17T00:01:10.000000Z",
    )

    assert claim is not None
    assert claim.runtime_run.runtime_run_id == "runtime_run_claim_1"
    assert claim.runtime_run.status == "starting"
    assert claim.runtime_run.current_stage == "starting"
    assert claim.claim_reason == "queued"
    assert claim.lease.runtime_run_id == "runtime_run_claim_1"
    assert claim.lease.executor_id == "executor_claim"
    assert claim.lease.attempt_no == 1
    assert claim.claimed_event.event_type == "runtime_worker_claimed"
    assert claim.claimed_event.event_seq == 1
    assert claim.claimed_event.stage == "starting"
    assert claim.claimed_event.visibility == "developer"

    stored_run = store.get_run("runtime_run_claim_1")
    assert stored_run.status == "starting"
    assert stored_run.current_stage == "starting"
    assert stored_run.latest_event_seq == 1
    snapshot = store.get_snapshot(runtime_run_id="runtime_run_claim_1")
    assert snapshot is not None
    assert snapshot.status == "starting"
    assert snapshot.current_stage == "starting"
    assert snapshot.latest_event_seq == 1
    assert snapshot.snapshot["executorId"] == "executor_claim"

    store.create_run(_run(runtime_run_id="runtime_run_resume_claim", status="resume_requested"))
    resume_claim = store.claim_next_runnable_run(
        executor_id="executor_resume_claim",
        claimed_at="2026-06-17T00:00:20.000000Z",
        lease_expires_at="2026-06-17T00:01:20.000000Z",
        runtime_run_id="runtime_run_resume_claim",
    )
    assert resume_claim is not None
    assert resume_claim.claim_reason == "resume_requested"


def test_concurrent_claims_never_return_same_run_or_lease_attempt(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    store.create_run(_run(runtime_run_id="runtime_run_concurrent_claim", status="queued"))
    worker_count = 8
    barrier = Barrier(worker_count)

    def claim(index: int):
        barrier.wait()
        worker_store = _store_for_path(tmp_path)
        return worker_store.claim_next_runnable_run(
            executor_id=f"executor_{index}",
            claimed_at=f"2026-06-17T00:00:{index:02d}.000000Z",
            lease_expires_at=f"2026-06-17T00:01:{index:02d}.000000Z",
            runtime_run_id="runtime_run_concurrent_claim",
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        claims = list(executor.map(claim, range(worker_count)))

    successful = [claim for claim in claims if claim is not None]
    assert len(successful) == 1
    assert successful[0].runtime_run.runtime_run_id == "runtime_run_concurrent_claim"
    assert successful[0].lease.attempt_no == 1

    with sqlite3.connect(tmp_path / "runtime_control.sqlite3") as conn:
        lease_attempts = conn.execute(
            """
            SELECT runtime_run_id, attempt_no
            FROM runtime_control_executor_leases
            WHERE runtime_run_id = ?
            """,
            ("runtime_run_concurrent_claim",),
        ).fetchall()
    assert lease_attempts == [("runtime_run_concurrent_claim", 1)]


def test_expire_executor_leases_is_bounded_by_batch_size(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    for index in range(3):
        runtime_run_id = f"runtime_run_expired_lease_{index}"
        store.create_run(_run(runtime_run_id=runtime_run_id, status="running"))
        store.acquire_executor_lease(
            runtime_run_id=runtime_run_id,
            executor_id=f"executor_expired_{index}",
            acquired_at=f"2026-06-17T00:00:0{index}.000000Z",
            lease_expires_at=f"2026-06-17T00:01:0{index}.000000Z",
        )

    first_batch = store.expire_executor_leases(
        now="2026-06-17T00:02:00.000000Z",
        batch_size=2,
    )
    second_batch = store.expire_executor_leases(
        now="2026-06-17T00:02:01.000000Z",
        batch_size=2,
    )

    assert [lease.executor_id for lease in first_batch] == ["executor_expired_0", "executor_expired_1"]
    assert [lease.executor_id for lease in second_batch] == ["executor_expired_2"]


def _store_for_path(tmp_path: Path):
    from seektalent_runtime_control.store import RuntimeControlStore

    return RuntimeControlStore(tmp_path / "runtime_control.sqlite3")


def _initialized_store(tmp_path: Path):
    store = _store_for_path(tmp_path)
    store.initialize()
    return store


def _run(
    *,
    runtime_run_id: str,
    run_intent_id: str | None = None,
    start_idempotency_key: str | None = None,
    approved_requirement_revision_id: str = "reqapproved_contract",
    status: str = "queued",
    run_kind: str = "primary",
):
    from seektalent_runtime_control.models import RuntimeRunRecord

    return RuntimeRunRecord(
        runtime_run_id=runtime_run_id,
        run_intent_id=run_intent_id or f"intent_{runtime_run_id}",
        start_idempotency_key=start_idempotency_key or f"start_{runtime_run_id}",
        run_kind=run_kind,
        agent_conversation_id="agent_conv_contract",
        workbench_session_id=None,
        approved_requirement_revision_id=approved_requirement_revision_id,
        status=status,
        current_stage="queued",
        current_round=None,
        latest_checkpoint_id=None,
        latest_event_seq=0,
        source_ids=["source_contract"],
        stop_reason_code=None,
        created_at="2026-06-17T00:00:00.000000Z",
        updated_at="2026-06-17T00:00:00.000000Z",
        completed_at=None,
    )


def _event(
    *,
    event_id: str = "rtevt_contract",
    runtime_run_id: str,
    idempotency_key: str | None,
    visibility: str = "internal",
    payload_kind: str = "compact",
    payload: dict[str, object] | None = None,
    created_at: str = "2026-06-17T00:00:01.000000Z",
):
    from seektalent_runtime_control.models import RuntimeControlEventInput

    return RuntimeControlEventInput(
        event_id=event_id,
        runtime_run_id=runtime_run_id,
        event_type="runtime_progress",
        stage="runtime",
        round_no=None,
        source_id=None,
        status="completed",
        summary="runtime progress",
        payload=payload or {},
        schema_version="runtime-control-event/v1",
        visibility=visibility,
        idempotency_key=idempotency_key,
        payload_kind=payload_kind,
        workbench_event_global_seq=None,
        created_at=created_at,
    )


def _create_v1_runtime_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE runtime_control_runs (
              runtime_run_id TEXT PRIMARY KEY,
              agent_conversation_id TEXT,
              workbench_session_id TEXT,
              approved_requirement_revision_id TEXT UNIQUE,
              status TEXT NOT NULL,
              current_stage TEXT NOT NULL,
              current_round INTEGER,
              latest_checkpoint_id TEXT,
              latest_event_seq INTEGER NOT NULL DEFAULT 0,
              source_ids_json TEXT NOT NULL,
              stop_reason_code TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT
            );
            CREATE TABLE runtime_control_events (
              event_id TEXT PRIMARY KEY,
              runtime_run_id TEXT NOT NULL,
              event_seq INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              stage TEXT NOT NULL,
              round_no INTEGER,
              source_id TEXT,
              status TEXT NOT NULL,
              summary TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              workbench_event_global_seq INTEGER,
              created_at TEXT NOT NULL,
              UNIQUE(runtime_run_id, event_seq),
              UNIQUE(runtime_run_id, event_id)
            );
            INSERT INTO runtime_control_runs (
              runtime_run_id, agent_conversation_id, workbench_session_id,
              approved_requirement_revision_id, status, current_stage, current_round,
              latest_checkpoint_id, latest_event_seq, source_ids_json, stop_reason_code,
              created_at, updated_at, completed_at
            )
            VALUES (
              'runtime_run_v1', 'agent_conv_v1', NULL, 'reqapproved_v1',
              'queued', 'queued', NULL, NULL, 1, '["source_v1"]', NULL,
              '2026-06-16T00:00:00.000000Z', '2026-06-16T00:00:00.000000Z', NULL
            );
            INSERT INTO runtime_control_events (
              event_id, runtime_run_id, event_seq, event_type, stage, round_no,
              source_id, status, summary, payload_json, workbench_event_global_seq, created_at
            )
            VALUES (
              'rtevt_v1', 'runtime_run_v1', 1, 'runtime_progress', 'runtime',
              NULL, NULL, 'completed', 'v1 event', '{}', NULL,
              '2026-06-16T00:00:01.000000Z'
            );
            PRAGMA user_version = 1;
            """
        )
