# Runtime-Owned Multi-Source Workbench Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Runtime-owned Workbench follow-up by making the strategy graph round-centric, source cards live and identity-safe, notes safe/deduped, and OpenCLI browser cleanup reliable.

**Architecture:** The current repository already has the Runtime-owned job, logical query dispatch, source-round dispatch, Runtime finalization, and lease heartbeat baseline. This follow-up adds a Runtime-layer public round event contract, projects cumulative source counts into Workbench, rebuilds the Svelte strategy graph around dynamic round rows, and hardens note/browser cleanup paths. Workbench remains a projection layer; Runtime emits public graph events without importing UI modules.

**Tech Stack:** Python 3.12, asyncio `TaskGroup`, SQLite Workbench store, existing Runtime retrieval/query planning, existing Liepin Pi/OpenCLI lane adapter, pytest, Svelte/Vitest.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-21-runtime-owned-multi-source-workbench-execution-design.md`

## Execution Notes

- Execute in a new worktree and branch.
- Use tests first for each behavior.
- This plan is a follow-up on top of the runtime-owned execution baseline. In this local workspace that baseline is present after `dc48d44 Implement runtime-owned multi-source Workbench`; public `origin/main` may lag behind, so verify the symbols below before building.
- Build starts at **Task 1** below. The previous runtime-owned execution tasks are represented only by the Current Baseline verification and must not be reimplemented unless that verification fails.
- Do not rewrite the full Runtime. This is a targeted graph/projection/QA hardening patch.
- Preserve CTS-only CLI and CTS-only Runtime behavior unless a test explicitly covers Workbench multi-source.
- Do not remove existing source-run rows. They remain UI/status projections.
- Do not use Workbench as a source orchestrator after this plan.
- Do not expose Pi/OpenCLI/DokoBot internals in Workbench public UI payloads.
- Do not implement source dispatch with broad `except Exception` coverage. Provider failures may degrade a source; Runtime invariant/programmer errors must fail the round.
- Do not disable CTS query-outcome scoring. Query-outcome scoring is an ephemeral round-control signal; final ranking still happens after merge.
- Do not derive Workbench final ranking from raw review item projection for new runtime-owned runs. Persist and read Runtime finalization order first.

## Current Baseline

The target worktree must contain these baseline pieces from the runtime-owned execution work:

- `src/seektalent_ui/workbench_store.py` has `runtime_sourcing_jobs`, `start_runtime_sourcing_job(...)`, `claim_next_runtime_sourcing_job(...)`, and `extend_runtime_sourcing_job_lease(...)`.
- `src/seektalent_ui/runtime_bridge.py` has `run_runtime_sourcing_job(...)`.
- `src/seektalent/runtime/logical_query_dispatch.py` has `LogicalQueryDispatch`.
- `src/seektalent/runtime/source_round_dispatch.py` has `SourceRoundDispatchRequest`, `SourceRoundAdapterResult`, and `dispatch_source_rounds(...)`.
- `tests/test_workbench_runtime_owned_execution.py` and `tests/test_runtime_multi_source_round_dispatch.py` already cover the core runtime-owned execution boundary.

Before implementing Task 1, verify the baseline with both symbol checks and behavior tests:

```bash
test -f src/seektalent/runtime/logical_query_dispatch.py
rg -n "class LogicalQueryDispatch" src/seektalent/runtime/logical_query_dispatch.py
rg -n "runtime_sourcing_jobs|WorkbenchRuntimeSourcingJob|start_runtime_sourcing_job|extend_runtime_sourcing_job_lease" src/seektalent_ui/workbench_store.py
rg -n "def run_runtime_sourcing_job" src/seektalent_ui/runtime_bridge.py
rg -n "dispatch_source_rounds" src/seektalent/runtime/orchestrator.py
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_runtime_multi_source_round_dispatch.py -q
```

Expected: all symbol checks match and tests pass. If any command fails, stop. The target branch does not have the runtime-owned baseline, so this follow-up plan cannot be executed directly; first merge or recreate the earlier runtime-owned execution baseline. Do not start Task 1 on a pre-baseline branch.

## File Map

Already present baseline, verify only:

- `src/seektalent_ui/workbench_store.py`
  - Owns `runtime_sourcing_jobs`, runtime job claim/heartbeat/reconcile, and finalization persistence.

- `src/seektalent_ui/runtime_bridge.py`
  - Owns `run_runtime_sourcing_job(...)`.

- `src/seektalent/runtime/logical_query_dispatch.py`
  - Owns immutable logical query dispatch metadata.

- `src/seektalent/runtime/source_round_dispatch.py`
  - Owns source-round fan-out contracts and provider-vs-invariant error boundaries.

- `src/seektalent/runtime/orchestrator.py`
  - Already routes Workbench multi-source execution through the mature round loop and source-round dispatch.

Modify for this follow-up:

- `src/seektalent/runtime/public_events.py`
  - New Runtime-layer module for `runtime_public_event_v1` graph envelopes, event-id/idempotency helpers, and safe source reason mapping.
  - This module must not import `seektalent_ui`.

- `src/seektalent/runtime/orchestrator.py`
  - Emit round-scoped public graph events at query, source dispatch/result, merge/dedupe, scoring, feedback, and finalization boundaries.
  - Write the same public events to a Runtime artifact such as `runtime/public_events.jsonl`.
  - Emit cumulative source counts for source-card projection.

- `src/seektalent_ui/runtime_bridge.py`
  - Reconcile Runtime public events from artifacts after `WorkflowRuntime.run(...)` completes.

- `src/seektalent_ui/job_runner.py`
  - Persist `runtime_public_event_v1` payloads from Runtime progress callbacks into Workbench events.

- `src/seektalent_ui/workbench_store.py`
  - Add append/reconcile helpers for already-sanitized Runtime public events if an existing generic append path cannot safely express them.
  - Add a database uniqueness invariant for `runtime_public_event_v1` rows keyed by `(tenant_id, workspace_id, user_id, session_id, eventId)`.
  - Add a store-level Runtime source-count projection for source cards.

- `src/seektalent_ui/workbench_routes.py`
  - Project source cards from the store-level Runtime cumulative source-count projection before falling back to stale source-run counts.

- `src/seektalent_ui/event_routes.py`
  - Apply the same public reason mapping and payload safety boundary to event responses and SSE payloads.

- `apps/web-svelte/src/lib/workbench/runStory.ts`
  - Build graph around Runtime-decided round modules: query bundle, selected source dispatch/results, merge/dedupe, scoring/top pool, feedback, and final Top 10.
  - Keep the legacy `cts-round-*` / `liepin-card-*` path only for old sessions without `runtime_public_event_v1` events.
  - Ignore `finalization` public events when building round rows so `roundNo: null` cannot become a fake round 0.

- `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
  - Render every public source reason code as business-facing text.

- `apps/web-svelte/src/lib/workbench/strategyGraphLayout.ts`
  - Layout round-centric graph rows with stage columns.
  - Let every Runtime round restart from the left on a new row.
  - Grow graph content height for many rounds instead of clamping later rows into overlap.
  - Degrade single-source rounds into a linear layout.

- `apps/web-svelte/src/lib/components/StrategyCanvas.svelte`
  - Remove fixed CTS/Liepin lane-band assumptions from the round-centric view.
  - Let the graph area scroll when round rows exceed the first viewport.

- `apps/web-svelte/src/lib/components/StrategyGraph.svelte`
  - Consume layout content dimensions if `strategyGraphLayout.ts` exposes them.

- `src/seektalent_ui/workbench_note_writer.py`
  - Reject hidden reasoning tags and browser/provider implementation terms.
  - Dedupe adjacent semantically identical business notes.
  - Stop swallowing async programmer errors as ordinary validation drops.

- `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Add conservative orphan-tab cleanup for tabs proven to be owned by the current SeekTalent OpenCLI session when no lease file exists.

- `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
  - Expose the orphan-tab cleanup action through the existing CLI wrapper.

- `scripts/start-dev-workbench.sh`
  - Call the stronger OpenCLI cleanup action during dev workbench shutdown.

Add or modify tests:

- `tests/test_workbench_api.py`
- `tests/test_workbench_semantic_guardrails.py`
- `tests/test_workbench_note_writer.py`
- `tests/test_pi_opencli_browser.py`
- `tests/test_pi_dokobot_local_setup.py`
- `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- `apps/web-svelte/src/lib/workbench/strategyGraphLayout.test.ts`
- `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`
- `apps/web-svelte/src/lib/workbench/finalCandidateCards.test.ts`

---

## Baseline Behaviors Already Covered

The earlier runtime-owned execution work is intentionally not repeated as executable plan steps. It is represented by the current baseline verification command above and by existing tests for:

- one runtime sourcing job per Workbench primary run
- runtime job claim, heartbeat, and reconciliation
- `LogicalQueryDispatch` metadata stability
- source-round dispatch provider-vs-invariant error boundaries
- multi-source dispatch inside the mature Runtime round loop
- merge-before-ranking and Runtime finalization-backed Top 10 persistence

If baseline verification fails, fix that regression directly before starting the follow-up tasks below. Do not re-create the old implementation from stale plan snippets.

## Task 1: Emit Round-Scoped Public Graph Events And Live Source Counts

**Files:**
- Create: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent_ui/job_runner.py`
- Modify: `src/seektalent_ui/workbench_store.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Test: `tests/test_workbench_api.py`
- Test: `tests/test_workbench_semantic_guardrails.py`

- [ ] **Step 1: Write failing public graph event tests**

Add to `tests/test_workbench_api.py`:

```python
def test_runtime_public_events_describe_round_source_merge_score_feedback(tmp_path: Path):
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    workbench_store = client.app.state.workbench_store
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_triage(client, session["sessionId"])

    _persist_runtime_round_graph_fixture(
        workbench_store,
        session_id=session["sessionId"],
        selected_sources=("cts", "liepin"),
        rounds=(
            {
                "round_no": 1,
                "cts_returned": 14,
                "liepin_returned": 8,
                "cts_cumulative_identities": 14,
                "liepin_cumulative_identities": 8,
                "merged_identities": 18,
                "top_pool": 10,
            },
            {
                "round_no": 2,
                "cts_returned": 9,
                "liepin_returned": 5,
                "cts_cumulative_identities": 17,
                "liepin_cumulative_identities": 11,
                "merged_identities": 11,
                "top_pool": 10,
            },
        ),
    )

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/events")
    assert response.status_code == 200
    events = response.json()["events"]
    graph_events = [event for event in events if event["eventName"].startswith("runtime_round_")]

    assert [event["payload"]["roundNo"] for event in graph_events if event["eventName"] == "runtime_round_query_ready"] == [1, 2]
    assert all(event["payload"]["runtimeRunId"].startswith("run-") for event in graph_events)
    assert all(event["payload"]["eventId"] for event in graph_events)
    assert [event["payload"]["eventSeq"] for event in graph_events] == sorted(event["payload"]["eventSeq"] for event in graph_events)
    assert {
        (event["payload"]["roundNo"], event["payload"]["sourceKind"])
        for event in graph_events
        if event["eventName"] == "runtime_round_source_dispatch"
    } == {(1, "cts"), (1, "liepin"), (2, "cts"), (2, "liepin")}
    assert {
        (event["payload"]["roundNo"], event["payload"]["sourceKind"])
        for event in graph_events
        if event["eventName"] == "runtime_round_source_result"
    } == {(1, "cts"), (1, "liepin"), (2, "cts"), (2, "liepin")}
    dispatch_seq = {
        (event["payload"]["roundNo"], event["payload"]["sourceKind"]): event["payload"]["eventSeq"]
        for event in graph_events
        if event["eventName"] == "runtime_round_source_dispatch"
    }
    result_seq = {
        (event["payload"]["roundNo"], event["payload"]["sourceKind"]): event["payload"]["eventSeq"]
        for event in graph_events
        if event["eventName"] == "runtime_round_source_result"
    }
    assert all(dispatch_seq[key] < result_seq[key] for key in result_seq)
    assert [event["payload"]["counts"]["roundIdentities"] for event in graph_events if event["eventName"] == "runtime_round_merge_completed"] == [18, 11]
    assert [event["payload"]["counts"]["topPoolCount"] for event in graph_events if event["eventName"] == "runtime_round_scoring_completed"] == [10, 10]


