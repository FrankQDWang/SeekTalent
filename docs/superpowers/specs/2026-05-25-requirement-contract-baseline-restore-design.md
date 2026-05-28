# Requirement Contract Baseline Restore Design

## Summary

SeekTalent's CLI runtime requirement contract is the first durable data boundary after user input. The canonical input is still text-shaped for the LLM:

```text
job_title + JD + optional notes
```

The canonical output is structured:

```text
RequirementExtractionDraft -> RequirementSheet -> RequirementDigest -> ScoringPolicy
```

After the Job Title hard cut, active core runtime already uses `job_title` instead of `role_title`. The next problem is that Workbench introduced a second requirement data model:

```text
mustHaves / niceToHaves / synonyms / seniorityFilters / exclusions / generatedQueryHints
```

That Workbench-only model is not the CLI runtime contract. It drops meaningful backend fields, forces the UI to display renamed concepts, mutates notes with approved triage text, and seeds a synthetic requirements cache so runtime execution does not naturally consume the same requirement data that the user reviewed.

This slice restores one requirement contract for CLI and Workbench. Workbench may still have a review/approval gate, but the reviewed object must be the actual `RequirementSheet`, not a parallel triage schema.

## Source Of Truth

The baseline is `0.6.2` CLI runtime, adjusted by the completed Job Title hard cut:

- Input truth:
  - `job_title`
  - `jd`
  - `notes`
  - sha256 fields for each input text
- Requirement LLM input:
  - serialized text sections, not JSON
  - `TASK`
  - `JOB TITLE`
  - `JOB DESCRIPTION`
  - `SOURCING NOTES`
- Requirement LLM structured output:
  - `RequirementExtractionDraft`
  - no `role_title`
  - no Workbench-only fields
- Normalized runtime requirement:
  - `RequirementSheet`
- Downstream runtime views:
  - `RequirementDigest`
  - `ScoringPolicy`

## Canonical Requirement Fields

The canonical active requirement contract is:

```text
RequirementExtractionDraft
- title_anchor_terms
- title_anchor_rationale
- jd_query_terms
- notes_query_terms
- role_summary
- must_have_capabilities
- preferred_capabilities
- exclusion_signals
- locations
- school_names
- degree_requirement
- school_type_requirement
- experience_requirement
- gender_requirement
- age_requirement
- company_names
- preferred_locations
- preferred_companies
- preferred_domains
- preferred_backgrounds
- preferred_query_terms
- scoring_rationale

RequirementSheet
- job_title
- title_anchor_terms
- title_anchor_rationale
- role_summary
- must_have_capabilities
- preferred_capabilities
- exclusion_signals
- hard_constraints
  - locations
  - school_names
  - degree_requirement
  - school_type_requirement
  - experience_requirement
  - gender_requirement
  - age_requirement
  - company_names
- preferences
  - preferred_locations
  - preferred_companies
  - preferred_domains
  - preferred_backgrounds
  - preferred_query_terms
- initial_query_term_pool
- scoring_rationale

RequirementDigest
- job_title
- role_summary
- top_must_have_capabilities
- top_preferences
- hard_constraint_summary

ScoringPolicy
- job_title
- role_summary
- must_have_capabilities
- preferred_capabilities
- exclusion_signals
- hard_constraints
- preferences
- scoring_rationale
```

## Current Code Facts

Core requirement extraction now lives in:

- `src/seektalent/models.py`
- `src/seektalent/requirements/extractor.py`
- `src/seektalent/requirements/normalization.py`
- `src/seektalent/prompts/requirements.md`
- `src/seektalent/runtime/requirements_runtime.py`

Workbench currently has a separate requirement gate in:

- `src/seektalent_ui/workbench_store.py`
- `src/seektalent_ui/models.py`
- `src/seektalent_ui/workbench_routes.py`
- `src/seektalent_ui/job_runner.py`
- `src/seektalent_ui/runtime_bridge.py`
- `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`
- `apps/web-svelte/src/lib/components/RequirementTriageGate.svelte`
- `apps/web-svelte/src/lib/workbench/recruiterAnimation.ts`
- `apps/web-svelte/src/lib/workbench/types.ts`
- `apps/web-svelte/src/lib/api/workbench.ts`

The active drift is:

