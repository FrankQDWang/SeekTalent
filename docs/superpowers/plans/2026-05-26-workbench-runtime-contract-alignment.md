# Workbench Runtime Contract Alignment And Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align Workbench/UI payloads with the Runtime final contract, move running-note fact projection onto Runtime-owned code, and remove old Workbench backend/UI execution paths.

**Architecture:** Runtime remains the source of truth for requirements, source dispatch, candidate intake, scoring, reflection, and finalization. Workbench persists Runtime output and exposes one final-top10 contract to Svelte; review items remain action/review state only. Cleanup removes old one-run APIs, primary source-run workers, and the React app after contract tests prove Svelte consumes Runtime fields directly.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLite Workbench store, pytest, Svelte 5, OpenAPI-generated TypeScript types, Vitest, Bun.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-26-workbench-runtime-contract-alignment-design.md`

## Execution Notes

- Execute this as stacked PR #6 after PR #5 is green and merged/rebased into the stack.
- Do not fix PR #5 typecheck failures in this PR unless they still block this branch after PR #5 lands.
- Do not change Runtime retrieval, dedupe, scoring, reflection, or finalizer behavior.
- Do not keep compatibility aliases for old UI fields.
- Keep Liepin detail-open request and provider-open actions; they are post-finalization action paths.
- Commit after each task.

## Baseline Verification

- [ ] **Step 1: Confirm the branch contains PR #5 Runtime baseline**

Run:

```bash
git status --short --branch
rg -n "latest_canonical_intake_summary|candidate_identity_by_resume_id|canonical_resume_by_identity_id" src/seektalent/runtime src/seektalent/models.py
uv run pytest tests/test_runtime_state_flow.py tests/test_runtime_source_lanes.py tests/test_runtime_candidate_identity.py tests/test_finalizer_contract.py tests/test_workbench_runtime_owned_execution.py -q
```

Expected:

- Branch is stacked on the PR #5 branch.
- Runtime canonical intake symbols exist.
- The listed tests pass.

If baseline tests fail because PR #5 is not fixed yet, stop and rebase after PR #5 is fixed. Do not implement this PR on a broken baseline.

## File Map

Final top10 contract:

- Modify: `src/seektalent_ui/models.py`
  - Add direct Runtime final candidate fields to `WorkbenchFinalTopCandidateResponse`.
- Modify: `src/seektalent_ui/workbench_store.py`
  - Persist `why_selected` and `source_round` at the review-item/final projection boundary.
  - Keep matched/risk/strength/weakness fields available directly on final-top review items.
- Modify: `src/seektalent_ui/workbench_routes.py`
  - Return direct Runtime final fields from `_runtime_final_top_candidate_response(...)`.
  - Return direct fields from legacy `project_final_top_candidates(...)` fallback for old sessions until the fallback is removed.
- Modify: `src/seektalent_ui/final_top_candidates.py`
  - Populate the expanded response for non-runtime fallback sessions.
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`
- Modify: `apps/web-svelte/src/lib/workbench/finalCandidateCards.ts`
  - Use final-top10 fields for business explanation/matching/risk fields.
  - Keep review-item join only for actions, notes, statuses, and resume refs.
- Modify: `apps/web-svelte/src/lib/workbench/finalCandidateCards.test.ts`
- Modify: `apps/web-svelte/src/lib/components/CandidateReviewCard.svelte`
  - Visibly render Runtime final fields: why selected, hard/preference matches, strengths, weaknesses, risks, and source round.
- Add: `apps/web-svelte/src/lib/components/CandidateReviewCard.test.svelte`
  - Test harness that provides a Svelte Query client for component rendering.
- Add: `apps/web-svelte/src/lib/components/CandidateReviewCard.test.ts`
  - Component-level assertions that final cards display the Runtime fields.
- Modify: `apps/web-svelte/src/lib/components/CandidateQueue.test.ts`
- Modify: `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- Modify: `apps/web-svelte/tests/e2e/parityMockApi.ts`
- Modify: `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/workbench-parity.spec.ts`
  - Keep typed fixtures and e2e mock payloads aligned with required final-top10 fields.

Running notes:

- Create: `src/seektalent/runtime/public_notes.py`
  - Runtime-owned safe business fact projection from Runtime progress/public events.
- Modify: `src/seektalent_ui/workbench_note_writer.py`
  - Replace Workbench-owned Runtime event parsing with `seektalent.runtime.public_notes`.
- Modify: `tests/test_workbench_note_writer.py`
- Add: `tests/test_runtime_public_notes.py`

Primary execution cleanup:

- Modify: `src/seektalent_ui/job_runner.py`
  - Remove primary source-run worker startup and execution loops.
- Modify: `src/seektalent_ui/runtime_bridge.py`
  - Remove `run_cts_source_run(...)` and `run_liepin_card_source_run(...)`.
  - Keep `run_liepin_detail_open_intent(...)`.
- Modify: `src/seektalent_ui/workbench_store.py`
  - Remove active `start_source_run_job(...)`, `claim_next_source_run_job(...)`, and primary completion helpers.
  - Keep `source_runs` status projection and Liepin detail-open storage.
- Modify: `src/seektalent_ui/server.py`
  - Remove startup calls that reconcile primary `source_run_jobs` as active work.
- Modify: `tests/test_workbench_runtime_owned_execution.py`
- Modify: `tests/test_workbench_semantic_guardrails.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_workbench_liepin_browser_session_probe.py`

Legacy API/UI deletion:

- Modify: `src/seektalent_ui/server.py`
  - Remove `/api/runs` route group, `RunRegistry`, and `create_server(...)`.
  - Change `create_app(...)` to accept `settings` and `runtime_factory` directly instead of a registry.
- Modify: `src/seektalent_ui/__init__.py`
  - Stop exporting deleted `create_server`.
- Modify: `src/seektalent_ui/models.py`
  - Remove old one-run models after route removal.
- Delete: `src/seektalent_ui/mapper.py`
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`
  - Remove generated `/api/runs` paths and deleted model schemas.
- Modify/Delete: tests that cover only `/api/runs` old behavior.
- Modify: `tests/test_workbench_security_audit.py`
- Modify: `tests/test_workbench_semantic_guardrails.py`
- Modify: `tests/test_workbench_auth_security.py`
- Modify: `tests/test_dev_mode_readiness.py`
- Modify: `tests/test_workbench_api.py`
- Delete: `tests/test_ui_api.py`
- Delete: `tests/test_ui_mapper.py`
- Modify: `tests/test_liepin_boundaries.py`
- Modify: `tests/test_liepin_api_scope.py`
- Modify: `tests/test_workbench_network_guard.py`
- Modify: `docs/architecture-dependencies.md`
- Delete: `apps/web`
- Modify: `src/seektalent/cli.py`
- Modify: `docs/ui.md`
- Modify: `docs/cli.md`
- Modify: `docs/architecture.md`
- Modify: `scripts/start-dev-workbench.sh` only if it still references removed React files.

---

## Task 1: Expand Final Top10 Backend Contract

**Files:**
- Modify: `src/seektalent_ui/models.py`
- Modify: `src/seektalent_ui/workbench_store.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `src/seektalent_ui/final_top_candidates.py`
- Test: `tests/test_workbench_runtime_owned_execution.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Write a failing store-level contract test**

Add this assertion block to `tests/test_workbench_runtime_owned_execution.py::test_runtime_completion_persists_finalization_order_and_all_source_evidence` after `identity_a = items[1]`:

```python
    identity_b = items[0]
    assert identity_b.summary == "B"
    assert identity_b.aggregate_score == 88
    assert identity_b.fit_bucket == "fit"
    assert identity_b.matched_must_haves == ["Python", "distributed systems"]
    assert identity_b.matched_preferences == ["agent tooling"]
    assert identity_b.missing_risks == ["management scope unclear"]
    assert identity_b.strengths == ["Strong backend systems"]
    assert identity_b.weaknesses == ["Needs calibration on leadership scope"]
```

