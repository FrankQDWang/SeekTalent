# Liepin Card Evidence And Workflow Boundary Design

## Status

Approved for design by the user on 2026-07-03. This document captures the B option: a focused Liepin provider boundary correction, not a generic workflow engine.

## Problem

The Liepin detail path is now structured-first, but the search result list still has a weaker boundary. The list page is the Liepin search results page, and each row on that page is a candidate card. Current card extraction already produces a limited `safe_card_summary`, but it still starts from visible card text and carries `visible_text` / `normalized_card_text` through parts of the card policy and generic `search_text` path.

At the same time, `LiepinSiteAdapter` owns too much behavior. It contains both Liepin page capabilities and the detail-backed search workflow: observe cards, cache detail URLs, select candidates, open detail pages, capture detail payloads, return to the list, refresh cards, and emit workflow events. That makes it harder to strengthen card evidence without also touching orchestration logic.

The target boundary is:

```text
OpenCliBrowserAutomation
  generic browser primitives only

LiepinSiteAdapter
  Liepin page capabilities and DOM extraction only

LiepinSearchWorkflow
  Liepin search orchestration and workflow events

LiepinOpenCliResumeRetriever
  stable worker/client entry point
```

## Goals

1. Keep OpenCLI decoupled from Liepin. OpenCLI remains a generic browser capability and does not expose Liepin-specific card or resume APIs.
2. Keep Liepin decoupled from OpenCLI internals. Liepin code calls stable browser methods through the existing wrapper boundary.
3. Move Liepin detail-backed search orchestration out of `LiepinSiteAdapter` into a dedicated workflow module.
4. Upgrade Liepin list/card extraction to structured card evidence instead of source-visible text as the main evidence.
5. Stop exposing or persisting raw card visible text, raw HTML, whole-page text, `fullText`, `rawText`, `visible_text`, or `normalized_card_text` as Liepin card evidence.
6. Preserve the existing detail-backed search behavior: parallel scoring is unchanged, the worker/client API remains stable, and detail pages remain the scoring source when details are available.
7. Preserve useful UI workflow steps, but make their production the workflow's responsibility instead of the site adapter's responsibility.

## Non-Goals

1. Do not build a cross-site workflow engine.
2. Do not redesign scoring concurrency, batching, ranking, or LLM prompt behavior in this slice.
3. Do not remove detail-backed search or switch scoring to card-only evidence.
4. Do not add Liepin-specific methods to `OpenCliBrowserAutomation`.
5. Do not keep raw card text in protected debug artifacts as a migration tail.
6. Do not remove source-owned structured free-text fields such as a work experience summary or project description from detail payloads.

## Current State

`OpenCliBrowserAutomation` exposes generic browser methods such as `find_css()` and `readonly_eval()`. That layer is the right place for browser primitives, not source semantics.

`LiepinSiteAdapter.extract_visible_liepin_cards()` currently reads search-result cards, parses text blocks, and returns a mixed payload containing card identity, structured summary fields, and `visible_text`.

`LiepinSiteAdapter.search_liepin_resumes()` currently orchestrates the whole detail-backed run. It submits search, observes visible cards, caches detail URLs, selects candidates, opens detail pages, captures detail payloads, returns to the search page, and refreshes card observations.

`opencli_workflow.py` currently maps safe action events to UI workflow-step payloads. Despite its name, it is not the execution workflow.

## Architecture

### Browser Automation Boundary

`OpenCliBrowserAutomation` stays source-neutral:

```text
status()
get_url()
find()
fill()
click()
click_ref()
scroll()
wait_time()
find_css()
readonly_eval()
```

It must not know about Liepin selectors, card fields, detail pages, resume schemas, or workflow steps.

### Liepin Site Adapter

`LiepinSiteAdapter` owns Liepin page semantics and DOM extraction:

```text
ensure_search_page()
apply_liepin_filters(...)
submit_liepin_search(...)
extract_structured_liepin_cards(...)
detail_url_for_card(...)
open_liepin_detail(...)
extract_liepin_detail_payload(...)
restore_liepin_search_page(...)
```

The adapter may use `find_css()` and `readonly_eval()` internally, but it returns source-owned structured observations. It should not own the full loop over target resumes.

A thin compatibility method may remain temporarily if needed by existing callers, but the orchestration logic must move to `LiepinSearchWorkflow`.

### Liepin Search Workflow

Introduce a focused workflow module, for example `liepin_search_workflow.py`, responsible for orchestration:

```text
LiepinSearchWorkflow.search_detail_backed_resumes(request) -> dict envelope
```

The workflow owns:

1. search start and completion events;
2. native filter application sequencing;
3. card observation;
4. detail URL caching;
5. candidate selection order;
6. detail open/capture loop;
7. partial failure handling;
8. return-to-list and refresh behavior;
9. final envelope status;
10. action events later projected to UI workflow steps.

The workflow calls adapter capabilities. It does not call OpenCLI primitives directly.

### Retriever Boundary

`LiepinOpenCliResumeRetriever` remains the stable worker/client entry point. It should depend on a runner/workflow protocol that exposes:

```text
status()
recover_connection()
search_detail_backed_resumes(...)
```