- `WorkbenchRequirementTriage` stores `must_haves`, `nice_to_haves`, `synonyms`, `seniority_filters`, `exclusions`, and `generated_query_hints`.
- The API exposes `mustHaves`, `niceToHaves`, `synonyms`, `seniorityFilters`, `exclusions`, and `generatedQueryHints`.
- The Svelte UI displays these as `必须条件`, `加分条件`, `同义词`, `资历过滤`, `排除项`, and `检索提示`.
- `src/seektalent_ui/runtime_bridge.py` appends approved triage into notes through `_notes_with_triage`.
- `src/seektalent_ui/runtime_bridge.py` fabricates a `RequirementExtractionDraft` from approved triage in `_requirement_draft_from_approved_triage`.
- Runtime execution is made to reuse that fabricated draft by `_seed_approved_requirement_cache`.

Those are not acceptable as the long-term data flow. They are a second requirement system.

## Goals

- Make `RequirementSheet` the single reviewed requirement object for Workbench.
- Keep CLI and Workbench on the same requirement extraction contract.
- Keep LLM input serialized as text.
- Keep LLM output structured.
- Remove Workbench-only requirement fields from active API, store, frontend state, and runtime bridge.
- Stop mutating `notes` with approved requirement data.
- Stop seeding synthetic requirement cache payloads from Workbench triage.
- Let Workbench runtime execution consume an approved `RequirementSheet` directly.
- Show meaningful backend requirement fields in the UI instead of only a reduced triage subset.

## Non-Goals

- Do not change scoring semantics.
- Do not change controller/reflection/finalizer logic except where they need to accept an already-approved `RequirementSheet`.
- Do not change Liepin browser automation, risk-control handling, source budgets, or detail-open policy.
- Do not delete old React UI in this slice.
- Do not fix final-candidate display fields in this slice.
- Do not introduce compatibility aliases for the old Workbench triage field names.
- Do not preserve old local Workbench triage rows. This is a local active-development product; old review rows may be discarded by the schema change.

## Target Architecture

### CLI Flow

CLI remains:

```text
job_title + jd + notes
-> RequirementExtractor
-> RequirementExtractionDraft
-> normalize_requirement_draft()
-> RequirementSheet
-> ScoringPolicy
-> runtime
```

### Workbench Prepare Flow

Workbench prepare becomes:

```text
session.job_title + session.jd_text + session.notes
-> runtime.extract_requirements()
-> RequirementSheet
-> session_requirement_reviews.requirement_sheet_json
-> UI requirement review panel
```

### Workbench Approval Flow

The user reviews and optionally edits the actual `RequirementSheet` fields. Approval stores the full requirement sheet. There is no separate triage model.

```text
RequirementSheet draft
-> user review
-> RequirementSheet approved
```

### Workbench Runtime Flow

Workbench run becomes:

```text
session.job_title + session.jd_text + original session.notes
+ approved RequirementSheet
-> WorkflowRuntime.run(job_title="AI Agent Engineer", jd="Build LangGraph systems.", notes="Prefer evaluation experience.", approved_requirement_sheet=RequirementSheet)
-> same runtime downstream as CLI
```

Runtime must not append approved requirements into notes, and must not read a fabricated requirements cache entry.
Workbench source-lane and detail-open helpers that need query terms must derive them from the approved `RequirementSheet`, not from a separate triage model. Candidate-card and detail projections that record matched requirement evidence must use `must_have_capabilities` and `preferred_capabilities` from the same approved sheet.

### API Shape

The active Workbench requirement API should expose the real requirement object. Field names may remain Python-style snake_case to preserve the core contract exactly:

```json
{
  "session_id": "session_1234567890abcdef",
  "status": "draft",
  "requirement_sheet": {
    "job_title": "AI Agent Engineer",
    "title_anchor_terms": ["AI Agent"],
    "title_anchor_rationale": "AI Agent is the stable searchable title anchor.",
    "role_summary": "Build agent workflow and retrieval systems.",
    "must_have_capabilities": ["LangGraph"],
    "preferred_capabilities": ["RAG"],
    "exclusion_signals": ["pure frontend"],
    "hard_constraints": {},
    "preferences": {},
    "initial_query_term_pool": [],
    "scoring_rationale": "Prioritize agent workflow depth and retrieval evidence."
  },
  "created_at": "2026-05-25T10:00:00+08:00",
  "updated_at": "2026-05-25T10:00:00+08:00",
  "approved_at": null
}
```

The update request should accept the same `requirement_sheet` object. The API must not accept `mustHaves`, `niceToHaves`, `synonyms`, `seniorityFilters`, or `generatedQueryHints`.

The Workbench session response must expose this review as `requirement_review`; it must not keep `requirementTriage` as an active response field.

## Required Behavior

### Requirement Extraction

