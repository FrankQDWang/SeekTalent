# Job Title Contract Hard Cut Design

## Summary

SeekTalent's CLI runtime contract uses `job_title` as the user's explicit role-title input. The active runtime still carries an older `role_title` field through requirement extraction, requirement sheets, scoring policy, controller/scoring/reflection prompts, diagnostics, provider policies, and candidate-feedback helpers. That makes the data contract look like there are two role title truths even though normalization already overwrites `role_title` with `job_title`.

This design hard-cuts the active runtime to one field name: `job_title`.

The change is intentionally narrow. It does not merge Workbench triage, remove old UI, change Liepin source execution, or redesign scoring. It removes the `role_title` contract from active runtime schemas and code paths so later data-flow consolidation starts from one canonical title field.

## Source Of Truth

The canonical baseline is the `0.6.2` CLI runtime data flow:

```text
job_title + jd + notes
-> requirements LLM text prompt
-> RequirementExtractionDraft structured output
-> RequirementSheet
-> ScoringPolicy
-> controller / retrieval / scoring / reflection / finalizer
-> FinalResult
```

In the hard-cut target state:

- `job_title` is the only role-title field in active runtime schemas.
- `RequirementExtractionDraft` does not contain `role_title`.
- `RequirementSheet` contains `job_title`.
- `RequirementDigest` contains `job_title`.
- `ScoringPolicy` contains `job_title`.
- Runtime artifacts, diagnostics, prompt summaries, query fingerprints, and provider/card policies use `job_title` where they mean the user's job title.
- Requirements prompt text does not ask the LLM to output `role_title`.
- No active runtime code provides `role_title` aliases, properties, validators, fallback readers, or compatibility fields.

## Current Code Facts

As of this plan, `rg -l "role_title" src/seektalent` returns these active files:

```text
src/seektalent/candidate_feedback/llm_prf.py
src/seektalent/candidate_feedback/llm_prf_bakeoff.py
src/seektalent/candidate_feedback/model_steps.py
src/seektalent/controller/react_controller.py
src/seektalent/models.py
src/seektalent/prompts/requirements.md
src/seektalent/providers/cts/filter_projection.py
src/seektalent/providers/liepin/card_policy.py
src/seektalent/providers/liepin/runtime_lane.py
src/seektalent/reflection/critic.py
src/seektalent/requirements/normalization.py
src/seektalent/retrieval/query_identity.py
src/seektalent/runtime/orchestrator.py
src/seektalent/runtime/requirements_runtime.py
src/seektalent/runtime/runtime_diagnostics.py
src/seektalent/runtime/source_filters.py
src/seektalent/scoring/scorer.py
```

`src/seektalent_ui/runtime_bridge.py` also constructs `RequirementExtractionDraft(role_title=...)`; that direct core-model constructor must be updated as part of this hard cut so Workbench still imports and runs. This does not authorize Workbench requirement-flow consolidation in this slice.

## Goals

- Make `job_title` the only active runtime title field.
- Remove `role_title` from requirement LLM output schema.
- Remove `role_title` from active runtime artifacts and prompt payloads.
- Bump the requirements cache schema key so cached `role_title` payloads are not reused.
- Keep source execution behavior unchanged except for field naming where the value is the job title.
- Keep Workbench triage, UI cleanup, and final-candidate display fixes out of this slice.

## Non-Goals

- Do not add compatibility aliases or properties.
- Do not support old requirement-cache payloads that contain `role_title`.
- Do not migrate historical run artifacts.
- Do not delete old UI.
- Do not merge Workbench triage with CLI runtime.
- Do not change `source_kinds`, CTS execution, Liepin execution, identity merge, scoring semantics, or finalization semantics.
- Do not remove unrelated legacy compatibility such as `title_anchor_term`; that is a separate cleanup slice.

## Required Behavior

### Requirement Extraction

The rendered requirements prompt must still serialize input as text:

```text
TASK
Extract one RequirementExtractionDraft from the job title, JD, and sourcing notes.

JOB TITLE
...

JOB DESCRIPTION
...

SOURCING NOTES
...
```

The `RequirementExtractionDraft` output schema must include requirement content fields and title anchors, but not `role_title`.

Normalization must derive the canonical title only from `InputTruth.job_title`. If the provided `job_title` normalizes to an empty string, normalization fails with a `job_title`-named error.

### Downstream Runtime Context

Downstream contexts must carry `job_title`:

- `RequirementSheet.job_title`
- `RequirementDigest.job_title`
- `ScoringPolicy.job_title`
- query identity fingerprint input key `job_title`
- runtime diagnostics and judge packet requirement payloads
- controller/scoring/reflection/finalize prompt surfaces
- candidate feedback and PRF input structs where the value is the user's job title
- CTS and Liepin source filter/card policy helpers where the value is the user's job title

Runtime call summaries may still include the job title because it is important retrieval context. When they do, they must read it from `InputTruth.job_title` or the LLM call input payload, not from `RequirementExtractionDraft`, because the draft no longer outputs any title field.

Display prompt text should say `Job Title` rather than `Role` when it is referring to this value.

Candidate-feedback PRF input artifacts must bump their schema version when the payload key changes from `role_title` to `job_title`.

### Cache Boundary

The requirements cache key must be bumped from the current schema version to a new version, such as:

```text
requirement_extraction_draft.v3
```

The implementation must not read old `role_title` cached payloads or repair them in active runtime code.

## Acceptance Criteria

- `rg -n "\\brole_title\\b" src/seektalent src/seektalent_ui tests` returns no active matches.
- `rg -n "role_title=|\"role_title\"|'role_title'|matched_role_title|compact_role_title" src/seektalent src/seektalent_ui tests` returns no active constructors, payload keys, reason codes, or test fixtures.
- `RequirementExtractionDraft.model_json_schema()` has no `role_title` property.
- `RequirementSheet.model_json_schema()` has `job_title` and no `role_title`.
- `ScoringPolicy.model_json_schema()` has `job_title` and no `role_title`.
- Requirements prompt text does not contain the old field name.
- Requirements cache key schema version is bumped.
- Runtime LLM call summaries that include the job title read it from input truth, not from requirement draft output.
- Candidate-feedback PRF input schema version is bumped for the `job_title` payload shape.
- Focused tests pass:

```bash
uv run pytest tests/test_requirement_extraction.py
uv run pytest tests/test_controller_contract.py
uv run pytest tests/test_reflection_contract.py
uv run pytest tests/test_runtime_audit.py
uv run pytest tests/test_llm_input_prompts.py
uv run pytest tests/test_runtime_state_flow.py
uv run pytest tests/test_candidate_feedback.py
uv run pytest tests/test_query_identity.py
uv run pytest tests/test_llm_prf.py
uv run pytest tests/test_llm_prf_bakeoff.py
uv run pytest tests/test_liepin_card_policy.py
uv run pytest tests/test_filter_projection.py
uv run pytest tests/test_runtime_source_adapter_boundary.py
uv run pytest tests/test_workbench_api.py
```

## Out Of Scope Follow-Ups

- Workbench requirement triage and cache seeding removal.
- Svelte final-candidate display of `why_selected`, `risk_flags`, `weaknesses`, and matched fields.
- Old React UI deletion.
- Legacy `/api/runs` deletion.
- Repository-wide compatibility cleanup beyond `role_title`.
