# Liepin Structured Normalization And FullText Removal Design

## Status

Approved for design by the user on 2026-07-02. This document is the implementation design, not the implementation plan.

## Problem

SeekTalent currently has two data shapes for the same candidate:

1. Workbench UI candidate detail uses structured runtime-control evidence such as `wtsDetail`, `safeDetail`, work experience, project experience, education, skills, and match payloads.
2. Runtime scoring still allows Liepin detail text to flow through `fullText` into `raw_text_excerpt`, then into the scoring prompt as `RAW EXCERPT`.

That split is now harmful. `fullText` comes from a broad DOM container and can include page chrome, duplicated labels, legal/footer text, clipped content, and unrelated text. It also encourages each stage to maintain separate text fallbacks instead of using one structured resume source of truth.

The target product no longer needs the old TUI transcript-driven display fields. The Workbench UI needs structured data, and scoring should use the same source facts as the UI.

## Goals

1. Remove Liepin `fullText` completely: do not collect it, store it, emit it, persist it in artifacts, pass it to UI, or pass it to any LLM.
2. Keep scoring parallelism unchanged. The current `score_candidates_parallel` semaphore plus `asyncio.gather` behavior remains intact.
3. Split normalization by source. CTS and Liepin are different source capabilities and should not share a single raw-payload fallback normalizer.
4. Keep Liepin decoupled from OpenCLI. Liepin code should call a small wrapper interface with stable browser/read methods, not raw OpenCLI primitives directly across the codebase.
5. Keep OpenCLI decoupled from Liepin. OpenCLI should remain a generic browser automation capability and should not know Liepin resume semantics.
6. Preserve `resume_quality_comment` behavior used by Workbench thinking process observation, even if the old stage name `tui_summary` remains during the first implementation slice.
7. Delete TUI-only model output fields where they no longer serve runtime control or UI needs, starting with `reflection_rationale`.

## Non-Goals

1. Do not redesign scoring concurrency, batching, prompt-cache behavior, or retry behavior.
2. Do not rewrite Controller, Scoring, and Reflection into a Codex-style transcript architecture.
3. Do not remove `reflection_summary`; it is still used by the UI and by the next Controller round.
4. Do not remove Controller fields that participate in the control loop, especially `response_to_reflection`.
5. Do not remove scoring explanation fields such as `reasoning_summary`, `strengths`, `weaknesses`, `risk_flags`, or final candidate presentation fields.
6. Do not keep a protected debug copy of Liepin `fullText`. The removal is hard, not a masking policy.

## Design Principles

1. Source adapters own source-specific extraction and normalization.
2. Shared runtime code consumes source-neutral normalized evidence, not provider raw payloads.
3. UI and scoring must draw from the same structured resume facts.
4. Free-form text is allowed only when it is a source-owned structured field, such as a work experience summary or project description. Whole-page resume text is not allowed.
5. Browser automation and provider semantics stay separated by a stable wrapper boundary.

## Current State

Liepin detail extraction currently captures structured fields and `fullText`. Structured fields include candidate identity, status, age, city, education, work years, current title and company, job intention, work experience, project experience, education, skills, and source URL. The problematic field is the broad page text captured from the resume detail root.

Normalization currently uses one shared `normalize_resume(candidate)` path. It reads source-specific fields but still has generic text fallback behavior, including `fullText`, `rawText`, `profile`, and `summary`. The result includes `raw_text_excerpt`, which scoring renders as `RAW EXCERPT`.

Workbench runtime-control already projects candidate detail into structured UI evidence through `candidateProfile`, `safeSummary`, `safeDetail`, and `wtsDetail`. Tests already assert that candidate detail excludes page noise such as source URL fragments, onboarding text, legal declarations, notes, and filing information.

## Architecture

### Source Normalization Registry

Introduce a source-normalization registry alongside the existing source registry pattern:

```text
ResumeNormalizerRegistry
  cts -> CTSResumeNormalizer
  liepin -> LiepinResumeNormalizer
  default/unknown -> explicit error or narrowly scoped legacy fallback
```

`score_round()` continues to build the scoring pool, normalize each candidate once, then call the existing parallel scorer. The only change is how each candidate is normalized:

```text
ResumeCandidate.source_provider/source/raw metadata
-> normalizer_registry.for_candidate(candidate)
-> NormalizedResume
-> build_scoring_context(...)
-> score_candidates_parallel(...)
```

The scoring caller must not branch on Liepin internals. It only dispatches to the registered normalizer.

### Liepin Structured Resume Evidence

Liepin normalization consumes only structured OpenCLI extraction results:

```text
candidate_name / candidateName
activeStatus
jobStatus
gender
age
city
education
workYears
currentTitle
currentCompany
jobIntention
workExperienceList
projectExperienceList
educationList
skills / skillTags / tags / keywords
locations
sourceUrl
safeCardSummary
```