Then change the `final_result` fixture inside the same test to include the fields:

```python
        final_result=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    resume_id="resume-a",
                    final_score=91,
                    fit_bucket="fit",
                    match_summary="A",
                    why_selected="A is selected for stronger direct evidence.",
                    strengths=["Strong retrieval evidence"],
                    weaknesses=["Compensation unknown"],
                    matched_must_haves=["retrieval systems"],
                    matched_preferences=["agent tooling"],
                    risk_flags=["availability unclear"],
                    source_round=1,
                ),
                SimpleNamespace(
                    resume_id="resume-b",
                    final_score=88,
                    fit_bucket="fit",
                    match_summary="B",
                    why_selected="B is selected for Python platform depth.",
                    strengths=["Strong backend systems"],
                    weaknesses=["Needs calibration on leadership scope"],
                    matched_must_haves=["Python", "distributed systems"],
                    matched_preferences=["agent tooling"],
                    risk_flags=["management scope unclear"],
                    source_round=1,
                ),
            ]
        ),
```

- [ ] **Step 2: Write a failing API contract test**

Add this test near the Runtime-owned Workbench API tests in `tests/test_workbench_api.py`:

```python
def test_final_top10_exposes_runtime_final_candidate_fields_directly(tmp_path: Path) -> None:
    _reset_fake_runtime()
    FakeWorkbenchRuntime.artifacts = _candidate_artifacts(run_id="runtime-final-contract")
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client, source_kinds=["cts"])
    _approve_requirement_review(session_id=session["sessionId"], client=client)

    start = _start_session(client, session["sessionId"])
    assert start.status_code == 202, start.text
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()

    final_response = client.get(f"/api/workbench/sessions/{session['sessionId']}/final-top10")
    assert final_response.status_code == 200, final_response.text
    items = final_response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["whySelected"] == "Best match for backend agent workflow."
    assert item["riskFlags"] == ["benchmark depth unclear"]
    assert item["matchedMustHaves"] == ["FastAPI", "retrieval systems"]
    assert item["matchedPreferences"] == ["agent tooling"]
    assert item["strengths"] == ["Built SSE APIs", "Owned retrieval ranking"]
    assert item["weaknesses"] == ["Limited public benchmark ownership"]
    assert item["sourceRound"] == 1
```

- [ ] **Step 3: Run the failing backend tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py::test_runtime_completion_persists_finalization_order_and_all_source_evidence tests/test_workbench_api.py::test_final_top10_exposes_runtime_final_candidate_fields_directly -q
```

Expected: tests fail because final-top10 response models and/or stored review items do not expose direct Runtime fields.

- [ ] **Step 4: Add direct fields to Workbench final response model**

In `src/seektalent_ui/models.py`, update `WorkbenchFinalTopCandidateResponse`:

```python
class WorkbenchFinalTopCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewItemId: str
    runtimeIdentityId: str
    canonicalReviewItemId: str
    mergedReviewItemIds: list[str]
    rank: int
    displayName: str
    title: str
    company: str
    location: str
    summary: str
    aggregateScore: int | None = None
    fitBucket: str | None = None
    whySelected: str
    riskFlags: list[str]
    matchedMustHaves: list[str]
    matchedPreferences: list[str]
    strengths: list[str]
    weaknesses: list[str]
    sourceRound: int | None = None
    sourceBadges: list[str]
    evidenceLevel: WorkbenchCandidateEvidenceLevel
    sourceEvidence: list[WorkbenchFinalTopCandidateEvidenceResponse]
```

- [ ] **Step 5: Persist `why_selected` and `source_round` on review items**

In `src/seektalent_ui/workbench_store.py`, add fields to `WorkbenchCandidateReviewItem`:

```python
    why_selected: str
    source_round: int | None
```

In table creation for `candidate_review_items`, add columns:

```sql
why_selected TEXT NOT NULL DEFAULT '',
source_round INTEGER,
```

In the migration section, add:

```python
_ensure_column(conn, "candidate_review_items", "why_selected", "TEXT NOT NULL DEFAULT ''")
_ensure_column(conn, "candidate_review_items", "source_round", "INTEGER")
```

In `_persist_runtime_final_candidate_results_conn(...)`, set:

```python
why_selected = _safe_candidate_text(_attr(finalizer_candidate, "why_selected"), 1000)
source_round = _int_or_none(_attr(finalizer_candidate, "source_round"))
```

Extend the `INSERT INTO candidate_review_items` column list and values to include `why_selected` and `source_round`, and update the conflict clause:

```sql
why_selected = excluded.why_selected,
source_round = excluded.source_round,
```

In `_review_item_from_row(...)`, pass:

```python
        why_selected=row["why_selected"],
        source_round=row["source_round"],
```

For legacy/non-runtime insert paths that do not have finalizer fields, write `why_selected=""` and `source_round=NULL`.

- [ ] **Step 6: Return direct fields from final-top10 routes**

In `src/seektalent_ui/workbench_routes.py::_runtime_final_top_candidate_response(...)`, add:

```python
        whySelected=item.why_selected or item.summary,
        riskFlags=item.missing_risks,
        matchedMustHaves=item.matched_must_haves,
        matchedPreferences=item.matched_preferences,
        strengths=item.strengths,
        weaknesses=item.weaknesses,
        sourceRound=item.source_round,
```

In `src/seektalent_ui/final_top_candidates.py::_project_group(...)`, add the same fields using the canonical/best-score items:

```python
        whySelected=canonical.why_selected or canonical.summary,
        riskFlags=canonical.missing_risks,
        matchedMustHaves=canonical.matched_must_haves,
        matchedPreferences=canonical.matched_preferences,
        strengths=canonical.strengths,
        weaknesses=canonical.weaknesses,
        sourceRound=canonical.source_round,
```

- [ ] **Step 7: Run backend tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py::test_runtime_completion_persists_finalization_order_and_all_source_evidence tests/test_workbench_api.py::test_final_top10_exposes_runtime_final_candidate_fields_directly -q
```

Expected: both tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/seektalent_ui/models.py src/seektalent_ui/workbench_store.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/final_top_candidates.py tests/test_workbench_runtime_owned_execution.py tests/test_workbench_api.py
git commit -m "feat: expose runtime final fields in workbench top10"
```

## Task 2: Make Svelte Final Cards Consume Final-Top10 Business Fields

**Files:**
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`
- Modify: `apps/web-svelte/src/lib/workbench/finalCandidateCards.ts`
- Modify: `apps/web-svelte/src/lib/workbench/finalCandidateCards.test.ts`
- Modify: `apps/web-svelte/src/lib/components/CandidateReviewCard.svelte`
- Add: `apps/web-svelte/src/lib/components/CandidateReviewCard.test.svelte`
- Add: `apps/web-svelte/src/lib/components/CandidateReviewCard.test.ts`
- Modify: `apps/web-svelte/src/lib/components/CandidateQueue.test.ts`
- Modify: `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- Modify: `apps/web-svelte/tests/e2e/parityMockApi.ts`
- Modify: `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/workbench-parity.spec.ts`

- [ ] **Step 1: Regenerate OpenAPI schema**

Run the backend and regenerate Svelte API types:

```bash
uv run seektalent-ui-api --host 127.0.0.1 --port 8012 &
api_pid=$!
until curl -fsS http://127.0.0.1:8012/openapi.json >/dev/null; do sleep 0.2; done
cd apps/web-svelte && bun run api:gen
kill "$api_pid"
```

Expected: `apps/web-svelte/src/lib/api/schema.d.ts` contains `whySelected`, `riskFlags`, `matchedMustHaves`, `matchedPreferences`, `strengths`, `weaknesses`, and `sourceRound` under `WorkbenchFinalTopCandidateResponse`.

- [ ] **Step 2: Write a failing Svelte projection test**

In `apps/web-svelte/src/lib/workbench/finalCandidateCards.test.ts`, add fields to `baseFinalTop`:

```ts
whySelected: 'Runtime-selected explanation.',
riskFlags: ['Runtime risk'],
matchedMustHaves: ['Runtime hard match'],
matchedPreferences: ['Runtime preference'],
strengths: ['Runtime strength'],
weaknesses: ['Runtime weakness'],
sourceRound: 2,
```

Then add this test:

```ts
it('uses final-top10 business fields instead of review item side-channel fields', () => {
	const cards = buildFinalCandidateCards({
		finalTop: finalTopList([baseFinalTop]),
		reviewItems: [
			reviewItem('review-cts', {
				matchedMustHaves: ['Review hard match'],
				matchedPreferences: ['Review preference'],
				missingRisks: ['Review risk'],
				strengths: ['Review strength'],
				weaknesses: ['Review weakness']
			})
		]
	});

	const card = expectSingle(cards);
	expect(card.whySelected).toBe('Runtime-selected explanation.');
	expect(card.matchedMustHaves).toEqual(['Runtime hard match']);
	expect(card.matchedPreferences).toEqual(['Runtime preference']);
	expect(card.missingRisks).toEqual(['Runtime risk']);
	expect(card.strengths).toEqual(['Runtime strength']);
	expect(card.weaknesses).toEqual(['Runtime weakness']);
	expect(card.sourceRound).toBe(2);
});
```

- [ ] **Step 3: Run the failing Svelte test**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/workbench/finalCandidateCards.test.ts
```

