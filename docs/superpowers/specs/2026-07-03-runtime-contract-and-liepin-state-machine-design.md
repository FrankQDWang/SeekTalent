# Runtime Contract And Liepin State Machine Design

## Summary

This design fixes two related reliability problems in the production runtime:

1. The reflection/controller boundary can pass semantically invalid query advice even though the model output is valid structured JSON.
2. Liepin browser operations are still too implicit for a volatile external website, so small state drift can cause repeated clicking, stale filters, or blocked retrieval.

The fix is not a repo-wide rewrite. It is a focused runtime hardening slice:

- add deterministic projection at the reflection/controller contract boundary;
- keep `secondary_title_anchor` valid for round 1 only;
- make Liepin browser operations explicit transitions inside the Liepin provider;
- preserve OpenCLI/Liepin decoupling, scoring parallelism, and structured-resume-only data flow.

## Current Failure

The latest failed run did not fail in Liepin retrieval. Liepin search, card extraction, detail collection, scoring, and reflection completed through round 2.

The run failed at round 3 controller validation:

```text
rounds after 1 must not use secondary_title_anchor as a support term: 主观投资
```

The root cause is contract drift:

- `src/seektalent/prompts/reflection.md` still says reflection may keep or reuse `secondary_title_anchor`.
- `src/seektalent/prompts/controller.md` and `src/seektalent/retrieval/query_plan.py` say round 2+ must not use `secondary_title_anchor`.
- `ReflectionAdviceDraft` and `ControllerDecision` are valid structured outputs, but the JSON Schema only validates shape and field types. It does not encode dynamic round-specific term legality.

## Clarified Contract

`secondary_title_anchor` is not deprecated.

It remains a valid `QueryTermCandidate.retrieval_role` for the round 1 title pairing:

```text
primary_role_anchor + secondary_title_anchor
```

The stale part is the reflection instruction that allows later reflection advice to reuse it. Reflection advice only feeds later rounds, and later rounds must use:

```text
primary_role_anchor + 1-2 active admitted non-secondary support terms
```

Therefore:

- keep the `secondary_title_anchor` enum value;
- keep round 1 compiler behavior;
- remove `secondary_title_anchor` from reflection advice before it enters controller context;
- add a narrow deterministic controller projection so a late model retry cannot fail the whole run solely because it chose the round-1-only term.

## Goals

- Prevent the current round 3 failure mode deterministically.
- Keep Pydantic AI structured output as the model-output shape contract.
- Add semantic projection for dynamic business rules that JSON Schema cannot express.
- Make Liepin browser automation observable as explicit named transitions.
- Ensure every Liepin browser action has an OpenCLI latest-state observation immediately before the action and an observation-backed postcondition immediately after the action.
- Prevent blind repeated clicking of toggle filters.
- Preserve fullText hard-delete behavior: no collection, no storage, no fallback.

## Non-Goals

- Do not remove `secondary_title_anchor` from the model layer.
- Do not introduce a generic cross-site workflow engine.
- Do not change scoring concurrency.
- Do not add compatibility fallback to `fullText`, `rawText`, `visible_text`, or `normalized_card_text`.
- Do not add modal-closing behavior.
- Do not change UI product shape in this slice.
- Do not expose generic runner failure labels such as `precondition_failed` or `postcondition_failed` as provider `safe_reason_code` values.

## Design

### 1. Reflection Advice Projection

`ReflectionAdviceDraft` remains the model-facing structured output.

During `materialize_reflection_advice()`, the runtime already filters keyword advice to admitted terms. Extend that deterministic materialization step to also drop terms whose `QueryTermCandidate.retrieval_role == "secondary_title_anchor"`.

This gives one stable rule:

```text
Reflection can mention admitted terms only if they are legal advice terms for later rounds.
```

The generated `reflection_summary` must be built from the projected advice, so UI thinking observations do not show stale or impossible advice.

### 2. Controller Query Projection

Controller validation stays strict. Invalid model output should still be rejected and repaired.

Add one narrow deterministic projection after normal repair/retry attempts:

```text
If round > 1 and the only remaining validation problem is use of secondary_title_anchor,
remove that term and fill the support slot with the best active admitted non-title support term.
```

This projection must not swallow other validation errors. It is only for the already-known round-1-only title-support drift.

### 3. Liepin State Machine Boundary

Keep the existing layering:

- `OpenCliBrowserAutomation`: generic browser primitives only.
- `LiepinSiteAdapter`: Liepin page capabilities and DOM/state parsing.
- `LiepinSearchWorkflow`: Liepin operation orchestration.
- `LiepinOpenCliResumeRetriever`: stable worker/client entrypoint.