def test_source_cards_prefer_live_runtime_source_cumulative_counts(tmp_path: Path):
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    workbench_store = client.app.state.workbench_store
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_triage(client, session["sessionId"])
    _persist_runtime_round_graph_fixture(
        workbench_store,
        session_id=session["sessionId"],
        selected_sources=("cts", "liepin"),
        rounds=(
            {
                "round_no": 1,
                "cts_returned": 14,
                "liepin_returned": 8,
                "cts_cumulative_identities": 14,
                "liepin_cumulative_identities": 8,
                "merged_identities": 18,
                "top_pool": 10,
            },
            {
                "round_no": 2,
                "cts_returned": 9,
                "liepin_returned": 5,
                "cts_cumulative_identities": 17,
                "liepin_cumulative_identities": 11,
                "merged_identities": 23,
                "top_pool": 10,
            },
        ),
    )

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}")
    assert response.status_code == 200
    cards = {card["sourceKind"]: card for card in response.json()["sourceCards"]}
    assert cards["cts"]["uniqueCandidatesCount"] == 17
    assert cards["liepin"]["uniqueCandidatesCount"] == 11
```

Define `_persist_runtime_round_graph_fixture(...)` in the same test file:

```python
def _persist_runtime_round_graph_fixture(workbench_store, *, session_id: str, selected_sources: tuple[str, ...], rounds: tuple[dict[str, int], ...]) -> None:
    with workbench_store._connect() as conn:
        session_row = conn.execute(
            "SELECT tenant_id, workspace_id, user_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    assert session_row is not None
    runtime_run_id = f"run-{session_id}"
    event_seq = 0
    for round_data in rounds:
        round_no = int(round_data["round_no"])
        event_seq += 1
        workbench_store.append_runtime_public_event_by_ids(
            tenant_id=session_row["tenant_id"],
            workspace_id=session_row["workspace_id"],
            user_id=session_row["user_id"],
            session_id=session_id,
            event_name="runtime_round_query_ready",
            source_kind=None,
            payload={
                "schemaVersion": "runtime_public_event_v1",
                "runtimeRunId": runtime_run_id,
                "eventId": f"{runtime_run_id}:round:{round_no}:query",
                "eventSeq": event_seq,
                "stage": "round_query",
                "roundNo": round_no,
                "sourceKind": None,
                "sourcePlanId": None,
                "roundQueryBundleId": f"{runtime_run_id}:round:{round_no}:query_bundle",
                "status": "completed",
                "counts": {"requested": 20},
                "safeReasonCode": None,
                "createdAt": "2026-05-22T00:00:00Z",
            },
        )
        for source_kind in selected_sources:
            returned = int(round_data[f"{source_kind}_returned"])
            cumulative_identities = int(round_data[f"{source_kind}_cumulative_identities"])
            event_seq += 1
            workbench_store.append_runtime_public_event_by_ids(
                tenant_id=session_row["tenant_id"],
                workspace_id=session_row["workspace_id"],
                user_id=session_row["user_id"],
                session_id=session_id,
                event_name="runtime_round_source_dispatch",
                source_kind=source_kind,
                payload={
                    "schemaVersion": "runtime_public_event_v1",
                    "runtimeRunId": runtime_run_id,
                    "eventId": f"{runtime_run_id}:round:{round_no}:source_dispatch:{source_kind}",
                    "eventSeq": event_seq,
                    "stage": "source_dispatch",
                    "roundNo": round_no,
                    "sourceKind": source_kind,
                    "sourcePlanId": f"{runtime_run_id}:source:{source_kind}",
                    "roundQueryBundleId": f"{runtime_run_id}:round:{round_no}:query_bundle",
                    "status": "running",
                    "counts": {"requested": 10},
                    "safeReasonCode": None,
                    "createdAt": "2026-05-22T00:00:00Z",
                },
            )
            event_seq += 1
            workbench_store.append_runtime_public_event_by_ids(
                tenant_id=session_row["tenant_id"],
                workspace_id=session_row["workspace_id"],
                user_id=session_row["user_id"],
                session_id=session_id,
                event_name="runtime_round_source_result",
                source_kind=source_kind,
                payload={
                    "schemaVersion": "runtime_public_event_v1",
                    "runtimeRunId": runtime_run_id,
                    "eventId": f"{runtime_run_id}:round:{round_no}:source_result:{source_kind}",
                    "eventSeq": event_seq,
                    "stage": "source_result",
                    "roundNo": round_no,
                    "sourceKind": source_kind,
                    "sourcePlanId": f"{runtime_run_id}:source:{source_kind}",
                    "roundQueryBundleId": f"{runtime_run_id}:round:{round_no}:query_bundle",
                    "status": "completed",
                    "counts": {
                        "requested": 10,
                        "roundReturned": returned,
                        "roundIdentities": returned,
                        "sourceCumulativeReturned": cumulative_identities,
                        "sourceCumulativeIdentities": cumulative_identities,
                    },
                    "safeReasonCode": None,
                    "createdAt": "2026-05-22T00:00:00Z",
                },
            )
        event_seq += 1
        workbench_store.append_runtime_public_event_by_ids(
            tenant_id=session_row["tenant_id"],
            workspace_id=session_row["workspace_id"],
            user_id=session_row["user_id"],
            session_id=session_id,
            event_name="runtime_round_merge_completed",
            source_kind=None,
            payload={
                "schemaVersion": "runtime_public_event_v1",
                "runtimeRunId": runtime_run_id,
                "eventId": f"{runtime_run_id}:round:{round_no}:merge",
                "eventSeq": event_seq,
                "stage": "merge",
                "roundNo": round_no,
                "sourceKind": None,
                "sourcePlanId": None,
                "roundQueryBundleId": f"{runtime_run_id}:round:{round_no}:query_bundle",
                "status": "completed",
                "counts": {"roundIdentities": int(round_data["merged_identities"])},
                "safeReasonCode": None,
                "createdAt": "2026-05-22T00:00:00Z",
            },
        )
        event_seq += 1
        workbench_store.append_runtime_public_event_by_ids(
            tenant_id=session_row["tenant_id"],
            workspace_id=session_row["workspace_id"],
            user_id=session_row["user_id"],
            session_id=session_id,
            event_name="runtime_round_scoring_completed",
            source_kind=None,
            payload={
                "schemaVersion": "runtime_public_event_v1",
                "runtimeRunId": runtime_run_id,
                "eventId": f"{runtime_run_id}:round:{round_no}:scoring",
                "eventSeq": event_seq,
                "stage": "scoring",
                "roundNo": round_no,
                "sourceKind": None,
                "sourcePlanId": None,
                "roundQueryBundleId": f"{runtime_run_id}:round:{round_no}:query_bundle",
                "status": "completed",
                "counts": {"topPoolCount": int(round_data["top_pool"])},
                "safeReasonCode": None,
                "createdAt": "2026-05-22T00:00:00Z",
            },
        )
        event_seq += 1
        workbench_store.append_runtime_public_event_by_ids(
            tenant_id=session_row["tenant_id"],
            workspace_id=session_row["workspace_id"],
            user_id=session_row["user_id"],
            session_id=session_id,
            event_name="runtime_round_feedback_completed",
            source_kind=None,
            payload={
                "schemaVersion": "runtime_public_event_v1",
                "runtimeRunId": runtime_run_id,
                "eventId": f"{runtime_run_id}:round:{round_no}:feedback",
                "eventSeq": event_seq,
                "stage": "feedback",
                "roundNo": round_no,
                "sourceKind": None,
                "sourcePlanId": None,
                "roundQueryBundleId": f"{runtime_run_id}:round:{round_no}:query_bundle",
                "status": "completed",
                "counts": {},
                "safeReasonCode": None,
                "createdAt": "2026-05-22T00:00:00Z",
            },
        )
```

Add paired store-helper regressions in the same file:

- `append_runtime_public_event_by_ids(...)` rejects a payload whose `stage` maps to a different `event_name`.
- `append_runtime_public_event_by_ids(...)` rejects an unknown `stage` instead of persisting it as a generic runtime public event.
- Appending the same `eventId` twice stores only one Workbench event.
- Direct duplicate insertion of the same `runtime_public_event_v1` `eventId` fails at the database level, not only through Python helper logic.
- Reconciliation from `runtime/public_events.jsonl` backfills an event that was not delivered through the progress callback.
- Every selected source persists `runtime_round_source_dispatch` before `runtime_round_source_result`; dispatch payloads must not include provider internals, query text, fingerprints, local paths, or browser command names.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_workbench_api.py::test_runtime_public_events_describe_round_source_merge_score_feedback tests/test_workbench_api.py::test_source_cards_prefer_live_runtime_source_cumulative_counts -q
```

Expected: fail because `append_runtime_public_event_by_ids(...)`, the Runtime public event contract, artifact reconciliation, and cumulative source-card count projection are not implemented.

- [ ] **Step 3: Add the public event sanitizer**

Create `src/seektalent/runtime/public_events.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, TypedDict

PublicRuntimeStage = Literal[
    "round_query",
    "source_dispatch",
    "source_result",
    "merge",
    "scoring",
    "feedback",
    "finalization",
]
PublicRuntimeStatus = Literal["pending", "running", "completed", "blocked", "degraded", "failed"]


class RuntimePublicEvent(TypedDict):
    schemaVersion: str
    runtimeRunId: str
    eventId: str
    eventSeq: int
    stage: PublicRuntimeStage
    roundNo: int | None
    sourceKind: str | None
    sourcePlanId: str | None
    roundQueryBundleId: str | None
    status: PublicRuntimeStatus
    counts: dict[str, int]
    safeReasonCode: str | None
    createdAt: str


_ALLOWED_COUNT_KEYS = {
    "requested",
    "roundReturned",
    "scanned",
    "roundIdentities",
    "topPoolCount",
    "sourceCumulativeReturned",
    "sourceCumulativeIdentities",
}
_ALLOWED_SOURCE_KINDS = {"cts", "liepin"}
_ALLOWED_REASON_CODES = {
    "source_account_mismatch",
    "source_browser_extension_disconnected",
    "source_browser_policy_blocked",
    "source_browser_timeout",
    "source_browser_backend_unavailable",
    "source_budget_exhausted",
    "source_login_required",
    "source_risk_or_verification_required",
    "source_provider_failed",
    "source_partial",
    "source_unknown",
}
_INTERNAL_TO_PUBLIC_REASON = {
    "blocked_backend_unavailable": "source_browser_backend_unavailable",
    "blocked_budget_exhausted": "source_budget_exhausted",
    "blocked_by_risk_control": "source_risk_or_verification_required",
    "blocked_login_required": "source_login_required",
    "blocked_permission_required": "source_risk_or_verification_required",
    "failed_internal_error": "source_provider_failed",
    "failed_provider_error": "source_provider_failed",
    "liepin_browser_account_mismatch": "source_account_mismatch",
    "liepin_browser_login_required": "source_login_required",
    "liepin_browser_probe_unavailable": "source_browser_backend_unavailable",
    "liepin_opencli_budget_exhausted": "source_budget_exhausted",
    "liepin_opencli_command_missing": "source_browser_backend_unavailable",
    "liepin_opencli_extension_disconnected": "source_browser_extension_disconnected",
    "liepin_opencli_forbidden_command": "source_browser_policy_blocked",
    "liepin_opencli_identity_intercept": "source_risk_or_verification_required",
    "liepin_opencli_login_required": "source_login_required",
    "liepin_opencli_malformed_state": "source_browser_backend_unavailable",
    "liepin_opencli_risk_page": "source_risk_or_verification_required",
    "liepin_opencli_status_unavailable": "source_browser_backend_unavailable",
    "liepin_opencli_timeout": "source_browser_timeout",
    "liepin_opencli_window_policy_blocked": "source_browser_policy_blocked",
    "liepin_pi_command_missing": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_mcp_command_missing": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_mcp_tool_names_missing": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_tool_unobserved": "source_browser_backend_unavailable",
    "liepin_pi_mcp_config_invalid": "source_browser_backend_unavailable",
    "liepin_pi_mcp_config_missing": "source_browser_backend_unavailable",
    "liepin_pi_mcp_config_not_project_local": "source_browser_backend_unavailable",
    "login_required": "source_login_required",
    "partial_timeout": "source_partial",
    "runtime_failed": "source_provider_failed",
}


def runtime_public_event(
    *,
    runtime_run_id: str,
    event_seq: int,
    stage: PublicRuntimeStage,
    round_no: int | None,
    source_kind: str | None,
    status: PublicRuntimeStatus,
    counts: dict[str, int] | None = None,
    reason_code: str | None = None,
    source_plan_id: str | None = None,
    round_query_bundle_id: str | None = None,
    created_at: str | None = None,
) -> RuntimePublicEvent:
    if source_kind is not None and source_kind not in _ALLOWED_SOURCE_KINDS:
        raise ValueError(f"Unsupported public source kind {source_kind!r}")
    safe_counts = {
        key: int(value)
        for key, value in (counts or {}).items()
        if key in _ALLOWED_COUNT_KEYS and isinstance(value, int) and value >= 0
    }
    event_id = runtime_public_event_id(
        runtime_run_id=runtime_run_id,
        stage=stage,
        round_no=round_no,
        source_kind=source_kind,
        event_seq=event_seq,
    )
    return {
        "schemaVersion": "runtime_public_event_v1",
        "runtimeRunId": runtime_run_id,
        "eventId": event_id,
        "eventSeq": event_seq,
        "stage": stage,
        "roundNo": round_no,
        "sourceKind": source_kind,
        "sourcePlanId": source_plan_id,
        "roundQueryBundleId": round_query_bundle_id,
        "status": status,
        "counts": safe_counts,
        "safeReasonCode": public_source_reason_code(reason_code),
        "createdAt": created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def runtime_public_event_id(
    *,
    runtime_run_id: str,
    stage: str,
    round_no: int | None,
    source_kind: str | None,
    event_seq: int,
) -> str:
    round_part = "final" if round_no is None else f"round:{round_no}"
    source_part = source_kind or "shared"
    return f"{runtime_run_id}:{round_part}:{stage}:{source_part}:{event_seq}"


def public_source_reason_code(reason_code: str | None) -> str | None:
    if not reason_code:
        return None
    mapped = _INTERNAL_TO_PUBLIC_REASON.get(reason_code, reason_code)
    return mapped if mapped in _ALLOWED_REASON_CODES else "source_unknown"


_RUNTIME_PUBLIC_EVENT_NAMES = {
    "round_query": "runtime_round_query_ready",
    "source_dispatch": "runtime_round_source_dispatch",
    "source_result": "runtime_round_source_result",
    "merge": "runtime_round_merge_completed",
    "scoring": "runtime_round_scoring_completed",
    "feedback": "runtime_round_feedback_completed",
    "finalization": "runtime_finalization_completed",
}


def runtime_public_event_name(stage: str) -> str:
    try:
        return _RUNTIME_PUBLIC_EVENT_NAMES[stage]
    except KeyError as exc:
        raise ValueError(f"Unsupported runtime public event stage {stage!r}") from exc
```

Use `public_source_reason_code(...)` as the shared public boundary for Workbench serializers too. `src/seektalent_ui/workbench_routes.py`, `src/seektalent_ui/event_routes.py`, source-card serialization, runtime source state serialization, and final-top10 evidence serialization must call the same mapping before returning public payloads. Internal audit rows and protected artifacts may keep internal reason codes.

- [ ] **Step 4: Add store helper and cumulative source-card projection**

In `src/seektalent_ui/workbench_store.py`, first add a database invariant beside the existing `session_events` idempotency indexes:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_runtime_public_event_idempotency
ON session_events(tenant_id, workspace_id, user_id, session_id, idempotency_key)
WHERE schema_version = 'runtime_public_event_v1' AND idempotency_key IS NOT NULL;
```

Then add a store helper that is idempotent even when called once from live progress and again from artifact reconciliation:

```python
def append_runtime_public_event_by_ids(
    self,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    event_name: str,
    source_kind: str | None,
    payload: Mapping[str, object],
) -> WorkbenchEvent:
    if payload.get("schemaVersion") != "runtime_public_event_v1":
        raise ValueError("Runtime public event payload must use runtime_public_event_v1")
    if source_kind is not None and payload.get("sourceKind") != source_kind:
        raise ValueError("Runtime public event source kind mismatch")
    # Unknown stages must fail here through runtime_public_event_name(...). Do not
    # persist them under a generic event name; the public graph contract is closed.
    expected_event_name = runtime_public_event_name(str(payload.get("stage") or ""))
    if event_name != expected_event_name:
        raise ValueError("Runtime public event name/stage mismatch")
    event_id = str(payload.get("eventId") or "")
    if not event_id:
        raise ValueError("Runtime public event requires eventId")
    safe_event_id = _bounded_text(event_id, 160)
    if not safe_event_id:
        raise ValueError("Runtime public event requires eventId")
    self._initialize()
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session_row = conn.execute(
            """
            SELECT *
            FROM sessions
            WHERE session_id = ? AND tenant_id = ? AND workspace_id = ? AND user_id = ?
            """,
            (session_id, tenant_id, workspace_id, user_id),
        ).fetchone()
        if session_row is None:
            raise ValueError("Workbench session does not exist.")
        existing = conn.execute(
            """
            SELECT *
            FROM session_events
            WHERE tenant_id = ?
              AND workspace_id = ?
              AND user_id = ?
              AND session_id = ?
              AND schema_version = 'runtime_public_event_v1'
              AND idempotency_key = ?
            """,
            (tenant_id, workspace_id, user_id, session_id, safe_event_id),
        ).fetchone()
        if existing is not None:
            return _event_from_row(existing)
        try:
            return _append_workbench_event_conn(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=source_kind,
                event_name=event_name,
                schema_version="runtime_public_event_v1",
                idempotency_key=safe_event_id,
                payload=dict(payload),
            )
        except sqlite3.IntegrityError:
            existing = conn.execute(
                """
                SELECT *
                FROM session_events
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                  AND schema_version = 'runtime_public_event_v1'
                  AND idempotency_key = ?
                """,
                (tenant_id, workspace_id, user_id, session_id, safe_event_id),
            ).fetchone()
            if existing is None:
                raise
            return _event_from_row(existing)
```

In `src/seektalent_ui/workbench_store.py`, add a store-level projection for live source-card counts. Do not scan events ad hoc inside route serializers:

```python
@dataclass(frozen=True)
class RuntimeSourceCountProjection:
    source_kind: str
    round_no: int
    event_seq: int
    status: str | None
    reason_code: str | None
    cards_scanned_count: int | None
    unique_candidates_count: int | None


def latest_runtime_source_count_projection(
    self,
    *,
    user: WorkbenchUser,
    session_id: str,
) -> dict[str, RuntimeSourceCountProjection]:
    events = self.list_recent_session_events(user=user, session_id=session_id, event_prefix="runtime_round_source_result", limit=200)
    latest_status: dict[str, RuntimeSourceCountProjection] = {}
    latest_counts: dict[str, RuntimeSourceCountProjection] = {}
    for event in events:
        if event.event_name != "runtime_round_source_result":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        source_kind = payload.get("sourceKind")
        payload_counts = payload.get("counts")
        if source_kind not in {"cts", "liepin"}:
            continue
        round_no = int(payload.get("roundNo") or 0)
        event_seq = int(payload.get("eventSeq") or event.global_seq)
        source_key = str(source_kind)
        status_projection = RuntimeSourceCountProjection(
            source_kind=source_key,
            round_no=round_no,
            event_seq=event_seq,
            status=str(payload.get("status") or "") or None,
            reason_code=public_source_reason_code(str(payload.get("safeReasonCode") or "")) if payload.get("safeReasonCode") else None,
            cards_scanned_count=None,
            unique_candidates_count=None,
        )
        current_status = latest_status.get(source_key)
        if current_status is None or (round_no, event_seq) >= (current_status.round_no, current_status.event_seq):
            latest_status[source_key] = status_projection
        if not isinstance(payload_counts, dict) or (
            "sourceCumulativeReturned" not in payload_counts
            and "sourceCumulativeIdentities" not in payload_counts
        ):
            continue
        if isinstance(payload_counts, dict):
            cards = int(payload_counts["sourceCumulativeReturned"]) if "sourceCumulativeReturned" in payload_counts else None
            unique = int(payload_counts["sourceCumulativeIdentities"]) if "sourceCumulativeIdentities" in payload_counts else None
        count_projection = RuntimeSourceCountProjection(
            source_kind=source_key,
            round_no=round_no,
            event_seq=event_seq,
            status=None,
            reason_code=None,
            cards_scanned_count=cards,
            unique_candidates_count=unique,
        )
        current_counts = latest_counts.get(source_key)
        if current_counts is None or (round_no, event_seq) >= (current_counts.round_no, current_counts.event_seq):
            latest_counts[source_key] = count_projection
    projections: dict[str, RuntimeSourceCountProjection] = {}
    for source_key in set(latest_status) | set(latest_counts):
        status = latest_status.get(source_key)
        counts = latest_counts.get(source_key)
        projections[source_key] = RuntimeSourceCountProjection(
            source_kind=source_key,
            round_no=max(status.round_no if status else 0, counts.round_no if counts else 0),
            event_seq=max(status.event_seq if status else 0, counts.event_seq if counts else 0),
            status=status.status if status else None,
            reason_code=status.reason_code if status else None,
            cards_scanned_count=counts.cards_scanned_count if counts else None,
            unique_candidates_count=counts.unique_candidates_count if counts else None,
        )
    return projections
```

When serializing `sourceCards` in `src/seektalent_ui/workbench_routes.py`, consume `latest_runtime_source_count_projection(...)` before falling back to `source_runs.cards_scanned_count` and `source_runs.unique_candidates_count`. A later blocked/failed event with no cumulative counts may update status/reason, but it must not overwrite prior cumulative counts with zero.

- [ ] **Step 5: Emit events from runtime progress callback**

In `src/seektalent_ui/job_runner.py`, update `_record_runtime_sourcing_progress(...)` so it passes through payloads already shaped as `runtime_public_event_v1`. This is the live-update path, not the only persistence path:

```python
if isinstance(event.payload, dict) and event.payload.get("schemaVersion") == "runtime_public_event_v1":
    payload = dict(event.payload)
    self.store.append_runtime_public_event_by_ids(
        tenant_id=context.job.tenant_id,
        workspace_id=context.job.workspace_id,
        user_id=context.job.user_id,
        session_id=context.job.session_id,
        event_name=runtime_public_event_name(str(payload["stage"])),
        source_kind=str(payload["sourceKind"]) if payload.get("sourceKind") else None,
        payload=payload,
    )
    return
```

Use `runtime_public_event_name(...)` from `seektalent.runtime.public_events`; do not duplicate the stage-to-event-name mapping in `job_runner.py`.

- [ ] **Step 6: Persist and reconcile public events from Runtime artifacts**

In `src/seektalent/runtime/orchestrator.py`, write every `runtime_public_event_v1` payload to a durable run artifact such as `runtime/public_events.jsonl` at the same time it is emitted through progress. Keep this artifact public-safe and separate from raw provider artifacts.

In `src/seektalent_ui/runtime_bridge.py`, after `artifacts = run_method(**run_kwargs)` and before or during `store.complete_runtime_sourcing_job_with_artifacts(...)`, call a store reconciliation helper:

```python
store.reconcile_runtime_public_events_from_artifacts(
    context=context,
    artifacts=artifacts,
)
```

The reconciliation helper must read `artifacts.run_dir / "runtime" / "public_events.jsonl"` if it exists and call `append_runtime_public_event_by_ids(...)` for each event. It must be idempotent by `eventId` so events already written through progress callback are not duplicated. Add a regression where the progress callback drops one event but completion reconciliation backfills it.

- [ ] **Step 7: Emit runtime public events from Runtime**

In `src/seektalent/runtime/orchestrator.py`, import `count` from `itertools` and `RuntimePublicEvent` / `runtime_public_event` from `seektalent.runtime.public_events`. Create one run-scoped sequence counter and one tiny helper so event emission, progress callback delivery, and JSONL persistence cannot drift:

```python
public_event_seq = count(1)


def next_public_event_seq() -> int:
    return next(public_event_seq)


def emit_runtime_public_event(
    *,
    event_type: str,
    message: str,
    public_event: RuntimePublicEvent,
) -> None:
    tracer.append_jsonl("runtime/public_events.jsonl", public_event)
    self._emit_progress(
        progress_callback,
        event_type,
        message,
        round_no=public_event.get("roundNo") if isinstance(public_event.get("roundNo"), int) else None,
        payload=dict(public_event),
    )
```

The helper must call the existing `_emit_progress(callback, event_type, message, *, round_no, payload)` signature. Do not call `_emit_progress(...)` with the public event as a positional argument.

Emit these progress events at the source-round fan-out/fan-in boundaries:

```python
emit_runtime_public_event(
    event_type="round_query_ready",
    message=f"第 {round_no} 轮查询包已生成。",
    public_event=runtime_public_event(
        runtime_run_id=tracer.run_id,
        event_seq=next_public_event_seq(),
        stage="round_query",
        round_no=round_no,
        source_kind=None,
        status="completed",
        counts={"requested": total_requested},
        round_query_bundle_id=f"{tracer.run_id}:round:{round_no}:query_bundle",
    ),
)
```

Use the same helper for source dispatch/result, merge/dedupe, scoring, feedback, and finalization. Emit `stage="source_dispatch"` after Runtime has frozen the logical query dispatch bundle and immediately before each selected source adapter is started. Dispatch payloads may include `requested` counts and source ids, but must not include query text, fingerprints, provider command names, local paths, or raw adapter context.

Source result events must include cumulative counts from Runtime state:

```python
emit_runtime_public_event(
    event_type="source_result",
    message=f"第 {round_no} 轮 {source} 检索完成。",
    public_event=runtime_public_event(
        runtime_run_id=tracer.run_id,
        event_seq=next_public_event_seq(),
        stage="source_result",
        round_no=round_no,
        source_kind=source,
        status=status,
        counts={
            "roundReturned": round_returned_count,
            "roundIdentities": round_identity_count,
            "sourceCumulativeReturned": cumulative_returned_count,
            "sourceCumulativeIdentities": cumulative_identity_count,
        },
        reason_code=reason_code,
        source_plan_id=source_plan_id,
        round_query_bundle_id=f"{tracer.run_id}:round:{round_no}:query_bundle",
    ),
)
```

For finalization, use `stage="finalization"` and `round_no=None`; the frontend must treat that event as finalization metadata, not as another round module.

Do not include query text, fingerprints, raw resumes, local paths, or provider command names in the public event payload.

- [ ] **Step 8: Run focused tests**

```bash
uv run pytest tests/test_workbench_api.py::test_runtime_public_events_describe_round_source_merge_score_feedback tests/test_workbench_api.py::test_source_cards_prefer_live_runtime_source_cumulative_counts tests/test_workbench_semantic_guardrails.py -q
```

Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/runtime/public_events.py src/seektalent/runtime/orchestrator.py src/seektalent_ui/runtime_bridge.py src/seektalent_ui/job_runner.py src/seektalent_ui/workbench_store.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/event_routes.py tests/test_workbench_api.py tests/test_workbench_semantic_guardrails.py
git commit -m "feat: expose runtime round graph events"
```

---

## Task 2: Build Round-Centric Workbench Graph Story

**Files:**
- Modify: `apps/web-svelte/src/lib/workbench/runStory.ts`
- Modify: `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- Modify: `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
- Modify: `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`
- Test: `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- Test: `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`

- [ ] **Step 1: Add graph story regressions**

Add to `apps/web-svelte/src/lib/workbench/runStory.test.ts`:

```ts
it('renders dual-source runtime rounds as fan-out fan-in modules', () => {
	const story = buildRunStory({
		session: sessionFixture({
			sourceCards: [
				sourceCardFixture({ sourceKind: 'cts', status: 'completed' }),
				sourceCardFixture({ sourceKind: 'liepin', status: 'completed' })
			]
		}),
		events: [
			runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
			runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', { roundReturned: 14 }),
			runtimeRoundEvent('runtime_round_source_result', 1, 'liepin', 'completed', { roundReturned: 8 }),
			runtimeRoundEvent('runtime_round_merge_completed', 1, null, 'completed', { roundIdentities: 18 }),
			runtimeRoundEvent('runtime_round_scoring_completed', 1, null, 'completed', { topPoolCount: 10 }),
			runtimeRoundEvent('runtime_round_feedback_completed', 1, null, 'completed'),
			runtimeRoundEvent('runtime_round_query_ready', 2, null, 'completed'),
			runtimeRoundEvent('runtime_round_source_result', 2, 'cts', 'completed', { roundReturned: 9 }),
			runtimeRoundEvent('runtime_round_source_result', 2, 'liepin', 'completed', { roundReturned: 5 }),
			runtimeRoundEvent('runtime_round_merge_completed', 2, null, 'completed', { roundIdentities: 11 }),
			runtimeRoundEvent('runtime_round_scoring_completed', 2, null, 'completed', { topPoolCount: 10 })
		],
		finalTopCandidates: Array.from({ length: 10 }, (_, index) =>
			finalTopCandidateFixture({ rank: index + 1 })
		)
	});

	expect(story.graphNodes.map((node) => node.id)).toEqual(
		expect.arrayContaining([
			'round-1-query',
			'round-1-source-cts',
			'round-1-source-liepin',
			'round-1-merge',
			'round-1-score',
			'round-1-feedback',
			'round-2-query',
			'round-2-source-cts',
			'round-2-source-liepin',
			'round-2-merge',
			'round-2-score',
			'final-shortlist'
		])
	);
	expect(story.graphEdges).toContainEqual(expect.objectContaining({ from: 'round-1-source-cts', to: 'round-1-merge' }));
	expect(story.graphEdges).toContainEqual(expect.objectContaining({ from: 'round-1-source-liepin', to: 'round-1-merge' }));
	expect(story.graphEdges).toContainEqual(expect.objectContaining({ from: 'round-1-feedback', to: 'round-2-query' }));
});

it('renders a selected single-source run without an unselected source or fake cross-source merge', () => {
	const story = buildRunStory({
		session: sessionFixture({
			sourceCards: [sourceCardFixture({ sourceKind: 'cts', status: 'completed' })]
		}),
		events: [
			runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
			runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', { roundReturned: 12 }),
			runtimeRoundEvent('runtime_round_scoring_completed', 1, null, 'completed', { topPoolCount: 10 })
		]
	});

	expect(story.graphNodes.some((node) => node.id === 'round-1-source-cts')).toBe(true);
	expect(story.graphNodes.some((node) => node.id === 'round-1-source-liepin')).toBe(false);
	expect(story.graphNodes.some((node) => node.id === 'round-1-merge')).toBe(false);
	expect(story.graphEdges).toContainEqual(expect.objectContaining({ from: 'round-1-source-cts', to: 'round-1-score' }));
});

it('keeps selected blocked Liepin visible while CTS continues into merge', () => {
	const story = buildRunStory({
		session: sessionFixture({
			sourceCards: [
				sourceCardFixture({ sourceKind: 'cts', status: 'running' }),
				sourceCardFixture({ sourceKind: 'liepin', status: 'blocked' })
			]
		}),
		events: [
			runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
			runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', { roundReturned: 11 }),
			runtimeRoundEvent('runtime_round_source_result', 1, 'liepin', 'blocked', { roundReturned: 0 }, 'source_login_required'),
			runtimeRoundEvent('runtime_round_merge_completed', 1, null, 'degraded', { roundIdentities: 11 })
		]
	});

	const liepinNode = story.graphNodes.find((node) => node.id === 'round-1-source-liepin');
	expect(liepinNode?.tone).toBe('amber');
	expect(liepinNode?.detail).toContain('登录');
	expect(story.graphEdges).toContainEqual(expect.objectContaining({ from: 'round-1-source-cts', to: 'round-1-merge' }));
});

it('does not render runtime finalization as a round-zero module', () => {
	const story = buildRunStory({
		session: sessionFixture({
			sourceCards: [sourceCardFixture({ sourceKind: 'cts', status: 'completed' })]
		}),
		events: [
			runtimeRoundEvent('runtime_round_query_ready', 1, null, 'completed'),
			runtimeRoundEvent('runtime_round_source_result', 1, 'cts', 'completed', { roundReturned: 12 }),
			runtimeRoundEvent('runtime_round_scoring_completed', 1, null, 'completed', { topPoolCount: 10 }),
			runtimeFinalizationEvent()
		],
		finalTopCandidates: Array.from({ length: 10 }, (_, index) =>
			finalTopCandidateFixture({ rank: index + 1 })
		)
	});

	expect(story.graphNodes.some((node) => node.id.startsWith('round-0-'))).toBe(false);
	expect(story.graphNodes.some((node) => node.id === 'final-shortlist')).toBe(true);
});
```

Add the helper in the same test file:

```ts
function runtimeRoundEvent(
	eventName: string,
	roundNo: number,
	sourceKind: 'cts' | 'liepin' | null,
	status: 'pending' | 'running' | 'completed' | 'blocked' | 'degraded' | 'failed',
	counts: Record<string, number> = {},
	safeReasonCode: string | null = null
) {
	const stageByEventName: Record<string, string> = {
		runtime_round_query_ready: 'round_query',
		runtime_round_source_dispatch: 'source_dispatch',
		runtime_round_source_result: 'source_result',
		runtime_round_merge_completed: 'merge',
		runtime_round_scoring_completed: 'scoring',
		runtime_round_feedback_completed: 'feedback'
	};
	return workbenchEventFixture({
		eventName,
		sourceKind,
		payload: {
			schemaVersion: 'runtime_public_event_v1',
			runtimeRunId: 'run-story-1',
			eventId: `run-story-1:${eventName}:${roundNo}:${sourceKind ?? 'shared'}`,
			eventSeq: roundNo * 10 + Object.keys(stageByEventName).indexOf(eventName),
			stage: stageByEventName[eventName],
			roundNo,
			sourceKind,
			sourcePlanId: sourceKind ? `run-story-1:source:${sourceKind}` : null,
			roundQueryBundleId: `run-story-1:round:${roundNo}:query_bundle`,
			status,
			counts,
			safeReasonCode,
			createdAt: '2026-05-22T00:00:00Z'
		}
	});
}

function runtimeFinalizationEvent() {
	return workbenchEventFixture({
		eventName: 'runtime_finalization_completed',
		sourceKind: null,
		payload: {
			schemaVersion: 'runtime_public_event_v1',
			runtimeRunId: 'run-story-1',
			eventId: 'run-story-1:final:finalization:shared:99',
			eventSeq: 99,
			stage: 'finalization',
			roundNo: null,
			sourceKind: null,
			sourcePlanId: null,
			roundQueryBundleId: null,
			status: 'completed',
			counts: { topPoolCount: 10 },
			safeReasonCode: null,
			createdAt: '2026-05-22T00:00:00Z'
		}
	});
}
```

In `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`, add coverage for every public source reason code:

```ts
it('maps public source reason codes to business-facing labels', () => {
	const publicReasons = [
		'source_login_required',
		'source_account_mismatch',
		'source_browser_timeout',
		'source_browser_backend_unavailable',
		'source_browser_extension_disconnected',
		'source_browser_policy_blocked',
		'source_risk_or_verification_required',
		'source_budget_exhausted',
		'source_provider_failed',
		'source_partial',
		'source_unknown'
	];

	for (const reason of publicReasons) {
		const label = sourceReasonLabel(reason) ?? '';
		expect(label.length).toBeGreaterThan(0);
		expect(label).not.toMatch(/OpenCLI|DokoBot|MCP|pi_agent|cookie|authorization/i);
		expect(label).not.toBe('检索源需要处理。');
	}
	expect(sourceReasonLabel('source_login_required')).toContain('登录');
});
```

In `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`, map each public reason code to a business-facing label. Keep old internal-code labels only as legacy fallback for older persisted sessions.

- [ ] **Step 2: Run tests to verify they fail**

```bash
bun --cwd apps/web-svelte test src/lib/workbench/runStory.test.ts src/lib/workbench/sourceDisplay.test.ts
```

Expected: the new tests fail because `buildRunStory(...)` still builds legacy `cts-round-*` and `liepin-card-*` nodes, and `sourceReasonLabel(...)` does not yet cover the public reason taxonomy.

- [ ] **Step 3: Add typed round event parsing**

In `apps/web-svelte/src/lib/workbench/runStory.ts`, add:

```ts
type RuntimePublicStage =
	| 'round_query'
	| 'source_dispatch'
	| 'source_result'
	| 'merge'
	| 'scoring'
	| 'feedback'
	| 'finalization';

type RuntimeRoundStage = Exclude<RuntimePublicStage, 'finalization'>;

type RuntimeRoundGraphEvent = {
	event: WorkbenchEvent;
	runtimeRunId: string;
	eventId: string;
	eventSeq: number;
	stage: RuntimeRoundStage;
	roundNo: number;
	sourceKind: SourceKind | null;
	status: 'pending' | 'running' | 'completed' | 'blocked' | 'degraded' | 'failed';
	counts: Record<string, number>;
	safeReasonCode: string | null;
};

function runtimeRoundGraphEvents(events: WorkbenchEvent[]): RuntimeRoundGraphEvent[] {
	return events
		.map((event) => {
			const payload = event.payload as Record<string, unknown>;
			if (payload?.schemaVersion !== 'runtime_public_event_v1') {
				return null;
			}
			const stage = String(payload.stage || '') as RuntimePublicStage;
			if (stage === 'finalization' || !isRuntimeRoundStage(stage)) {
				return null;
			}
			if (payload.roundNo === null || payload.roundNo === undefined) {
				return null;
			}
			const roundNo = Number(payload.roundNo);
			if (!Number.isInteger(roundNo) || roundNo < 1) {
				return null;
			}
			const sourceKind = payload.sourceKind === 'cts' || payload.sourceKind === 'liepin' ? payload.sourceKind : null;
			const status = String(payload.status || 'pending') as RuntimeRoundGraphEvent['status'];
			const counts = typeof payload.counts === 'object' && payload.counts ? (payload.counts as Record<string, number>) : {};
			const eventSeq = Number(payload.eventSeq);
			return {
				event,
				runtimeRunId: String(payload.runtimeRunId || ''),
				eventId: String(payload.eventId || ''),
				eventSeq: Number.isFinite(eventSeq) ? eventSeq : event.globalSeq,
				stage,
				roundNo,
				sourceKind,
				status,
				counts,
				safeReasonCode: typeof payload.safeReasonCode === 'string' ? payload.safeReasonCode : null
			};
		})
		.filter((event): event is RuntimeRoundGraphEvent => event !== null);
}

function isRuntimeRoundStage(stage: RuntimePublicStage): stage is RuntimeRoundStage {
	return (
		stage === 'round_query' ||
		stage === 'source_dispatch' ||
		stage === 'source_result' ||
		stage === 'merge' ||
		stage === 'scoring' ||
		stage === 'feedback'
	);
}
```

- [ ] **Step 4: Replace legacy source lanes with round modules when public round events exist**

In `buildRunStory(...)`, before calling the existing legacy `appendCtsLane(...)` / `appendLiepinLane(...)`, add:

```ts
const roundEvents = runtimeRoundGraphEvents(scopedEvents);
if (roundEvents.length > 0) {
	const roundTerminalNode = appendRuntimeRoundModules({
		graphNodes,
		graphEdges,
		roundEvents,
		sourceKinds,
		startNodeId: requirementsStarted || requirements || triageHasInput ? 'requirements' : 'job',
		finalTopCandidates,
		finalTopStatus
	});
	appendFinalNode({
		graphNodes,
		graphEdges,
		fromNodeId: roundTerminalNode,
		finalTopCandidates,
		finalTopStatus,
		finalReport
	});
} else {
	// Keep the existing legacy CTS/Liepin append path here for old sessions without public round events.
}
```

Add the helper:

```ts
function appendRuntimeRoundModules(input: {
	graphNodes: RecruiterGraphNode[];
	graphEdges: RecruiterGraphEdge[];
	roundEvents: RuntimeRoundGraphEvent[];
	sourceKinds: SourceKind[];
	startNodeId: string;
	finalTopCandidates: WorkbenchFinalTopCandidate[];
	finalTopStatus: BuildRunStoryInput['finalTopStatus'];
}): string {
	const rounds = [...new Set(input.roundEvents.map((event) => event.roundNo))].sort((left, right) => left - right);
	let previousNodeId = input.startNodeId;
	for (const [roundIndex, roundNo] of rounds.entries()) {
		const events = input.roundEvents.filter((event) => event.roundNo === roundNo);
		const queryId = `round-${roundNo}-query`;
		const scoreId = `round-${roundNo}-score`;
		const feedbackId = `round-${roundNo}-feedback`;
		const activeSources = input.sourceKinds.filter((sourceKind) =>
			events.some((event) => event.sourceKind === sourceKind)
		);
		input.graphNodes.push(roundNode(queryId, roundNo, '检索', `第 ${roundNo} 轮 · 查询包`, 'round_query', events, null, 20, 50));
		input.graphEdges.push({ from: previousNodeId, to: queryId, tone: 'neutral', label: roundIndex === 0 ? '开始检索' : '下一轮' });
		const sourceNodeIds = activeSources.map((sourceKind, sourceIndex) => {
			const event = lastRoundEvent(events, 'source_result', sourceKind) ?? lastRoundEvent(events, 'source_dispatch', sourceKind);
			const nodeId = `round-${roundNo}-source-${sourceKind}`;
			input.graphNodes.push(sourceRoundNode(nodeId, roundNo, sourceKind, event, sourceIndex, activeSources.length));
			input.graphEdges.push({ from: queryId, to: nodeId, tone: 'neutral', label: '执行' });
			return nodeId;
		});
		if (sourceNodeIds.length > 1) {
			const mergeId = `round-${roundNo}-merge`;
			input.graphNodes.push(roundNode(mergeId, roundNo, '命中', `第 ${roundNo} 轮 · 合并去重`, 'merge', events, null, 60, 50));
			for (const sourceNodeId of sourceNodeIds) {
				input.graphEdges.push({ from: sourceNodeId, to: mergeId, tone: 'blue', label: '证据合并' });
			}
			input.graphEdges.push({ from: mergeId, to: scoreId, tone: 'blue', label: '排序' });
		} else if (sourceNodeIds[0]) {
			input.graphEdges.push({ from: sourceNodeIds[0], to: scoreId, tone: 'blue', label: '排序' });
		}
		input.graphNodes.push(roundNode(scoreId, roundNo, '排序', `第 ${roundNo} 轮 · Top Pool`, 'scoring', events, null, 74, 50));
		if (events.some((event) => event.stage === 'feedback')) {
			input.graphNodes.push(roundNode(feedbackId, roundNo, '反思', `第 ${roundNo} 轮 · 下一轮策略`, 'feedback', events, null, 88, 50));
			input.graphEdges.push({ from: scoreId, to: feedbackId, tone: 'green', label: '反馈' });
			previousNodeId = feedbackId;
		} else {
			previousNodeId = scoreId;
		}
	}
	return previousNodeId;
}
```

Implement `roundNode(...)`, `sourceRoundNode(...)`, and `lastRoundEvent(...)` beside the existing graph helpers:

```ts
function roundNode(
	id: string,
	roundNo: number,
	kind: RecruiterGraphNode['kind'],
	label: string,
	stage: RuntimePublicStage,
	events: RuntimeRoundGraphEvent[],
	sourceKind: SourceKind | null,
	x: number,
	y: number
): RecruiterGraphNode {
	const stageEvent = lastRoundEvent(events, stage, sourceKind);
	const countText =
		stageEvent?.counts.topPoolCount !== undefined
			? `${stageEvent.counts.topPoolCount} 位进入 Top Pool`
			: stageEvent?.counts.roundIdentities !== undefined
				? `${stageEvent.counts.roundIdentities} 位身份`
				: stageEvent?.counts.roundReturned !== undefined
					? `${stageEvent.counts.roundReturned} 位候选人`
					: `第 ${roundNo} 轮`;
	return {
		id,
		at: roundNo,
		kind,
		label,
		detail: countText,
		x,
		y,
		tone: toneForRuntimeStatus(stageEvent?.status ?? 'pending'),
		sourceKind: sourceKind ?? 'all',
		sourceLabel: sourceKind ? sourceLabels[sourceKind] : '全部来源',
		lane: sourceKind ?? 'shared',
		eventIds: events.filter((event) => event.stage === stage && event.sourceKind === sourceKind).map((event) => eventId(event.event)),
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function sourceRoundNode(
	id: string,
	roundNo: number,
	sourceKind: SourceKind,
	event: RuntimeRoundGraphEvent | null | undefined,
	sourceIndex: number,
	sourceCount: number
): RecruiterGraphNode {
	const y = sourceCount === 1 ? 50 : sourceIndex === 0 ? 36 : 64;
	return {
		id,
		at: roundNo,
		kind: '检索',
		label: `第 ${roundNo} 轮 · ${sourceLabels[sourceKind]} 检索`,
		detail: event?.safeReasonCode ? sourceReasonLabel(event.safeReasonCode) : `${event?.counts.roundReturned ?? 0} 位候选人`,
		x: 42,
		y,
		tone: toneForRuntimeStatus(event?.status ?? 'pending'),
		sourceKind,
		sourceLabel: sourceLabels[sourceKind],
		lane: sourceKind,
		eventIds: event ? [eventId(event.event)] : [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function lastRoundEvent(
	events: RuntimeRoundGraphEvent[],
	stage: RuntimePublicStage,
	sourceKind: SourceKind | null
): RuntimeRoundGraphEvent | null {
	const matching = events.filter((event) => event.stage === stage && event.sourceKind === sourceKind);
	return matching[matching.length - 1] ?? null;
}

function toneForRuntimeStatus(status: RuntimeRoundGraphEvent['status']): RecruiterGraphNode['tone'] {
	if (status === 'completed') return 'green';
	if (status === 'blocked' || status === 'degraded') return 'amber';
	if (status === 'failed') return 'rose';
	if (status === 'running') return 'blue';
	return 'neutral';
}
```

Keep the old `appendCtsLane(...)` and `appendLiepinLane(...)` path only as legacy fallback for sessions that have no `runtime_public_event_v1` events.

- [ ] **Step 5: Pass finalTop10 status from the Svelte page**

Verify `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte` passes `finalTopCandidates` and `finalTopStatus` to `buildRunStory(...)`. If it does not, update the call:

```ts
const story = $derived(
	sessionQuery.data
		? buildRunStory({
				session: sessionQuery.data,
				candidateReviewItems: candidatesQuery.data?.items ?? [],
				finalTopCandidates: finalTopQuery.data?.items ?? [],
				finalTopStatus: finalTopQuery.isPending ? 'loading' : finalTopQuery.error ? 'error' : 'success',
				events: eventsQuery.data?.events ?? []
			})
		: null
);
```

- [ ] **Step 6: Run focused tests**

```bash
bun --cwd apps/web-svelte test src/lib/workbench/runStory.test.ts src/lib/workbench/sourceDisplay.test.ts
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add apps/web-svelte/src/lib/workbench/runStory.ts apps/web-svelte/src/lib/workbench/runStory.test.ts apps/web-svelte/src/lib/workbench/sourceDisplay.ts apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts 'apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte'
git commit -m "fix: render runtime rounds in strategy graph"
```

---

## Task 3: Layout Strategy Graph As Dynamic Round Rows

**Files:**
- Modify: `apps/web-svelte/src/lib/workbench/strategyGraphLayout.ts`
- Modify: `apps/web-svelte/src/lib/workbench/strategyGraphLayout.test.ts`
- Modify: `apps/web-svelte/src/lib/components/StrategyCanvas.svelte`
- Test: `apps/web-svelte/src/lib/workbench/strategyGraphLayout.test.ts`

- [ ] **Step 1: Add layout regressions**

Add to `apps/web-svelte/src/lib/workbench/strategyGraphLayout.test.ts`. Import `NODE_HEIGHT` from `strategyGraphLayout.ts` if the file does not already import it:

```ts
it('lays out runtime rounds as vertical rows that restart at the query column', () => {
	const nodes = [
		graphNode('job'),
		graphNode('requirements'),
		graphNode('round-1-query'),
		graphNode('round-1-source-cts', 'cts'),
		graphNode('round-1-source-liepin', 'liepin'),
		graphNode('round-1-merge'),
		graphNode('round-1-score'),
		graphNode('round-1-feedback'),
		graphNode('round-2-query'),
		graphNode('round-2-source-cts', 'cts'),
		graphNode('round-2-source-liepin', 'liepin'),
		graphNode('round-2-merge'),
		graphNode('round-2-score'),
		graphNode('final-shortlist')
	];

	const layout = fallbackLayout(nodes, [], { width: 1280, height: 760 });
	const positions = new Map(layout.nodes.map((node) => [node.id, node.position]));

	expect(positions.get('round-2-query')!.x).toBe(positions.get('round-1-query')!.x);
	expect(positions.get('round-2-query')!.y).toBeGreaterThan(positions.get('round-1-query')!.y);
	expect(positions.get('round-1-source-cts')!.y).toBeLessThan(positions.get('round-1-source-liepin')!.y);
	expect(positions.get('round-1-merge')!.x).toBeGreaterThan(positions.get('round-1-source-cts')!.x);
	expect(positions.get('final-shortlist')!.y).toBeGreaterThanOrEqual(positions.get('round-2-score')!.y - 16);
});

it('lays out a single-source runtime round without reserving an empty Liepin lane', () => {
	const nodes = [
		graphNode('job'),
		graphNode('requirements'),
		graphNode('round-1-query'),
		graphNode('round-1-source-cts', 'cts'),
		graphNode('round-1-score'),
		graphNode('final-shortlist')
	];

	const layout = fallbackLayout(nodes, [], { width: 980, height: 420 });
	const positions = new Map(layout.nodes.map((node) => [node.id, node.position]));

	expect(Math.abs(positions.get('round-1-query')!.y - positions.get('round-1-source-cts')!.y)).toBeLessThan(80);
	expect(positions.has('round-1-source-liepin')).toBe(false);
});

it('does not clamp many dual-source runtime rounds into overlapping bottom rows', () => {
	const nodes = [
		graphNode('job'),
		graphNode('requirements'),
		...Array.from({ length: 6 }, (_, index) => index + 1).flatMap((roundNo) => [
			graphNode(`round-${roundNo}-query`),
			graphNode(`round-${roundNo}-source-cts`, 'cts'),
			graphNode(`round-${roundNo}-source-liepin`, 'liepin'),
			graphNode(`round-${roundNo}-merge`),
			graphNode(`round-${roundNo}-score`)
		]),
		graphNode('final-shortlist')
	];

	const layout = fallbackLayout(nodes, [], { width: 1280, height: 520 });
	const positions = new Map(layout.nodes.map((node) => [node.id, node.position]));

	expect(positions.get('round-6-query')!.y).toBeGreaterThan(520);
	expect(positions.get('round-6-query')!.y).toBeGreaterThan(positions.get('round-5-query')!.y);
	for (let roundNo = 1; roundNo < 6; roundNo += 1) {
		const currentLiepinBottom = positions.get(`round-${roundNo}-source-liepin`)!.y + NODE_HEIGHT;
		const nextCtsTop = positions.get(`round-${roundNo + 1}-source-cts`)!.y;
		expect(nextCtsTop).toBeGreaterThan(currentLiepinBottom + 16);
	}
	expect(layout.contentHeight).toBeGreaterThan(positions.get('round-6-source-liepin')!.y + NODE_HEIGHT);
});
```

Add `graphNode(...)` in the test file if it does not already exist:

```ts
function graphNode(id: string, lane: 'shared' | 'cts' | 'liepin' = 'shared'): RecruiterGraphNode {
	return {
		id,
		at: 0,
		kind: '岗位',
		label: id,
		detail: id,
		x: 0,
		y: 0,
		tone: 'neutral',
		sourceKind: lane === 'shared' ? 'all' : lane,
		sourceLabel: lane,
		lane,
		eventIds: [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}
```

Also add a component-level or Playwright regression that renders at least six Runtime round rows in the real `StrategyGraph`/Svelte Flow DOM, scrolls the strategy flow shell, and asserts the sixth round node is visible after scrolling. A coordinate-only layout test is not enough; the renderer must not clip the positioned nodes inside an internal fixed-height layer.

- [ ] **Step 2: Run tests to verify they fail**

```bash
bun --cwd apps/web-svelte test src/lib/workbench/strategyGraphLayout.test.ts
```

Expected: fail because `businessWorkflowLayout(...)` only recognizes `cts-round-*` nodes and hard-codes Liepin as a lower lane.

- [ ] **Step 3: Replace legacy workflow stage detection with runtime round detection**

In `apps/web-svelte/src/lib/workbench/strategyGraphLayout.ts`, replace `BUSINESS_STAGE_X` with:

```ts
const BUSINESS_STAGE_X = {
	start: GRAPH_INSET,
	requirements: GRAPH_INSET + BUSINESS_STAGE_STEP,
	query: GRAPH_INSET + BUSINESS_STAGE_STEP * 2,
	source: GRAPH_INSET + BUSINESS_STAGE_STEP * 3,
	merge: GRAPH_INSET + BUSINESS_STAGE_STEP * 4,
	score: GRAPH_INSET + BUSINESS_STAGE_STEP * 5,
	feedback: GRAPH_INSET + BUSINESS_STAGE_STEP * 6,
	final: GRAPH_INSET + BUSINESS_STAGE_STEP * 7
};
```

Add:

```ts
type RuntimeRoundNodeInfo = {
	roundNo: number;
	stage: 'query' | 'source' | 'merge' | 'score' | 'feedback';
	sourceKind: 'cts' | 'liepin' | null;
};

function runtimeRoundNodeInfo(nodeId: string): RuntimeRoundNodeInfo | null {
	const match = /^round-(\d+)-(query|merge|score|feedback)$/.exec(nodeId);
	if (match) {
		return { roundNo: Number(match[1]), stage: match[2] as RuntimeRoundNodeInfo['stage'], sourceKind: null };
	}
	const sourceMatch = /^round-(\d+)-source-(cts|liepin)$/.exec(nodeId);
	if (sourceMatch) {
		return { roundNo: Number(sourceMatch[1]), stage: 'source', sourceKind: sourceMatch[2] as 'cts' | 'liepin' };
	}
	return null;
}
```

- [ ] **Step 4: Implement dynamic round row layout**

Replace the current `businessWorkflowLayout(...)` body with logic that prefers runtime round rows when any node matches `runtimeRoundNodeInfo(...)`:

```ts
function businessWorkflowLayout(
	nodes: RecruiterGraphNode[],
	bounds: GraphBounds
): Map<string, GraphPosition> {
	const runtimeRounds = uniqueSortedNumbers(
		nodes
			.map((node) => runtimeRoundNodeInfo(node.id)?.roundNo)
			.filter((value): value is number => typeof value === 'number')
	);
	if (runtimeRounds.length > 0) {
		return runtimeRoundWorkflowLayout(nodes, bounds, runtimeRounds);
	}
	return legacyBusinessWorkflowLayout(nodes, bounds);
}
```

Also extend the layout return type so the canvas can scroll to the full graph:

```ts
export type LaidOutStrategyGraph = {
	nodes: StrategyFlowNode[];
	edges: StrategyFlowEdge[];
	contentWidth?: number;
	contentHeight?: number;
};
```

Add:

```ts
function runtimeRoundWorkflowLayout(
	nodes: RecruiterGraphNode[],
	bounds: GraphBounds,
	rounds: number[]
): Map<string, GraphPosition> {
	const positions = new Map<string, GraphPosition>();
	const sourceSpread = Math.round(NODE_HEIGHT * 0.62);
	const hasDualSourceRound = rounds.some((roundNo) =>
		nodes.some((node) => node.id === `round-${roundNo}-source-cts`) &&
		nodes.some((node) => node.id === `round-${roundNo}-source-liepin`)
	);
	const rowGap = hasDualSourceRound
		? NODE_HEIGHT + BUSINESS_ROW_GAP + sourceSpread * 2 + 24
		: NODE_HEIGHT + BUSINESS_ROW_GAP + 28;
	const firstRoundY = GRAPH_INSET + NODE_HEIGHT + BUSINESS_ROW_GAP;
	const rowY = new Map(rounds.map((roundNo, index) => [roundNo, firstRoundY + index * rowGap]));
	const sharedY = rowY.get(rounds[0]) ?? verticalCenter(bounds);
	for (const node of nodes) {
		if (node.id === 'requirements') {
			positions.set(node.id, { x: columnX('requirements'), y: sharedY });
			continue;
		}
		const info = runtimeRoundNodeInfo(node.id);
		if (!info) {
			continue;
		}
		const baseY = rowY.get(info.roundNo) ?? sharedY;
		const sourceOffset =
			info.stage === 'source' && info.sourceKind === 'cts'
				? -sourceSpread
				: info.stage === 'source' && info.sourceKind === 'liepin'
					? sourceSpread
					: 0;
		positions.set(node.id, {
			x: columnX(info.stage),
			y: Math.max(GRAPH_INSET, baseY + sourceOffset)
		});
	}
	if (nodes.some((node) => node.id === FINAL_SHORTLIST_ID)) {
		const lastRound = rounds[rounds.length - 1];
		positions.set(FINAL_SHORTLIST_ID, { x: columnX('final'), y: rowY.get(lastRound) ?? sharedY });
	}
	return positions;
}
```

Rename the old `businessWorkflowLayout(...)` implementation to `legacyBusinessWorkflowLayout(...)` and keep it as fallback for legacy sessions.

Update `fallbackLayout(...)`, `layoutStrategyGraph(...)`, and `stackLanePositions(...)` so the returned graph includes:

```ts
function contentBounds(positions: Map<string, GraphPosition>, bounds: GraphBounds): Pick<LaidOutStrategyGraph, 'contentWidth' | 'contentHeight'> {
	const maxX = Math.max(bounds.width, ...[...positions.values()].map((position) => position.x + NODE_WIDTH + GRAPH_INSET));
	const maxY = Math.max(bounds.height, ...[...positions.values()].map((position) => position.y + NODE_HEIGHT + GRAPH_INSET));
	return { contentWidth: maxX, contentHeight: maxY };
}
```

Use this helper whenever positions are converted to Svelte Flow nodes. Do not clamp runtime round rows to `bounds.height`; the canvas should scroll instead.

- [ ] **Step 5: Remove fixed lane bands from round-centric canvas**

In `apps/web-svelte/src/lib/components/StrategyCanvas.svelte`, only show the fixed CTS/Liepin lane bands when no round-centric node exists:

```svelte
{@const hasRuntimeRoundRows = nodes.some((node) => /^round-\d+-/.test(node.id))}
{#if activeLaneKinds.length > 1 && !hasRuntimeRoundRows}
	<div class="source-lane-bands" aria-hidden="true">
		{#each activeLaneKinds as sourceKind (sourceKind)}
			<div
				class={`source-lane-band ${sourceKind}`}
				style={`--lane-y: ${sourceKind === 'cts' ? '30%' : '70%'}`}
			>
				<span>{sourceLabel(sourceKind)}</span>
			</div>
		{/each}
	</div>
{/if}
```

In `apps/web-svelte/src/lib/components/StrategyGraph.svelte`, apply the returned content dimensions to the flow shell:

```svelte
<div
	class="strategy-flow-shell"
	bind:this={shellElement}
	style={`--strategy-content-width: ${laidOutGraph.contentWidth ?? defaultGraphBounds.width}px; --strategy-content-height: ${laidOutGraph.contentHeight ?? defaultGraphBounds.height}px;`}
>
```

Then update the CSS for the flow shell/flow surface so the first viewport remains compact but many rounds scroll:

```css
.strategy-flow-shell {
	overflow: auto;
}

.strategy-flow {
	min-width: var(--strategy-content-width);
	min-height: var(--strategy-content-height);
}
```

- [ ] **Step 6: Run focused tests**

```bash
bun --cwd apps/web-svelte test src/lib/workbench/strategyGraphLayout.test.ts src/lib/workbench/runStory.test.ts
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add apps/web-svelte/src/lib/workbench/strategyGraphLayout.ts apps/web-svelte/src/lib/workbench/strategyGraphLayout.test.ts apps/web-svelte/src/lib/components/StrategyCanvas.svelte apps/web-svelte/src/lib/components/StrategyGraph.svelte
git commit -m "fix: layout strategy graph by runtime round"
```

---

## Task 4: Harden Workbench Notes And OpenCLI Browser Cleanup

**Files:**
- Modify: `src/seektalent_ui/workbench_note_writer.py`
- Modify: `tests/test_workbench_note_writer.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
- Modify: `scripts/start-dev-workbench.sh`
- Modify: `tests/test_pi_opencli_browser.py`
- Modify: `tests/test_pi_dokobot_local_setup.py`

- [ ] **Step 1: Add note writer regressions**

Add to `tests/test_workbench_note_writer.py`:

```python
def test_workbench_note_validation_rejects_hidden_reasoning_tags():
    from seektalent_ui.workbench_note_writer import WorkbenchNoteValidationError, validate_workbench_note_text

    context = {
        "safeNumbers": [2, 10],
        "statusHint": "in_progress",
        "previousNotes": [],
    }

    with pytest.raises(WorkbenchNoteValidationError):
        validate_workbench_note_text("</think> 第一轮已评分 10 位候选人", context)

    with pytest.raises(WorkbenchNoteValidationError):
        validate_workbench_note_text("<think>hidden</think> 正在继续检索", context)


def test_workbench_note_writer_skips_duplicate_adjacent_note(tmp_path, monkeypatch):
    store, user, session = _user_and_session(tmp_path)
    writer = WorkbenchNoteWriter(store=store, settings=_settings(tmp_path), lease_owner="note-test")
    monkeypatch.setattr(writer, "_run_agent", lambda context: "正在根据已确认需求整理候选人搜索进展。")

    first = writer.tick_session(user=user, session_id=session.session_id, now=1_000)
    second = writer.tick_session(user=user, session_id=session.session_id, now=1_020)

    assert first is not None
    assert second is None
```

Update the existing `test_unchanged_waiting_context_still_lets_model_decide_after_heartbeat(...)` expectation in the same file. The writer may still call the model on a later heartbeat, but it must not append an identical visible note:

```python
assert first is not None
assert second is None
assert len(fake_agent.prompts) == 2
with sqlite3.connect(store.db_path) as conn:
    count = conn.execute(
        "SELECT COUNT(*) FROM session_events WHERE event_name = 'workbench_note_created'"
    ).fetchone()[0]
assert count == 1
```

- [ ] **Step 2: Implement note sanitization and adjacent dedupe**

In `src/seektalent_ui/workbench_note_writer.py`, add:

```python
HIDDEN_REASONING_PATTERN = re.compile(r"</?think\b[^>]*>|</?reasoning\b[^>]*>|</?analysis\b[^>]*>", re.I)
NOTE_TECHNICAL_DENY_TERMS = (
    "opencli",
    "dokobot",
    "mcp",
    "pi_agent",
    "pi tool",
    "browser command",
    "source_lane_run_id",
    "runtime_run_id",
    "artifact://",
    "trace",
    "lease file",
)
```

Update `validate_workbench_note_text(...)`:

```python
if HIDDEN_REASONING_PATTERN.search(text):
    raise WorkbenchNoteValidationError("Note exposes hidden reasoning tags.")
```

Add:

```python
def _normalized_note_for_dedupe(text: str) -> str:
    normalized = " ".join(text.strip().split()).lower()
    normalized = re.sub(r"[，。,.!！?？；;：:\\s]+", "", normalized)
    return normalized


def _is_duplicate_recent_note(note_text: str, context: Mapping[str, object]) -> bool:
    current = _normalized_note_for_dedupe(note_text)
    previous = context.get("previousNotes")
    if not isinstance(previous, list):
        return False
    for item in previous[:5]:
        if isinstance(item, str) and _normalized_note_for_dedupe(item) == current:
            return True
    return False
```

In `tick_session(...)`, after validation and before `try_append_workbench_note(...)`, add:

```python
if _is_duplicate_recent_note(note_text, context):
    return None
```

Keep catching `WorkbenchNoteValidationError` as a deliberate dropped note. Do not use broad `except Exception` around `_run_agent(...)`; let unexpected async/runtime errors surface in tests and logs.

Use explicit error handling in `tick_session(...)`:

```python
try:
    output = self._run_agent(context)
    note_text = validate_workbench_note_text(output, context)
except WorkbenchNoteValidationError as exc:
    self._record_note_writer_drop(user=user, session_id=session_id, reason_code="note_validation_failed")
    return None
except (RuntimeError, TypeError, ValueError) as exc:
    self._record_note_writer_failure(user=user, session_id=session_id, exc=exc)
    raise
```

`_record_note_writer_drop(...)` and `_record_note_writer_failure(...)` must write safe event payloads only; they must not include model output, traceback text, local paths, or provider names. Add a regression where `_run_agent(...)` raises `TypeError` and `tick_session(...)` does not silently return `None`.

- [ ] **Step 3: Add OpenCLI orphan-tab cleanup regressions**

Add to `tests/test_pi_opencli_browser.py`:

```python
def test_cleanup_orphaned_owned_tabs_closes_liepin_tabs_without_lease(tmp_path: Path) -> None:
    commands = RecordingOpenCliCommands(
        outputs={
            ("browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [
                    {"id": "page-owned-1", "url": "https://h.liepin.com/search/getConditionItem#session"},
                    {"id": "page-user-1", "url": "https://h.liepin.com/search/getConditionItem#session"},
                    {"id": "page-other-1", "url": "https://example.com/"},
                ]
            ),
            ("browser", "seektalent-liepin", "tab", "close", "page-owned-1"): "",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-owned-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )

    result = runner.cleanup_orphaned_tabs(force=True)

    assert result.ok
    assert result.counts == {"leases": 0, "closedTabs": 1, "blankWindows": 0}
    assert ("browser", "seektalent-liepin", "tab", "close", "page-owned-1") in commands.calls
    assert ("browser", "seektalent-liepin", "tab", "close", "page-user-1") not in commands.calls
    assert ("browser", "seektalent-liepin", "tab", "close", "page-other-1") not in commands.calls


def test_cleanup_orphaned_owned_tabs_keeps_tabs_when_force_is_false(tmp_path: Path) -> None:
    commands = RecordingOpenCliCommands(
        outputs={
            ("browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"id": "page-owned-1", "url": "https://h.liepin.com/search/getConditionItem#session"}]
            )
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-owned-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )

    result = runner.cleanup_orphaned_tabs(force=False)

    assert result.ok
    assert result.counts == {"leases": 0, "closedTabs": 0, "blankWindows": 0}
```

Add paired stale-marker and malformed-marker regressions:

- Write an owned marker older than the configured TTL, include a matching Liepin tab in `tab list`, call `cleanup_orphaned_tabs(force=True)`, and assert the tab is not closed and the stale marker is removed.
- Write malformed owned-marker JSON or a marker with the wrong schema, include a matching Liepin tab in `tab list`, call `cleanup_orphaned_tabs(force=True)`, and assert no tab close command is issued. The implementation may raise `OpenCliBrowserError("liepin_opencli_malformed_state")` or delete only the malformed marker, but it must never fall back to URL-only tab ownership.
- Write malformed owned-marker JSON, call `open_liepin_tab(...)`, and assert a new owned marker is written for the newly opened tab. Opening a new owned tab may quarantine/delete the malformed marker, because stale local GC state must not block future real-browser tests.

- [ ] **Step 4: Implement OpenCLI orphan-tab cleanup**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add durable owned-page markers. `open_liepin_tab(...)` must call `_write_owned_page_marker(...)` after parsing the new page id. `cleanup_idle_lease(...)` must call `_forget_owned_page_marker(page_id)` after it closes the leased tab.

Set a conservative marker TTL, for example:

```python
OWNED_PAGE_MARKER_TTL_SECONDS = 24 * 60 * 60
```

Add these helpers:

```python
def _owned_pages_path(self) -> Path:
    directory = self._config.lease_dir or (Path(tempfile.gettempdir()) / "seektalent-opencli-leases")
    return directory / f"{_safe_filename(self._config.session)}-owned-pages.json"


def _read_owned_page_markers(self) -> dict[str, dict[str, object]]:
    try:
        loaded = json.loads(self._owned_pages_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
    if not isinstance(loaded, dict):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    markers: dict[str, dict[str, object]] = {}
    for page_id, marker in loaded.items():
        if not _is_safe_page_id(str(page_id)) or not isinstance(marker, dict):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        if marker.get("schema_version") != "seektalent.opencli_owned_page.v1":
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        if marker.get("session") != self._config.session:
            continue
        markers[str(page_id)] = dict(marker)
    return markers


def _read_owned_page_markers_for_write(self) -> dict[str, dict[str, object]]:
    try:
        return self._read_owned_page_markers()
    except OpenCliBrowserError as exc:
        if exc.safe_reason_code != "liepin_opencli_malformed_state":
            raise
        # New owned tab creation is allowed to recover from stale local marker
        # corruption. Cleanup remains conservative and must not close by URL only.
        self._quarantine_owned_page_marker_file()
        return {}


def _quarantine_owned_page_marker_file(self) -> None:
    path = self._owned_pages_path()
    if not path.exists():
        return
    target = path.with_name(f"{path.name}.malformed-{int(time.time())}")
    try:
        path.replace(target)
    except OSError:
        path.unlink(missing_ok=True)


def _write_owned_page_marker(
    self,
    *,
    page_id: str,
    url: str,
    runtime_run_id: str | None,
    source_lane_run_id: str | None,
    owner_nonce: str,
    opened_at: float | None = None,
) -> None:
    if not _is_safe_page_id(page_id) or not self._is_owned_liepin_tab(url):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    markers = self._read_owned_page_markers_for_write()
    markers[page_id] = {
        "schema_version": "seektalent.opencli_owned_page.v1",
        "session": self._config.session,
        "page_id": page_id,
        "url": url,
        "opened_at": opened_at or time.time(),
        "runtime_run_id": runtime_run_id,
        "source_lane_run_id": source_lane_run_id,
        "owner_nonce": owner_nonce,
    }
    path = self._owned_pages_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _forget_owned_page_marker(self, page_id: str) -> None:
    markers = self._read_owned_page_markers()
    if page_id not in markers:
        return
    markers.pop(page_id)
    path = self._owned_pages_path()
    if markers:
        path.write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
```

Then add:

```python
def cleanup_orphaned_tabs(self, *, force: bool = False) -> OpenCliBrowserResult:
    lease = self._read_lease()
    if lease is not None:
        return self.cleanup_idle_lease(force=force)
    if not force:
        return OpenCliBrowserResult(ok=True, action="cleanup_orphaned_tabs", counts={"leases": 0, "closedTabs": 0, "blankWindows": 0})
    owned_pages = self._read_owned_page_markers()
    closed = 0
    for tab in self._list_tabs():
        page_id = str(tab.get("id") or tab.get("page_id") or "")
        tab_url = str(tab.get("url") or "")
        if not _is_safe_page_id(page_id):
            continue
        marker = owned_pages.get(page_id)
        if marker is None:
            continue
        opened_at = marker.get("opened_at")
        if not isinstance(opened_at, int | float) or time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
            self._forget_owned_page_marker(page_id)
            continue
        if marker.get("session") != self._config.session or marker.get("url") != tab_url:
            continue
        self._run_browser_command("tab", ("close", page_id))
        self._forget_owned_page_marker(page_id)
        closed += 1
    blank_windows = 1 if self._close_blank_window_if_enabled() else 0
    return OpenCliBrowserResult(
        ok=True,
        action="cleanup_orphaned_tabs",
        counts={"leases": 0, "closedTabs": closed, "blankWindows": blank_windows},
    )
```

In `src/seektalent/providers/pi_agent/opencli_browser_cli.py`, add:

```python
if action == "cleanup_orphaned_tabs":
    return runner.cleanup_orphaned_tabs(force=bool(payload.get("force") or False))
```

In `scripts/start-dev-workbench.sh`, replace the shutdown cleanup command with:

```bash
printf '{"force": true}' | uv run python -m seektalent.providers.pi_agent.opencli_browser_cli cleanup_orphaned_tabs >/dev/null 2>&1 || true
```

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest tests/test_workbench_note_writer.py::test_workbench_note_validation_rejects_hidden_reasoning_tags tests/test_workbench_note_writer.py::test_workbench_note_writer_skips_duplicate_adjacent_note tests/test_pi_opencli_browser.py::test_cleanup_orphaned_owned_tabs_closes_liepin_tabs_without_lease tests/test_pi_opencli_browser.py::test_cleanup_orphaned_owned_tabs_keeps_tabs_when_force_is_false tests/test_pi_opencli_browser.py::test_cleanup_orphaned_owned_tabs_ignores_stale_marker tests/test_pi_dokobot_local_setup.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent_ui/workbench_note_writer.py tests/test_workbench_note_writer.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py scripts/start-dev-workbench.sh tests/test_pi_opencli_browser.py tests/test_pi_dokobot_local_setup.py
git commit -m "fix: harden notes and browser cleanup"
```

---

## Task 5: End-To-End Regression And Safety Verification

**Files:**
- Modify: `tests/test_workbench_api.py`
- Modify: `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`

- [ ] **Step 1: Add backend degraded coverage regression**

Add to `tests/test_workbench_api.py`:

```python
async def _blocked_liepin_logical_query_bundle(**kwargs):
    from seektalent.runtime.source_lanes import RuntimeSourceLaneResult

    runtime_run_id = str(kwargs["runtime_run_id"])
    source_plan_id = str(kwargs["source_plan_id"])
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=f"{source_plan_id}:liepin:blocked",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="blocked",
        raw_candidate_count=0,
        blocked_reason_code="source_browser_backend_unavailable",
    )


def test_dual_source_runtime_job_keeps_cts_when_liepin_blocks(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    workbench_store = client.app.state.workbench_store
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_triage(client, session["sessionId"])
    monkeypatch.setattr(
        "seektalent.providers.liepin.runtime_lane.run_liepin_logical_query_bundle",
        _blocked_liepin_logical_query_bundle,
    )

    response = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/start",
        headers=_csrf_header(client),
        json={"idempotencyKey": "degraded-run"},
    )

    assert response.status_code == 202
    _run_next_runtime_sourcing_job_for_test(workbench_store, tmp_path)
    final_top = client.get(f"/api/workbench/sessions/{session['sessionId']}/final-top10")
    assert final_top.status_code == 200
    assert final_top.json()["coverageStatus"] in {"degraded", "complete"}
    assert len(final_top.json()["items"]) <= 10
```

If the production adapter function keeps a different name in the current source-round dispatch baseline, monkeypatch the public Liepin logical-bundle entrypoint used there. Do not patch a lower browser/session function for this regression; the test must validate source-scoped degraded coverage at the Runtime dispatch boundary.
Define `_run_next_runtime_sourcing_job_for_test(...)` locally by claiming one runtime sourcing job from the store and calling `run_runtime_sourcing_job(...)` with the same fake/runtime settings pattern used by `tests/test_workbench_runtime_owned_execution.py`. Do not depend on an undefined background worker fixture.

- [ ] **Step 2: Add Svelte e2e graph count and round-layout regression**

In `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`, add mocked session/events/final-top10 responses that include two Runtime rounds, CTS and Liepin source result events per round, final-top10 response with 10 items, and candidate review response with 24 items. Include realistic source evidence for at least one merged identity. Assert the final node displays 10, does not display 24, and the second round starts back at the query column:

```ts
		await expect(page.getByText('最终短名单')).toBeVisible();
		await expect(page.getByText(/10 位候选人/)).toBeVisible();
		await expect(page.getByText(/24 位候选人/)).not.toBeVisible();
		await expect(page.getByText(/第 1 轮/)).toBeVisible();
		await expect(page.getByText(/第 2 轮/)).toBeVisible();
		await expect(page.getByText('CTS')).toBeVisible();
		await expect(page.getByText('Liepin')).toBeVisible();
```

- [ ] **Step 3: Add public payload no-leak regressions**

Add backend API payload tests in `tests/test_workbench_api.py`:

```python
import json

PUBLIC_PAYLOAD_DENY_TERMS = (
    "OpenCLI",
    "DokoBot",
    "mcp",
    "pi_agent",
    "cookie",
    "authorization",
    "raw_provider_payload",
    "raw_resume",
    "/Users/",
)


def _assert_public_payload_safe(payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for term in PUBLIC_PAYLOAD_DENY_TERMS:
        assert term not in text


def test_workbench_public_payloads_do_not_expose_provider_internals(tmp_path: Path):
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    workbench_store = client.app.state.workbench_store
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _persist_public_payload_safety_fixture(workbench_store, session["sessionId"])

    for suffix in ("", "/events", "/final-top10"):
        response = client.get(f"/api/workbench/sessions/{session['sessionId']}{suffix}")
        assert response.status_code == 200
        _assert_public_payload_safe(response.json())
```

Define `_persist_public_payload_safety_fixture(...)` in the test file. It should insert a blocked Liepin projection using an internal stored reason such as `liepin_opencli_timeout`, then assert the API returns the mapped business-safe reason such as `source_browser_timeout`.

Add a focused reason-mapping regression that stores representative internal reason codes in source-run warning fields, runtime source lane latest-state payloads, Workbench event payloads, and final-top10 evidence payloads. Assert all public serializers return only the business-safe taxonomy from the spec, including login required, account mismatch, timeout, browser backend unavailable, extension disconnected, risk/verification required, budget exhausted, provider failed, partial, and unknown.

Add a DOM no-leak assertion to the Svelte e2e test after the mocked dual-source page renders:

```ts
const html = await page.locator('body').innerHTML();
for (const term of ['OpenCLI', 'DokoBot', 'mcp', 'pi_agent', 'cookie', 'authorization', 'raw_provider_payload', 'raw_resume']) {
	expect(html).not.toContain(term);
}
```

- [ ] **Step 4: Run full focused verification**

```bash
test -f src/seektalent/runtime/logical_query_dispatch.py
rg -n "class LogicalQueryDispatch" src/seektalent/runtime/logical_query_dispatch.py
rg -n "runtime_sourcing_jobs|WorkbenchRuntimeSourcingJob|start_runtime_sourcing_job|extend_runtime_sourcing_job_lease" src/seektalent_ui/workbench_store.py
rg -n "def run_runtime_sourcing_job" src/seektalent_ui/runtime_bridge.py
rg -n "dispatch_source_rounds" src/seektalent/runtime/orchestrator.py
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py tests/test_workbench_maintenance.py -q
uv run pytest tests/test_workbench_note_writer.py -q
uv run pytest tests/test_pi_opencli_browser.py tests/test_pi_dokobot_local_setup.py -q
bun --cwd apps/web-svelte test src/lib/workbench/runStory.test.ts src/lib/workbench/strategyGraphLayout.test.ts src/lib/workbench/sourceDisplay.test.ts src/lib/workbench/finalCandidateCards.test.ts
uv run ruff check src tests
uv run --group dev ty check src tests
git diff --check
```

Expected:

- pytest passes
- Svelte tests pass
- ruff passes
- ty passes
- diff check passes

- [ ] **Step 5: Run auxiliary public-surface source scan**

```bash
rg -n "cookie|authorization|storageState|raw_provider_payload|OpenCLI|DokoBot|mcp|localStorage|session secret|Bearer" src/seektalent_ui src/seektalent/runtime/public_events.py apps/web-svelte/src apps/web-svelte/tests/e2e tests/test_workbench_api.py
```

Expected: investigate any matches in public route serializers, public event builders, Svelte render paths, or fixture payloads. Matches are allowed only for deny lists, internal-to-public mapping tests, or comments asserting absence. Internal provider modules and Python source files may retain internal reason names; the hard pass/fail condition is the API/DOM payload tests above.

- [ ] **Step 6: Commit verification updates**

```bash
git add tests/test_workbench_api.py apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts
git commit -m "test: cover runtime-owned dual-source workbench flow"
```

---

## Deferred Follow-Ups

Record these only if they are not already present in the repository follow-up document:

- Manual Liepin card-review UI with explicit human approval.
- Manual merge/unmerge controls for ambiguous duplicates.
- Source quality/cost metrics and automatic source strategy optimization.
- A stable internal source capability descriptor for future Boss/Zhaopin/ATS sources.
- Browser action trace replay fixtures for live Liepin debugging.

## Final Verification

Run before review:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py tests/test_workbench_maintenance.py -q
uv run pytest tests/test_workbench_note_writer.py -q
uv run pytest tests/test_pi_opencli_browser.py tests/test_pi_dokobot_local_setup.py -q
uv run pytest tests/test_runtime_state_flow.py tests/test_rescue_router.py -q
bun --cwd apps/web-svelte test src/lib/workbench/runStory.test.ts src/lib/workbench/strategyGraphLayout.test.ts src/lib/workbench/sourceDisplay.test.ts src/lib/workbench/finalCandidateCards.test.ts
uv run ruff check src tests
uv run --group dev ty check src tests
git diff --check
```

Expected: all commands pass.

## Self-Review

- Spec coverage: the baseline verification covers one runtime job per session, shared Runtime round loop, 70/30 query allocation, candidate-feedback priority, parallel source dispatch, source-scoped failure, identity merge, and final Top 10 cap. The executable follow-up tasks cover round-centric graph projection, live source-card counts, public payload safety, note sanitization/dedupe, and OpenCLI orphan-tab cleanup.
- Placeholder scan: task-local helpers and contracts are explicitly defined before use. Remaining ellipses are limited to conceptual Python stubs or shortened context in explanatory snippets, not missing implementation steps.
- Type consistency: `WorkbenchRuntimeSourcingJob`, `WorkbenchRuntimeSourcingJobContext`, `WorkbenchRuntimeSourcingJobStartResponse`, `LogicalQueryDispatch`, `RuntimePublicEvent`, `RuntimeRoundGraphEvent`, `SourceRoundDispatchRequest`, `SourceRoundAdapterResult`, `SourceRoundDispatchResult`, and the single merge helper contract are introduced before use in later tasks.
