# Requirement Contract Baseline Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `RequirementSheet` the single requirement review and runtime contract across CLI and Workbench.

**Architecture:** Core CLI requirement extraction stays unchanged: serialized text input into structured `RequirementExtractionDraft`, normalized into `RequirementSheet`. Workbench keeps a review gate but stores and edits the actual `RequirementSheet`; runtime execution receives that approved sheet directly instead of mutating notes or seeding a synthetic requirements cache. The old Workbench-only triage fields are removed from active API, store, frontend state, and runtime bridge.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, SQLite, pytest, Svelte 5, TypeScript, Bun, existing SeekTalent runtime and Workbench modules.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-25-requirement-contract-baseline-restore-design.md`

## Execution Notes

- Do not run CEO review in this slice.
- Do not change Liepin browser automation or source budgets.
- Do not delete old React UI in this slice.
- Do not fix final candidate display in this slice.
- Do not add compatibility readers for old Workbench triage fields.
- Existing local Workbench requirement-review rows may be discarded by the schema change.
- Keep CLI behavior unchanged when no approved requirement sheet is passed.

## File Map

Core runtime approved-requirement path:

- Modify: `src/seektalent/runtime/orchestrator.py`
  - Add optional `approved_requirement_sheet: RequirementSheet | None` to `run()`, `run_async()`, and `_build_run_state()` call sites.
- Modify: `src/seektalent/runtime/requirements_runtime.py`
  - Add optional `approved_requirement_sheet` input to `build_run_state()`.
  - Skip `RequirementExtractor` only when an approved sheet is provided.
  - Write runtime artifacts that identify the approved-sheet source.

Workbench backend requirement review:

- Modify: `src/seektalent_ui/workbench_store.py`
  - Replace `WorkbenchRequirementTriage` active dataclass with a `WorkbenchRequirementReview` that stores `RequirementSheet | None`.
  - Replace `session_requirement_triage` active schema with `session_requirement_reviews(requirement_sheet_json TEXT)`.
  - Replace update/approve/get helpers to read and write full `RequirementSheet`.
- Modify: `src/seektalent_ui/models.py`
  - Replace `WorkbenchRequirementTriage*` API models with `WorkbenchRequirementReview*` models that contain `requirement_sheet`.
- Modify: `src/seektalent_ui/workbench_routes.py`
  - Return and update `requirement_sheet`.
  - Remove old triage payload fields.
- Modify: `src/seektalent_ui/job_runner.py`
  - Store extracted `RequirementSheet` during prepare.
  - Require approved `RequirementSheet` before starting runtime/source jobs.
- Modify: `src/seektalent_ui/runtime_bridge.py`
  - Remove notes mutation and synthetic cache seed.
  - Pass approved `RequirementSheet` into runtime runs.
  - Build source-lane and detail-open query terms from the approved `RequirementSheet`.
- Modify: `src/seektalent_ui/workbench_note_writer.py`
  - Replace triage counts with requirement-sheet counts.
- Modify: `src/seektalent_ui/maintenance.py`
  - Update schema validation for the active Workbench database schema.

Workbench frontend:

- Modify or replace: `apps/web-svelte/src/lib/components/RequirementTriageGate.svelte`
  - Prefer replacing with `RequirementReviewPanel.svelte`.
- Modify or replace: `apps/web-svelte/src/lib/components/RequirementTriagePanel.svelte`
- Modify: `apps/web-svelte/src/lib/components/SourceRunControlPanel.svelte`
- Modify: `apps/web-svelte/src/lib/components/CriteriaHighlights.svelte`
- Modify: `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`
  - Use requirement review state, not triage state.
- Modify: `apps/web-svelte/src/lib/api/workbench.ts`
  - Call the new requirement review endpoints or new payload shape.
- Modify: `apps/web-svelte/src/lib/workbench/types.ts`
  - Replace triage input/types with requirement-sheet types.
- Modify: `apps/web-svelte/src/lib/workbench/recruiterAnimation.ts`
  - Use requirement-sheet counts and labels.
- Modify: `apps/web-svelte/src/lib/workbench/runStory.ts`
  - Use `requirement_review.requirement_sheet` for graph requirement nodes and log text.
- Modify tests and mocks under `apps/web-svelte/src/lib/**` and `apps/web-svelte/tests/e2e/**` that still provide triage-shaped session payloads.
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`

Tests:

- Modify: `tests/test_requirement_extraction.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_runtime_audit.py`
- Modify: `tests/test_workbench_semantic_guardrails.py`
- Modify: `tests/test_workbench_runtime_owned_execution.py`
- Modify: `tests/test_workbench_api.py`
- Add if needed: `tests/test_workbench_requirement_contract.py`
- Add or modify Svelte tests near the changed component.

---

### Task 1: Add Backend Contract Tests For Workbench RequirementSheet Review

**Files:**
- Create: `tests/test_workbench_requirement_contract.py`
- Modify: `tests/test_workbench_semantic_guardrails.py`
- Test: `tests/test_workbench_requirement_contract.py`
- Test: `tests/test_workbench_semantic_guardrails.py`

- [ ] **Step 1: Add a focused Workbench requirement contract test file**

Create `tests/test_workbench_requirement_contract.py` with these helpers and tests:

```python
from __future__ import annotations

import json
from pathlib import Path

from seektalent.models import RequirementSheet
from seektalent_ui.workbench_store import WorkbenchStore

from tests.settings_factory import make_settings

def _sheet(job_title: str = "AI Agent Engineer") -> RequirementSheet:
    return RequirementSheet(
        job_title=job_title,
        title_anchor_terms=["AI Agent"],
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agent workflow and retrieval systems.",
        must_have_capabilities=["LangGraph", "RAG"],
        preferred_capabilities=["evaluation"],
        exclusion_signals=["pure frontend"],
        hard_constraints={},
        preferences={"preferred_query_terms": ["LangGraph", "RAG"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize agent workflow depth and retrieval evidence.",
    )


def _store(tmp_path: Path) -> WorkbenchStore:
    settings = make_settings(workspace_root=str(tmp_path), workbench_enabled=True)
    return WorkbenchStore(settings.resolve_workspace_path(settings.workbench_db_path))


def _user(store: WorkbenchStore):
    user, _created = store.bootstrap_admin(
        email="admin@example.com",
        display_name="Admin",
        password_hash="hash",
    )
    return user


def test_workbench_requirement_review_stores_requirement_sheet(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="AI Agent Engineer",
        jd_text="Build LangGraph and RAG systems.",
        notes="Prefer evaluation experience.",
        source_kinds=["cts"],
    )
    sheet = _sheet()

    review = store.update_requirement_review(
        user=user,
        session_id=session.session_id,
        requirement_sheet=sheet,
    )

    assert review is not None
    assert review.requirement_sheet == sheet
    assert review.requirement_sheet.job_title == "AI Agent Engineer"
    payload = json.dumps(review.requirement_sheet.model_dump(mode="json"))
    assert "must_have_capabilities" in payload
    assert "preferred_capabilities" in payload
    assert "exclusion_signals" in payload
    assert "niceToHaves" not in payload
    assert "generatedQueryHints" not in payload


def test_workbench_requirement_review_rejects_job_title_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="AI Agent Engineer",
        jd_text="Build LangGraph and RAG systems.",
        notes="",
        source_kinds=["cts"],
    )

    try:
        store.update_requirement_review(
            user=user,
            session_id=session.session_id,
            requirement_sheet=_sheet(job_title="Backend Engineer"),
        )
    except ValueError as exc:
        assert str(exc) == "requirement_sheet_job_title_mismatch"
    else:
        raise AssertionError("expected requirement_sheet_job_title_mismatch")
```

- [ ] **Step 2: Update blank approval guardrail expectations**

In `tests/test_workbench_semantic_guardrails.py`, replace tests that approve blank triage with blank requirement-review tests. The expected backend error should become `requirement_review_empty`.

Use this assertion shape:

```python
with pytest.raises(PermissionError) as exc_info:
    store.approve_requirement_review(user=user, session_id=session.session_id)

assert str(exc_info.value) == "requirement_review_empty"
```

For HTTP approval:

```python
blank = client.post(
    f"/api/workbench/sessions/{session.session_id}/requirements/approve",
    headers=csrf_headers,
)
assert blank.status_code == 409
assert blank.json()["detail"] == "requirement_review_empty"
```

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_requirement_contract.py tests/test_workbench_semantic_guardrails.py -k "requirement" -q
```

Expected: FAIL because `WorkbenchStore` still exposes triage methods and old triage error names.

---

### Task 2: Add Runtime Approved RequirementSheet Override

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/requirements_runtime.py`
- Modify: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Add a failing test proving approved sheets skip extraction**

Add this test to `tests/test_runtime_state_flow.py` near existing requirement-runtime tests:

```python
def test_build_run_state_uses_approved_requirement_sheet_without_extraction(tmp_path: Path) -> None:
    from seektalent.models import RequirementSheet
    from seektalent.runtime.requirements_runtime import build_run_state
    from seektalent.tracing import RunTracer

    class FailingExtractor:
        async def extract_with_draft(self, **_: object) -> object:
            raise AssertionError("requirements extractor must not run when approved sheet is provided")

    emitted_events: list[tuple[str, str]] = []

    def emit_llm_event(**kwargs: object) -> None:
        emitted_events.append((str(kwargs["event_type"]), str(kwargs["status"])))

    def emit_progress(*_: object, **__: object) -> None:
        return None

    def snapshot_factory(**kwargs: object):
        class Snapshot:
            def model_dump(self, *, mode: str) -> dict[str, object]:
                assert mode == "json"
                return dict(kwargs)

        return Snapshot()

    sheet = RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=["AI Agent"],
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agent workflow systems.",
        must_have_capabilities=["LangGraph"],
        preferred_capabilities=["RAG"],
        exclusion_signals=[],
        hard_constraints={},
        preferences={},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize agent workflow evidence.",
    )
    settings = make_settings(workspace_root=str(tmp_path))
    tracer = RunTracer(settings.artifacts_path)

    try:
        run_state = asyncio.run(
            build_run_state(
                settings=settings,
                requirement_extractor=FailingExtractor(),
                tracer=tracer,
                job_title="AI Agent Engineer",
                jd="Build LangGraph systems.",
                notes="Original notes only.",
                requirement_cache_scope=None,
                approved_requirement_sheet=sheet,
                progress_callback=None,
                emit_llm_event=emit_llm_event,
                emit_progress=emit_progress,
                build_llm_call_snapshot=snapshot_factory,
                write_aux_llm_call_artifact=lambda **_: None,
                run_stage_error_factory=lambda stage, message: RuntimeError(f"{stage}:{message}"),
            )
        )
    finally:
        tracer.close(status="completed")

    assert run_state.requirement_sheet == sheet
    assert run_state.input_truth.notes == "Original notes only."
    assert ("requirements_completed", "succeeded") in emitted_events
```

- [ ] **Step 2: Add the approved sheet parameter to runtime orchestration**

In `src/seektalent/runtime/orchestrator.py`, import `RequirementSheet` if it is not already imported, then add `approved_requirement_sheet` to `run()`, `run_async()`, and `_build_run_state()` forwarding.

Use this signature pattern:

```python
def run(
    self,
    *,
    job_title: str,
    jd: str,
    notes: str,
    source_kinds: Sequence[str] | None = None,
    liepin_context: Mapping[str, str | int | bool | None] | None = None,
    progress_callback: ProgressCallback | None = None,
    runtime_start_callback: RuntimeStartCallback | None = None,
    requirement_cache_scope: str | None = None,
    approved_requirement_sheet: RequirementSheet | None = None,
) -> RunArtifacts:
```

Forward it into `run_async()` and `_build_run_state()`:

```python
approved_requirement_sheet=approved_requirement_sheet,
```

Do not add this parameter to CLI calls.

- [ ] **Step 3: Add the approved sheet branch to `build_run_state()`**

In `src/seektalent/runtime/requirements_runtime.py`, add this parameter:

```python
approved_requirement_sheet: RequirementSheet | None = None,
```

Import `RequirementSheet`:

```python
from seektalent.models import RequirementSheet, RetrievalState, RunState
```

Before calling `requirement_extractor.extract_with_draft`, branch on the approved sheet:

```python
if approved_requirement_sheet is not None:
    if approved_requirement_sheet.job_title != input_truth.job_title:
        raise run_stage_error_factory("requirement_extraction", "approved_requirement_sheet_job_title_mismatch")
    requirement_draft = None
    requirement_sheet = approved_requirement_sheet
else:
    if requirement_cache_scope is None:
        requirement_draft, requirement_sheet = await requirement_extractor.extract_with_draft(input_truth=input_truth)
    else:
        requirement_draft, requirement_sheet = await requirement_extractor.extract_with_draft(
            input_truth=input_truth,
            cache_scope=requirement_cache_scope,
        )
```

When writing artifacts, only write the extraction draft if it exists:

```python
if requirement_draft is not None:
    tracer.write_json("input.requirement_extraction_draft", requirement_draft.model_dump(mode="json"))
```

For the requirements call snapshot, set structured output and output refs based on the source:

```python
approved_source = approved_requirement_sheet is not None
structured_output = (
    {"source": "approved_requirement_sheet", "requirement_sheet": requirement_sheet.model_dump(mode="json")}
    if approved_source
    else requirement_draft.model_dump(mode="json")
)
output_refs = (
    ["input.requirement_sheet"]
    if approved_source
    else ["input.requirement_extraction_draft", "input.requirement_sheet"]
)
```

Use `output_refs` for `output_artifact_refs`.

Use the same source-aware refs for `artifact_paths` in the progress/LLM lifecycle events. In the approved-sheet path, do not advertise `input/requirement_extraction_draft.json` as an emitted artifact.

- [ ] **Step 4: Run the runtime test**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py -k "approved_requirement_sheet or requirement" -q
```

Expected: PASS for the new approved-sheet test and no regression in nearby requirement tests.

---

### Task 3: Replace Workbench Store Triage With Requirement Review

**Files:**
- Modify: `src/seektalent_ui/workbench_store.py`
- Modify: `src/seektalent_ui/maintenance.py`
- Modify: `tests/test_workbench_requirement_contract.py`
- Modify: `tests/test_workbench_semantic_guardrails.py`
- Test: `tests/test_workbench_requirement_contract.py`

- [ ] **Step 1: Replace the active dataclass**

In `src/seektalent_ui/workbench_store.py`, replace `WorkbenchRequirementTriage` with:

```python
@dataclass(frozen=True)
class WorkbenchRequirementReview:
    session_id: str
    status: Literal["draft", "approved"]
    requirement_sheet: RequirementSheet | None
    created_at: str
    updated_at: str
    approved_at: str | None
```

Update `WorkbenchSession` and job context dataclasses so their field is:

```python
requirement_review: WorkbenchRequirementReview
```

Update `WorkbenchSourceRunJobContext`, `WorkbenchRuntimeSourcingJobContext`, and `WorkbenchLiepinDetailOpenJobContext` to carry `requirement_review`. If a context still has `triage`, rename it in the same edit. Do not keep both.

- [ ] **Step 2: Store full `RequirementSheet` JSON**

Change session creation so new sessions create a draft review with `requirement_sheet=None`.

Use a new active table:

```sql
CREATE TABLE IF NOT EXISTS session_requirement_reviews (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'approved')),
    requirement_sheet_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
)
```

In local-development schema initialization, stop inserting new rows into `session_requirement_triage`. Do not write both tables.

For existing local SQLite databases, create missing `session_requirement_reviews` rows for existing `sessions` with `requirement_sheet_json=NULL`. Do not migrate or reinterpret old `session_requirement_triage` values into the new sheet.

- [ ] **Step 3: Add JSON parse and validation helpers**

Add these helpers near the old triage row helper location:

```python
def _requirement_sheet_json(requirement_sheet: RequirementSheet | None) -> str | None:
    if requirement_sheet is None:
        return None
    return json.dumps(requirement_sheet.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _requirement_sheet_from_json(value: object) -> RequirementSheet | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return RequirementSheet.model_validate(json.loads(value))
```

Add a mismatch guard:

```python
def _validate_requirement_sheet_for_session(session: WorkbenchSession, requirement_sheet: RequirementSheet) -> None:
    if requirement_sheet.job_title != session.job_title:
        raise ValueError("requirement_sheet_job_title_mismatch")
```

- [ ] **Step 4: Replace store methods**

Replace:

```python
get_requirement_triage()
update_requirement_triage()
approve_requirement_triage()
```

with:

```python
get_requirement_review()
update_requirement_review()
approve_requirement_review()
```

The update method should take:

```python
requirement_sheet: RequirementSheet
```

and set `status='draft'`, `approved_at=NULL`.

The approve method should reject missing sheets:

```python
if review.requirement_sheet is None:
    raise PermissionError("requirement_review_empty")
```

Update source-run and runtime-sourcing start guards that currently check `requirement_triage_not_approved` to check `requirement_review.status == "approved"` and `requirement_review.requirement_sheet is not None`. Use the new error names `requirement_review_not_approved` and `requirement_review_empty`.

- [ ] **Step 5: Update schema maintenance**

In `src/seektalent_ui/maintenance.py`, replace the required active table entry for `session_requirement_triage` with `session_requirement_reviews`. The canonical schema validation should require `requirement_sheet_json` and should not require old triage JSON columns.

Update maintenance queries that join `session_requirement_triage` for source-run repair to join `session_requirement_reviews` and use the review status. Do not read old triage columns from maintenance.

- [ ] **Step 6: Run store tests**

Run:

```bash
uv run pytest tests/test_workbench_requirement_contract.py tests/test_workbench_semantic_guardrails.py -k "requirement" -q
```

Expected: PASS.

---

### Task 4: Update Workbench API Models And Routes

**Files:**
- Modify: `src/seektalent_ui/models.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `tests/test_workbench_api.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Replace API models**

In `src/seektalent_ui/models.py`, import `RequirementSheet` and replace the triage request/response models with:

```python
class WorkbenchRequirementReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_sheet: RequirementSheet


class WorkbenchRequirementReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: WorkbenchRequirementReviewStatus
    requirement_sheet: RequirementSheet | None
    created_at: str
    updated_at: str
    approved_at: str | None = None
```

Replace the old status alias with:

```python
WorkbenchRequirementReviewStatus = Literal["draft", "approved"]
```

Do not keep `WorkbenchTriageStatus` as an active API type.

- [ ] **Step 2: Replace routes**

In `src/seektalent_ui/workbench_routes.py`, replace the old triage endpoints with requirement endpoints:

```python
@router.get(
    "/api/workbench/sessions/{session_id}/requirements",
    response_model=WorkbenchRequirementReviewResponse,
)
def get_requirement_review(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchRequirementReviewResponse:
    review = store.get_requirement_review(user=user, session_id=session_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _requirement_review_response(review)
```

Use matching `PUT /requirements`, `POST /requirements/prepare`, and `POST /requirements/approve` routes.

Approval should map the new empty error:

```python
except PermissionError as exc:
    if str(exc) == "requirement_review_empty":
        raise HTTPException(status_code=409, detail="requirement_review_empty") from exc
    raise
```

Replace `_triage_response()` with:

```python
def _requirement_review_response(review: WorkbenchRequirementReview) -> WorkbenchRequirementReviewResponse:
    return WorkbenchRequirementReviewResponse(
        session_id=review.session_id,
        status=review.status,
        requirement_sheet=review.requirement_sheet,
        created_at=review.created_at,
        updated_at=review.updated_at,
        approved_at=review.approved_at,
    )
```

- [ ] **Step 3: Update session response**

In the session response model and mapper, replace:

```python
requirement_review=_requirement_review_response(session.requirement_review)
```

The session response field should be named `requirement_review`, not `requirementTriage` or `requirementReview`, so the Workbench session payload and direct requirement endpoint expose the same contract shape.

- [ ] **Step 4: Update API tests**

In `tests/test_workbench_api.py`, update requirement endpoints to:

```python
GET /api/workbench/sessions/{session_id}/requirements
PUT /api/workbench/sessions/{session_id}/requirements
POST /api/workbench/sessions/{session_id}/requirements/prepare
POST /api/workbench/sessions/{session_id}/requirements/approve
```

Add an assertion that the response contains `requirement_sheet` and does not contain old field names:

```python
payload = response.json()
assert "requirement_sheet" in payload
assert "mustHaves" not in payload
assert "niceToHaves" not in payload
assert "generatedQueryHints" not in payload
```

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest tests/test_workbench_api.py -k "requirement" -q
```

Expected: PASS.

---

### Task 5: Remove Runtime Bridge Notes Mutation And Synthetic Cache Seed

**Files:**
- Modify: `src/seektalent_ui/runtime_bridge.py`
- Modify: `src/seektalent_ui/job_runner.py`
- Modify: `src/seektalent_ui/workbench_store.py`
- Modify: `src/seektalent_ui/workbench_note_writer.py`
- Modify: `tests/test_workbench_runtime_owned_execution.py`
- Test: `tests/test_workbench_runtime_owned_execution.py`

- [ ] **Step 1: Update runtime bridge tests first**

In `tests/test_workbench_runtime_owned_execution.py`, replace the old assertion:

```python
assert "Approved requirement triage:" in str(call["notes"])
```

with:

```python
assert call["notes"] == session.notes
assert call["approved_requirement_sheet"].job_title == session.job_title
```

If the test captures kwargs as dictionaries, compare the dumped sheet:

```python
assert call["approved_requirement_sheet"].model_dump(mode="json")["job_title"] == session.job_title
```

- [ ] **Step 2: Delete synthetic requirement helpers**

In `src/seektalent_ui/runtime_bridge.py`, remove:

```python
_notes_with_triage
_seed_approved_requirement_cache
_requirement_draft_from_approved_triage
_role_summary_from_triage
_triage_from_requirement_sheet
_query_hints_from_requirement_sheet
```

Also remove imports that become unused:

```python
from seektalent.models import RequirementExtractionDraft
from seektalent.prompting import PromptRegistry
from seektalent.requirements import build_input_truth
from seektalent.requirements.extractor import requirement_cache_key
from seektalent.runtime.exact_llm_cache import put_cached_json
```

- [ ] **Step 3: Pass approved sheets directly to full runtime runs**

In `run_cts_source_run()` and `run_runtime_sourcing_job()`, use original notes:

```python
"notes": context.session.notes,
```

Pass the approved sheet:

```python
"approved_requirement_sheet": context.requirement_review.requirement_sheet,
```

Before calling runtime, guard that the approved sheet exists:

```python
if context.requirement_review.requirement_sheet is None:
    raise PermissionError("requirement_review_empty")
```

- [ ] **Step 4: Replace source-lane query term derivation**

In `run_liepin_card_source_run()` and `run_liepin_detail_open_intent()`, pass original notes and build `source_query_terms` from the approved `RequirementSheet`, not from triage.

Use a helper shape like:

```python
def _requirement_query_terms(requirement_sheet: RequirementSheet, *, fallback_job_title: str) -> list[str]:
    terms: list[object] = [
        *requirement_sheet.initial_query_term_pool,
        *requirement_sheet.title_anchor_terms,
        *requirement_sheet.must_have_capabilities,
        *requirement_sheet.preferences.preferred_query_terms,
        fallback_job_title,
    ]
    values: list[str] = []
    for term in terms:
        if isinstance(term, QueryTermCandidate):
            values.append(term.term)
        elif isinstance(term, str):
            values.append(term)
    return _unique_bounded_strings(values, max_items=8) or [fallback_job_title]
```

All source-lane helpers should reject missing sheets with `requirement_review_empty`. Do not map the sheet back into `must_haves` or `generated_query_hints`.

- [ ] **Step 5: Update candidate projection matching terms**

In `src/seektalent_ui/workbench_store.py`, update card/detail candidate projection code that currently uses `context.triage.must_haves`, `context.triage.nice_to_haves`, and `context.triage.synonyms`.

Use:

```python
sheet = context.requirement_review.requirement_sheet
matched_must_haves = _matched_terms(sheet.must_have_capabilities, card_text)
matched_preferences = _matched_terms(sheet.preferred_capabilities, card_text)
```

If there is no approved sheet in these projection paths, fail with `requirement_review_empty`; do not silently emit empty matched fields.

- [ ] **Step 6: Update `extract_requirement_review` prepare path**

Rename `extract_requirement_triage()` to `extract_requirement_review()` or keep a short local function name only if it returns a `RequirementSheet`. The function should be:

```python
def extract_requirement_review(
    *,
    session,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> RequirementSheet:
    runtime = runtime_factory(settings)
    extractor = getattr(runtime, "extract_requirements", None)
    if extractor is None:
        raise RuntimeError("Runtime does not support requirement extraction.")
    return extractor(
        job_title=session.job_title,
        jd=session.jd_text,
        notes=session.notes,
        progress_callback=progress_callback,
        requirement_cache_scope=session.session_id,
    )
```

- [ ] **Step 7: Update job runner**

In `src/seektalent_ui/job_runner.py`, update prepare execution to call the new extractor and store the returned `RequirementSheet`:

```python
requirement_sheet = extract_requirement_review(
    session=session,
    settings=self.settings,
    runtime_factory=self.runtime_factory,
    progress_callback=lambda event: self._record_requirement_progress(user=user, session=session, event=event),
)
review = self.store.update_requirement_review(
    user=user,
    session_id=session_id,
    requirement_sheet=requirement_sheet,
)
```

Update event payloads from triage counts to requirement counts:

```python
payload={
    "sessionId": session_id,
    "mustHaveCapabilityCount": len(requirement_sheet.must_have_capabilities),
    "preferredCapabilityCount": len(requirement_sheet.preferred_capabilities),
    "queryTermCount": len(requirement_sheet.initial_query_term_pool),
}
```

Replace event names and error names:

```python
requirement_triage_used -> requirement_review_used
requirement_triage_updated -> requirement_review_updated
requirement_triage_approved -> requirement_review_approved
requirement_triage_not_approved -> requirement_review_not_approved
```

- [ ] **Step 8: Update Workbench note writer facts**

In `src/seektalent_ui/workbench_note_writer.py`, replace requirement triage counts with:

```python
sheet = session.requirement_review.requirement_sheet
must_have_capability_count = len(sheet.must_have_capabilities) if sheet else 0
preferred_capability_count = len(sheet.preferred_capabilities) if sheet else 0
query_term_count = len(sheet.initial_query_term_pool) if sheet else 0
```

Do not mention `generated_query_hint_count` in note-writer prompts or facts.

- [ ] **Step 9: Run Workbench runtime execution tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py -q
```

Expected: PASS and no assertion that approved requirements were appended into notes.

---

### Task 6: Update Svelte Workbench Requirement Review UI

**Files:**
- Create: `apps/web-svelte/src/lib/components/RequirementReviewPanel.svelte`
- Delete or stop importing: `apps/web-svelte/src/lib/components/RequirementTriageGate.svelte`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`
- Modify: `apps/web-svelte/src/lib/api/workbench.ts`
- Modify: `apps/web-svelte/src/lib/workbench/types.ts`
- Modify: `apps/web-svelte/src/lib/workbench/recruiterAnimation.ts`
- Modify: `apps/web-svelte/src/lib/workbench/runStory.ts`
- Modify: `apps/web-svelte/src/lib/components/RequirementTriagePanel.svelte`
- Modify: `apps/web-svelte/src/lib/components/SourceRunControlPanel.svelte`
- Modify: `apps/web-svelte/src/lib/components/CriteriaHighlights.svelte`
- Modify: `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte`
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`

- [ ] **Step 1: Replace frontend requirement types**

In `apps/web-svelte/src/lib/workbench/types.ts`, replace the triage types with:

```ts
export type RequirementSheet = components['schemas']['RequirementSheet'];
export type HardConstraintSlots = components['schemas']['HardConstraintSlots'];
export type PreferenceSlots = components['schemas']['PreferenceSlots'];
export type QueryTermCandidate = components['schemas']['QueryTermCandidate'];
export type WorkbenchRequirementReview =
	components['schemas']['WorkbenchRequirementReviewResponse'];
export type WorkbenchRequirementReviewInput =
	components['schemas']['WorkbenchRequirementReviewUpdateRequest'];
```

Remove active exports named `WorkbenchRequirementTriage` and `WorkbenchRequirementTriageInput`.

- [ ] **Step 2: Update API helpers**

In `apps/web-svelte/src/lib/api/workbench.ts`, replace triage helper calls with requirement helpers using the generated OpenAPI client:

```ts
export async function prepareRequirementReview(sessionId: string) {
	return requireData(
		await api.POST('/api/workbench/sessions/{session_id}/requirements/prepare', {
			params: { path: { session_id: sessionId } }
		})
	);
}

export async function updateRequirementReview(
	sessionId: string,
	input: WorkbenchRequirementReviewInput
) {
	return requireData(
		await api.PUT('/api/workbench/sessions/{session_id}/requirements', {
			params: { path: { session_id: sessionId } },
			body: input
		})
	);
}

export async function approveRequirementReview(sessionId: string) {
	return requireData(
		await api.POST('/api/workbench/sessions/{session_id}/requirements/approve', {
			params: { path: { session_id: sessionId } }
		})
	);
}
```

- [ ] **Step 3: Build the review panel**

Create `apps/web-svelte/src/lib/components/RequirementReviewPanel.svelte`. Use labels that match the backend contract. The code below is a starting shape; before the step is complete, the panel must display every downstream-significant field: `role_summary`, `title_anchor_terms`, `title_anchor_rationale`, `must_have_capabilities`, `preferred_capabilities`, `exclusion_signals`, `hard_constraints`, `preferences`, `initial_query_term_pool`, and `scoring_rationale`.

```svelte
<script lang="ts">
	import type {
		RequirementSheet,
		WorkbenchRequirementReview
	} from '$lib/workbench/types';

	let {
		review,
		saving = false,
		approving = false,
		error = null,
		onSave,
		onApprove
	} = $props<{
		review: WorkbenchRequirementReview;
		saving?: boolean;
		approving?: boolean;
		error?: string | null;
		onSave: (sheet: RequirementSheet) => void;
		onApprove: () => void;
	}>();

	let editing = $state(false);
	let localError = $state('');
	let draft = $state<RequirementSheet | null>(review.requirement_sheet);

	$effect(() => {
		if (!editing) draft = review.requirement_sheet;
	});

	const approved = $derived(review.status === 'approved');
	const hasSheet = $derived(Boolean(review.requirement_sheet));
	const mutating = $derived(saving || approving);

	function listText(values: string[] | undefined) {
		return (values ?? []).join('\n');
	}

	function lines(value: string) {
		return value
			.split('\n')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function updateList(key: keyof RequirementSheet, value: string) {
		if (!draft) return;
		draft = Object.assign({}, draft, { [key]: lines(value) });
	}

	function updateText(key: keyof RequirementSheet, value: string) {
		if (!draft) return;
		draft = Object.assign({}, draft, { [key]: value });
	}

	function save() {
		localError = '';
		if (!draft) {
			localError = '需求结构不能为空。';
			return;
		}
		onSave(draft);
		editing = false;
	}
<\/script>

<section class="triage-gate">
	<div class="triage-head">
		<div>
			<p class="section-label">需求确认</p>
			<h3>RequirementSheet</h3>
		</div>
		<span class:approved class="status-pill">{approved ? '已确认' : '待确认'}</span>
	</div>

	{#if !hasSheet}
		<p class="triage-empty-copy">Agent 将先从岗位标题、JD 和 notes 提取结构化 RequirementSheet。</p>
	{:else if draft}
		{#if editing}
			<label class="field triage-field">
				<span>role_summary</span>
				<textarea rows="2" value={draft.role_summary} oninput={(event) => updateText('role_summary', event.currentTarget.value)}></textarea>
			</label>
			<label class="field triage-field">
				<span>must_have_capabilities</span>
				<textarea rows="3" value={listText(draft.must_have_capabilities)} oninput={(event) => updateList('must_have_capabilities', event.currentTarget.value)}></textarea>
			</label>
			<label class="field triage-field">
				<span>preferred_capabilities</span>
				<textarea rows="3" value={listText(draft.preferred_capabilities)} oninput={(event) => updateList('preferred_capabilities', event.currentTarget.value)}></textarea>
			</label>
			<label class="field triage-field">
				<span>exclusion_signals</span>
				<textarea rows="2" value={listText(draft.exclusion_signals)} oninput={(event) => updateList('exclusion_signals', event.currentTarget.value)}></textarea>
			</label>
			<label class="field triage-field">
				<span>scoring_rationale</span>
				<textarea rows="2" value={draft.scoring_rationale} oninput={(event) => updateText('scoring_rationale', event.currentTarget.value)}></textarea>
			</label>
		{:else}
			<div class="runtime-criteria-summary" aria-label="RequirementSheet">
				<div class="runtime-criteria-row"><span>job_title</span><p>{draft.job_title}</p></div>
				<div class="runtime-criteria-row"><span>role_summary</span><p>{draft.role_summary}</p></div>
				<div class="runtime-criteria-row"><span>title_anchor_terms</span><p>{draft.title_anchor_terms.join(' / ')}</p></div>
				<div class="runtime-criteria-row"><span>must_have_capabilities</span><p>{draft.must_have_capabilities.join(' / ')}</p></div>
				<div class="runtime-criteria-row"><span>preferred_capabilities</span><p>{draft.preferred_capabilities.join(' / ')}</p></div>
				<div class="runtime-criteria-row"><span>exclusion_signals</span><p>{draft.exclusion_signals.join(' / ')}</p></div>
				<div class="runtime-criteria-row"><span>preferred_query_terms</span><p>{String((draft.preferences?.preferred_query_terms as string[] | undefined)?.join(' / ') ?? '')}</p></div>
				<div class="runtime-criteria-row"><span>scoring_rationale</span><p>{draft.scoring_rationale}</p></div>
			</div>
		{/if}
	{/if}

	{#if localError || error}
		<p class="form-error" role="alert">{localError || error}</p>
	{/if}

	<div class="triage-actions">
		{#if hasSheet && !editing}
			<button class="secondary-link" type="button" disabled={mutating} onclick={() => (editing = true)}>修改</button>
			<button class="primary-action" type="button" disabled={mutating || approved} onclick={onApprove}>{approving ? '确认中' : '确认需求'}</button>
		{:else if editing}
			<button class="secondary-link" type="button" disabled={mutating} onclick={() => (editing = false)}>取消</button>
			<button class="primary-action" type="button" disabled={mutating} onclick={save}>{saving ? '保存中' : '保存'}</button>
		{/if}
	</div>
</section>
```

- [ ] **Step 4: Update the session page**

In `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`, replace triage imports and state with requirement review equivalents:

```ts
import RequirementReviewPanel from '$lib/components/RequirementReviewPanel.svelte';
import {
	approveRequirementReview,
	prepareRequirementReview,
	updateRequirementReview
} from '$lib/api/workbench';
```

Replace derived state:

```ts
const requirementSheet = $derived(sessionQuery.data?.requirement_review.requirement_sheet ?? null);
const requirementApproved = $derived(sessionQuery.data?.requirement_review.status === 'approved');
const requirementPreparationRunning = $derived(
	sessionQuery.data?.sourceRuns.some((run) => run.status === 'running') ?? false
);
const canPrepareRequirements = $derived(!requirementSheet && !requirementPreparationRunning);
```

Replace mutation calls with:

```ts
mutationFn: () => prepareRequirementReview(data.sessionId)
mutationFn: (sheet: RequirementSheet) =>
	updateRequirementReview(data.sessionId, { requirement_sheet: sheet })
mutationFn: () => approveRequirementReview(data.sessionId)
```

Render:

```svelte
<RequirementReviewPanel
	review={sessionQuery.data.requirement_review}
	saving={saveRequirementMutation.isPending}
	approving={approveRequirementMutation.isPending}
	error={requirementError}
	onSave={(sheet) => saveRequirementMutation.mutate(sheet)}
	onApprove={() => approveRequirementMutation.mutate()}
/>
```

Also replace `requirementTriage` in the session page with `requirement_review` everywhere. Do not keep local `reviewCriteria` as a triage-shaped projection.

- [ ] **Step 5: Update graph, controls, and compact summaries**

Update the active Svelte nodes that currently consume triage-shaped criteria:

- `apps/web-svelte/src/lib/workbench/runStory.ts`
  - Replace `criteriaFromTriage()` and `WorkbenchRequirementTriageInput` with a requirement-sheet view.
  - Requirements graph nodes should include `requirement_sheet`, not `{ mustHaves, niceToHaves, generatedQueryHints }`.
- `apps/web-svelte/src/lib/workbench/recruiterAnimation.ts`
  - Replace triage input types with a compact `RequirementSheet`-derived summary only where animation state needs one.
- `apps/web-svelte/src/lib/components/CriteriaHighlights.svelte`
  - Show chips from `must_have_capabilities`, `preferred_capabilities`, and `initial_query_term_pool`.
- `apps/web-svelte/src/lib/components/SourceRunControlPanel.svelte`
  - Gate start on `requirement_review.status === "approved"` and a non-null `requirement_sheet`.
- `apps/web-svelte/src/lib/components/RequirementTriagePanel.svelte`
  - Replace or delete it; active UI must not render `mustHaves`, `niceToHaves`, `synonyms`, or `generatedQueryHints`.
- `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte`
  - Requirement nodes must display `role_summary`, `title_anchor_terms`, `must_have_capabilities`, `preferred_capabilities`, `exclusion_signals`, `hard_constraints`, `preferences`, `initial_query_term_pool`, and `scoring_rationale`.

Update related component tests and e2e mock API session payloads so they provide `requirement_review` and `requirement_sheet`.

- [ ] **Step 6: Regenerate OpenAPI TypeScript schema**

Start the Workbench backend only if the execution context permits local server startup. Do not start Liepin/OpenCLI. Use a backend config with Liepin disabled.

Then run:

```bash
cd apps/web-svelte
bun run api:gen
```

Expected: `src/lib/api/schema.d.ts` contains `WorkbenchRequirementReviewResponse` and does not contain `WorkbenchRequirementTriageResponse`.

- [ ] **Step 7: Run frontend tests**

Run:

```bash
cd apps/web-svelte
bun run test -- Requirement
bun run check
bun run build
```

Expected: PASS. If no specific Requirement tests exist, add a component test for `RequirementReviewPanel` before running.

---

### Task 7: Remove Old Workbench Requirement Contract References

**Files:**
- Modify: all changed Workbench backend/frontend files from previous tasks
- Test: repository grep checks

- [ ] **Step 1: Run active-code grep checks**

Run:

```bash
rg -n "mustHaves|niceToHaves|synonyms|seniorityFilters|generatedQueryHints" src/seektalent_ui apps/web-svelte/src
rg -n "must_haves|nice_to_haves|synonyms|seniority_filters|generated_query_hints" src/seektalent_ui apps/web-svelte/src
rg -n "_notes_with_triage|_seed_approved_requirement_cache|_requirement_draft_from_approved_triage" src/seektalent_ui
```

Expected: no active matches.

- [ ] **Step 2: Remove stale tests and fixtures**

If grep finds old field names only in tests that intentionally assert removal, keep those tests. Otherwise rename or delete the stale fixture/test code in the same commit.

Use this expected fixture shape for new tests:

```python
{
    "requirement_sheet": {
        "job_title": "AI Agent Engineer",
        "title_anchor_terms": ["AI Agent"],
        "title_anchor_rationale": "AI Agent is the searchable title anchor.",
        "role_summary": "Build agent workflow systems.",
        "must_have_capabilities": ["LangGraph"],
        "preferred_capabilities": ["RAG"],
        "exclusion_signals": [],
        "hard_constraints": {},
        "preferences": {},
        "initial_query_term_pool": [],
        "scoring_rationale": "Prioritize agent workflow evidence.",
    }
}
```

- [ ] **Step 3: Commit cleanup**

Run:

```bash
git add src/seektalent src/seektalent_ui apps/web-svelte tests
git commit -m "refactor: restore workbench requirement sheet contract"
```

Expected: commit succeeds with no old active Workbench requirement fields.

---

### Task 8: Run Focused Verification

**Files:**
- No source edits unless verification exposes a bug.
- Test: focused backend/frontend commands.

- [ ] **Step 1: Run core requirement tests**

Run:

```bash
uv run pytest tests/test_requirement_extraction.py -q
```

Expected: PASS.

- [ ] **Step 2: Run runtime requirement tests**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py -k "requirement" -q
uv run pytest tests/test_runtime_audit.py -k "requirements" -q
```

Expected: PASS.

- [ ] **Step 3: Run Workbench requirement tests**

Run:

```bash
uv run pytest tests/test_workbench_requirement_contract.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_runtime_owned_execution.py -q
uv run pytest tests/test_workbench_api.py -k "requirement" -q
```

Expected: PASS.

- [ ] **Step 4: Run frontend requirement tests**

Run:

```bash
cd apps/web-svelte
bun run test -- Requirement
bun run check
bun run build
```

Expected: PASS.

- [ ] **Step 5: Run final grep acceptance**

Run:

```bash
rg -n "mustHaves|niceToHaves|synonyms|seniorityFilters|generatedQueryHints" src/seektalent_ui apps/web-svelte/src
rg -n "must_haves|nice_to_haves|synonyms|seniority_filters|generated_query_hints" src/seektalent_ui apps/web-svelte/src
rg -n "_notes_with_triage|_seed_approved_requirement_cache|_requirement_draft_from_approved_triage" src/seektalent_ui
```

Expected: no active matches except intentionally named removal tests.

- [ ] **Step 6: Record verification output**

In the implementation handoff, report:

```text
Requirement contract restored to RequirementSheet.
Workbench no longer appends approved requirements into notes.
Workbench no longer seeds synthetic requirement cache entries.
Focused backend and frontend tests passed.
```

---

## Self-Review

Spec coverage:

- Single `RequirementSheet` contract is covered by Tasks 1, 3, 4, and 6.
- Runtime approved-sheet execution is covered by Tasks 2 and 5.
- Removal of notes mutation and synthetic cache seed is covered by Task 5.
- Old Workbench field cleanup is covered by Task 7.
- Focused verification is covered by Task 8.

Placeholder scan:

- The plan contains no implementation placeholders.
- Every file group has concrete edits and commands.

Type consistency:

- Backend uses `RequirementSheet`.
- API uses `requirement_sheet`.
- Frontend uses `RequirementSheet` and `WorkbenchRequirementReview`.
- Runtime override uses `approved_requirement_sheet`.