Expected: fails because `FinalCandidateViewModel` and `buildFinalCandidateCards(...)` do not expose/use direct final-top10 fields.

- [ ] **Step 4: Update Svelte final card view model**

In `apps/web-svelte/src/lib/workbench/finalCandidateCards.ts`, add fields to `FinalCandidateViewModel`:

```ts
whySelected: string;
sourceRound: number | null;
weaknesses: string[];
```

Update the returned object inside `buildFinalCandidateCards(...)` so business fields come from `candidate`:

```ts
whySelected: candidate.whySelected,
matchedMustHaves: candidate.matchedMustHaves,
matchedPreferences: candidate.matchedPreferences,
missingRisks: candidate.riskFlags,
strengths: candidate.strengths,
weaknesses: candidate.weaknesses,
sourceRound: candidate.sourceRound ?? null,
```

Keep these fields from joined review items only:

```ts
status: canonicalItem?.status ?? null,
note: safeUserNote(canonicalItem?.note ?? ''),
actionReviewItemId: actionItem?.reviewItemId ?? null,
detailActionReviewItemId: detailActionItem?.reviewItemId ?? null,
providerActionReviewItemId: providerActionItem?.reviewItemId ?? null,
resumeGraphCandidateId: resumeItem?.graphCandidateId ?? null,
canExpandResume: Boolean(resumeItem),
```

- [ ] **Step 5: Run Svelte test**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/workbench/finalCandidateCards.test.ts
```

Expected: passes.

- [ ] **Step 6: Update all typed Svelte final-top fixtures and e2e mock payloads**

In every existing object that is typed as `WorkbenchFinalTopCandidate`, returned by `/final-top10` e2e mocks, or used by `BuildRunStoryInput.finalTopCandidates`, add the required direct final fields:

```ts
whySelected: 'Runtime selected this candidate for agent workflow depth.',
riskFlags: ['management scope unclear'],
matchedMustHaves: ['Python backend', 'distributed systems'],
matchedPreferences: ['agent tooling'],
strengths: ['Strong backend systems'],
weaknesses: ['Needs leadership calibration'],
sourceRound: 2,
```

Apply this to:

- `apps/web-svelte/src/lib/components/CandidateQueue.test.ts`, inside the `items` fixture that `satisfies WorkbenchFinalTopCandidate[]`
- `apps/web-svelte/src/lib/workbench/runStory.test.ts`, inside `finalTopCandidate(...)`
- `apps/web-svelte/tests/e2e/parityMockApi.ts`, inside the object returned by `reviewCandidate(...)` for `finalTop10(...)`
- `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`, inside `finalTop10.items[0]`

Do not add these fields to `WorkbenchCandidateReviewItem` fixtures unless those fixtures also represent `final-top10` response items. Review items are no longer the source for final business explanation fields.

- [ ] **Step 7: Add a failing component rendering test**

Create `apps/web-svelte/src/lib/components/CandidateReviewCard.test.svelte`:

```svelte
<script lang="ts">
	import { QueryClientProvider } from '@tanstack/svelte-query';
	import { createQueryClient } from '$lib/query/client';
	import type { FinalCandidateViewModel } from '$lib/workbench/finalCandidateCards';
	import CandidateReviewCard from './CandidateReviewCard.svelte';

	let { sessionId, card }: { sessionId: string; card: FinalCandidateViewModel } = $props();
	const queryClient = createQueryClient();
</script>

<QueryClientProvider client={queryClient}>
	<CandidateReviewCard {sessionId} {card} />
</QueryClientProvider>
```

Create `apps/web-svelte/src/lib/components/CandidateReviewCard.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { FinalCandidateViewModel } from '$lib/workbench/finalCandidateCards';
import CandidateReviewCardHarness from './CandidateReviewCard.test.svelte';

const card = {
	reviewItemId: 'review-final',
	runtimeIdentityId: 'identity-1',
	canonicalReviewItemId: 'review-final',
	mergedReviewItemIds: ['review-final'],
	rank: 1,
	displayName: 'Lin Qian',
	title: 'Senior Backend Engineer',
	company: 'SearchCo',
	location: 'Shanghai',
	summary: 'Runtime match summary.',
	aggregateScore: 94,
	fitBucket: 'fit',
	whySelected: 'Runtime selected this candidate for agent workflow depth.',
	sourceRound: 2,
	sourceBadges: ['CTS final', 'Liepin detail'],
	evidenceLevel: 'detail',
	sourceEvidence: [],
	actionReviewItemId: 'review-final',
	detailActionReviewItemId: null,
	providerActionReviewItemId: null,
	canRequestLiepinDetail: false,
	canOpenProviderAction: false,
	status: 'promising',
	note: '',
	mergedStateHint: null,
	resumeGraphCandidateId: null,
	canExpandResume: false,
	matchedMustHaves: ['Python backend', 'distributed systems'],
	matchedPreferences: ['agent tooling', 'recruiter workflow'],
	missingRisks: ['management scope unclear'],
	strengths: ['Strong backend systems'],
	weaknesses: ['Needs leadership calibration'],
	coverageExplanation: 'CTS and Liepin detail evidence are both available.',
	mergeExplanation: null,
	canonicalResumeHint: null
} satisfies FinalCandidateViewModel;

describe('CandidateReviewCard', () => {
	it('visibly renders runtime final-top10 business fields', () => {
		render(CandidateReviewCardHarness, { props: { sessionId: 'session-1', card } });

		expect(screen.getByText('选择理由')).toBeInTheDocument();
		expect(screen.getByText('Runtime selected this candidate for agent workflow depth.')).toBeInTheDocument();
		expect(screen.getByText('硬性匹配')).toBeInTheDocument();
		expect(screen.getByText('Python backend / distributed systems')).toBeInTheDocument();
		expect(screen.getByText('偏好匹配')).toBeInTheDocument();
		expect(screen.getByText('agent tooling / recruiter workflow')).toBeInTheDocument();
		expect(screen.getByText('优势')).toBeInTheDocument();
		expect(screen.getByText('Strong backend systems')).toBeInTheDocument();
		expect(screen.getByText('弱项')).toBeInTheDocument();
		expect(screen.getByText('Needs leadership calibration')).toBeInTheDocument();
		expect(screen.getByText('风险')).toBeInTheDocument();
		expect(screen.getByText('management scope unclear')).toBeInTheDocument();
		expect(screen.getByText('第 2 轮')).toBeInTheDocument();
	});
});
```

- [ ] **Step 8: Run the failing component test**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/components/CandidateReviewCard.test.ts
```

