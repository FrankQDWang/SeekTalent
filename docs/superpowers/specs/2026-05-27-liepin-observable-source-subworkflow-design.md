# Liepin Observable Source Subworkflow Design

## Summary

Turn the current deterministic Liepin OpenCLI adapter flow into an explicit observable source subworkflow. This is a visibility and control-boundary change, not the full durable workflow rewrite.

The runtime should continue to execute Liepin as a normal selected source and should continue to block merge/scoring until selected source coverage is complete. Inside the Liepin source lane, the OpenCLI flow should emit structured, public-safe substep events for each business step:

```text
prepare_search
apply_filters
submit_search
observe_cards
cache_detail_urls
open_detail
capture_detail
cleanup_detail_tabs (reserved; not emitted by the current OpenCLI path)
finalize
```

Those events should be persisted through the existing runtime source lane event path and shown in source node details so manual testing can answer exactly which Liepin step ran, which step is blocked, and how many cards/details were observed.

## Current Code Facts

- Runtime source dispatch already treats CTS and Liepin as source adapters through `src/seektalent/runtime/source_round_dispatch.py`.
- Liepin execution enters `src/seektalent/providers/liepin/runtime_lane.py`.
- Deterministic OpenCLI execution lives in `src/seektalent/providers/pi_agent/opencli_browser.py`.
- `OpenCliBrowserRunner.search_liepin_resumes()` already runs a multi-step browser flow and writes protected action traces.
- The action trace currently uses free-form `action_kind` values such as `visible_cards_observed`, `detail_candidate_selected`, `capture_detail_succeeded`, and `cleanup_detail_tabs_after_capture`.
- `RuntimeSourceLaneEvent` currently only exposes coarse lane events such as `source_lane_completed`, `source_lane_partial`, and `detail_completed`.
- `WorkbenchStore` already persists runtime source lane events from `RuntimeSourceLaneResult.events`.
- The workbench runtime graph already renders source nodes and node detail sections from backend event/session data.
- The current OpenCLI execution contract intentionally leaves source-run detail tabs open for user inspection. Verified source-run-owned detail-tab closing is deferred until the OpenCLI fork can provide the required tab lifecycle guarantees.

## Problem

Liepin retrieval is deterministic now, but it is still mostly opaque to runtime and the UI while it runs. When manual testing sees "Liepin 0", "about:blank tabs", or "first detail captured but second missing", the backend can only report a final lane status plus protected action trace. That forces debugging to jump into artifacts and logs instead of using the runtime source graph.

The current implementation also makes cancellation/debug reasoning harder because the runtime sees Liepin as one large adapter call. Full durable restartability is larger than needed for the demo, but the system needs a clear subworkflow boundary before durable execution is worth adding.

## Goals

- Introduce a typed, public-safe Liepin OpenCLI substep vocabulary.
- Convert OpenCLI action trace events into runtime source lane substep events.
- Persist those substep events with the existing runtime source lane event storage.
- Expose substep summaries in workbench runtime source state and graph source node details.
- Preserve current successful Liepin retrieval behavior and current budgets:
  - first round Liepin exploit target remains 2 resumes
  - second and later rounds remain exploit 2 + explore 1
- Preserve selected-source fan-in behavior: merge/scoring must not proceed while selected Liepin is partial, blocked, failed, empty, or still missing.
- Preserve owned tab rules:
  - keep detail tabs opened by the workflow available for user inspection
  - never auto-close user-owned or workflow-created Liepin detail tabs in this slice
  - keep owned markers so a future OpenCLI fork can implement safe source-run cleanup
- Keep event payloads public-safe: no raw resume text, URLs, cookies, provider ids, direct contact fields, or protected artifact paths outside sanitized artifact refs.

## Non-Goals

- Do not implement durable checkpoint/resume.
- Do not split each OpenCLI primitive into a runtime-level node.
- Do not change controller query/filter ownership.
- Do not change CTS behavior.
- Do not change scoring, reflection, dedupe, finalization, or candidate selection semantics.
- Do not add LLM calls.
- Do not add arbitrary JavaScript execution beyond the existing constrained OpenCLI runner helpers.
- Do not redesign the runtime graph visual layout.
- Do not expose normalized resumes in the frontend.

## Target Architecture