The retriever continues to turn the source envelope into `LiepinResumeSearchResponse`. It should not know DOM selectors, card parsing scripts, or page restoration details.

### Workflow Step Projection

Keep safe UI workflow-step projection separate from execution. The current event-to-step mapping can remain in `opencli_workflow.py` for the first slice, or be renamed later to `workflow_steps.py` if that reduces confusion.

The key rule is that the workflow emits safe action events, and the projection module turns those events into public step payloads. The site adapter should not be the owner of that lifecycle.

## Structured Card Evidence

Replace the card/list evidence payload with an explicit structured model. Suggested shape:

```text
LiepinStructuredCardEvidence
  provider_rank: int
  ref: str
  masked_name: bool
  gender: str | None
  age: int | None
  work_years: int | None
  city: str | None
  expected_city: str | None
  education_level: str | None
  current_or_recent_company: str | None
  current_or_recent_title: str | None
  job_intention: str | None
  active_status: str | None
  badges: tuple[str, ...]
  skill_tags: tuple[str, ...]
  experience_preview: tuple[LiepinCardExperiencePreview, ...]
  education_preview: tuple[LiepinCardEducationPreview, ...]
```

```text
LiepinCardExperiencePreview
  company: str | None
  title: str | None
  date_range: str | None
  duration: str | None
  is_current: bool | None
```

```text
LiepinCardEducationPreview
  school: str | None
  major: str | None
  degree: str | None
  recruitment_type: str | None
  date_range: str | None
```

The evidence must not include:

```text
raw_html
inner_html
inner_text
visible_text
normalized_card_text
fullText
rawText
page_text
whole-page resume text
```

If a generic interface still requires `ResumeCandidate.search_text`, derive it in memory from the structured evidence allowlist. It is a compatibility string, not source evidence, not an artifact, and not a fallback source for LLM scoring.

## Data Flow

### Before

```text
OpenCLI browser primitives
-> LiepinSiteAdapter search_liepin_resumes()
-> extract visible card text
-> parse limited safe_card_summary
-> use visible_text / normalized_card_text in card policy and search_text
-> open detail pages
-> capture structured detail payload
-> build final envelope
```

### After

```text
OpenCLI browser primitives
-> LiepinSiteAdapter page methods
-> structured card evidence from Liepin DOM
-> LiepinSearchWorkflow orchestration
-> open selected detail pages
-> structured detail payload
-> final envelope
-> LiepinOpenCliResumeRetriever maps envelope to worker response
```

The detail payload remains the primary resume evidence for scoring. Card evidence is used for list-stage filtering, dedupe/search compatibility, traceability, and detail-open planning.

## Error Handling

1. Adapter methods return safe reason codes for page or extraction failures.
2. Workflow decides whether to retry, skip a card, continue with partial results, or fail the run.
3. A failed detail open for one card should not automatically fail the whole run if more cards remain.
4. A failed card extraction blocks the run because the workflow cannot safely choose candidates without card evidence.
5. No failure path may fall back to whole-page text or raw visible card blocks.
6. Existing recoverable OpenCLI readiness handling stays at the retriever/runner boundary.

## Testing

Add or update tests for these contracts:

1. `OpenCliBrowserAutomation` has no Liepin-specific API.
2. `LiepinSiteAdapter.extract_structured_liepin_cards()` returns structured card evidence with no `visible_text`, `normalized_card_text`, `raw_html`, `fullText`, or `rawText`.
3. Card evidence extraction captures the fields visible on a Liepin result card: status, demographics where available, job intention, skill tags, recent experience previews, and education previews.
4. Card policy consumes structured evidence fields and does not require `normalized_card_text`.
5. Any compatibility `search_text` for card candidates is derived from structured evidence only.
6. `LiepinSearchWorkflow` owns the detail-backed loop and can be tested with a fake site adapter.
7. `LiepinSiteAdapter` does not contain the target-resume selection loop after the workflow extraction, except for a temporary delegate if needed.
8. Detail payload extraction remains structured and does not reintroduce `fullText`.
9. Existing worker/client response tests still pass.
10. Partial failure tests preserve useful candidates and safe workflow steps.

## Rollout

Implement in narrow commits:

1. Add structured card evidence tests and models.
2. Change Liepin card extraction to produce structured evidence and remove raw card text fields from card payloads.
3. Move card policy and compatibility search text to structured evidence.
4. Extract `LiepinSearchWorkflow` and move the orchestration loop out of `LiepinSiteAdapter`.
5. Wire `LiepinOpenCliResumeRetriever` to the workflow-backed runner.
6. Run Liepin provider, normalization, runtime source lane, and workbench API tests that cover candidate evidence.

## Acceptance Criteria

1. The Liepin card/list path has no persisted or emitted `visible_text` / `normalized_card_text` evidence.
2. The Liepin detail path remains structured and still emits no `fullText` / `rawText`.
3. OpenCLI remains source-neutral.
4. Liepin page extraction and Liepin automation workflow are separate modules with clear responsibilities.
5. Existing detail-backed search behavior remains stable from the worker/client API perspective.
6. UI workflow observations still work from safe workflow events.
7. The implementation does not change scoring parallelism.
8. Tests prove both the structured card evidence contract and the adapter/workflow boundary.