Expected: fails because `CandidateReviewCard.svelte` does not visibly render `whySelected`, `matchedPreferences`, `weaknesses`, or `sourceRound`.

- [ ] **Step 9: Render final-top10 fields in the candidate card**

In `apps/web-svelte/src/lib/components/CandidateReviewCard.svelte`, add a source-round badge after the rank badge:

```svelte
{#if card.sourceRound !== null}
	<span class="source-badge muted-badge">第 {card.sourceRound} 轮</span>
{/if}
```

Render the direct Runtime fields in the fact area:

```svelte
{#if card.whySelected}
	<div class="candidate-facts">
		<span>选择理由</span>
		<p>{card.whySelected}</p>
	</div>
{/if}
{#if card.matchedMustHaves.length > 0}
	<div class="candidate-facts">
		<span>硬性匹配</span>
		<p>{card.matchedMustHaves.slice(0, 4).join(' / ')}</p>
	</div>
{/if}
{#if card.matchedPreferences.length > 0}
	<div class="candidate-facts">
		<span>偏好匹配</span>
		<p>{card.matchedPreferences.slice(0, 4).join(' / ')}</p>
	</div>
{/if}
{#if card.strengths.length > 0}
	<div class="candidate-facts">
		<span>优势</span>
		<p>{card.strengths.slice(0, 4).join(' / ')}</p>
	</div>
{/if}
{#if card.weaknesses.length > 0}
	<div class="candidate-facts">
		<span>弱项</span>
		<p>{card.weaknesses.slice(0, 4).join(' / ')}</p>
	</div>
{/if}
{#if card.missingRisks.length > 0}
	<div class="candidate-facts">
		<span>风险</span>
		<p>{card.missingRisks.slice(0, 4).join(' / ')}</p>
	</div>
{/if}
```

- [ ] **Step 10: Add e2e coverage for visible final-card business fields**

In `apps/web-svelte/tests/e2e/workbench-parity.spec.ts`, extend the completed/partial candidate assertion in `models completed, login-required, and partial source states`:

```ts
const finalCard = page.getByTestId('candidate-card-identity-parity-1');
await expect(finalCard.getByText('选择理由')).toBeVisible();
await expect(finalCard.getByText('Runtime selected this candidate for agent workflow depth.')).toBeVisible();
await expect(finalCard.getByText('偏好匹配')).toBeVisible();
await expect(finalCard.getByText('agent tooling')).toBeVisible();
await expect(finalCard.getByText('弱项')).toBeVisible();
await expect(finalCard.getByText('Needs leadership calibration')).toBeVisible();
await expect(finalCard.getByText('风险')).toBeVisible();
await expect(finalCard.getByText('management scope unclear')).toBeVisible();
await expect(finalCard.getByText('第 2 轮')).toBeVisible();
```

This is the browser-level guard that proves the denser final-card content renders inside the real Workbench shell, not only in an isolated component test.

Remove or replace the old `strengths` block that labels strengths as `入围理由`; `whySelected` is now the selection reason.

- [ ] **Step 11: Run Svelte projection, component, and typed fixture tests**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/workbench/finalCandidateCards.test.ts src/lib/components/CandidateReviewCard.test.ts src/lib/components/CandidateQueue.test.ts src/lib/workbench/runStory.test.ts
```

Expected: passes.

- [ ] **Step 12: Run focused e2e parity test**

Run:

```bash
cd apps/web-svelte && bun run test:e2e -- workbench-parity.spec.ts
```

Expected: passes and the final candidate card visibly renders why selected, preference match, weaknesses, risks, and source round in the Workbench session shell.

- [ ] **Step 13: Commit**

Run:

```bash
git add apps/web-svelte/src/lib/api/schema.d.ts apps/web-svelte/src/lib/workbench/finalCandidateCards.ts apps/web-svelte/src/lib/workbench/finalCandidateCards.test.ts apps/web-svelte/src/lib/components/CandidateReviewCard.svelte apps/web-svelte/src/lib/components/CandidateReviewCard.test.svelte apps/web-svelte/src/lib/components/CandidateReviewCard.test.ts apps/web-svelte/src/lib/components/CandidateQueue.test.ts apps/web-svelte/src/lib/workbench/runStory.test.ts apps/web-svelte/tests/e2e/parityMockApi.ts apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts apps/web-svelte/tests/e2e/workbench-parity.spec.ts
git commit -m "feat: render final cards from runtime top10 contract"
```

## Task 3: Move Running-Note Runtime Fact Projection Into Runtime

**Files:**
- Create: `src/seektalent/runtime/public_notes.py`
- Modify: `src/seektalent_ui/workbench_note_writer.py`
- Test: `tests/test_runtime_public_notes.py`
- Test: `tests/test_workbench_note_writer.py`

- [ ] **Step 1: Add Runtime public-note tests**

Create `tests/test_runtime_public_notes.py`:

```python
from seektalent.runtime.public_notes import runtime_note_facts_from_events


def test_runtime_note_facts_extract_safe_public_runtime_counts() -> None:
    facts, numbers = runtime_note_facts_from_events(
        [
            {
                "eventName": "runtime_round_source_result",
                "payload": {
                    "schemaVersion": "runtime_public_event_v1",
                    "stage": "source_result",
                    "roundNo": 2,
                    "sourceKind": "liepin",
                    "status": "blocked",
                    "safeReasonCode": "source_risk_or_verification_required",
                    "counts": {"roundReturned": 7, "roundIdentities": 6},
                },
            },
            {
                "eventName": "runtime_finalization_completed",
                "payload": {
                    "schemaVersion": "runtime_public_event_v1",
                    "stage": "finalization",
                    "roundNo": None,
                    "sourceKind": None,
                    "status": "completed",
                    "counts": {"selectedIdentityCount": 10},
                },
            },
        ]
    )

    assert "runtime_source_result_round_2=seen" in facts
    assert "runtime_source_result_round_2_source=liepin" in facts
    assert "runtime_source_result_round_2_status=blocked" in facts
    assert "runtime_source_result_round_2_reason=source_risk_or_verification_required" in facts
    assert "runtime_source_result_round_2_roundReturned=7" in facts
    assert "runtime_source_result_round_2_roundIdentities=6" in facts
    assert "runtime_finalization=seen" in facts
    assert "runtime_finalization_selectedIdentityCount=10" in facts
    assert set(numbers) >= {2, 7, 6, 10}


def test_runtime_note_facts_ignore_technical_or_unknown_payload_keys() -> None:
    facts, numbers = runtime_note_facts_from_events(
        [
            {
                "eventName": "runtime_round_source_result",
                "payload": {
                    "schemaVersion": "runtime_public_event_v1",
                    "stage": "source_result",
                    "roundNo": 1,
                    "sourceKind": "cts",
                    "status": "completed",
                    "runtimeRunId": "secret-run-id",
                    "artifactPath": "/tmp/private.json",
                    "counts": {"roundReturned": 3, "secretCount": 99},
                },
            }
        ]
    )

    serialized = " ".join(facts)
    assert "secret-run-id" not in serialized
    assert "/tmp/private" not in serialized
    assert "secretCount" not in serialized
    assert numbers == [1, 3]
```

- [ ] **Step 2: Run the failing Runtime note tests**

Run:

```bash
uv run pytest tests/test_runtime_public_notes.py -q
```

Expected: fails because `seektalent.runtime.public_notes` does not exist.

- [ ] **Step 3: Implement Runtime public-note fact projection**

Create `src/seektalent/runtime/public_notes.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence

from seektalent.runtime.public_events import PUBLIC_EVENT_SCHEMA_VERSION