It does not read `fullText` or `rawText`. In the Liepin path, both keys are treated as prohibited whole-page text aliases. If either key appears in a Liepin payload, validation rejects the payload in source-boundary tests and production mapping drops the key before persistence.

The normalized evidence preserves structured timelines instead of flattening everything into one excerpt:

```text
identity fields
current role fields
locations
education summary
skills
recent work experience entries
project experience entries
education experience entries
job intention
source/evidence metadata
```

Add a source-neutral `StructuredResumeEvidence` model and attach it to `NormalizedResume`:

```text
StructuredResumeEvidence
  identity
  current_role
  status
  job_intention
  work_experience[]
  project_experience[]
  education_experience[]
  skills[]
  source_metadata
```

The UI/runtime can consume `StructuredResumeEvidence`, but scoring must not consume that full model directly. Scoring uses a separate `StructuredScoringEvidence` allowlist derived from structured resume evidence:

```text
StructuredScoringEvidence
  current_role
  job_intention
  work_experience[]
  project_experience[]
  skills[]
```

`StructuredScoringEvidence` must exclude identity, candidate name, age, gender, source URL, source metadata, education school/degree/major, and any other field whose primary value is trace/display rather than job-fit evidence. The scorer must also remove candidate identity and education summary from non-JSON prompt sections such as `RESUME CARD`; otherwise the allowlist does not protect the final prompt. This preserves the existing protected-attribute boundary while removing the raw text excerpt.

Scoring renders `STRUCTURED_RESUME_EVIDENCE` from `StructuredScoringEvidence`. `raw_text_excerpt` is removed from the scoring contract only after scoring, candidate feedback, runtime-control, and tests no longer consume it. New Liepin normalized resume artifacts must not contain `fullText`, `rawText`, or a raw page excerpt derived from either field.

### CTS Normalization

CTS keeps its own normalizer. It may use CTS-native response fields and existing CTS response models, but should no longer depend on Liepin-safe-card conventions. Any CTS text fallback should be explicit to CTS and bounded by CTS semantics, not shared as a global fallback rule.

### OpenCLI Wrapper Boundary

Define a small provider-agnostic browser client wrapper used by source adapters:

```text
BrowserAutomationClient
  status()
  get_url()
  find(...)
  click(...)
  click_ref(...)
  fill(...)
  scroll(...)
  wait_time(...)
  find_css(...)
  readonly_eval(...)
  run_browser_command(...)
  open_tab(...)
  close_blank_window()
  count_windows()
```

Liepin code should depend on this wrapper protocol, not on broad OpenCLI internals. The wrapper can be backed by OpenCLI today, but its methods are the stable surface. Liepin-owned recovery stays in `LiepinSiteAdapter` because it opens `LIEPIN_RECRUITER_SEARCH_URL`; do not put `recover_connection()` or Liepin-specific pacing actions into the generic browser protocol.

OpenCLI remains generic. It should not expose Liepin-specific methods like `extract_liepin_resume_detail`. Liepin owns selectors, parsing scripts, source-specific safe payload validation, and mapping from browser observations to Liepin worker contracts.

### Liepin Adapter Boundary

Liepin provider modules should present source-level methods to runtime:

```text
LiepinSourceClient
  search_detail_backed_resumes(request) -> LiepinResumeSearchResponse
  fetch_candidate_details(request) -> LiepinDetailResponse
```

Internally, the client may use `BrowserAutomationClient`. Runtime lane code should not know individual OpenCLI commands or DOM selectors. Runtime lane code should work with Liepin request/response contracts and source-neutral runtime lane outputs.

## Data Flow

### Before

```text
OpenCLI DOM read
-> Liepin payload with structured fields + fullText
-> mapper copies fullText/rawText
-> shared normalize_resume()
-> raw_text_excerpt
-> scoring prompt RAW EXCERPT
-> UI separately uses wtsDetail/safeDetail
```

### After

```text
OpenCLI stable browser wrapper
-> Liepin adapter extracts structured detail only
-> Liepin worker/detail contract without fullText/rawText
-> LiepinResumeNormalizer
-> source-neutral structured resume evidence
-> scoring prompt STRUCTURED_RESUME_EVIDENCE
-> runtime-control candidate truth and UI use the same structured facts
```

## Prompt And Schema Changes

### Scoring

Replace scoring prompt `RAW EXCERPT` with a structured scoring evidence block.

The block uses bounded JSON generated from `StructuredScoringEvidence`:

```text
STRUCTURED_RESUME_EVIDENCE
  current_role
  job_intention
  work_experience[]
  project_experience[]
  skills[]
```

