# Workbench Runtime Contract Alignment And Cleanup Design

## Summary

PR #5 makes the active Runtime own source intake, normalization, deterministic dedupe, scoring, reflection, and finalization. The next stacked PR must close the boundary between that Runtime contract and Workbench/UI projection.

The target data flow is:

```text
job_title + JD + notes
-> Runtime requirement extraction
-> approved RequirementSheet
-> one Runtime sourcing job for selected sources
-> CTS adapter + Liepin PI adapter
-> Runtime canonical intake, dedupe, scoring, reflection
-> Runtime FinalResult
-> Workbench final-top10 API as the UI contract
-> Svelte UI renders only that contract plus action/review state
```

Workbench may persist and render Runtime state, but it must not create a second backend execution flow or derive final candidate business fields through review-item side channels. Old React UI and old one-run APIs should be removed from active product code once the Svelte Workbench is the only UI surface.

## Current Code Facts

- `src/seektalent_ui/runtime_bridge.py::extract_requirement_review(...)` calls Runtime requirement extraction with `job_title`, `jd`, and `notes`.
- `src/seektalent_ui/runtime_bridge.py::run_runtime_sourcing_job(...)` calls `WorkflowRuntime.run(...)` once with selected `source_kinds`, `approved_requirement_sheet`, and optional Liepin context.
- `src/seektalent_ui/workbench_routes.py::start_session_source_runs(...)` enqueues `start_runtime_sourcing_job(...)` for the primary Workbench run.
- `tests/test_workbench_runtime_owned_execution.py::test_starting_dual_source_session_does_not_enqueue_primary_source_run_jobs` verifies the primary Workbench run creates no `source_run_jobs`.
- Runtime finalizer materializes `FinalCandidate` fields including:
  - `final_score`
  - `fit_bucket`
  - `match_summary`
  - `strengths`
  - `weaknesses`
  - `matched_must_haves`
  - `matched_preferences`
  - `risk_flags`
  - `why_selected`
  - `source_round`
- Workbench final persistence currently stores many of those fields in `candidate_evidence`, while `/api/workbench/sessions/{session_id}/final-top10` returns only a smaller `WorkbenchFinalTopCandidateResponse`.
- Svelte `buildFinalCandidateCards(...)` currently reads final ranking/display fields from `final-top10`, then joins `reviewItems` to recover `matchedMustHaves`, `matchedPreferences`, `missingRisks`, and `strengths`.
- `apps/web-svelte/src/lib/components/CandidateReviewCard.svelte` currently does not render `whySelected`, `matchedPreferences`, `weaknesses`, or `sourceRound`. Passing those fields through the view model is not enough; final cards must visibly show them.
- `src/seektalent_ui/workbench_note_writer.py` constructs running-note context inside the Workbench layer from session/source-run rows and recent events. It reads Runtime events, but Runtime does not own the safe running-note fact projection.
- Legacy active code still exists:
  - old `/api/runs` routes in `src/seektalent_ui/server.py`
  - old `RunRegistry` and `create_server(...)` execution surfaces in `src/seektalent_ui/server.py`
  - old response models in `src/seektalent_ui/models.py`
  - old mapper in `src/seektalent_ui/mapper.py`
  - old `/api/runs` generated OpenAPI entries in `apps/web-svelte/src/lib/api/schema.d.ts`
  - old source-run worker code in `src/seektalent_ui/job_runner.py`
  - old source-run execution helpers in `src/seektalent_ui/runtime_bridge.py` and `src/seektalent_ui/workbench_store.py`
  - old source-run startup/reconcile calls and active tests that still exercise `source_run_jobs`
  - old `/api/runs` Liepin scoped tests that should either move to `/api/liepin` connection/compliance routes or be deleted with the legacy run API
  - old React app in `apps/web`