_SAFE_COUNT_KEYS = {
    "roundReturned",
    "roundIdentities",
    "sourceCumulativeReturned",
    "sourceCumulativeIdentities",
    "mergedIdentities",
    "topPoolCount",
    "selectedIdentityCount",
    "feedbackCandidateCount",
}


def runtime_note_facts_from_events(events: Sequence[Mapping[str, object]]) -> tuple[list[str], list[int]]:
    facts: list[str] = []
    numbers: list[int] = []
    for event in events[-25:]:
        payload = _mapping(event.get("payload"))
        if payload is None or payload.get("schemaVersion") != PUBLIC_EVENT_SCHEMA_VERSION:
            continue
        stage = _safe_token(payload.get("stage"))
        if not stage:
            continue
        round_no = _optional_int(payload.get("roundNo"))
        prefix = f"runtime_{stage}"
        if round_no is not None:
            numbers.append(round_no)
            prefix = f"{prefix}_round_{round_no}"
        facts.append(f"{prefix}=seen")
        source = _safe_token(payload.get("sourceKind"))
        if source:
            facts.append(f"{prefix}_source={source}")
        status = _safe_token(payload.get("status"))
        if status:
            facts.append(f"{prefix}_status={status}")
        reason = _safe_token(payload.get("safeReasonCode"))
        if reason:
            facts.append(f"{prefix}_reason={reason}")
        counts = _mapping(payload.get("counts"))
        if counts is None:
            continue
        for key, raw_value in counts.items():
            if not isinstance(key, str) or key not in _SAFE_COUNT_KEYS:
                continue
            value = _optional_int(raw_value)
            if value is None:
                continue
            numbers.append(value)
            facts.append(f"{prefix}_{key}={value}")
    return facts, numbers


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _safe_token(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    result = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in text)
    return result[:80]


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
```

- [ ] **Step 4: Route Workbench note writer through Runtime fact projection**

In `src/seektalent_ui/workbench_note_writer.py`, import:

```python
from seektalent.runtime.public_notes import runtime_note_facts_from_events
```

Replace:

```python
    runtime_facts, runtime_numbers = _runtime_business_facts(runtime_events)
```

with:

```python
    runtime_facts, runtime_numbers = runtime_note_facts_from_events(
        [
            {"eventName": event.event_name, "payload": event.payload}
            for event in runtime_events
        ]
    )