The scorer output schema and parallel scoring execution stay unchanged.

### Reflection

Remove `reflection_rationale` completely from the Reflection LLM output schema, materialized runtime model, public runtime events, UI payloads, artifacts, and tests. The reflection prompt should ask for structured keyword advice, filter advice, and stop advice only. The deterministic `reflection_summary` stays as the human-readable UI/controller summary and is the only reflection text field needed by the UI/controller loop.

Keep:

```text
reflection_summary
keyword_advice
filter_advice
suggest_stop
suggested_stop_reason
```

Remove:

```text
reflection_rationale
```

### Resume Quality Observation

Keep the `resume_quality_comment` behavior. It powers the Workbench thinking process observation. Renaming the implementation stage from `tui_summary` is outside this implementation and must not block the fullText removal.

## Artifact And Persistence Rules

1. No artifact should contain Liepin `fullText`.
2. No runtime-control candidate evidence payload should contain Liepin `fullText`.
3. No normalized resume JSON should contain Liepin `fullText` or a raw page excerpt derived from it.
4. No scoring call artifact should contain `RAW EXCERPT` sourced from Liepin whole-page text.
5. No UI API payload should contain Liepin `fullText`.
6. Test fixtures should remove `fullText`, except for explicit negative tests that assert source-boundary rejection or mapper dropping.

## Error Handling

If Liepin structured detail extraction is incomplete:

1. Use available structured fields.
2. Mark missing fields through normalization notes or source evidence completeness.
3. Do not fall back to whole-page text.
4. If required detail fields are missing because the page is not open, blocked, malformed, or login-gated, return a source-specific blocked/error reason through the existing source lane path.

## Testing

Add or update tests for:

1. Liepin parser payload does not include `fullText`.
2. Liepin mapper does not copy `fullText` or `rawText`.
3. Liepin normalizer rejects or ignores `fullText` if it appears in raw payload.
4. Scoring prompt for Liepin contains structured evidence and no `RAW EXCERPT`.
5. Scoring still calls `score_candidates_parallel` and preserves configured concurrency.
6. CTS normalization still works through the CTS normalizer.
7. Registry dispatch selects the correct normalizer for CTS and Liepin.
8. Runtime-control candidate truth and Workbench candidate detail still render structured work/project/education/skills.
9. Public runtime feedback events no longer emit `reflectionRationale` after the field is removed.
10. Existing `resumeQualityComment` observation remains visible in Workbench thinking process.

## Rollout Plan

1. Add safety-gate tests that prove Liepin source payloads, raw snapshots, provider snapshots, normalized resumes, scoring prompts, runtime-control payloads, and UI payloads do not carry whole-page text.
2. Remove `fullText`, `rawText`, and `page_text` at Liepin source boundaries before changing scoring.
3. Add structured resume evidence and source-specific normalizers while temporarily keeping old `raw_text_excerpt` consumers intact.
4. Switch scoring to `StructuredScoringEvidence` and verify protected fields are excluded.
5. Switch candidate feedback and runtime-control to structured evidence.
6. Delete `raw_text_excerpt` only after all consumers have moved.
7. Remove `reflection_rationale` from Reflection prompts, output schemas, materialization, public output paths, artifacts, UI schemas, and tests.
8. Run focused tests for Liepin provider mapping, normalization, scoring prompt construction, runtime-control candidate truth, Workbench runtime service, and reflection contract.

## Acceptance Criteria

1. `rg "fullText|full_text|raw_text|page_text|pageText|resumeText|resume_text|resume_free_text|detailBody|detail_body|raw_text_excerpt|RESUME_RAW_EXCERPT|RAW EXCERPT|reflection_rationale|reflectionRationale" src apps tests scripts tools` returns no production path, except explicit negative tests if kept.
   `rawText` is prohibited in Liepin resume payload and candidate-evidence paths, but runtime-control requirement-review `ReviewItem.raw_text` / `reviewItems[].rawText` is a separate contract and remains out of scope.
2. A Liepin runtime run produces structured candidate detail in UI without relying on whole-page text.
3. Scoring prompts for Liepin contain allowlisted structured scoring evidence and do not include a raw page excerpt, candidate identity, candidate name, age, gender, source URL, or education school/degree/major.
4. Scoring concurrency remains implemented through the existing parallel scorer.
5. CTS and Liepin normalization are dispatched through source-specific normalizers.
6. OpenCLI generic automation code has no Liepin resume semantics.
7. Liepin source code uses a stable browser automation wrapper boundary.
8. Workbench thinking process still shows the per-round resume quality observation.
9. Reflection summary remains available, while TUI-only `reflection_rationale` no longer exists in model output, runtime materialization, public output, artifacts, or UI payloads.
