from __future__ import annotations

import sqlite3
from pathlib import Path

from seektalent_ui.workbench_store import WorkbenchStore

from tests.test_workbench_runtime_owned_execution import _approved_dual_source_session


def test_attached_runtime_run_id_is_scoped_to_job_source_runs(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    refreshed = store.get_workbench_session(user=user, session_id=session.session_id)
    assert refreshed is not None
    liepin_run = next(source_run for source_run in refreshed.source_runs if source_run.source_kind == "liepin")
    store.block_source_run_for_start_probe(
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        warning_code="liepin_opencli_risk_page",
        warning_message="Risk verification required.",
    )
    created = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="runtime",
    )
    assert created is not None
    job, _was_created = created
    assert job.source_kinds == ("cts",)
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None

    store.attach_runtime_sourcing_job_runtime_run_id(context=context, runtime_run_id="run-cts-only")

    with sqlite3.connect(store.db_path) as conn:
        rows = {
            source_kind: runtime_run_id
            for source_kind, runtime_run_id in conn.execute(
                "SELECT source_kind, runtime_run_id FROM source_runs WHERE session_id = ?",
                (session.session_id,),
            ).fetchall()
        }
    assert rows == {"cts": "run-cts-only", "liepin": None}