```

Then delete the old `_runtime_business_facts(...)` helper and its helper code that is no longer used only by that function. Keep generic helpers still used elsewhere in the file.

- [ ] **Step 5: Update note-writer test to prove Runtime ownership**

Add to `tests/test_workbench_note_writer.py`:

```python
def test_note_context_uses_runtime_public_note_facts(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _session_with_sources(store)
    store.append_workbench_event(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        user_id=user.user_id,
        session_id=session.session_id,
        source_run_id=None,
        source_kind=None,
        event_name="runtime_round_scoring_completed",
        schema_version="runtime_public_event_v1",
        payload={
            "schemaVersion": "runtime_public_event_v1",
            "runtimeRunId": "private-run-id",
            "eventId": "event-1",
            "eventSeq": 1,
            "stage": "scoring",
            "roundNo": 1,
            "sourceKind": None,
            "status": "completed",
            "counts": {"topPoolCount": 8},
            "safeReasonCode": None,
            "createdAt": "2026-05-26T00:00:00+08:00",
        },
    )

    context = build_workbench_note_context(store=store, user=user, session_id=session.session_id)

    assert context is not None
    assert "runtime_scoring_round_1_topPoolCount=8" in context["recentBusinessFacts"]
    assert "private-run-id" not in " ".join(context["recentBusinessFacts"])
```

If helper names in `tests/test_workbench_note_writer.py` differ, use the existing local session fixture helper in that file and keep the same assertions.

- [ ] **Step 6: Run note tests**

Run:

```bash
uv run pytest tests/test_runtime_public_notes.py tests/test_workbench_note_writer.py -q
```

Expected: passes.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/seektalent/runtime/public_notes.py src/seektalent_ui/workbench_note_writer.py tests/test_runtime_public_notes.py tests/test_workbench_note_writer.py
git commit -m "refactor: move runtime note facts into runtime"
```

## Task 4: Remove Old Primary Source-Run Execution Path

**Files:**
- Modify: `src/seektalent_ui/job_runner.py`
- Modify: `src/seektalent_ui/runtime_bridge.py`
- Modify: `src/seektalent_ui/workbench_store.py`
- Modify: `src/seektalent_ui/server.py`
- Modify: `tests/test_workbench_runtime_owned_execution.py`
- Modify: `tests/test_workbench_semantic_guardrails.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Inventory active primary source-run references**

Run:

```bash
rg -n "run_cts_source_run|run_liepin_card_source_run|start_source_run_job|claim_next_source_run_job|complete_cts_source_run_with_candidate_results|complete_liepin_card_source_run_with_lane_result|complete_liepin_source_run_with_lane_result|mark_source_run_failed|reconcile_expired_running_jobs|source_run_jobs" src/seektalent_ui tests/test_workbench_runtime_owned_execution.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py tests/test_workbench_liepin_browser_session_probe.py
```

Expected: matches are limited to the legacy implementation and tests being edited in this task:

- `src/seektalent_ui/job_runner.py`
- `src/seektalent_ui/runtime_bridge.py`
- `src/seektalent_ui/workbench_store.py`
- `src/seektalent_ui/server.py`
- `tests/test_workbench_runtime_owned_execution.py`
- `tests/test_workbench_semantic_guardrails.py`
- `tests/test_workbench_api.py`
- `tests/test_workbench_liepin_browser_session_probe.py`

If additional active files match, add them to this task before editing. Do not leave any active primary source-run queue reference outside historical migrations/maintenance.

- [ ] **Step 2: Add public cleanup contract tests**

Add to `tests/test_workbench_runtime_owned_execution.py`:

```python
def test_workbench_store_no_longer_exposes_primary_source_run_queue_api() -> None:
    removed_methods = {
        "start_source_run_job",
        "claim_next_source_run_job",
        "extend_source_run_job_lease",
        "complete_cts_source_run_with_candidate_results",
        "complete_liepin_card_source_run_with_lane_result",
        "complete_liepin_source_run_with_lane_result",
        "mark_source_run_failed",
        "reconcile_expired_running_jobs",
    }

    for method_name in removed_methods:
        assert not hasattr(WorkbenchStore, method_name), method_name
```

Add imports if missing:

```python
from seektalent_ui.workbench_store import WorkbenchStore
```

Update `tests/test_workbench_runtime_owned_execution.py::test_starting_dual_source_session_does_not_enqueue_primary_source_run_jobs` so it still asserts the active path creates one `runtime_sourcing_jobs` row and zero `source_run_jobs` rows for the session. This keeps the historical table as a non-active projection while proving the primary runtime path is single-job.

- [ ] **Step 3: Run the failing cleanup contract tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py::test_workbench_store_no_longer_exposes_primary_source_run_queue_api tests/test_workbench_runtime_owned_execution.py::test_starting_dual_source_session_does_not_enqueue_primary_source_run_jobs -q
```

Expected: the new cleanup test fails because `WorkbenchStore` still exposes primary source-run queue methods.

- [ ] **Step 4: Remove source-run workers from job runner**

In `src/seektalent_ui/job_runner.py`:

Remove these imports:

```python
    run_cts_source_run,
    run_liepin_card_source_run,
```

Remove constants:

```python
CTS_WORKER_COUNT = 2
LIEPIN_WORKER_COUNT = 1
```

Remove this instance field:

```python
self._threads: dict[Literal["cts", "liepin"], list[threading.Thread]] = {"cts": [], "liepin": []}
```

Change `wake(...)` to:

```python
    def wake(self) -> None:
        with self._lock:
            self._start_runtime_workers(worker_count=RUNTIME_WORKER_COUNT)
            self._start_liepin_detail_workers(worker_count=LIEPIN_DETAIL_WORKER_COUNT)
```

Delete methods and any direct calls to primary source-run claim/complete/fail APIs:

```python
_start_lane_workers
_run_until_idle
_execute
_record_runtime_progress
_start_lease_heartbeat
_lease_heartbeat_loop
_tick_note_writer
```

If `_tick_note_writer(...)` is still referenced only by deleted methods, remove it. Keep `_tick_note_writer_for_session(...)`.

- [ ] **Step 5: Remove primary source-run bridge helpers**

In `src/seektalent_ui/runtime_bridge.py`, delete:

```python
run_cts_source_run
run_liepin_card_source_run
```

Keep:

```python
extract_requirement_review
run_runtime_sourcing_job
run_liepin_detail_open_intent
```

Remove imports that become unused:

```python
Sequence
QueryTermCandidate
WorkbenchSourceRunJobContext
```

Keep `RuntimeSourceLaneRequest` and `RuntimeApprovedDetailLease` for detail-open intent.

- [ ] **Step 6: Remove active source-run job store methods**

In `src/seektalent_ui/workbench_store.py`, delete active methods:

```python
start_source_run_job
claim_next_source_run_job
extend_source_run_job_lease
complete_cts_source_run_with_candidate_results
complete_liepin_card_source_run_with_lane_result
complete_liepin_source_run_with_lane_result
mark_source_run_failed
reconcile_expired_running_jobs
```

Keep helper methods used by runtime sourcing, source card projection, detail requests, migration, and maintenance. Keep historical table creation and migration code for `source_run_jobs` if existing local databases still need it, but it must not be claimed, leased, reconciled, or completed by active runtime code.

- [ ] **Step 7: Remove source-run startup reconcile from the app server**

In `src/seektalent_ui/server.py`, remove:

```python
app.state.workbench_store.reconcile_expired_running_jobs()
```

Keep runtime job reconciliation through `reconcile_expired_runtime_sourcing_jobs(...)` wherever it is already called by `WorkbenchStore`.

- [ ] **Step 8: Convert or delete active tests that still exercise primary source-run jobs**

In `tests/test_workbench_semantic_guardrails.py`, remove helpers that only create primary source-run jobs through `start_source_run_job(...)` and `claim_next_source_run_job(...)`. Convert the following tests to use runtime-sourcing artifacts through `WorkbenchStore.start_runtime_sourcing_job(...)`, `WorkbenchStore.claim_next_runtime_sourcing_job(...)`, and `WorkbenchStore.complete_runtime_sourcing_job_with_artifacts(...)`, or delete the assertion when the behavior belonged only to the deleted queue:

- `test_liepin_lane_result_statuses_do_not_fake_complete_source_runs`
- `test_completion_paths_do_not_persist_field_derived_runtime_identity_ids`

In `tests/test_workbench_api.py`, convert or delete primary source-run completion tests that directly call `complete_cts_source_run_with_candidate_results(...)` or inspect `source_run_jobs` as an active job table:

- `test_cts_completion_retry_rejects_runtime_run_id_conflict_after_completion`
- `test_cts_runtime_link_repair_is_idempotent_for_missing_source_run_link`
- `test_cts_source_runs_can_execute_in_parallel`
- `test_liepin_source_run_can_complete_while_cts_is_running`

The runtime-owned replacement assertions should use existing runtime job tests where possible:

```python
store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="runtime")
context = store.claim_next_runtime_sourcing_job(
    owner_id="test-runtime-worker",
    lease_expires_at=_lease_time(),
)
assert context is not None
store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)
```

In `tests/test_workbench_liepin_browser_session_probe.py`, update `RuntimeSourceStartRecorder` so it records `start_runtime_sourcing_job(...)` calls rather than calling `start_source_run_job(...)`. Repeated-start tests should assert a single runtime sourcing job remains active for the session.

- [ ] **Step 9: Run cleanup search**

Run:

```bash
rg -n "run_cts_source_run|run_liepin_card_source_run|start_source_run_job|claim_next_source_run_job|complete_cts_source_run_with_candidate_results|complete_liepin_card_source_run_with_lane_result|complete_liepin_source_run_with_lane_result|mark_source_run_failed|reconcile_expired_running_jobs" src/seektalent_ui tests
```

Expected: no matches except comments in this plan or historical docs outside `src`/active tests. If active tests still match, update or delete those tests in this task.

- [ ] **Step 10: Run Workbench runtime tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py tests/test_workbench_liepin_browser_session_probe.py -q
```

Expected: passes.

- [ ] **Step 11: Commit**

Run:

```bash
git add src/seektalent_ui/job_runner.py src/seektalent_ui/runtime_bridge.py src/seektalent_ui/workbench_store.py src/seektalent_ui/server.py tests/test_workbench_runtime_owned_execution.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py tests/test_workbench_liepin_browser_session_probe.py
git commit -m "refactor: remove legacy primary source-run workers"
```

## Task 5: Remove Legacy `/api/runs` API And Mapper

**Files:**
- Modify: `src/seektalent_ui/server.py`
- Modify: `src/seektalent_ui/__init__.py`
- Modify: `src/seektalent_ui/models.py`
- Delete: `src/seektalent_ui/mapper.py`
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`
- Modify: `tests/test_workbench_security_audit.py`
- Modify: `tests/test_workbench_semantic_guardrails.py`
- Modify: `tests/test_workbench_auth_security.py`
- Modify: `tests/test_dev_mode_readiness.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_workbench_network_guard.py`
- Modify: `tests/test_liepin_boundaries.py`
- Modify: `tests/test_liepin_api_scope.py`
- Delete: `tests/test_ui_api.py`
- Delete: `tests/test_ui_mapper.py`
- Modify: `docs/architecture.md`
- Modify: `docs/architecture-dependencies.md`

- [ ] **Step 1: Inventory all legacy run API references**

Run:

```bash
rg -n "/api/runs|RunRegistry|create_server|RunCreateRequest|RunCreateResponse|RunStatusResponse|AgentShortlistCandidate|CandidateDetailResponse|LiepinRunStatusResponse|LiepinRunResultsResponse|seektalent_ui.mapper" src tests apps scripts docs --glob '!docs/superpowers/**' --glob '!docs/v-0.2/**'
```

Expected: matches exist in `src/seektalent_ui/server.py`, `src/seektalent_ui/models.py`, `src/seektalent_ui/mapper.py`, `src/seektalent_ui/__init__.py`, generated Svelte schema, active tests, and active docs. Every match must be removed or moved to an active Workbench or `/api/liepin` route in this task. `docs/v-0.2/**` is a historical archive and is intentionally excluded from active cleanup scans.

- [ ] **Step 2: Add removal guard tests**

In `tests/test_workbench_api.py`, add:

```python
def test_legacy_runs_api_is_removed(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.post(
        "/api/runs",
        json={"jobTitle": "Backend Engineer", "jdText": "Python", "sourcingPreferenceText": ""},
    ).status_code == 404
    assert client.post("/api/runs/legacy-run-id/stream-token").status_code == 404
    assert client.get("/api/runs/legacy-run-id/events").status_code == 404
    assert client.get("/api/runs/legacy-run-id/results").status_code == 404
    assert client.get("/api/runs/legacy-run-id/candidates/candidate-1").status_code == 404
    assert client.get("/api/runs/legacy-run-id").status_code == 404
```

In `tests/test_liepin_api_scope.py`, add:

```python
def test_legacy_runs_routes_are_removed_from_liepin_scoped_api(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.post("/api/runs", headers=API_HEADERS, json={"provider": "liepin"}).status_code == 404
    assert client.post("/api/runs/run-1/stream-token", headers=API_HEADERS).status_code == 404
    assert client.get("/api/runs/run-1/events").status_code == 404
    assert client.get("/api/runs/run-1/results", headers=API_HEADERS).status_code == 404
    assert client.get("/api/runs/run-1", headers=API_HEADERS).status_code == 404
```

- [ ] **Step 3: Run the failing removal guard tests**

Run:

```bash
uv run pytest tests/test_workbench_api.py::test_legacy_runs_api_is_removed tests/test_liepin_api_scope.py::test_legacy_runs_routes_are_removed_from_liepin_scoped_api -q
```

Expected: fails because `/api/runs` routes still exist.

- [ ] **Step 4: Remove `RunRegistry` and `create_server`**

In `src/seektalent_ui/server.py`, delete:

```python
UiRunRecord
RunNotFoundError
CandidateNotFoundError
RunNotReadyError
RunRegistry
create_server
```

Remove imports that exist only for those deleted symbols:

```python
re
threading
uuid
field
HTTPStatus
BaseHTTPRequestHandler
ThreadingHTTPServer
unquote
urlparse
build_ui_payloads
CandidateDetailResponse
RunCreateRequest
RunCreateResponse
RunStatus
RunStatusResponse
LiepinRunResultsResponse
LiepinRunStatusResponse
```

Change the app factory signature from registry-owned runtime injection to direct runtime injection:

```python
def create_app(
    settings: AppSettings | None = None,
    *,
    runtime_factory=WorkflowRuntime,
    network_guard: NetworkGuard | None = None,
    dev_mode_env_diagnostics: DevModeStatus | None = None,
) -> FastAPI:
    app_settings = settings or AppSettings()
```

Update the job runner construction:

```python
app.state.workbench_job_runner = WorkbenchJobRunner(
    store=app.state.workbench_store,
    settings=app_settings,
    runtime_factory=runtime_factory,
)
```

Update `main(...)` to call:

```python
create_app(
    settings=settings,
    runtime_factory=WorkflowRuntime,
    network_guard=network_guard,
    dev_mode_env_diagnostics=dev_mode_env_diagnostics,
)
```

In `src/seektalent_ui/__init__.py`, replace the file with:

```python
from seektalent_ui.server import main

__all__ = ["main"]
```

- [ ] **Step 5: Remove legacy route handlers**

In `src/seektalent_ui/server.py`, delete the route handlers and helpers:

```python
@app.post("/api/runs", ...)
@app.post("/api/runs/{run_id}/stream-token", ...)
@app.get("/api/runs/{run_id}/events")
@app.get("/api/runs/{run_id}/results")
@app.get("/api/runs/{run_id}/candidates/{candidate_id}")
@app.get("/api/runs/{run_id}")
optional_scope
_liepin_run_status
_liepin_run_counters
```

Keep `/api/liepin/compliance-gates`, `/api/liepin/connections`, `/api/liepin/connections/{connection_id}/stream-token`, and `/api/liepin/connections/{connection_id}/events`.

- [ ] **Step 6: Remove old models, mapper, and old API-only tests**

In `src/seektalent_ui/models.py`, delete old one-run models:

```python
RunStatus
RunCreateRequest
RunCreateResponse
AgentShortlistCandidate
ResumeWorkExperienceItem
ResumeEducationItem
ResumeProjection
CandidateCard
CandidateResumeView
ResumeAnalysis
CandidateDetailResponse
RunStatusResponse
LiepinRunResultsResponse
LiepinRunStatusResponse
```

Delete files:

```bash
git rm src/seektalent_ui/mapper.py
git rm tests/test_ui_api.py tests/test_ui_mapper.py
```

Move no assertions from those files unless they cover an active Workbench route. The old `/api/runs` runtime and mapper behavior is intentionally deleted, not preserved as compatibility.

- [ ] **Step 7: Update tests and docs to the direct Workbench app factory**

Replace test imports:

```python
from seektalent_ui.server import RunRegistry, create_app
```

with:

```python
from seektalent_ui.server import create_app
```

Replace app construction:

```python
create_app(RunRegistry(settings), settings=settings)
```

with:

```python
create_app(settings=settings)
```

Replace runtime-factory app construction:

```python
create_app(RunRegistry(settings, runtime_factory=runtime_factory), settings=settings)
```

with:

```python
create_app(settings=settings, runtime_factory=runtime_factory)
```

Apply those replacements in:

- `tests/test_workbench_security_audit.py`
- `tests/test_workbench_semantic_guardrails.py`
- `tests/test_workbench_auth_security.py`
- `tests/test_dev_mode_readiness.py`
- `tests/test_workbench_api.py`
- `tests/test_workbench_network_guard.py`
- `tests/test_liepin_boundaries.py`
- `tests/test_liepin_api_scope.py`

In `tests/test_liepin_boundaries.py`, delete tests that inspect `create_server` source. Keep boundary tests that inspect or exercise active `/api/liepin` and Workbench routes.

In `tests/test_liepin_api_scope.py`, delete or replace tests that create or stream old Liepin runs through `/api/runs`. Keep connection/compliance scope tests on `/api/liepin/*`.

In `tests/test_workbench_network_guard.py`, replace legacy host/origin guard probes against `/api/runs` with equivalent probes against active Workbench routes, for example:

```python
client.post("/api/workbench/sessions", headers={"Host": "evil.example"}, json={})
```

In `docs/architecture.md`, remove the paragraph that says the local web UI follows `RunRegistry.create_run(...)` and `src/seektalent_ui/mapper.py`. Replace it with a sentence that the Workbench UI starts `runtime_sourcing_jobs` through `workbench_routes` and renders `/api/workbench/sessions/{session_id}/final-top10`.

In `docs/architecture-dependencies.md`, replace the `seektalent_ui.mapper` dependency description with:

```markdown
`seektalent_ui` 依赖 `seektalent.runtime` 和共享 core 文件，主要依赖来自 UI server、Workbench routes、Workbench store 和 UI models：

- `seektalent_ui.server` 启动 FastAPI、Workbench store、runtime job runner、Liepin compliance/connection routes。
- `seektalent_ui.workbench_routes` 负责 Workbench session、requirement review、runtime sourcing job、candidate review、final-top10 API。
- `seektalent_ui.workbench_store` 持久化 Workbench session、runtime sourcing jobs、source projections、candidate review items、detail approvals、events。
- `seektalent_ui.models` 定义 Workbench API response/request contracts。
```

- [ ] **Step 8: Regenerate Svelte API schema after route removal**

Run:

```bash
uv run seektalent-ui-api --host 127.0.0.1 --port 8012 &
api_pid=$!
until curl -fsS http://127.0.0.1:8012/openapi.json >/dev/null; do sleep 0.2; done
cd apps/web-svelte && bun run api:gen
kill "$api_pid"
```

Expected: `apps/web-svelte/src/lib/api/schema.d.ts` no longer contains `/api/runs`, `RunCreateRequest`, `RunStatusResponse`, `AgentShortlistCandidate`, `CandidateDetailResponse`, `LiepinRunStatusResponse`, or `LiepinRunResultsResponse`.

- [ ] **Step 9: Run legacy cleanup search**

Run:

```bash
rg -n "/api/runs|RunRegistry|create_server|RunCreateRequest|RunCreateResponse|RunStatusResponse|AgentShortlistCandidate|CandidateDetailResponse|LiepinRunStatusResponse|LiepinRunResultsResponse|seektalent_ui.mapper" src tests apps scripts docs --glob '!docs/superpowers/**' --glob '!docs/v-0.2/**'
```

Expected: no matches in active code, active tests, apps, scripts, or active docs.

- [ ] **Step 10: Run API tests**

Run:

```bash
uv run pytest tests/test_workbench_api.py tests/test_workbench_security_audit.py tests/test_workbench_auth_security.py tests/test_dev_mode_readiness.py tests/test_workbench_network_guard.py tests/test_liepin_boundaries.py tests/test_liepin_api_scope.py -q
```

Expected: passes.

- [ ] **Step 11: Commit**

Run:

```bash
git add src/seektalent_ui/server.py src/seektalent_ui/__init__.py src/seektalent_ui/models.py apps/web-svelte/src/lib/api/schema.d.ts tests/test_workbench_security_audit.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_auth_security.py tests/test_dev_mode_readiness.py tests/test_workbench_api.py tests/test_workbench_network_guard.py tests/test_liepin_boundaries.py tests/test_liepin_api_scope.py docs/architecture.md docs/architecture-dependencies.md
git add -u src/seektalent_ui/mapper.py tests/test_ui_api.py tests/test_ui_mapper.py
git commit -m "refactor: remove legacy runs api"
```

## Task 6: Delete Old React UI And Update Active Frontend Pointers

**Files:**
- Delete: `apps/web`
- Modify: `src/seektalent/cli.py`
- Modify: `docs/ui.md`
- Modify: `docs/cli.md`
- Modify: scripts/tests that actively point to `apps/web`

- [ ] **Step 1: Add active frontend guard test**

In `tests/test_cli.py`, add:

```python
def test_ui_info_defaults_to_svelte_frontend() -> None:
    result = CliRunner().invoke(app, ["ui", "info", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["default_frontend"] == "apps/web-svelte"
```

If `tests/test_cli.py` uses a different Typer runner helper, use the existing helper in that file and keep the same assertion.

- [ ] **Step 2: Run the failing frontend pointer test**

Run:

```bash
uv run pytest tests/test_cli.py::test_ui_info_defaults_to_svelte_frontend -q
```

Expected: fails because CLI still reports `apps/web`.

- [ ] **Step 3: Update CLI frontend metadata**

In `src/seektalent/cli.py`, replace:

```python
"default_frontend": "apps/web",
```

with:

```python
"default_frontend": "apps/web-svelte",
```

- [ ] **Step 4: Delete React app**

Run:

```bash
git rm -r apps/web
```

- [ ] **Step 5: Update active docs**

In `docs/ui.md`, make `apps/web-svelte` the only frontend app in active docs:

```markdown
- Frontend app: `apps/web-svelte`
```

Replace the loopback startup commands with:

```bash
cd apps/web-svelte
bun install
bun run dev -- --host 127.0.0.1 --port 5178
```

Remove language that says React remains the golden master.

Remove active verification commands that reference deleted tests such as `tests/test_ui_api.py` or `tests/test_ui_mapper.py`. Replace them with the surviving Workbench test set from Task 5.

In `docs/cli.md`, replace active `apps/web` references with `apps/web-svelte`.

- [ ] **Step 6: Run active frontend pointer search**

Run:

```bash
rg -n "apps/web(?!-svelte)" src tests apps scripts docs --pcre2 --glob '!docs/superpowers/**' --glob '!docs/v-0.2/**'
```

Expected: no active references. `docs/v-0.2/**` remains a historical archive and is excluded intentionally.

- [ ] **Step 7: Run Svelte verification**

Run:

```bash
./scripts/verify-dev-workbench.sh
```

Expected: passes.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/seektalent/cli.py docs/ui.md docs/cli.md tests/test_cli.py
git add -u apps/web
git commit -m "refactor: remove legacy react workbench"
```

## Task 7: Full Verification And Cleanup Scan

**Files:**
- No new files expected.
- Modify only files required by failing verification.

- [ ] **Step 1: Run backend test suite slice**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py tests/test_runtime_source_lanes.py tests/test_runtime_candidate_identity.py tests/test_finalizer_contract.py tests/test_workbench_runtime_owned_execution.py tests/test_workbench_api.py tests/test_workbench_note_writer.py tests/test_runtime_public_notes.py tests/test_workbench_security_audit.py tests/test_workbench_auth_security.py tests/test_dev_mode_readiness.py tests/test_workbench_network_guard.py tests/test_liepin_boundaries.py tests/test_liepin_api_scope.py -q
```

Expected: passes.

- [ ] **Step 2: Run typecheck**

Run:

```bash
uv run --group dev ty check src tests
```

Expected: passes.

- [ ] **Step 3: Run Svelte checks**

Run:

```bash
cd apps/web-svelte && bun run check && bun run lint && bun run test && bun run build && bun run test:e2e
```

Expected: passes.

- [ ] **Step 4: Run full dev Workbench verification script**

Run:

```bash
./scripts/verify-dev-workbench.sh
```

Expected: passes, including OpenAPI schema regeneration check, Svelte lint/check/test/build, focused parity e2e, and real-backend smoke routes.

- [ ] **Step 5: Run contract cleanup scans**

Run:

```bash
rg -n "run_cts_source_run|run_liepin_card_source_run|start_source_run_job|claim_next_source_run_job" src/seektalent_ui tests
rg -n "/api/runs|RunRegistry|create_server|RunCreateRequest|RunCreateResponse|RunStatusResponse|AgentShortlistCandidate|CandidateDetailResponse|LiepinRunStatusResponse|LiepinRunResultsResponse|seektalent_ui.mapper" src tests apps scripts docs --glob '!docs/superpowers/**' --glob '!docs/v-0.2/**'
rg -n "apps/web(?!-svelte)" src tests apps scripts docs --pcre2 --glob '!docs/superpowers/**' --glob '!docs/v-0.2/**'
test ! -d apps/web
```

Expected:

- all `rg` commands return no matches
- `test ! -d apps/web` exits `0`

- [ ] **Step 6: Run code-size baseline**

Run:

```bash
tokei src apps scripts tests --exclude apps/web-svelte/node_modules --exclude .seektalent
```

Expected: record the output in the PR description. The codebase should be materially smaller after deleting `apps/web`, legacy mapper, and legacy API paths.

- [ ] **Step 7: Run diff hygiene**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. `git status --short` shows only intentional tracked changes.

- [ ] **Step 8: Commit verification fixes**

If verification required small fixes, commit them:

```bash
git status --short
git add <only-the-specific-files-you-changed-for-this-verification-fix>
git commit -m "test: verify workbench runtime contract alignment"
```

If no fixes were required, do not create an empty commit.

## Self-Review Checklist

- Spec coverage: final-top10 direct fields are covered by Tasks 1 and 2; visible card rendering is covered by Task 2 component and e2e tests; running notes by Task 3; primary source-run cleanup by Task 4; old API and React deletion by Tasks 5 and 6; verification by Task 7.
- Type consistency: backend response fields use camelCase Pydantic names and Svelte generated OpenAPI aliases.
- Fixture consistency: typed Svelte fixtures and e2e mock final-top10 payloads receive the same required fields as generated OpenAPI types.
- Boundary consistency: Runtime modules do not import `seektalent_ui`; Workbench may import `seektalent.runtime.public_notes`.
- Cleanup consistency: `source_runs` rows remain for source cards; `source_run_jobs` primary workers are removed from active execution.
- API cleanup consistency: `RunRegistry`, `create_server`, `/api/runs`, old run models, old mapper, generated schema entries, old tests, and active docs are removed together. Historical `docs/v-0.2/**` is excluded from active scans.
- Safety consistency: final-top10 exposes safe Runtime final fields, not raw provider payloads or private runtime paths.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| Eng Review | `fw-plan-review` | Architecture, tests, cleanup safety | 2 | REPAIRED - NEEDS RERUN | Run 2 blockers patched: Task 2 now updates typed Svelte fixtures and e2e mock payloads; Tasks 5-7 exclude historical `docs/v-0.2/**` while cleaning active docs; Task 7 now runs lint/e2e and the dev Workbench verification script. |
| Design Review | `fw-plan-review` | Final candidate UI fields change | 2 | REPAIRED - NEEDS RERUN | Run 2 design blocker patched: Task 2 now adds browser-level e2e assertions that visible final-card business fields render inside the real Workbench shell. |

UNRESOLVED: 0 known run-2 plan gaps remain. Gate still requires a fresh `fw-plan-review`.

VERDICT: PLAN REPAIRED — rerun `fw-plan-review`; do not enter `fw-build` until the gate clears.