- The requirements prompt continues to ask for `RequirementExtractionDraft`.
- The rendered user prompt continues to serialize `job_title`, `JD`, and `notes` as text sections.
- `RequirementExtractionDraft` remains structured output.
- `RequirementSheet` remains the normalized runtime contract.

### Workbench Review

- Empty/new sessions may have no requirement sheet yet.
- Preparing requirements stores the extracted `RequirementSheet`.
- The UI displays the requirement sheet fields that affect downstream behavior:
  - job title
  - role summary
  - title anchors and rationale
  - must-have capabilities
  - preferred capabilities
  - exclusion signals
  - hard constraints
  - preferences
  - initial query term pool
  - scoring rationale
- The UI editing model edits `RequirementSheet`, not a reduced triage model.
- Approval is blocked if there is no valid requirement sheet.
- Strategy graph, requirement highlights, source controls, node detail panels, and running notes must read from `requirement_review.requirement_sheet` and must not project it back into triage-shaped fields.

### Runtime Execution With Approved Requirements

- `WorkflowRuntime.run()` and `run_async()` should accept an optional approved `RequirementSheet`.
- If an approved sheet is provided:
  - validate `approved_requirement_sheet.job_title == input_truth.job_title`
  - skip live requirement extraction
  - do not call `RequirementExtractor.extract_with_draft`
  - build `ScoringPolicy` from the approved sheet
  - seed `RetrievalState.query_term_pool` from `approved_requirement_sheet.initial_query_term_pool`
  - write `input.requirement_sheet` and `input.scoring_policy`
  - write a requirements call artifact that records `source="approved_requirement_sheet"`
- CLI calls do not pass approved requirements and keep the current extraction behavior.

### Removed Active Runtime Behavior

Remove these active Workbench-runtime behaviors:

- `_notes_with_triage`
- `_seed_approved_requirement_cache`
- `_requirement_draft_from_approved_triage`
- storing `must_haves_json`, `nice_to_haves_json`, `synonyms_json`, `seniority_filters_json`, `exclusions_json`, `generated_query_hints_json` as the active review contract

## Acceptance Criteria

- `rg -n "mustHaves|niceToHaves|synonyms|seniorityFilters|generatedQueryHints" src/seektalent_ui apps/web-svelte/src` has no active Workbench requirement contract matches.
- `rg -n "must_haves|nice_to_haves|seniority_filters|generated_query_hints" src/seektalent_ui apps/web-svelte/src` has no active Workbench requirement contract matches.
- `rg -n "_notes_with_triage|_seed_approved_requirement_cache|_requirement_draft_from_approved_triage" src/seektalent_ui` returns no active matches.
- Workbench requirement API response contains `requirement_sheet`.
- Workbench requirement API response does not contain `mustHaves`, `niceToHaves`, `synonyms`, `seniorityFilters`, or `generatedQueryHints`.
- Workbench session API response contains `requirement_review` and does not contain `requirementTriage`.
- Workbench prepare stores a `RequirementSheet` generated by the same runtime requirement extraction path as CLI.
- Workbench run with an approved requirement sheet does not call requirement extraction again.
- Workbench run passes original `session.notes` to runtime; approved requirement data is not appended to notes.
- Workbench source lanes and detail-open lanes use query terms derived from `RequirementSheet`.
- UI shows `role_summary`, `title_anchor_terms`, `must_have_capabilities`, `preferred_capabilities`, `exclusion_signals`, `hard_constraints`, `preferences`, `initial_query_term_pool`, and `scoring_rationale`.
- CLI requirement extraction tests still pass.
- Focused tests pass:

```bash
uv run pytest tests/test_requirement_extraction.py
uv run pytest tests/test_runtime_state_flow.py -k "requirement"
uv run pytest tests/test_runtime_audit.py -k "requirements"
uv run pytest tests/test_workbench_semantic_guardrails.py
uv run pytest tests/test_workbench_runtime_owned_execution.py
uv run pytest tests/test_workbench_api.py -k "requirement"
cd apps/web-svelte && bun test -- --run Requirement
```

## Out Of Scope Follow-Ups

- Liepin risk-control hard stop and global source budget.
- Final candidate UI parity for `why_selected`, `weaknesses`, `risk_flags`, and matched signals.
- Old React UI deletion.
- Legacy `/api/runs` deletion.
- Full Workbench route naming cleanup if the implementation keeps `/triage` route paths temporarily while changing payload shape. Payload shape is mandatory in this slice; route naming can be cleaned later only if changing it would balloon this slice.