```text
Runtime source round
  -> Liepin runtime source lane
    -> LiepinOpenCliResumeRetriever
      -> OpenCliBrowserRunner.search_liepin_resumes()
        -> OpenCLI action trace events
      -> LiepinOpenCliWorkflowStep summaries
    -> RuntimeSourceLaneEvent substep events
  -> WorkbenchStore runtime_source_lane_latest_state/session events
  -> Runtime graph source node detail sections
```

The key boundary is the source lane. Runtime still owns source coverage, fan-in, artifacts, and graph events. The OpenCLI runner owns browser actions. The new workflow adapter maps browser action trace events into typed business steps.

## Step Vocabulary

The public step names are fixed:

- `prepare_search`: status check and search tab readiness.
- `apply_filters`: native filter application.
- `submit_search`: filling query and submitting the search.
- `observe_cards`: visible card scan and card count.
- `cache_detail_urls`: extraction of safe detail URLs for visible cards.
- `open_detail`: opening a selected detail page.
- `capture_detail`: capturing and normalizing a detail page.
- `cleanup_detail_tabs`: reserved for a future OpenCLI fork-backed cleanup step. The current runner does not emit this step and does not close Liepin detail tabs.
- `finalize`: producing the deterministic resume envelope.

Each event has:

- `event_type`: one of `source_workflow_step_started`, `source_workflow_step_completed`, or `source_workflow_step_failed`.
- `step_name`: one of the public step names above.
- `safe_counts`: integer counts only.
- `safe_reason_code`: public-safe failure reason when applicable.
- `artifact_refs`: sanitized artifact refs when applicable.

## Event Mapping

The action trace should map to public workflow steps as follows:

| OpenCLI action kind | Step | Event |
| --- | --- | --- |
| `search_cards_started` | `prepare_search` | completed |
| `apply_filters_started` | `apply_filters` | started |
| `apply_filters_completed` | `apply_filters` | completed |
| `search_submitted` | `submit_search` | completed |
| `visible_cards_observed` | `observe_cards` | completed |
| `detail_urls_cached` | `cache_detail_urls` | completed |
| `detail_candidate_selected` | `open_detail` | started |
| `open_detail_succeeded` | `open_detail` | completed |
| `open_detail_failed` | `open_detail` | failed |
| `observe_detail` | `capture_detail` | completed |
| `capture_detail_succeeded` | `capture_detail` | completed |
| `capture_detail_failed` | `capture_detail` | failed |
| `cleanup_detail_tabs_after_capture` | `cleanup_detail_tabs` | legacy/reserved only; current OpenCLI runner does not emit this action |
| `visible_cards_refresh_failed_after_cleanup` | `observe_cards` | failed |
| `detail_target_not_met` | `finalize` | failed |
| final envelope status | `finalize` | completed, partial, or failed |

If multiple detail pages are opened, repeated step events are expected. The UI can display them as a timeline.

## Public Safety

Subworkflow event payloads must not include:

- raw resume text
- normalized resume text
- provider subject ids
- protected browser output
- full URLs
- cookies or tokens
- direct contact details

Allowed payload content:

- step name
- event status
- rank
- counts such as cards seen, visible cards, resumes returned, details opened, closed tabs
- public-safe reason codes
- sanitized `artifact://protected/...` refs only in `artifact_refs`

## Runtime Graph Behavior

For each Liepin source node:

- Detail sections should include a "猎聘步骤" or equivalent step timeline.
- Timeline entries should use natural text, not raw JSON.
- The source node candidate scope remains the recalled original resumes for that source/round.
- Normalized resumes must not be displayed by this work.
- If a step fails, the detail section should show the step and safe reason code.

## Acceptance Criteria

- Liepin source lane result contains workflow substep events for a successful two-detail retrieval.
- A failed or partial Liepin run includes the last failed substep and safe reason code.
- Workbench store persists the substep events through the existing runtime source lane event path.
- Runtime graph node details for Liepin source nodes show the substep timeline.
- The latest source state API includes enough public-safe step information to debug which Liepin step is current/latest.
- No raw or normalized resume text appears in workflow step events.
- Existing Liepin counts and fan-in behavior remain unchanged.
- Existing OpenCLI tab ownership remains intact; verified detail-tab closing is deferred and tracked in `TODOS.md`.
- Full backend tests pass.