- `docs/ui.md`, `docs/cli.md`, `docs/architecture.md`, `docs/architecture-dependencies.md`, and `src/seektalent/cli.py` still reference `apps/web`, deleted tests, `RunRegistry`, or `seektalent_ui.mapper` as active/default surfaces.
- `docs/v-0.2/**` is historical archive material. It may keep versioned references, but active cleanup scans must explicitly exclude it so historical text does not mask active-runtime cleanup failures.

## Goals

- Make `final-top10` the canonical UI-facing Runtime final candidate contract.
- Expose every useful Runtime final candidate field directly from `WorkbenchFinalTopCandidateResponse`:
  - `whySelected`
  - `riskFlags`
  - `matchedMustHaves`
  - `matchedPreferences`
  - `strengths`
  - `weaknesses`
  - `sourceRound`
- Make the Svelte final candidate card visibly render those fields. The UI must show why the candidate was selected, hard matches, preference matches, strengths, weaknesses, risks, and source round when present.
- Keep review items only for user review/action state:
  - note
  - status
  - graph candidate expansion
  - detail-open request action
  - provider open action
- Move safe running-note fact extraction to Runtime-owned code. Workbench may provide session identity, previous visible notes, and durable events, but the business facts derived from Runtime progress/public events must be produced by a Runtime module.
- Remove the old primary source-run backend execution path from active Workbench code.
- Remove the old one-run `/api/runs` UI API, `RunRegistry`, `create_server(...)`, generated schema entries, and mapper from active code. Liepin connection/compliance routes remain under `/api/liepin`; legacy run-level `/api/runs` surfaces are not part of the Runtime Workbench contract.
- Delete the old React `apps/web` UI after Svelte is confirmed as the only active frontend surface.
- Update active docs and CLI metadata so they no longer point users to `apps/web`.
- Keep Liepin detail-open execution after finalization, because it is a user-approved action path, not the primary retrieval path.

## Non-Goals

- Do not change Runtime retrieval, normalization, dedupe, scoring, reflection, or finalizer behavior from PR #5.
- Do not fix PR #5 typecheck issues in this PR.
- Do not redesign Svelte visual layout.
- Do not remove historical docs under `docs/superpowers/**`.
- Do not remove historical docs under `docs/v-0.2/**`; exclude them from active cleanup scans instead.
- Do not delete Workbench `source_runs` rows; they remain status projections for selected source cards.
- Do not delete Liepin detail-open lease, approval, ledger, or provider-open action flows.
- Do not add compatibility aliases for old final candidate fields.

## Product Contract

### Requirement Review

`RequirementSheet` is the only active requirement review contract. Active UI must display and edit Runtime fields such as `job_title`, `role_summary`, `must_have_capabilities`, `preferred_capabilities`, `hard_constraints`, `preferences`, and `initial_query_term_pool`.

Old `mustHaves`, `niceToHaves`, `synonyms`, `generatedQueryHints`, or equivalent triage-only contracts must not be active UI/backend contracts.

### Runtime Sourcing

Starting a Workbench session creates one session-level Runtime sourcing job. It does not enqueue per-source CTS or Liepin primary execution jobs. CTS and Liepin remain selected source status projections, while Runtime owns the round loop and adapter dispatch.

The only active primary execution bridge is:

```text
Workbench start route
-> WorkbenchStore.start_runtime_sourcing_job
-> WorkbenchJobRunner runtime worker
-> runtime_bridge.run_runtime_sourcing_job
-> WorkflowRuntime.run
```

### Final Top 10

`/api/workbench/sessions/{session_id}/final-top10` is the only UI-facing final shortlist contract.

For Runtime-finalized sessions, each response item must be built from Runtime finalization order plus Runtime final candidate fields. It must not require the frontend to join `/candidate-review` results to recover scoring explanation, matching, or risk data.

The response shape must include:

```json
{
  "reviewItemId": "review_...",
  "runtimeIdentityId": "identity_...",
  "canonicalReviewItemId": "review_...",
  "mergedReviewItemIds": ["review_..."],
  "rank": 1,
  "displayName": "Candidate",
  "title": "Senior Engineer",
  "company": "Example",
  "location": "Shanghai",
  "summary": "Runtime match summary.",
  "aggregateScore": 91,
  "fitBucket": "fit",
  "whySelected": "Why this candidate is selected.",
  "riskFlags": ["risk text"],
  "matchedMustHaves": ["must-have evidence"],
  "matchedPreferences": ["preference evidence"],
  "strengths": ["strength"],
  "weaknesses": ["weakness"],
  "sourceRound": 1,
  "sourceBadges": ["CTS final", "Liepin detail", "Multiple sources"],
  "evidenceLevel": "detail",
  "sourceEvidence": []
}
```

The frontend may still use review items for:

- current review status
- user note
- resume expansion ref
- detail request target
- provider action target

The frontend must not use review items as the source for final candidate business explanation fields.

### Running Notes

Runtime owns conversion from Runtime events into safe business facts. Workbench owns note persistence, note-writer leases, and visible event rows.

Allowed dependency direction:

```text
seektalent_ui.workbench_note_writer
-> seektalent.runtime.public_notes
```

Disallowed dependency direction:

```text
seektalent.runtime.*
-> seektalent_ui.*
```

Running-note context must include safe Runtime facts for:

- current stage
- round number
- selected source count
- per-source completion/block/failure status
- safe source reason category
- raw returned count when public
- identity/merge count when public
- top-pool count when public
- finalization revision and reason code

The note-writer prompt must not receive raw provider payloads, browser/OpenCLI internals, artifact paths, runtime run IDs, candidate hashes, cookies, tokens, or full raw resumes.

### Cleanup

Active code after this PR should not expose:

- `/api/runs`
- `RunRegistry`
- `create_server`
- `RunCreateRequest`
- `RunCreateResponse`
- `RunStatusResponse`
- `AgentShortlistCandidate`
- `CandidateDetailResponse`
- `LiepinRunStatusResponse`
- `LiepinRunResultsResponse`
- `seektalent_ui.mapper`
- `apps/web`
- primary `run_cts_source_run`
- primary `run_liepin_card_source_run`
- primary `source_run_jobs` execution workers

It is acceptable for database migration/maintenance code to keep historical table support during this PR if removing the table would make existing local data unrecoverable. Such code must not be an active execution path.

## Acceptance Criteria

- `final-top10` response contains direct Runtime final fields.
- Svelte final candidate cards visibly render `final-top10` fields for `whySelected`, risks, matched must-haves/preferences, strengths, weaknesses, and source round.
- Typed Svelte test fixtures and e2e mock final-top10 payloads include every required `WorkbenchFinalTopCandidateResponse` field.
- Svelte final candidate cards use joined review items only for actions/status/notes/resume refs.
- Running-note business facts are generated by a Runtime module and consumed by Workbench.
- Active Workbench primary run path contains exactly one Runtime sourcing job path.
- `rg -n "run_cts_source_run|run_liepin_card_source_run|start_source_run_job|claim_next_source_run_job" src/seektalent_ui` returns no active execution definitions or imports after cleanup.
- `rg -n "/api/runs|RunRegistry|create_server|RunCreateRequest|RunCreateResponse|RunStatusResponse|AgentShortlistCandidate|CandidateDetailResponse|LiepinRunStatusResponse|LiepinRunResultsResponse|seektalent_ui.mapper" src tests apps scripts docs --glob '!docs/superpowers/**' --glob '!docs/v-0.2/**'` returns no active references.
- `test -d apps/web` fails.
- `scripts/start-dev-workbench.sh` and active docs point to `apps/web-svelte`.
- Backend, typecheck, `bun run check`, `bun run lint`, `bun run test`, `bun run build`, `bun run test:e2e`, and `./scripts/verify-dev-workbench.sh` pass.