Add a small Liepin-only transition runner under the provider:

```text
Transition {
  name
  phase
  observe_pre_state
  precondition
  action
  observe_post_state
  postcondition
  retry_policy
  safe_reason_code
  trace_event
}
```

This is not a framework. It is a thin way to make each unstable Liepin browser operation explicit and testable.

The transition runner owns lifecycle order, trace emission, and public reason-code emission. `LiepinSiteAdapter` should expose small idempotent page primitives and state-parsing predicates; it must not hide multi-step workflow decisions that bypass the transition runner.

Every transition follows the same order:

```text
OpenCLI state/readiness probe
-> parse/classify latest state
-> precondition
-> action
-> OpenCLI state/readiness probe
-> postcondition
-> trace event with phase/action_kind/safe_reason_code
```

Runner-internal debug labels can exist for logs, but provider envelopes and UI-visible workflow steps must use concrete `liepin_opencli_*` safe reason codes.

### 4. Search And Filter Transitions

The search path should become:

```text
OpenSearch
WaitSearchReady
ClearFiltersOncePerWorkflow
FillKeyword
ClickSearch
WaitResultsReady
ApplyNativeFilters
ExtractStructuredCards
```

Every phase records one workflow-level event using canonical snake_case `action_kind` values:

```text
open_search
wait_search_ready
clear_native_filters
fill_search
click_search
observe_results
apply_native_filter
extract_structured_cards
```

Page-level actions can still record detailed adapter events. Any new `action_kind` must be added to the workflow-step mapper and client safe allowlist in the same implementation slice.

Native filters must follow this rule:

```text
observe latest state
if target already selected, return success
click once
observe latest state
verify selected
if unverified, do not blindly click the same toggle again
```

City selection must keep the deterministic order:

```text
direct visible option
other picker visible option
overseas tab when target is overseas
search inside picker
confirm
verify section summary
```

### 5. Detail Transitions

The detail path should become:

```text
SelectCard
OpenDetail
WaitDetailReady
CaptureStructuredDetail
RestoreSearchPage
RefreshStructuredCards
Finalize
```

Detail trace events must use canonical snake_case `action_kind` values:

```text
detail_candidate_selected
open_detail
open_detail_succeeded
wait_detail_ready
observe_detail
capture_detail_succeeded
return_to_search_after_capture
visible_cards_refreshed_after_return
detail_target_not_met
```

`WaitDetailReady` must wait for a valid Liepin detail resume state instead of assuming the tab is ready after a fixed delay.

If search-page restore fails after a successful capture, the workflow may use cached detail URLs for remaining selected cards. It must still preserve the target resume budget and detail consumption safety semantics.

## Transition And Reason-Code Matrix

Every listed transition must be implemented through the transition runner, not as an untracked private helper call.

| Transition | Latest State Source | Precondition | Action | Postcondition | Public Safe Reason Code |
| --- | --- | --- | --- | --- | --- |
| `open_search` | OpenCLI tab/url state | Search route can be opened or current route is reusable | Open/select search tab | Search URL or known search surface is active | `liepin_opencli_search_not_ready` |
| `wait_search_ready` | OpenCLI state/readiness probe | Page is not terminal | Wait/observe search page | Search input and submit surface are visible | `liepin_opencli_search_not_ready` |
| `clear_native_filters` | OpenCLI state | Clear action exists and current workflow has not already cleared | Click clear once | Filter summaries are empty or clear action is gone | `liepin_opencli_filter_unapplied` |
| `fill_search` | OpenCLI state | Search input ref is visible in latest state | Fill query | Input/search surface remains valid | `liepin_opencli_search_input_missing` |
| `click_search` | OpenCLI state | Submit button is visible in latest state | Click search | Loading, result list, or empty-result state is observed | `liepin_opencli_search_submit_unconfirmed` |
| `observe_results` | OpenCLI state/readiness probe | Search page is non-terminal | Wait/observe result surface | Result list or empty-result state is classified | `liepin_opencli_results_not_ready` |
| `apply_native_filter` | OpenCLI state | Target is not already selected and target option is visible or reachable | One click/fill/confirm sequence | Selected state or section summary verifies target | `liepin_opencli_filter_unapplied` |
| `extract_structured_cards` | OpenCLI state plus read-only structured probe | Results are ready | Extract structured cards | Structured card payload validates | `liepin_opencli_results_not_ready` or `liepin_opencli_malformed_state` |
| `open_detail` | OpenCLI state | Card ref or cached detail URL is valid in latest state | Open detail tab/URL | Active tab is detail route or detail pending state | `liepin_opencli_detail_not_opened` |
| `wait_detail_ready` | OpenCLI state/readiness probe | Detail route or pending blank tab exists | Wait/observe detail page | Detail resume state is ready | `liepin_opencli_detail_not_opened` |
| `observe_detail` | OpenCLI state plus read-only structured probe | Detail page is ready | Extract structured detail | Structured detail payload validates before artifact write | `liepin_opencli_detail_not_opened` or `liepin_opencli_malformed_state` |
| `return_to_search_after_capture` | OpenCLI tab/url state | Search page id or cached route exists | Select/restore search page | Search page is active or cached-detail mode is enabled | `liepin_opencli_search_restore_failed` |
| `visible_cards_refreshed_after_return` | OpenCLI state plus read-only structured probe | Search page was restored | Refresh visible card refs | Card payload validates or cached-detail mode continues | `liepin_opencli_results_not_ready` |

Terminal state classifications remain terminal unless a transition explicitly declares a safe local recovery path: `liepin_opencli_terminal_state`, `liepin_opencli_status_unavailable`, `liepin_opencli_host_blocked`, and `liepin_opencli_malformed_state`.

## Data Flow

```text
ReflectionAdviceDraft
-> materialize_reflection_advice()
-> projected ReflectionAdvice.keyword_advice
-> ControllerContext.latest_reflection_keyword_advice
-> ControllerDecision
-> strict validation / repair / narrow projection
-> RoundRetrievalPlan
```

```text
LiepinOpenCliResumeRetriever
-> LiepinSearchWorkflow
-> Liepin state transitions
-> LiepinSiteAdapter page operations
-> structured card/detail payloads
-> mapper / runtime lane / normalizer
```

## Error Handling

- Reflection projection is silent and deterministic because reflection is advisory.
- Controller projection is narrow and traceable; other validation errors still fail.
- Liepin transition failure returns concrete safe reason semantics through the provider envelope.
- Generic transition-runner labels are debug-only and must never be surfaced as provider `safe_reason_code`.
- Toggle filters do not retry by repeating the same click when postcondition is unknown.
- Terminal Liepin states remain terminal: login expired, identity verification, host blocked, unavailable backend, malformed state.

## Testing

Add focused regression tests for:

- reflection materialization drops `secondary_title_anchor` from all keyword advice lists;
- reflection summary is built from projected advice;
- controller round 2+ can recover from a model choosing `secondary_title_anchor` after repair/retry;
- query projection never removes round 1 use of `secondary_title_anchor`;
- Liepin transition runner enforces observe-pre-state/precondition/action/observe-post-state/postcondition order;
- every mutating OpenCLI command in Liepin tests is immediately preceded by `state` or an explicit readiness probe;
- transition failures surface concrete `liepin_opencli_*` reason codes, not generic runner labels;
- `action-trace.json.events` and provider `workflow_steps` use the same canonical snake_case `action_kind` mapping;
- every transition in the reason-code matrix has success, precondition-blocked, action-failed, and postcondition-failed coverage;
- key external-drift branches are covered: stale card ref, status unavailable, terminal state no retry, toggle no repeat, and restore failure followed by cached detail URL mode;
- toggle filters do not blindly click twice;
- city filter verifies current state before opening picker;
- detail capture waits for resume-ready state;
- fullText/rawText/visible_text/normalized_card_text do not reappear in production read or artifact paths.

## Rollout

Ship in two commits or two task groups:

1. Runtime contract fix: reflection projection, prompt correction, controller projection, tests.
2. Liepin state-machine hardening: transition runner, search/filter transitions, detail transitions, tests.

The first task group can be shipped independently to unblock current failed runs.

## Acceptance Criteria

- The latest failure class cannot recur from `secondary_title_anchor` advice after round 1.
- `secondary_title_anchor` remains valid for round 1.
- Reflection UI summary does not display impossible `secondary_title_anchor` advice.
- Liepin search/filter/detail operations are represented as named transitions with explicit latest-state sources, preconditions, actions, postconditions, safe reason codes, and workflow trace events.
- No Liepin browser action can run without an immediately preceding OpenCLI latest-state observation.
- Existing focused runtime and Liepin tests pass.
- Ruff and ty pass on touched files.
- No full-text collection/storage/fallback path is introduced.

## Self-Review

- No placeholder requirements remain.
- The design separates current runtime failure from Liepin long-term hardening.
- The design preserves the existing stable external boundaries: Pydantic AI output, OpenCLI wrapper, Liepin retriever entrypoint, and scoring concurrency.
