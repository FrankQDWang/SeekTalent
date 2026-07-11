# Query Novelty, Candidate Quality, And First-Page Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one SeekTalent release that enforces run-wide non-anchor keyword-family novelty, reduces the Workbench keyword section to one main-path and one expansion-path keyword list, restores Liepin `3/2` baseline detail targets, computes total candidate score deterministically, hides candidates below 60 in Workbench, and always expands every high-quality logical-query lane across the remainder of its original first page without pagination.

**Architecture:** Runtime owns query-family consumption, scoring policy, lane-quality decisions, candidate merge, and reflection; Liepin owns browser navigation and private first-page detail URLs. Planning resolves and persists non-anchor family IDs, then enforces family disjointness before dispatch. The Workbench projection narrows rich runtime query evidence to two minimal keyword paths. Each baseline Liepin query freezes its original first page as a protected opaque continuation, returns its initial `3/2` candidates, and is scored normally. Runtime then applies a fixed deterministic quality gate and asks the source adapter to consume every remaining candidate in each qualified continuation before reflection.

**Tech Stack:** Python 3.12+, Pydantic v2, PydanticAI structured output, asyncio, OpenCLI/Liepin, protected local artifacts, SQLite-backed runtime control, FastAPI BFF, pytest, Ruff, ty, React/TypeScript contract verification.

**Execution status (2026-07-12):** Tasks 0–8 and the independent Brooks `CLEAR` loop are complete. The v0.7.40 package, packaged React bundle, exact Domi-Python installation, deterministic acceptance fixtures, focused suites, and privacy gates are verified. The real production/Chrome acceptance is explicitly blocked: the isolated execution process has the exact Domi Python and an app-owned Domi Node, but does not have `SEEKTALENT_DOMI_JWT`; the supplied credential was not copied into a command, file, report, or log. This Task 9 attempt launched no server, but read-only inspection found a pre-existing Domi-owned wrapper/server pair listening on `127.0.0.1:8011`, so the required fail-fast port-ownership gate also blocks Step 6. The existing processes were not stopped or adopted, and no live acceptance is asserted. `uv run ty check` also retains eight unrelated pre-existing diagnostics under `experiments/`, and `verify-source-decoupling.sh` retains the pre-existing concrete-source branch in `runtime/normalized_artifacts.py`; neither was changed as part of this plan. The release commit/index scope is clean; the worktree intentionally retains untracked `.superpowers/sdd` review evidence, so the literal empty-worktree invariant is not met.

## Global Constraints

- Deliver all behavior in one release; task commits are review boundaries, not separately released product slices.
- Execute with Superpowers subagent-driven development: one active implementation owner per task, with GPT-5.6 Terra at highest reasoning when the execution surface exposes model controls. Independent Gate R reviewers stay read-only and use the separately requested Sol profile.
- The compiler primary-anchor family is the only family allowed to repeat. Every non-anchor family in a logical query with any `dispatch_started=True` receipt is consumed for the rest of the run, across rounds, sources, exploit, generic explore, and PRF lanes.
- A preflight-blocked query consumes no family. Completed, partial, and failed-after-start queries consume every persisted non-anchor family exactly once at logical-query level.
- Workbench renders only `主路径` and, when an actual second lane exists, `扩展路径`, each followed by one deduplicated plain-text keyword line. Do not render a visible `关键词` heading, cards, borders, backgrounds, pills/chips, microcopy, `keywordQuery`, lifecycle/status, source/provider rows, raw/new/duplicate counts, query IDs, or term-group keys in that section.
- Liepin production defaults are `exploit=3` and every second lane (`generic_explore` or `prf_probe`) at 2; round 1 therefore targets 3, and later dual-lane rounds target 5. Preserve the existing validated target overrides; only first-page expansion itself and its 30-card/one-page scan are fixed product behavior.
- A skipped duplicate never consumes a lane target. The workflow continues down the same frozen first page until the target is met or no eligible card remains.
- First-page expansion is a fixed product feature. Do not add a feature flag, environment variable, settings field, rollout percentage, or disabled mode.
- “First page” means the original ordered set of at most 30 visible Liepin cards captured by that exact logical query. Never issue a pagination command and never re-run the search to reconstruct the page.
- A lane expands only when its full baseline target was scored successfully and every baseline candidate has `fit_bucket == "fit"`, `overall_score >= 80`, and `must_have_match_score >= 70`; when risk is applicable, every baseline candidate must also have `risk_score <= 30`.
- The run-owned `DetailOpenClaimLedger` remains authoritative before every browser detail open, including expansion opens.
- Must-have scoring is always applicable. Preferred and risk dimensions are applicable only when the canonical Requirement Sheet supplies their standards.
- Base weights are `must=60`, `preferred=25`, and inverted risk `100-risk=15`; remove inapplicable dimensions and renormalize the remaining weights to 100.
- Exact renormalization is: all dimensions `60/25/15`; no risk `60/85` and `25/85`; no preferred `60/75` and `15/75`; only must-have `100%`. `preferred_query_terms` alone do not make preferred scoring applicable because they are retrieval vocabulary, not a preference standard.
- LLM scoring output must not contain `overall_score`. Runtime computes it with deterministic round-half-up semantics and clamps it to `0..100`.
- `preferred_match_score` is `None` when no preferred standard exists. `risk_score` is `None` when no exclusion standard exists. Those nullable values are the single persisted applicability truth; do not add parallel applicability booleans. Resume completeness affects confidence, not role risk applicability.
- Workbench candidate lists show only candidates with a persisted deterministic `overall_score >= 60`. Lower scores remain in runtime state, reflection context, diagnostics, and audit records.
- Raw detail URLs, `res_id_encode`, browser refs, and provider candidate hashes stay in protected provider storage. Public events and Workbench DTOs may expose safe counts only.
- Expansion provider failures and expansion-only scoring failures are lane-local and yield a truthful partial expansion outcome; neither may discard already-scored baseline candidates nor fail the entire round.
- Preserve one logical query receipt per original source intent. Expansion augments that receipt; it is not a second logical query and does not consume another `term_group_key`.
- Keep count semantics disjoint: Liepin detail-backed `raw_candidate_count` means captured detail resumes, `duplicate_candidate_count` means explicit pre-click skips plus post-merge identity duplicates, `first_page_visible_count` means every frozen visible card, and `first_page_eligible_count` means the subset with a valid cached detail target and stable candidate hash. Never derive duplicates as `visible_cards - captured_details`.
- Preserve the existing bounded browser-open retry inside one granted candidate claim. Do not add network/tool retry chains.
- Preserve existing OpenCLI action and timeout budgets. Reaching either boundary ends only that lane's expansion as `partial`; do not add a separate expansion setting.
- Preserve the previously approved OpenCLI rule that SeekTalent does not automatically close browser tabs without ownership proof. Full-page expansion therefore increases owned detail-tab volume; record tab growth in prod acceptance and perform only the established manual/user cleanup. Automatic tab closing or a reusable-tab redesign requires a separate safety decision and is not smuggled into this slice.
- Checkpoint the run-level detail-open ledger immediately after recording each browser-open attempt and after each terminal claim transition, so every runtime-control checkpoint emitted during a long expansion contains the latest at-most-once state.
- Delete every protected continuation after its expand/discard decision reaches a terminal provider result. On provider startup, remove orphaned continuation files older than the fixed seven-day safety window; this is privacy cleanup, not a feature setting.
- Preserve the user-owned `.gitignore` change and do not stage it.

---

## Execution Gates

- **Gate A — scoring truth:** Tasks 1–3 must pass before any provider continuation code is merged. A reviewer must confirm that LLM output no longer owns total score and that low-score filtering is projection-only.
- **Gate 0 — query correctness:** Task 0 must pass before scoring or provider work. A reviewer must confirm that attempted non-anchor families are globally unique and that the Workbench keyword section contains only the two requested path labels and one copy of each keyword.
- **Gate B — provider continuation:** Tasks 4–6 must pass before runtime invokes expansion. A reviewer must confirm that the original first page is frozen once, private data never crosses the public boundary, and expansion issues no search or pagination action.
- **Gate C — integrated runtime:** Tasks 7–8 must pass before version bump. A real dual-lane fixture must prove independent quality decisions and original-page expansion for both lanes.
- **Gate R — independent Brooks CLEAR:** After Task 8 and all full tests, an independent read-only review subagent must run the applicable Brooks review/audit/test skills over the complete diff. The execution owner fixes every actionable finding, reruns affected/full gates, and dispatches a fresh independent review. Repeat until the reviewer explicitly returns `CLEAR`; no version bump or prod acceptance may start earlier.
- **Gate D — release readiness:** Task 9 must pass all focused, full, packaging, and Domi production checks before push or release.

---

## File Structure

- Modify query identity, logical-query, intent, receipt, controller, second-lane, rescue, and orchestrator modules — persist non-anchor family IDs and enforce run-wide/sibling-lane family disjointness.
- Modify `apps/web-react/src/components/workbench/ThinkingProcessRail.tsx` and `.css` — render plain, borderless `主路径 / 扩展路径` keyword text only, with each term shown once.
- Create `src/seektalent/scoring/weighted_score.py` — dimension applicability, deterministic weight normalization, total-score calculation, and nullable-risk helpers.
- Create `src/seektalent/providers/liepin/first_page_continuation.py` — provider-private protected snapshot model and atomic continuation storage.
- Create `src/seektalent/runtime/first_page_expansion.py` — source-neutral quality gate and receipt augmentation policy.
- Modify `src/seektalent/models.py` — nullable optional dimension scores and safe expansion decision/counter fields on query receipts.
- Modify `src/seektalent/scoring/scorer.py` and `src/seektalent/prompts/scoring.md` — remove LLM total score and materialize deterministic score.
- Modify scoring consumers in scorer confidence, controller, reflection, rescue, feedback extraction, PRF, and orchestrator modules — treat absent risk/preferred dimensions as not applicable.
- Modify `src/seektalent/config.py` and `src/seektalent/source_adapters/query_policy.py` — fixed `3/2` detail targets and a 30-card first-page scan for both Liepin lanes.
- Modify provider/core/source contracts — carry one private opaque continuation from provider search to runtime without serializing it publicly.
- Modify Liepin workflow, site adapter, retriever, worker client, provider adapter, and runtime lane — persist, return, and consume the original first page.
- Modify `src/seektalent/source_contracts/detail_open_claims.py` and runtime ledger construction — checkpoint attempted/opened/failed claims during long expansions.
- Modify runtime composition, source dispatch, retrieval result, and orchestrator — score baseline, decide per lane, expand qualified lanes, score expansion candidates, and reflect over the complete round.
- Modify `src/seektalent_workbench_v2/runtime_service.py` — filter candidate summaries at the BFF projection boundary.
- Modify version/package/bootstrap files only after all behavior gates pass.

### Task 0: Enforce Run-Wide Family Novelty And Distill The Keyword UI

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-logical-query-execution-contract-design.md`
- Modify: `docs/superpowers/plans/2026-07-11-candidate-quality-first-page-expansion.md`
- Modify: `src/seektalent/retrieval/query_identity.py`
- Modify: `src/seektalent/retrieval/query_plan.py`
- Modify: `src/seektalent/models.py:450-500,1250-1280,1390-1425`
- Modify: `src/seektalent/source_contracts/logical_query.py`
- Modify: `src/seektalent/runtime/logical_query_dispatch.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `src/seektalent/runtime/source_query_intent.py`
- Modify: `src/seektalent/runtime/source_round_dispatch.py`
- Modify: `src/seektalent/runtime/query_identity.py`
- Modify: `src/seektalent/runtime/reflection_context.py`
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/runtime/controller_context.py`
- Modify: `src/seektalent/controller/react_controller.py`
- Modify: `src/seektalent/runtime/round_decision_runtime.py`
- Modify: `src/seektalent/runtime/second_lane_runtime.py`
- Modify: `src/seektalent/runtime/rescue_execution_runtime.py`
- Modify: `src/seektalent/runtime/stop_reasons.py`
- Modify: `src/seektalent/runtime/orchestrator.py:1560-1600,3970-4080`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.tsx`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.css`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.test.tsx`
- Modify: `apps/web-react/src/components/workbench/ConversationScreenV2.test.tsx`
- Modify: `apps/web-react/src/components/workbench/ConversationScreen.test.tsx`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.stories.tsx`
- Test: `tests/test_query_execution_contract.py`
- Test: `tests/test_query_plan.py`
- Test: `tests/test_second_lane_runtime.py`
- Test: `tests/test_controller_contract.py`
- Test: `tests/test_reflection_contract.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_production_contract.py`
- Test: `tests/test_agent_workbench_contract.py`
- Test: `tests/test_workbench_v2_service.py`

**Interfaces:**
- Produces one immutable query identity resolution containing `term_group_key`, `primary_anchor_family_id`, and `non_anchor_term_family_ids`.
- Keeps the complete immutable `ResolvedQueryIdentity` on `LogicalQueryState`, then persists its `term_group_key`, `primary_anchor_family_id`, and `non_anchor_term_family_ids` through dispatch; source intent, receipt, and logical outcome retain the exact-group and non-anchor-family scalars needed by their own boundary.
- Produces `consumed_non_anchor_term_family_ids()` and a final bundle assertion that rejects history or sibling-lane family overlap; controller and reflection receive the same receipt-derived consumed-family truth.
- Keeps the rich `queryGroups` transport intact but renders only borderless `主路径 / 扩展路径` labels and one plain keyword line per path.

- [ ] **Step 1: Add failing family-identity and consumption tests**

First extend the existing local `_receipt()` fixture with required `non_anchor_term_family_ids: list[str] | None = None` and pass `non_anchor_term_family_ids or ["skill.python"]` into `QueryExecutionReceipt`. Then add:

```python
def test_attempted_query_consumes_non_anchor_families_but_blocked_preflight_does_not() -> None:
    attempted = _receipt(
        source_kind="liepin",
        dispatch_started=True,
        non_anchor_term_family_ids=["domain.multiagent", "domain.python"],
    )
    blocked = _receipt(
        source_kind="cts",
        dispatch_started=False,
        non_anchor_term_family_ids=["domain.rag"],
    )
    assert consumed_non_anchor_term_family_ids([attempted, blocked]) == {
        "domain.multiagent",
        "domain.python",
    }


def test_query_identity_uses_explicit_prf_family_override() -> None:
    identity = resolve_query_identity(
        query_terms=["Platform", "agentic memory"],
        query_term_pool=_pool(),
        explicit_family_overrides={"agentic memory": "prf.memory.system"},
    )
    assert identity.non_anchor_term_family_ids == ("prf.memory.system",)


def test_bundle_novelty_rejects_history_and_sibling_family_reuse() -> None:
    with pytest.raises(ValueError, match="non_anchor_term_family_already_executed"):
        assert_novel_query_identities(
            identities=[
                ResolvedQueryIdentity(
                    term_group_key="group-exploit",
                    primary_anchor_family_id="role.aiagent",
                    non_anchor_term_family_ids=("domain.python",),
                ),
                ResolvedQueryIdentity(
                    term_group_key="group-explore",
                    primary_anchor_family_id="role.aiagent",
                    non_anchor_term_family_ids=("domain.rag",),
                ),
            ],
            used_term_group_keys=set(),
            consumed_non_anchor_family_ids={"domain.rag"},
        )
```

Add a second assertion where history is empty but exploit and explore both contain `domain.python`; the same invariant error must occur. Add a CTS+Liepin receipt fixture for one logical query and prove the family set is consumed once. Keep the existing exact-group ordering/source-independence tests unchanged.

- [ ] **Step 2: Confirm the v0.7.39 red phase**

Run:

```bash
uv run pytest -q tests/test_query_execution_contract.py -k 'family or bundle_novelty'
```

Expected: collection or assertions fail because receipts do not persist family IDs and the runtime exposes only exact-group keys.

- [ ] **Step 3: Resolve and persist stable family identity**

In `retrieval/query_identity.py`, add:

```python
@dataclass(frozen=True)
class ResolvedQueryIdentity:
    term_group_key: str
    primary_anchor_family_id: str
    non_anchor_term_family_ids: tuple[str, ...]


def resolve_query_identity(
    *,
    query_terms: Sequence[str],
    query_term_pool: Sequence[QueryTermCandidate],
    explicit_family_overrides: Mapping[str, str] | None = None,
) -> ResolvedQueryIdentity:
    overrides: dict[str, str] = {}
    for term, family_id in (explicit_family_overrides or {}).items():
        normalized_term = normalize_term(term)
        normalized_family_id = normalize_term(family_id)
        if not normalized_term or not normalized_family_id:
            raise ValueError("query_family_override_invalid")
        overrides[normalized_term] = normalized_family_id
    candidates = {normalize_term(item.term): item for item in query_term_pool}
    anchor_families: set[str] = set()
    non_anchor_families: list[str] = []
    seen_families: set[str] = set()
    for term in query_terms:
        term_key = normalize_term(term)
        if not term_key:
            continue
        candidate = candidates.get(term_key)
        family_id = overrides.get(term_key) or (
            normalize_term(candidate.family) if candidate is not None else f"term:{term_key}"
        )
        if not family_id or family_id in seen_families:
            raise ValueError("query_family_identity_invalid")
        seen_families.add(family_id)
        if candidate is not None and is_primary_anchor_role(candidate.retrieval_role):
            anchor_families.add(family_id)
        else:
            non_anchor_families.append(family_id)
    if len(anchor_families) != 1:
        raise ValueError("query_primary_anchor_family_required")
    primary_anchor_family_id = next(iter(anchor_families))
    semantic_members = sorted({primary_anchor_family_id, *non_anchor_families})
    payload = json.dumps(
        {"version": "term-group-v2", "members": semantic_members},
        sort_keys=True,
        separators=(",", ":"),
    )
    return ResolvedQueryIdentity(
        term_group_key=sha256(payload.encode("utf-8")).hexdigest()[:32],
        primary_anchor_family_id=primary_anchor_family_id,
        non_anchor_term_family_ids=tuple(non_anchor_families),
    )
```

Extract one private semantic-family resolver and one private stable group-hash function, then use them from both public functions. `resolve_query_identity()` requires exactly one primary anchor and rejects an empty override key/value, empty family ID, or two surfaces from one family. `build_term_group_key()` remains an anchor-agnostic compatibility API for existing exact-key/alias callers and hashes the same resolved semantic members without requiring an anchor; keep the current `Python`/`Py` alias test unchanged. Runtime planning, dispatch, family consumption, and PRF must use `resolve_query_identity()`, so the compatibility wrapper cannot bypass the new anchor/family invariant on executable logical queries. Surface fallback is legal only when no override was supplied for that term; a PRF call site that knows a stable family ID must supply it and fails closed when it is empty.

Add `resolved_query_identity: ResolvedQueryIdentity` to `LogicalQueryState`. Remove its independently mutable `term_group_key` storage and expose read-only `term_group_key` and `non_anchor_term_family_ids` properties that delegate to `resolved_query_identity`. Delete the orchestrator's post-build `query_state.term_group_key = build_term_group_key(...)` loop. `build_logical_query_dispatches()` copies both values only from the immutable identity.

Add required `primary_anchor_family_id` and `non_anchor_term_family_ids` fields to `LogicalQueryDispatch`; `runtime/logical_query_dispatch.py` must copy all three identity scalars from state. `RetrievalRuntime.execute_logical_dispatch_search()` reconstructs `LogicalQueryState` from those persisted dispatch scalars rather than re-resolving against a term pool. Add required `non_anchor_term_family_ids` fields to `RuntimeSourceQueryIntent`, `QueryExecutionReceipt`, and `LogicalQueryOutcome`; their `term_group_key` and family tuple/list are copied from the same state identity. Do not add family IDs to `RuntimeQueryPackage`: CTS can construct that display package from `ProviderQuery`, which intentionally has no family field, while receipts are built from the original intent. `_receipts_for_source_result()` must take family IDs from that intent, never from provider output or the current term pool. PRF passes `{accepted_expression.canonical_expression: accepted_expression.term_family_id}` as the explicit override and fails if the known ID is empty. Add regressions proving `LogicalQueryState.term_group_key` cannot be assigned, dispatch-to-retrieval reconstruction preserves the complete immutable identity, and intents/receipts preserve the exact same non-anchor tuple.

These fields are required and have no empty compatibility default. Mechanically enumerate and update every production/test constructor before the Task 0 commit:

```bash
rg -n "LogicalQueryState\(|build_logical_query_state\(|LogicalQueryDispatch\(|RuntimeSourceQueryIntent\(|QueryExecutionReceipt\(|LogicalQueryOutcome\(" src tests
```

Add validators rejecting a non-anchor query whose persisted family tuple/list is empty. Update every match with identity derived from its fixture term pool; do not insert arbitrary placeholder families that disagree across state/intent/receipt/outcome.

Because required-field migration touches every real constructor, derive the staging list from the same live scan instead of relying on the illustrative file list at the top of this task:

```bash
rg -l "LogicalQueryState\(|build_logical_query_state\(|LogicalQueryDispatch\(|RuntimeSourceQueryIntent\(|QueryExecutionReceipt\(|LogicalQueryOutcome\(" src tests \
  | sort -u > /tmp/seektalent-query-identity-migration-files.txt
sed -n '1,240p' /tmp/seektalent-query-identity-migration-files.txt
```

Every listed constructor must be intentionally migrated and every changed listed file must be included in the Task 0 commit. Do not use `git add -u` or `git add .`.

- [ ] **Step 4: Add deterministic family-aware selection tests**

Replace the two current tests that explicitly allow `generic_explore` to reuse the exploit/history `trace` family. Add this matrix across `tests/test_query_plan.py`, `tests/test_second_lane_runtime.py`, `tests/test_controller_contract.py`, and `tests/test_runtime_state_flow.py`:

```text
[anchor,A] already attempted; controller proposes [anchor,A,B]
  -> repaired result retains only fresh B (and a deterministic fresh fill when required)

history consumed A; current exploit selects B; candidates are A/B/C/D
  -> explore uses only C/D

PRF family consumed in history or selected by current exploit
  -> PRF rejected with family-conflict reason; fresh generic explore selected

all non-anchor families consumed
  -> reserve/candidate-feedback/novel anchor-only/stop path; no source dispatch

same-family alias appended later
  -> alias remains consumed
```

Use the real AI Agent pool (`Multi-Agent`, `记忆系统`, `Python`, `Java`, `ADK`, `Prompt`, `Function Calling`, `RAG`) for a four-round deterministic regression. Assert that `role.aiagent` is the only repeated attempted family and no test requires two lanes after fresh families are exhausted.

- [ ] **Step 5: Enforce family novelty at every planning boundary**

Add to `runtime/query_identity.py`:

```python
def consumed_non_anchor_term_family_ids(
    receipts: Sequence[QueryExecutionReceipt],
) -> set[str]:
    return {
        family_id
        for receipt in receipts
        if receipt.dispatch_started
        for family_id in receipt.non_anchor_term_family_ids
    }


def assert_novel_query_identities(
    *,
    identities: Sequence[ResolvedQueryIdentity],
    used_term_group_keys: Collection[str],
    consumed_non_anchor_family_ids: Collection[str],
) -> None:
    assert_novel_term_group_keys(
        term_group_keys=[item.term_group_key for item in identities],
        used_term_group_keys=used_term_group_keys,
    )
    seen_families = set(consumed_non_anchor_family_ids)
    for identity in identities:
        overlap = seen_families & set(identity.non_anchor_term_family_ids)
        if overlap:
            raise ValueError("non_anchor_term_family_already_executed")
        seen_families.update(identity.non_anchor_term_family_ids)
```

Expose the consumed set in both `ControllerContext` and `ReflectionContext`, populated only from the receipt ledger. Extend the existing deterministic term selector with `excluded_non_anchor_family_ids`; controller sanitization retains fresh proposed families and fills from the existing deterministic admitted-term order. Generic explore excludes history plus the current exploit family set before combination generation. PRF uses its explicit family ID and falls back when it overlaps. Reserve and candidate-feedback routes verify the new family before activation. Change `reflection/critic.py::_untried_admitted_terms()` and `_term_bank_rows()` to use the context's consumed family set for tried/untried status; keep `sent_query_history` only for physical source/city diagnostics. Add a Liepin-only reflection regression where a family appears in an attempted receipt but not `sent_query_history`: the prompt/context marks it tried and never recommends it as untried.

Immediately before source intent construction, call `assert_novel_query_identities()` with the persisted identities for the full current bundle. If deterministic repair has no fresh candidate, route to reserve, candidate feedback, novel anchor-only, or stop; do not dispatch and do not surface a normal `RunStageError` to the user. Keep exact-group novelty as a second invariant.

Add `tuple(receipt.non_anchor_term_family_ids)` to `_logical_identity()` in `runtime/query_identity.py`. Two source receipts grouped under one `query_instance_id` must agree on family identity; add a regression where CTS and Liepin disagree and `logical_outcomes_from_receipts()` raises `logical_query_receipt_identity_mismatch`.

Make exhaustion routing concrete with a small result type in `rescue_execution_runtime.py`:

```python
@dataclass(frozen=True)
class FamilyExhaustionResolution:
    action: Literal["reserve", "candidate_feedback", "anchor_only", "stop"]
    reason_code: str
```

Do not wait until `resolve_round_decision()` to discover total exhaustion: the current orchestrator calls the validating ReAct controller first, so an illegal proposal can exhaust its repair/retry path before deterministic rescue runs. Add `resolve_family_exhaustion_before_controller()` and call it immediately after `build_controller_context()` and before `run_controller_stage()`. It returns `None` only while at least one fresh active/controller-selectable non-anchor family exists. Otherwise it resolves in this exact order: atomically activate and force a fresh admitted inactive reserve; use an unused candidate-feedback family only when `settings.candidate_feedback_enabled` is true and feedback has not already been attempted; use an unused anchor-only group; deterministic stop with `query_family_exhausted`. If candidate feedback returns no safe fresh term, remain in the same preflight and continue to anchor-only/stop. A preflight result skips `run_controller_stage()` and `finalize_controller_stage()`, writes the same controller/rescue artifacts explicitly, and proceeds only for a legal search decision; the terminal stop bypasses `_raise_if_stop_disallowed()` even when ordinary `stop_guidance.can_stop` is false because no legal search action remains.

For the non-exhausted controller path, add `try_sanitize_fresh_families()` before the existing raising sanitizer. It drops consumed non-anchor families, deterministically fills from fresh active/controller-selectable families, and returns `None` instead of raising when it cannot make a legal query. A `None` result re-enters the same exhaustion resolver in the current turn; it must not invoke a second controller call. Pass `candidate_feedback_enabled=self.settings.candidate_feedback_enabled` explicitly rather than reading settings inside pure selection code.

Make runtime the single owner of exact-group and family novelty repair: remove the exact-used-group check from `react_controller.validate_controller_decision()` so controller validation remains structural/semantic and does not enter LLM repair/retry for a novelty conflict. `run_controller_stage()` returns the first valid structured decision, and the shared runtime sanitizer repairs exact/family overlap deterministically before any dispatch. Add a recording controller regression for consumed `[anchor,A]`: exactly one controller model call, zero repair/full-retry calls, and a deterministic fresh replacement or exhaustion rescue.

Add `query_family_exhausted` to `PUBLIC_STOP_REASON_ALLOWLIST` in `runtime/stop_reasons.py` and production-contract tests proving it survives normalization and runtime-control finalization unchanged. Add tests for each resolution step, including an inactive-reserve preflight that activates the reserve with zero controller calls, and a real recording controller stub with `can_stop=False` plus no reserve/feedback/anchor-only: the round ends with `query_family_exhausted`, controller call count is zero, provider call count is zero, and the dispatch gate is never reached with an invalid bundle.

- [ ] **Step 6: Add failing minimal-keyword UI tests**

Update `ThinkingProcessRail.test.tsx` with a main group whose `keywordQuery` repeats its `queryTerms`, plus an expansion/PRF group and source/count data. Assert:

```tsx
expect(within(queryArea).getByText("主路径")).toBeInTheDocument();
expect(within(queryArea).getByText("扩展路径")).toBeInTheDocument();
const mainPath = within(queryArea).getByRole("group", { name: "主路径" });
const expansionPath = within(queryArea).getByRole("group", { name: "扩展路径" });
expect(within(mainPath).getAllByText("AI Agent")).toHaveLength(1);
expect(within(mainPath).getAllByText("RAG")).toHaveLength(1);
expect(within(expansionPath).getAllByText("AI Agent")).toHaveLength(1);
expect(within(queryArea).queryByText(/AI Agent AND RAG/)).toBeNull();
expect(within(queryArea).queryByText("已执行")).toBeNull();
expect(within(queryArea).queryByText("猎聘")).toBeNull();
expect(within(queryArea).queryByText("原始")).toBeNull();
expect(within(queryArea).queryByText("新增")).toBeNull();
expect(within(queryArea).queryByText("重复")).toBeNull();
expect(container.querySelector(".thinking-query-group")).toBeNull();
```

Also assert a one-lane round renders only `主路径`, `prf_probe` maps to `扩展路径` rather than a third visible path, and a duplicate group of the same visible path type does not produce a second path. Run the same visible assertions through both `ConversationScreenV2.test.tsx` and `ConversationScreen.test.tsx`; the shared rail must keep V2 and legacy conversation routes identical. In Python, retain the canonical reducer assertions in `tests/test_agent_workbench_contract.py` and `tests/test_workbench_v2_service.py`: one/two logical groups survive planning/executed replacement, and one logical query executed by two sources remains one group before React distills it.

Run:

```bash
(cd apps/web-react && npm test -- --run src/components/workbench/ThinkingProcessRail.test.tsx src/components/workbench/ConversationScreenV2.test.tsx src/components/workbench/ConversationScreen.test.tsx)
```

Expected: FAIL because `keywordQuery` and chips render the same terms twice, and the card still exposes badges, counts, and source rows.

- [ ] **Step 7: Render plain borderless paths only**

In `ThinkingProcessRail.tsx`, remove `AgentWorkbenchQueryExecution`, `QueryExecution`, lifecycle/source/count renderers, `keywordQuery`, the visible `关键词` heading, and the query-group card markup. Import `Fragment` from React for the plain separator list. Derive the visible path from each merged query group:

```tsx
function queryPathLabel(laneType: string): "主路径" | "扩展路径" {
  return laneType === "exploit" ? "主路径" : "扩展路径";
}

function visibleKeywords(queryTerms: readonly string[]): string[] {
  const seen = new Set<string>();
  const visible: string[] = [];
  for (const term of queryTerms) {
    const display = term.trim().replace(/\s+/g, " ");
    const key = display.toLocaleLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    visible.push(display);
  }
  return visible;
}

function visibleKeywordPaths(
  queryGroups: readonly AgentWorkbenchQueryGroup[],
): AgentWorkbenchQueryGroup[] {
  const seen = new Set<"主路径" | "扩展路径">();
  const paths: AgentWorkbenchQueryGroup[] = [];
  for (const group of queryGroups) {
    const label = queryPathLabel(group.laneType);
    if (seen.has(label)) continue;
    seen.add(label);
    paths.push(group);
  }
  return paths;
}

function QueryGroup({ queryGroup }: { queryGroup: AgentWorkbenchQueryGroup }) {
  const keywords = visibleKeywords(queryGroup.queryTerms);
  return (
    <div role="group" aria-label={queryPathLabel(queryGroup.laneType)} className="thinking-query-path">
      <h3>{queryPathLabel(queryGroup.laneType)}</h3>
      <p>
        {keywords.map((keyword, index) => (
          <Fragment key={keyword}>
            {index > 0 ? "、" : null}
            <span>{keyword}</span>
          </Fragment>
        ))}
      </p>
    </div>
  );
}
```

Have `QueryGroups` iterate over `visibleKeywordPaths(queryGroups)`, keep `queryInstanceId` as the React key, and preserve runtime order. The helper deliberately keeps only the first main and first expansion group after the reducer has merged planned/executed identity; add a regression for duplicate path types. In CSS, delete the query card, header badge, keyword prose, chip, metric-grid, and execution-row rules. The replacement has only vertical spacing and normal product typography:

```css
.thinking-query-groups__list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.thinking-query-path h3,
.thinking-query-path p {
  margin: 0;
  color: #23314d;
  font-size: 14px;
  line-height: 1.5;
}

.thinking-query-path h3 {
  margin-bottom: 4px;
  font-weight: 700;
}
```

Do not add a border, background, shadow, radius, pill, microcopy, tooltip, accordion, or replacement metric. Update Storybook to show the exact one-path and two-path states.

- [ ] **Step 8: Run Gate 0 and commit**

Run:

```bash
uv run pytest -q \
  tests/test_query_execution_contract.py \
  tests/test_query_plan.py \
  tests/test_second_lane_runtime.py \
  tests/test_controller_contract.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_state_flow.py \
  tests/test_agent_workbench_contract.py \
  tests/test_workbench_v2_service.py
uv run pytest -q
(cd apps/web-react && \
  npm test -- --run src/components/workbench/ThinkingProcessRail.test.tsx src/components/workbench/ConversationScreenV2.test.tsx src/components/workbench/ConversationScreen.test.tsx && \
  npm run check && \
  npm run lint)
```

Expected: PASS; all required constructor migrations are green across the full suite, all attempted non-anchor families are run-wide unique, exact-group protection remains, Python V2/legacy projections preserve canonical one/two groups, and the keyword UI contains only plain `主路径 / 扩展路径` text with each keyword visible once per path.

```bash
git add \
  docs/superpowers/specs/2026-07-10-logical-query-execution-contract-design.md \
  docs/superpowers/plans/2026-07-11-candidate-quality-first-page-expansion.md \
  src/seektalent/retrieval/query_identity.py \
  src/seektalent/retrieval/query_plan.py \
  src/seektalent/models.py \
  src/seektalent/source_contracts/logical_query.py \
  src/seektalent/runtime/logical_query_dispatch.py \
  src/seektalent/runtime/retrieval_runtime.py \
  src/seektalent/runtime/source_query_intent.py \
  src/seektalent/runtime/source_round_dispatch.py \
  src/seektalent/runtime/query_identity.py \
  src/seektalent/runtime/controller_context.py \
  src/seektalent/runtime/reflection_context.py \
  src/seektalent/reflection/critic.py \
  src/seektalent/controller/react_controller.py \
  src/seektalent/runtime/round_decision_runtime.py \
  src/seektalent/runtime/second_lane_runtime.py \
  src/seektalent/runtime/rescue_execution_runtime.py \
  src/seektalent/runtime/stop_reasons.py \
  src/seektalent/runtime/orchestrator.py \
  apps/web-react/src/components/workbench/ThinkingProcessRail.tsx \
  apps/web-react/src/components/workbench/ThinkingProcessRail.css \
  apps/web-react/src/components/workbench/ThinkingProcessRail.test.tsx \
  apps/web-react/src/components/workbench/ConversationScreenV2.test.tsx \
  apps/web-react/src/components/workbench/ConversationScreen.test.tsx \
  apps/web-react/src/components/workbench/ThinkingProcessRail.stories.tsx \
  tests/test_query_execution_contract.py \
  tests/test_query_plan.py \
  tests/test_second_lane_runtime.py \
  tests/test_controller_contract.py \
  tests/test_reflection_contract.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_production_contract.py \
  tests/test_agent_workbench_contract.py \
  tests/test_workbench_v2_service.py
xargs git add -- < /tmp/seektalent-query-identity-migration-files.txt
if git diff --cached --name-only | rg -x '\.gitignore'; then
  echo "user-owned .gitignore was staged" >&2
  exit 1
fi
comm -23 \
  /tmp/seektalent-query-identity-migration-files.txt \
  <(git diff --cached --name-only | sort -u) \
  | tee /tmp/seektalent-unstaged-query-identity-files.txt
test ! -s /tmp/seektalent-unstaged-query-identity-files.txt
git commit -m "fix: enforce fresh keyword families per run"
```

### Task 1: Define Deterministic Weighted Scoring

**Files:**
- Create: `src/seektalent/scoring/weighted_score.py`
- Test: `tests/test_scoring_cache.py`

**Interfaces:**
- Consumes: `ScoringPolicy.must_have_capabilities`, `ScoringPolicy.preferred_capabilities`, `ScoringPolicy.preferences`, and `ScoringPolicy.exclusion_signals`.
- Produces: `ScoreDimensionApplicability`, `score_dimension_applicability()`, and `calculate_overall_score()`.

- [ ] **Step 1: Write failing applicability and weighted-score tests**

Add to `tests/test_scoring_cache.py`:

```python
import pytest

from seektalent.models import PreferenceSlots, ScoringPolicy
from seektalent.scoring.weighted_score import (
    ScoreDimensionApplicability,
    calculate_overall_score,
    score_dimension_applicability,
)


def _policy(*, preferred: bool, risk: bool) -> ScoringPolicy:
    return ScoringPolicy(
        job_title="AI Agent 工程师",
        role_summary="构建生产级 Agent 系统",
        must_have_capabilities=["Multi-Agent 架构"],
        preferred_capabilities=["B2B 电商"] if preferred else [],
        exclusion_signals=["没有软件工程经验"] if risk else [],
        preferences=PreferenceSlots(),
        scoring_rationale="必须项优先",
    )


@pytest.mark.parametrize(
    ("preferred", "risk", "expected"),
    [
        (True, True, ScoreDimensionApplicability(preferred=True, risk=True)),
        (True, False, ScoreDimensionApplicability(preferred=True, risk=False)),
        (False, True, ScoreDimensionApplicability(preferred=False, risk=True)),
        (False, False, ScoreDimensionApplicability(preferred=False, risk=False)),
    ],
)
def test_requirement_sheet_controls_dimension_applicability(preferred, risk, expected) -> None:
    assert score_dimension_applicability(_policy(preferred=preferred, risk=risk)) == expected


@pytest.mark.parametrize(
    ("applicability", "preferred", "risk", "expected"),
    [
        (ScoreDimensionApplicability(preferred=True, risk=True), 80, 20, 77),
        (ScoreDimensionApplicability(preferred=True, risk=False), 80, None, 76),
        (ScoreDimensionApplicability(preferred=False, risk=True), None, 20, 76),
        (ScoreDimensionApplicability(preferred=False, risk=False), None, None, 75),
    ],
)
def test_total_score_renormalizes_only_applicable_dimensions(applicability, preferred, risk, expected) -> None:
    assert calculate_overall_score(
        must_have_match_score=75,
        preferred_match_score=preferred,
        risk_score=risk,
        applicability=applicability,
    ) == expected


def test_total_score_rejects_missing_or_extra_dimension_values() -> None:
    with pytest.raises(ValueError, match="preferred_score_required"):
        calculate_overall_score(
            must_have_match_score=80,
            preferred_match_score=None,
            risk_score=None,
            applicability=ScoreDimensionApplicability(preferred=True, risk=False),
        )
    with pytest.raises(ValueError, match="risk_score_not_applicable"):
        calculate_overall_score(
            must_have_match_score=80,
            preferred_match_score=None,
            risk_score=10,
            applicability=ScoreDimensionApplicability(preferred=False, risk=False),
        )
```

- [ ] **Step 2: Run the focused tests and confirm the red phase**

Run:

```bash
uv run pytest -q tests/test_scoring_cache.py -k 'dimension_applicability or total_score'
```

Expected: collection fails because `seektalent.scoring.weighted_score` does not exist.

- [ ] **Step 3: Implement applicability and exact weighted calculation**

Create `src/seektalent/scoring/weighted_score.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from seektalent.models import ScoringPolicy

MUST_HAVE_WEIGHT = 60
PREFERRED_WEIGHT = 25
INVERTED_RISK_WEIGHT = 15


@dataclass(frozen=True)
class ScoreDimensionApplicability:
    preferred: bool
    risk: bool


def score_dimension_applicability(policy: ScoringPolicy) -> ScoreDimensionApplicability:
    preferred = bool(
        policy.preferred_capabilities
        or policy.preferences.preferred_locations
        or policy.preferences.preferred_companies
        or policy.preferences.preferred_domains
        or policy.preferences.preferred_backgrounds
    )
    return ScoreDimensionApplicability(
        preferred=preferred,
        risk=bool(policy.exclusion_signals),
    )


def calculate_overall_score(
    *,
    must_have_match_score: int,
    preferred_match_score: int | None,
    risk_score: int | None,
    applicability: ScoreDimensionApplicability,
) -> int:
    if applicability.preferred != (preferred_match_score is not None):
        code = "preferred_score_required" if applicability.preferred else "preferred_score_not_applicable"
        raise ValueError(code)
    if applicability.risk != (risk_score is not None):
        code = "risk_score_required" if applicability.risk else "risk_score_not_applicable"
        raise ValueError(code)

    weighted = [(must_have_match_score, MUST_HAVE_WEIGHT)]
    if preferred_match_score is not None:
        weighted.append((preferred_match_score, PREFERRED_WEIGHT))
    if risk_score is not None:
        weighted.append((100 - risk_score, INVERTED_RISK_WEIGHT))
    numerator = sum(Decimal(value * weight) for value, weight in weighted)
    denominator = Decimal(sum(weight for _value, weight in weighted))
    rounded = int((numerator / denominator).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return min(100, max(0, rounded))


def risk_at_or_above(score: int | None, threshold: int) -> bool:
    return score is not None and score >= threshold


def risk_at_or_below(score: int | None, threshold: int) -> bool:
    return score is None or score <= threshold
```

- [ ] **Step 4: Run formula tests**

Run:

```bash
uv run pytest -q tests/test_scoring_cache.py -k 'dimension_applicability or total_score'
```

Expected: PASS; no existing scorecard or scorer behavior has changed yet.

- [ ] **Step 5: Commit the score-domain foundation**

```bash
git add src/seektalent/scoring/weighted_score.py tests/test_scoring_cache.py
git commit -m "feat: define deterministic candidate scoring"
```

### Task 2: Remove LLM Ownership Of Total Score

**Files:**
- Modify: `src/seektalent/models.py:1110-1198,1800-1808`
- Modify: `src/seektalent/prompts/scoring.md`
- Modify: `src/seektalent/scoring/scorer.py:30-160,280-720`
- Modify: `docs/v-0.2/scoring-rules-map.md`
- Modify: `docs/llm-context-composition.zh-CN.md`
- Modify: `tests/test_scoring_cache.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_llm_lifecycle.py`
- Modify: `tests/test_runtime_audit.py`
- Modify: `tests/test_v02_models.py`

**Interfaces:**
- Consumes: `calculate_overall_score()` and `score_dimension_applicability()` from Task 1.
- Produces: `_materialize_scored_candidate(*, draft: ScoredCandidateDraft, scoring_policy: ScoringPolicy, resume_id: str, source_round: int, source_provider: str | None = None, score_evidence_source: str | None = None, card_scorecard_ref: str | None = None, detail_scorecard_ref: str | None = None, score_delta: int | None = None, detail_open_reason: str | None = None, detail_open_policy_version: str | None = None) -> ScoredCandidate` with a runtime-owned `overall_score`.
- Later tasks rely on `SCORING_CACHE_SCHEMA_VERSION == "scored_candidate.v2"` to prevent stale v1 scorecards from being read.

- [ ] **Step 1: Add failing scorer-contract tests**

Add `from pydantic import ValidationError` and `from seektalent.scoring.scorer import _materialize_scored_candidate` to `tests/test_scoring_cache.py`, then add:

```python
def test_scoring_draft_schema_does_not_accept_llm_overall_score() -> None:
    with pytest.raises(ValidationError, match="overall_score"):
        ScoredCandidateDraft.model_validate(
            {
                "fit_bucket": "fit",
                "overall_score": 99,
                "must_have_match_score": 70,
                "preferred_match_score": None,
                "risk_score": None,
                "reasoning_summary": "证据匹配",
            }
        )


def test_materializer_calculates_total_and_applicability_from_policy() -> None:
    result = _materialize_scored_candidate(
        draft=ScoredCandidateDraft(
            fit_bucket="fit",
            must_have_match_score=75,
            preferred_match_score=80,
            risk_score=None,
            reasoning_summary="必须项与加分项匹配",
        ),
        scoring_policy=_policy(preferred=True, risk=False),
        resume_id="resume-1",
        source_round=1,
    )
    assert result.overall_score == 76
    assert result.preferred_match_score == 80
    assert result.risk_score is None


def test_materializer_rejects_model_score_for_inapplicable_dimension() -> None:
    with pytest.raises(ValueError, match="risk_score_not_applicable"):
        _materialize_scored_candidate(
            draft=ScoredCandidateDraft(
                fit_bucket="fit",
                must_have_match_score=80,
                preferred_match_score=None,
                risk_score=10,
                reasoning_summary="不应生成风险分",
            ),
            scoring_policy=_policy(preferred=False, risk=False),
            resume_id="resume-1",
            source_round=1,
        )
```

- [ ] **Step 2: Run the scorer-contract tests and confirm failure**

Run:

```bash
uv run pytest -q tests/test_scoring_cache.py -k 'draft_schema or materializer'
```

Expected: failures show the current draft still owns `overall_score` and `_materialize_scored_candidate` does not accept `scoring_policy`.

- [ ] **Step 3: Tighten the scoring prompt**

Replace the score-specific rules in `src/seektalent/prompts/scoring.md` with:

```markdown
- Output `must_have_match_score` against the supplied must-have capabilities and hard constraints.
- Output `preferred_match_score` only when the scoring policy contains preferred capabilities or structured preferences; otherwise output null.
- Output `risk_score` only when the scoring policy contains explicit exclusion signals; otherwise output null.
- Do not output `overall_score`; runtime computes it deterministically.
- Evidence incompleteness affects fit confidence and reasoning, but does not create an exclusion standard that is absent from the scoring policy.
```

Remove the instruction that asks the model to keep an overall-score band coherent. Keep `fit_bucket`, rationale, evidence lists, and protected-attribute rules.

- [ ] **Step 4: Make optional score dimensions nullable in persisted models**

In `src/seektalent/models.py`, remove `overall_score` from `ScoredCandidateDraft`; make its two optional dimension scores nullable:

```python
class ScoredCandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit_bucket: FitBucket
    must_have_match_score: int = Field(ge=0, le=100)
    preferred_match_score: int | None = Field(default=None, ge=0, le=100)
    risk_score: int | None = Field(default=None, ge=0, le=100)
    # Keep the existing evidence/rationale fields unchanged.
```

Keep runtime-owned `ScoredCandidate.overall_score: int`, but change `preferred_match_score` and `risk_score` to `int | None` with the same bounds. Change `TopPoolEntryView.risk_score` to `int | None`. Normalize nullable risk only at the existing deterministic sort boundary:

```python
def scored_candidate_sort_key(candidate: ScoredCandidate) -> tuple[int, int, int, int, str]:
    return (
        0 if candidate.fit_bucket == "fit" else 1,
        -candidate.overall_score,
        -candidate.must_have_match_score,
        candidate.risk_score if candidate.risk_score is not None else 0,
        candidate.resume_id,
    )
```

Add to `tests/test_v02_models.py` a regression proving that the existing order remains `fit` first, then higher overall, then higher must-have, then lower applicable risk, and that `risk_score=None` sorts deterministically as zero risk without raising `TypeError`. This task changes the canonical score model before any scorer or consumer is compiled, so downstream tasks never depend on a half-migrated schema.

- [ ] **Step 5: Materialize deterministic total score**

Set `SCORING_CACHE_SCHEMA_VERSION = "scored_candidate.v2"`. Change `_materialize_scored_candidate()` to accept `scoring_policy: ScoringPolicy`, derive applicability, validate nullable fields, and calculate total:

```python
applicability = score_dimension_applicability(scoring_policy)
overall_score = calculate_overall_score(
    must_have_match_score=draft.must_have_match_score,
    preferred_match_score=draft.preferred_match_score,
    risk_score=draft.risk_score,
    applicability=applicability,
)
return ScoredCandidate(
    resume_id=resume_id,
    source_provider=source_provider,
    source_round=source_round,
    fit_bucket=draft.fit_bucket,
    overall_score=overall_score,
    must_have_match_score=draft.must_have_match_score,
    preferred_match_score=draft.preferred_match_score,
    risk_score=draft.risk_score,
    risk_flags=draft.risk_flags,
    reasoning_summary=draft.reasoning_summary,
    evidence=_derived_evidence(draft),
    confidence=_derived_confidence(draft=draft, overall_score=overall_score),
    matched_must_haves=draft.matched_must_haves,
    missing_must_haves=draft.missing_must_haves,
    matched_preferences=draft.matched_preferences,
    negative_signals=draft.negative_signals,
    strengths=_derived_strengths(draft),
    weaknesses=_derived_weaknesses(draft),
    score_evidence_source=score_evidence_source,
    card_scorecard_ref=card_scorecard_ref,
    detail_scorecard_ref=detail_scorecard_ref,
    score_delta=score_delta,
    detail_open_reason=detail_open_reason,
    detail_open_policy_version=detail_open_policy_version,
)
```

Pass `context.scoring_policy` from `_score_one()` into the materializer. Update every direct materializer call in production and tests to pass either `context.scoring_policy` or an explicit fixture `ScoringPolicy`; do not add a default policy. Replace `_derived_confidence()` with a nullable-risk implementation that consumes the runtime total:

```python
def _derived_confidence(*, draft: ScoredCandidateDraft, overall_score: int) -> ScoringConfidence:
    score_gap = abs(overall_score - draft.must_have_match_score)
    high_risk = risk_at_or_above(draft.risk_score, 65)
    low_risk = risk_at_or_below(draft.risk_score, 35)
    if draft.fit_bucket == "fit":
        if overall_score >= 75 and draft.must_have_match_score >= 70 and low_risk and score_gap <= 25:
            return "high"
        if overall_score < 60 or draft.must_have_match_score < 50 or high_risk or score_gap > 35:
            return "low"
        return "medium"
    if overall_score <= 55 or draft.must_have_match_score <= 50 or risk_at_or_above(draft.risk_score, 60):
        return "high"
    if overall_score >= 75 and draft.must_have_match_score >= 70 and low_risk:
        return "low"
    return "medium"
```

Update `_timeout_scored_candidate()` from the same policy-derived applicability. Timeout `overall_score` remains 0; applicable dimensions receive failure-safe values and absent dimensions remain null:

```python
applicability = score_dimension_applicability(context.scoring_policy)
return ScoredCandidate(
    resume_id=candidate.resume_id,
    fit_bucket="not_fit",
    overall_score=0,
    must_have_match_score=0,
    preferred_match_score=0 if applicability.preferred else None,
    risk_score=100 if applicability.risk else None,
    risk_flags=["scoring_timeout"],
    reasoning_summary=(
        f"Scoring timed out after {timeout_seconds:g}s before producing a reliable assessment; "
        "excluded from the ranked pool."
    ),
    evidence=[],
    confidence="low",
    matched_must_haves=[],
    missing_must_haves=context.scoring_policy.must_have_capabilities,
    matched_preferences=[],
    negative_signals=["scoring_timeout"],
    strengths=[],
    weaknesses=["Scoring did not complete within the configured timeout."],
    source_round=candidate.source_round or context.round_no,
    source_provider=candidate.source_provider,
    score_evidence_source=candidate.score_evidence_source,
    card_scorecard_ref=candidate.card_scorecard_ref,
    detail_scorecard_ref=candidate.detail_scorecard_ref,
    score_delta=candidate.score_delta,
    detail_open_reason=candidate.detail_open_reason,
    detail_open_policy_version=candidate.detail_open_policy_version,
)
```

Remove `overall_score` from every `ScoredCandidateDraft` fixture in `tests/test_llm_lifecycle.py` and `tests/test_runtime_audit.py`; keep `overall_score` on materialized `ScoredCandidate` fixtures. Each draft fixture must have this score-field shape:

```python
ScoredCandidateDraft(
    fit_bucket="fit",
    must_have_match_score=90,
    preferred_match_score=80,
    risk_score=10,
    reasoning_summary="fixture evidence",
)
```

- [ ] **Step 6: Update cache and trace summaries to report runtime total**

Ensure cache writes only the materialized `ScoredCandidate`. Use this exact summary shape for cache-hit and live paths:

```python
output_summary=(
    f"fit_bucket={result.fit_bucket}; overall={result.overall_score}; "
    f"must={result.must_have_match_score}; preferred={result.preferred_match_score}; "
    f"risk={result.risk_score}"
)
```

Add this contract to both scoring descriptions, adapting only the surrounding heading level:

```markdown
### Deterministic total score

The scoring model outputs `must_have_match_score`, `preferred_match_score`, and `risk_score`; it never outputs `overall_score`.

- Must-have is always applicable.
- Preferred is null when the approved Requirement Sheet contains no preferred capability or structured preference.
- Risk is null when the approved Requirement Sheet contains no exclusion signal.
- Runtime computes `overall_score` from must-have `60`, preferred `25`, and inverted risk (`100 - risk`) `15`.
- Runtime removes null dimensions, renormalizes the remaining weights to 100, and rounds half up to an integer in `0..100`.
```

In `docs/v-0.2/scoring-rules-map.md`, delete or rewrite every existing statement and worked example claiming that there is no fixed formula, that the model writes `overall_score`, or that overall is deliberately non-formulaic. In `docs/llm-context-composition.zh-CN.md`, remove the same stale ownership language. Verify no contradictory contract remains:

```bash
rg -n "没有固定公式|非公式|模型.*overall_score|overall_score.*模型|LLM.*overall_score" \
  docs/v-0.2/scoring-rules-map.md \
  docs/llm-context-composition.zh-CN.md
```

Expected: no stale model-owned-total statement remains; matches may occur only in the new explicit sentence that the model does not output `overall_score`.

- [ ] **Step 7: Run scoring and runtime scoring tests**

Run:

```bash
uv run pytest -q tests/test_scoring_cache.py tests/test_runtime_state_flow.py tests/test_llm_lifecycle.py tests/test_runtime_audit.py tests/test_v02_models.py -k 'scor or score or sort_key'
```

Expected: PASS; cached v1 keys are not reused because the schema version participates in the cache key.

- [ ] **Step 8: Commit deterministic score materialization**

```bash
git add src/seektalent/models.py src/seektalent/prompts/scoring.md src/seektalent/scoring/scorer.py docs/v-0.2/scoring-rules-map.md docs/llm-context-composition.zh-CN.md tests/test_scoring_cache.py tests/test_runtime_state_flow.py tests/test_llm_lifecycle.py tests/test_runtime_audit.py tests/test_v02_models.py
git commit -m "feat: calculate candidate totals in runtime"
```

### Task 3: Update Score Consumers, Restore 3/2, And Filter Workbench

**Files:**
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/runtime/controller_context.py`
- Modify: `src/seektalent/runtime/rescue_execution_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py:4400-4430`
- Modify: `src/seektalent/candidate_feedback/extraction.py`
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `src/seektalent/config.py:547-548`
- Modify: `src/seektalent/source_adapters/query_policy.py`
- Create: `src/seektalent/candidate_visibility.py`
- Modify: `src/seektalent_ui/agent_workbench_projection.py`
- Modify: `src/seektalent_workbench_v2/runtime_service.py:317-367`
- Test: `tests/test_reflection_contract.py`
- Test: `tests/test_context_builder.py`
- Test: `tests/test_candidate_feedback.py`
- Test: `tests/test_liepin_config.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_agent_workbench_contract.py`
- Test: `tests/test_workbench_v2_runtime_service.py`

**Interfaces:**
- Consumes: nullable `risk_score` and the fixed deterministic `overall_score` from Tasks 1–2.
- Produces: safe downstream comparisons, `3/2` production defaults, and one shared `WORKBENCH_MIN_CANDIDATE_SCORE = 60` projection policy used by both live Workbench surfaces.
- Provider tasks consume `liepin_exploit_detail_target == 3` and `liepin_explore_detail_target == 2`.

- [ ] **Step 1: Add failing downstream and BFF tests**

Add this local fixture and test to `tests/test_workbench_v2_runtime_service.py`:

```python
class CandidateThresholdStore:
    def list_candidate_identities(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateIdentity]:
        return [
            RuntimeControlCandidateIdentity(
                runtime_run_id=runtime_run_id,
                identity_id=identity_id,
                canonical_resume_id=f"resume-{identity_id}",
                display_name=identity_id,
                title="AI Agent Engineer",
                company="Accio",
                location="Hangzhou",
                summary="score threshold fixture",
                score=score,
                fit_bucket="fit" if score is not None else None,
                payload_hash=f"hash-{identity_id}",
                updated_at=NOW,
            )
            for identity_id, score in (("low", 59), ("edge", 60), ("high", 90), ("unscored", None))
        ]

    def list_candidate_evidence(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateEvidence]:
        del runtime_run_id
        return []


def test_candidate_summary_hides_scores_below_sixty_and_reranks() -> None:
    service = WorkbenchV2RuntimeService(store=CandidateThresholdStore())  # type: ignore[arg-type]
    summaries = service.list_candidate_summaries("rtrun_candidate")
    assert [(item["candidateId"], item["rank"]) for item in summaries] == [
        ("high", 1),
        ("edge", 2),
    ]
```

Add to `tests/test_liepin_config.py`:

```python
def test_default_liepin_detail_targets_are_three_and_two() -> None:
    settings = AppSettings(_env_file=None)
    assert settings.liepin_exploit_detail_target == 3
    assert settings.liepin_explore_detail_target == 2
```

Change `tests/test_context_builder.py::_scored_candidate()` to accept `risk_score: int | None`, then add:

```python
def test_absent_risk_is_not_counted_as_high_risk() -> None:
    run_state = _run_state_for_stop_gate(
        candidates=[_scored_candidate("r-no-risk", round_no=1, risk_score=None)],
        completed_rounds=1,
        include_untried_family=True,
    )
    context = build_controller_context(
        run_state=run_state,
        round_no=2,
        min_rounds=1,
        max_rounds=4,
        target_new=5,
    )
    assert context.stop_guidance.high_risk_fit_count == 0
```

Keep each fixture in its existing test module; do not add cross-test imports.

- [ ] **Step 2: Run focused tests and confirm old assumptions fail**

Run:

```bash
uv run pytest -q \
  tests/test_liepin_config.py \
  tests/test_workbench_v2_runtime_service.py \
  tests/test_reflection_contract.py \
  tests/test_context_builder.py \
  tests/test_candidate_feedback.py
```

Expected: the default-target and Workbench-threshold tests fail; nullable-risk fixtures expose unsafe direct comparisons.

- [ ] **Step 3: Guard every optional-risk consumer**

Import and use `risk_at_or_above()` / `risk_at_or_below()` rather than numeric sentinels. Apply these exact semantic replacements:

```python
# high risk
risk_at_or_above(candidate.risk_score, 60)

# acceptable risk; absent risk is acceptable because the dimension is not applicable
risk_at_or_below(candidate.risk_score, 30)

# averages include applicable values only
risk_scores = [
    float(candidate.risk_score)
    for candidate in candidates
    if candidate.risk_score is not None
]
```

For risk-based ordering, use `candidate.risk_score if candidate.risk_score is not None else 0`; absent risk sorts as no role-defined risk. Update the listed reflection, controller, rescue, feedback, and orchestrator call sites. Do not coerce absent risk to 100 or treat it as a scoring failure. Prove there is no unsafe numeric comparison left:

```bash
rg -n "risk_score\s*(<=|>=|<|>)|-[a-zA-Z_]*\.risk_score" src/seektalent
```

Expected: no direct comparison or unary minus remains on nullable `ScoredCandidate.risk_score`; model validators and SQL column names are allowed matches only when they do not compare the value.

- [ ] **Step 4: Restore fixed Liepin baseline targets**

Change `AppSettings` defaults in `src/seektalent/config.py`:

```python
liepin_exploit_detail_target: int = 3
liepin_explore_detail_target: int = 2
```

Update existing assertions in `tests/test_liepin_config.py`; retain the `1..10` validators. In `source_adapters/query_policy.py`, make all three lane caps consume those existing settings:

```python
requested_count_caps_by_lane={
    "exploit": settings.liepin_exploit_detail_target,
    "generic_explore": settings.liepin_explore_detail_target,
    "prf_probe": settings.liepin_explore_detail_target,
}
```

Add a source-lane contract test proving default exploit/generic/PRF requests are `3/2/2`, and an override test proving the validated existing settings remain effective.

- [ ] **Step 5: Filter candidate summaries at the projection boundary**

Create `src/seektalent/candidate_visibility.py`:

```python
WORKBENCH_MIN_CANDIDATE_SCORE = 60


def is_workbench_visible_score(score: int | None) -> bool:
    return score is not None and score >= WORKBENCH_MIN_CANDIDATE_SCORE
```

Import this policy into both `src/seektalent_workbench_v2/runtime_service.py` and the legacy `src/seektalent_ui/agent_workbench_projection.py`; do not duplicate the threshold. In each route, build rows using the exact persisted deterministic score that will be projected, filter before sorting/slicing/ranking, and rerank the remaining rows contiguously. For V2:

```python
eligible_identities: list[
    tuple[RuntimeControlCandidateIdentity, list[RuntimeControlCandidateEvidence], int]
] = []
for identity in identities:
    evidence = evidence_by_identity.get(identity.identity_id, [])
    score = _candidate_score(identity, evidence)
    if not is_workbench_visible_score(score):
        continue
    eligible_identities.append((identity, evidence, score))
eligible_identities.sort(key=lambda row: (-row[2], row[0].identity_id))
for index, (identity, evidence, score) in enumerate(eligible_identities[: max(0, limit)], start=1):
    source_kinds = _candidate_source_kinds(evidence)
    headline = _candidate_headline(identity, evidence)
    display_name = _candidate_display_name(identity, evidence, fallback=f"候选人 {index}")
    evidence_level = _candidate_evidence_level(evidence)
    detail_availability = _candidate_detail_availability(identity, evidence)
    city = identity.location or _candidate_location(evidence)
    work_years = _candidate_experience_years(evidence)
    candidates.append(
        {
            "candidateId": identity.identity_id,
            "rank": index,
            "displayName": display_name,
            "avatarLabel": _candidate_avatar_label(display_name),
            "avatarColorKey": _candidate_avatar_color_key(identity.identity_id),
            "headline": headline,
            "company": identity.company or None,
            "currentTitle": _candidate_current_title(identity, evidence),
            "currentCompany": _candidate_current_company(identity, evidence),
            "location": city,
            "city": city,
            "education": _candidate_education(evidence),
            "experienceYears": work_years,
            "workYears": work_years,
            "age": _candidate_age(evidence),
            "gender": _candidate_gender(evidence),
            "activeStatus": _candidate_active_status(evidence),
            "jobStatus": _candidate_job_status(evidence),
            "sourceKinds": source_kinds,
            "sourceLabel": _candidate_source_label(source_kinds),
            "matchScore": score,
            "matchSummary": identity.summary or None,
            "status": identity.fit_bucket or "scored",
            "detailAvailability": detail_availability,
            "accessState": "allowed" if detail_availability != "unavailable" else "denied",
            "evidenceLevel": evidence_level,
        }
    )
```

Apply the same predicate inside legacy `_candidate_summaries()` after its canonical score projection and before its existing sort/slice/rank loop. Add a legacy contract fixture containing `59`, `60`, `90`, and unscored candidates and assert it returns only `90` then `60` with ranks `1, 2`; keep the V2 test equivalent. Do not delete low-score identities or evidence from runtime-control storage. Leave candidate-detail lookup unchanged on both routes.

- [ ] **Step 6: Run Gate A**

Run:

```bash
uv run pytest -q \
  tests/test_query_execution_contract.py \
  tests/test_query_plan.py \
  tests/test_second_lane_runtime.py \
  tests/test_controller_contract.py \
  tests/test_scoring_cache.py \
  tests/test_reflection_contract.py \
  tests/test_context_builder.py \
  tests/test_candidate_feedback.py \
  tests/test_runtime_state_flow.py \
  tests/test_liepin_config.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_agent_workbench_contract.py \
  tests/test_workbench_v2_runtime_service.py
```

Expected: PASS with deterministic totals, nullable optional dimensions, `3/2` defaults, and no summary below 60.

- [ ] **Step 7: Commit scoring consumers and baseline product behavior**

```bash
git add \
  src/seektalent/reflection/critic.py \
  src/seektalent/runtime/controller_context.py \
  src/seektalent/runtime/rescue_execution_runtime.py \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/candidate_feedback/extraction.py \
  src/seektalent/candidate_feedback/llm_prf.py \
  src/seektalent/config.py \
  src/seektalent/source_adapters/query_policy.py \
  src/seektalent/candidate_visibility.py \
  src/seektalent_ui/agent_workbench_projection.py \
  src/seektalent_workbench_v2/runtime_service.py \
  tests/test_reflection_contract.py \
  tests/test_context_builder.py \
  tests/test_candidate_feedback.py \
  tests/test_runtime_state_flow.py \
  tests/test_liepin_config.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_agent_workbench_contract.py \
  tests/test_workbench_v2_runtime_service.py
git commit -m "feat: align candidate quality behavior"
```

### Task 4: Add A Provider-Private First-Page Continuation Store

**Files:**
- Create: `src/seektalent/providers/liepin/first_page_continuation.py`
- Modify: `src/seektalent/core/retrieval/provider_contract.py`
- Test: `tests/test_liepin_search_workflow.py`
- Test: `tests/test_liepin_boundaries.py`

**Interfaces:**
- Produces `ProviderSearchContinuation`, `ProviderFirstPageExpansionResult`, `LiepinFirstPageContinuation`, `LiepinFirstPageCandidate`, and `LiepinFirstPageContinuationStore`.
- Task 5 writes baseline candidate states through `create()` and `mark_candidate()`.
- Task 6 consumes `load()` and the opaque protected reference, then calls `delete()` for every terminal expand/discard result.

- [ ] **Step 1: Write failing persistence and recovery tests**

Create focused tests in `tests/test_liepin_search_workflow.py`:

```python
def test_first_page_continuation_roundtrips_in_original_rank_order(tmp_path) -> None:
    store = LiepinFirstPageContinuationStore(tmp_path)
    continuation = store.create(
        source_run_id="source-run-1",
        logical_round_no=2,
        query_instance_id="query-2-exploit",
        keyword_query="AI Agent Python",
        visible_candidate_count=2,
        candidates=[
            LiepinFirstPageCandidate(
                rank=2,
                ref="private-ref-2",
                detail_url="https://h.liepin.com/resume/showresumedetail/?res_id_encode=subject2",
                provider_candidate_key_hash="b" * 64,
            ),
            LiepinFirstPageCandidate(
                rank=1,
                ref="private-ref-1",
                detail_url="https://h.liepin.com/resume/showresumedetail/?res_id_encode=subject1",
                provider_candidate_key_hash="a" * 64,
            ),
        ],
    )
    restored = store.load(continuation.opaque_ref)
    assert [item.rank for item in restored.candidates] == [1, 2]
    assert restored.candidates[0].state == "remaining"


def test_continuation_updates_are_atomic(tmp_path) -> None:
    store = LiepinFirstPageContinuationStore(tmp_path)
    continuation = store.create(
        source_run_id="source-run-1",
        logical_round_no=2,
        query_instance_id="query-2-exploit",
        keyword_query="AI Agent Python",
        visible_candidate_count=1,
        candidates=[
            LiepinFirstPageCandidate(
                rank=1,
                ref="private-ref-1",
                detail_url="https://h.liepin.com/resume/showresumedetail/?res_id_encode=subject1",
                provider_candidate_key_hash="a" * 64,
            )
        ],
    )
    store.mark_candidate(continuation.opaque_ref, rank=1, state="opened")
    assert store.load(continuation.opaque_ref).candidates[0].state == "opened"
    assert not list(tmp_path.rglob("*.tmp"))


def test_continuation_delete_and_fixed_orphan_cleanup(tmp_path) -> None:
    store = LiepinFirstPageContinuationStore(tmp_path)
    expired = _stored_continuation(store, query_instance_id="expired")
    fresh = _stored_continuation(store, query_instance_id="fresh")
    expired_path = safe_artifact_path(
        tmp_path.resolve(),
        store._relative_path(expired.opaque_ref).as_posix(),
    )
    old_timestamp = datetime.now(tz=timezone.utc).timestamp() - (8 * 24 * 60 * 60)
    os.utime(expired_path, (old_timestamp, old_timestamp))
    assert store.delete_expired() == 1
    with pytest.raises(FileNotFoundError):
        store.load(expired.opaque_ref)
    assert store.load(fresh.opaque_ref).query_instance_id == "fresh"
    store.delete(fresh.opaque_ref)
    with pytest.raises(FileNotFoundError):
        store.load(fresh.opaque_ref)
```

Use a small `_stored_continuation()` test helper and import `datetime`, `timezone`, `os`, and `safe_artifact_path`. Tests may inspect the private `_relative_path()` seam; do not expose a raw path on a public provider API.

- [ ] **Step 2: Run tests and confirm missing types**

Run:

```bash
uv run pytest -q tests/test_liepin_search_workflow.py tests/test_liepin_boundaries.py -k 'continuation'
```

Expected: collection fails because the continuation module and provider contract do not exist.

- [ ] **Step 3: Add the source-neutral private continuation carrier**

Add to `src/seektalent/core/retrieval/provider_contract.py`:

```python
ProviderContinuationKind = Literal["first_page_detail_expansion"]


@dataclass(frozen=True, kw_only=True)
class ProviderSearchContinuation:
    kind: ProviderContinuationKind
    continuation_id: str
    opaque_ref: str
    source_kind: str
    round_no: int
    query_instance_id: str
    visible_candidate_count: int
    eligible_candidate_count: int
    initial_opened_count: int


@dataclass(frozen=True, kw_only=True)
class ProviderFirstPageExpansionResult:
    search_result: SearchResult
    first_page_visible_count: int
    first_page_eligible_count: int
    initial_opened_count: int
    expansion_opened_count: int
    expansion_skipped_seen_count: int
    expansion_terminal_failure_count: int
    status: Literal["completed", "partial", "blocked", "failed"]
    safe_reason_code: str | None = None
    continuation_deleted: bool = False
```

Add this exact field to the existing `SearchResult` dataclass, after `latency_ms`:

```python
private_continuations: tuple[ProviderSearchContinuation, ...] = ()
```

`ProviderFirstPageExpansionResult` is declared after `SearchResult`, so its annotation resolves without a forward reference. Do not add either private type to any `to_public_payload()` method or HTTP schema.

- [ ] **Step 4: Implement the protected continuation store**

Create `src/seektalent/providers/liepin/first_page_continuation.py` with strict Pydantic models and atomic `0600` writes:

```python
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.artifacts import atomic_write_text, safe_artifact_path
from seektalent.providers.liepin.liepin_site_parsing import _safe_artifact_segment

CandidateState = Literal["remaining", "opened", "skipped_seen", "terminal_failed"]
ORPHAN_RETENTION_SECONDS = 7 * 24 * 60 * 60


class LiepinFirstPageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=30)
    ref: str = Field(min_length=1)
    detail_url: str = Field(min_length=1)
    provider_candidate_key_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: CandidateState = "remaining"


class LiepinFirstPageContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["liepin.first_page_continuation.v1"] = "liepin.first_page_continuation.v1"
    source_run_id: str
    logical_round_no: int = Field(ge=1)
    query_instance_id: str
    keyword_query: str = Field(min_length=1)
    visible_candidate_count: int = Field(ge=0, le=30)
    candidates: list[LiepinFirstPageCandidate] = Field(max_length=30)
    opaque_ref: str


class LiepinFirstPageContinuationStore:
    def __init__(self, protected_root: Path) -> None:
        self._protected_root = protected_root.resolve()
        self._lock = RLock()

    def create(
        self,
        *,
        source_run_id: str,
        logical_round_no: int,
        query_instance_id: str,
        keyword_query: str,
        visible_candidate_count: int,
        candidates: list[LiepinFirstPageCandidate],
    ) -> LiepinFirstPageContinuation:
        safe_run_id = _safe_artifact_segment(source_run_id)
        safe_query_id = (
            f"{_safe_artifact_segment(query_instance_id)[:48]}-"
            f"{sha256(query_instance_id.encode('utf-8')).hexdigest()[:16]}"
        )
        relative = Path("pi-detail") / safe_run_id / "first-page-continuations" / f"{safe_query_id}.json"
        opaque_ref = f"artifact://protected/{relative.as_posix()}"
        continuation = LiepinFirstPageContinuation(
            source_run_id=source_run_id,
            logical_round_no=logical_round_no,
            query_instance_id=query_instance_id,
            keyword_query=keyword_query,
            visible_candidate_count=visible_candidate_count,
            candidates=sorted(candidates, key=lambda item: item.rank),
            opaque_ref=opaque_ref,
        )
        self._write(relative, continuation)
        return continuation

    def load(self, opaque_ref: str) -> LiepinFirstPageContinuation:
        with self._lock:
            relative = self._relative_path(opaque_ref)
            return LiepinFirstPageContinuation.model_validate_json(
                safe_artifact_path(self._protected_root, relative.as_posix()).read_text(encoding="utf-8")
            )

    def mark_candidate(self, opaque_ref: str, *, rank: int, state: CandidateState) -> None:
        with self._lock:
            continuation = self.load(opaque_ref)
            updated = [
                item.model_copy(update={"state": state}) if item.rank == rank else item
                for item in continuation.candidates
            ]
            if not any(item.rank == rank for item in continuation.candidates):
                raise ValueError("first_page_continuation_rank_missing")
            relative = self._relative_path(opaque_ref)
            self._write(relative, continuation.model_copy(update={"candidates": updated}))

    def delete(self, opaque_ref: str) -> None:
        with self._lock:
            path = safe_artifact_path(
                self._protected_root,
                self._relative_path(opaque_ref).as_posix(),
            )
            path.unlink(missing_ok=True)

    def delete_expired(self, *, now_timestamp: float | None = None) -> int:
        cutoff = (time() if now_timestamp is None else now_timestamp) - ORPHAN_RETENTION_SECONDS
        removed = 0
        with self._lock:
            root = safe_artifact_path(self._protected_root, "pi-detail")
            for path in (root.rglob("first-page-continuations/*.json") if root.exists() else ()):
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _relative_path(self, opaque_ref: str) -> Path:
        prefix = "artifact://protected/"
        if not opaque_ref.startswith(prefix):
            raise ValueError("first_page_continuation_ref_invalid")
        relative = Path(opaque_ref.removeprefix(prefix))
        try:
            safe_artifact_path(self._protected_root, relative.as_posix())
        except ValueError as exc:
            raise ValueError("first_page_continuation_ref_invalid") from exc
        return relative

    def _write(self, relative: Path, continuation: LiepinFirstPageContinuation) -> None:
        path = safe_artifact_path(self._protected_root, relative.as_posix())
        atomic_write_text(path, continuation.model_dump_json())
        path.chmod(0o600)
```

- [ ] **Step 5: Run continuation-store tests**

Run:

```bash
uv run pytest -q tests/test_liepin_search_workflow.py tests/test_liepin_boundaries.py -k 'continuation'
```

Expected: PASS; protected refs and URLs appear only in private continuation files and test-local objects.

- [ ] **Step 6: Commit the private continuation foundation**

```bash
git add \
  src/seektalent/core/retrieval/provider_contract.py \
  src/seektalent/providers/liepin/first_page_continuation.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_boundaries.py
git commit -m "feat: persist private Liepin first pages"
```

### Task 5: Freeze Every Baseline Query's Original First Page

**Files:**
- Modify: `src/seektalent/runtime/source_query_intent.py:67-100`
- Modify: `src/seektalent/source_adapters/query_policy.py`
- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py:37-410`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py:1123-1185,2340-2500`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py:66-210`
- Modify: `src/seektalent/providers/liepin/worker_contracts.py:296-305`
- Modify: `src/seektalent/providers/liepin/client.py:584-628`
- Modify: `src/seektalent/sources/liepin/runtime_lane.py:67-225,361-398`
- Modify: `src/seektalent/source_contracts/contracts.py:93-112`
- Modify: `src/seektalent/source_contracts/runtime_lanes.py:318-371`
- Modify: `src/seektalent/runtime/source_round_dispatch.py`
- Modify: `src/seektalent/models.py`
- Test: `tests/test_runtime_source_adapter_boundary.py`
- Test: `tests/test_liepin_search_workflow.py`
- Test: `tests/test_liepin_opencli_retriever.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_liepin_boundaries.py`

**Interfaces:**
- Consumes: Task 4 continuation store and the existing stable candidate-key hash.
- Produces: one `ProviderSearchContinuation` for every successful baseline logical query, propagated privately through lane and retrieval results.
- Task 7 consumes continuations keyed by `query_instance_id`.

- [ ] **Step 1: Write failing first-page freeze tests**

Add `Path`, `Sequence`, the continuation classes, and `CandidateState` to the existing test imports. Extend `FakeLiepinSearchWorkflowSite` in `tests/test_liepin_search_workflow.py` with these fields and methods:

```python
continuation_store: LiepinFirstPageContinuationStore | None = None
saved_continuations: list[ProviderSearchContinuation] = field(default_factory=list)

def save_liepin_first_page_continuation(
    self,
    *,
    source_run_id: str,
    logical_round_no: int,
    query_instance_id: str,
    keyword_query: str,
    visible_candidate_count: int,
    candidates: Sequence[LiepinFirstPageCandidate],
) -> ProviderSearchContinuation:
    assert self.continuation_store is not None
    saved = self.continuation_store.create(
        source_run_id=source_run_id,
        logical_round_no=logical_round_no,
        query_instance_id=query_instance_id,
        keyword_query=keyword_query,
        visible_candidate_count=visible_candidate_count,
        candidates=list(candidates),
    )
    carrier = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id=source_run_id,
        opaque_ref=saved.opaque_ref,
        source_kind="liepin",
        round_no=logical_round_no,
        query_instance_id=query_instance_id,
        visible_candidate_count=saved.visible_candidate_count,
        eligible_candidate_count=len(saved.candidates),
        initial_opened_count=0,
    )
    self.saved_continuations.append(carrier)
    return carrier

def mark_liepin_first_page_candidate(
    self,
    *,
    opaque_ref: str,
    rank: int,
    state: CandidateState,
) -> None:
    assert self.continuation_store is not None
    self.continuation_store.mark_candidate(opaque_ref, rank=rank, state=state)
```

Add the baseline tests with a real 30-card fake page:

```python
def _first_page_site(tmp_path: Path, *, card_count: int) -> FakeLiepinSearchWorkflowSite:
    refs = tuple(str(70 + index) for index in range(card_count))
    return FakeLiepinSearchWorkflowSite(
        continuation_store=LiepinFirstPageContinuationStore(tmp_path),
        search_states=[_search_state_with_detail_targets(*refs)],
        structured_cards=[
            [
                {"ref": ref, "provider_rank": rank}
                for rank, ref in enumerate(refs, start=1)
            ]
        ],
    )


def test_baseline_search_freezes_thirty_visible_cards_but_opens_only_target(tmp_path) -> None:
    site = _first_page_site(tmp_path, card_count=30)
    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=3, max_cards=30),
        detail_open_claim_context=_private_claim_context(DetailOpenClaimLedger({})),
    )
    assert envelope["resumes_returned"] == 3
    assert site.saved_continuations[0].visible_candidate_count == 30
    assert site.calls.count("open_liepin_detail_cached_url") == 3
    assert "next_page" not in site.calls


def test_visible_and_eligible_first_page_counts_are_distinct(tmp_path) -> None:
    site = _first_page_site(tmp_path, card_count=30)
    site.detail_urls_by_ref.pop("99")
    LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=3, max_cards=30),
        detail_open_claim_context=_private_claim_context(DetailOpenClaimLedger({})),
    )
    continuation = site.saved_continuations[0]
    assert continuation.visible_candidate_count == 30
    assert continuation.eligible_candidate_count == 29
```

In `tests/test_liepin_runtime_source_lane.py`, import `replace` from `dataclasses` and `ProviderSearchContinuation` from the provider contract. Make `_run_fixture_two_query_liepin_bundle()` accept `worker_client: FakeWorker | None = None` and pass `worker_client or FakeWorker()` to the bundle. Then add:

```python
class ContinuationWorker(FakeWorker):
    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        result = await super().search(
            request,
            round_no=round_no,
            trace_id=trace_id,
            provider_account_hash=provider_account_hash,
        )
        query_instance_id = request.provider_context["query_instance_id"]
        continuation = ProviderSearchContinuation(
            kind="first_page_detail_expansion",
            continuation_id=trace_id,
            opaque_ref=f"artifact://protected/pi-detail/{query_instance_id}.json",
            source_kind="liepin",
            round_no=round_no,
            query_instance_id=query_instance_id,
            visible_candidate_count=30,
            eligible_candidate_count=30,
            initial_opened_count=1,
        )
        return replace(result, private_continuations=(continuation,))


def test_both_lanes_receive_independent_private_continuations() -> None:
    result = asyncio.run(_run_fixture_two_query_liepin_bundle(ContinuationWorker()))
    by_query = {item.query_instance_id: item for item in result.private_first_page_continuations}
    assert set(by_query) == {"primary-1", "explore-1"}
    assert by_query["primary-1"].opaque_ref != by_query["explore-1"].opaque_ref
    assert "opaque_ref" not in json.dumps(result.to_public_payload(), ensure_ascii=False)
```

- [ ] **Step 2: Run the first-page tests and confirm failure**

Run:

```bash
uv run pytest -q \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  -k 'first_page or continuation or thirty_visible'
```

Expected: failures show the current provider scan limit is `9/6`, no continuation is returned, and the workflow's URL cache disappears on return.

- [ ] **Step 3: Make Liepin scan the fixed full first page**

Extend `RuntimeSourceQueryPolicy` with an explicit per-lane scan limit:

```python
provider_scan_limits_by_lane: Mapping[LaneType, int] = field(default_factory=dict)

def provider_scan_limit(self, *, lane_type: LaneType, requested_count: int) -> int:
    fixed = self.provider_scan_limits_by_lane.get(lane_type)
    if fixed is not None:
        return fixed
    scan_limit = max(requested_count, requested_count * max(1, self.provider_scan_multiplier))
    if self.provider_scan_cap is not None:
        scan_limit = min(scan_limit, max(0, self.provider_scan_cap))
    return scan_limit
```

Preserve Task 3's settings-backed `requested_count_caps_by_lane` unchanged and add the fixed scan policy:

```python
provider_scan_limits_by_lane={"exploit": 30, "generic_explore": 30, "prf_probe": 30},
provider_scan_multiplier=3,
provider_scan_cap=settings.liepin_opencli_max_cards_per_task,
```

The per-lane value `30` is fixed product policy and is not read from a new setting. Keep the existing operational cap only for non-fixed lanes. Change `_provider_scan_limit()` to call `policy.provider_scan_limit(lane_type=lane_type, requested_count=requested_count)` and update its callers/tests accordingly.

For detail-backed Liepin requests, also hard-code the physical page budget to one in `_card_search_request()` regardless of `max_cards` or `page_size`:

```python
provider_context["liepin_max_pages"] = "1"
```

Do not call `_liepin_max_pages_for()` on this route. Add request-contract tests in `tests/test_liepin_runtime_source_lane.py`: exploit has target 3, generic explore has target 2, and PRF probe has target 2; every lane carries `liepin_max_cards == "30"` and `liepin_max_pages == "1"`. This is the executable proof that the fixed 30-card scan never turns into multiple pages.

In `run_liepin_logical_query_bundle()`, enforce the `3/2` target across all physical location/filter targets, not separately per target. Preserve the original total separately from the per-target remainder:

```python
logical_target_total = logical_requested_count
for target in targets:
    captured_detail_count = sum(len(item.candidate_store_updates) for item in target_results)
    remaining_target = max(0, logical_target_total - captured_detail_count)
    if remaining_target == 0:
        break
    target_result = await run_target(
        target=target,
        logical_requested_count=remaining_target,
        # existing fixed first-page scan inputs remain unchanged
    )
    target_results.append(target_result)
    captured_detail_count = sum(len(item.candidate_store_updates) for item in target_results)
    if captured_detail_count >= logical_target_total:
        break
```

Delete/replace the old loop break that compares total captures against the changing `logical_requested_count`; only compare against `logical_target_total`. Pass `remaining_target` as that target's request count and keep its fixed first-page scan limit at 30. Count successfully captured detail resumes, not identity-merged uniques and not visible cards. A pre-click duplicate has no candidate-store update, so it does not consume the remaining target and the workflow continues to the next eligible card.

Add multi-target regressions: exploit targets returning `2 + 1` and `1 + 1 + 1` must stop at 3; explore targets returning `1 + 1` must stop at 2; a duplicate skipped before click must not reduce the remaining target; and no later physical target is dispatched once the logical target is satisfied.

- [ ] **Step 4: Save the continuation before the first browser open**

Add provider-private site protocol methods:

```python
def save_liepin_first_page_continuation(
    self,
    *,
    source_run_id: str,
    logical_round_no: int,
    query_instance_id: str,
    keyword_query: str,
    visible_candidate_count: int,
    candidates: Sequence[LiepinFirstPageCandidate],
) -> ProviderSearchContinuation:
    raise NotImplementedError

def mark_liepin_first_page_candidate(
    self,
    *,
    opaque_ref: str,
    rank: int,
    state: CandidateState,
) -> None:
    raise NotImplementedError
```

In `LiepinSearchWorkflow`, after `detail_urls_by_rank` is complete and before `opened = 0`, build candidates only for cards with a valid stable hash. Generate a continuation only on the claim-aware runtime route where `detail_open_claim_context` supplies real round/query provenance. The existing public `search_detail_backed_resumes()` path permits `detail_open_claim_context=None`; preserve that behavior and return `_private_first_page_continuations=()` rather than inventing IDs or dereferencing `None`.

Inside the non-null branch, save the continuation and retain the returned private carrier:

```python
first_page_candidates: list[LiepinFirstPageCandidate] = []
for card in card_items:
    selected = _card_ref_and_rank(card)
    if selected is None:
        continue
    ref, rank = selected
    detail_url = detail_urls_by_rank.get(rank)
    provider_candidate_key_hash = (
        stable_liepin_detail_candidate_key_hash(detail_url) if detail_url is not None else None
    )
    if detail_url is None or provider_candidate_key_hash is None:
        continue
    first_page_candidates.append(
        LiepinFirstPageCandidate(
            rank=rank,
            ref=ref,
            detail_url=detail_url,
            provider_candidate_key_hash=provider_candidate_key_hash,
        )
    )
private_continuation = self._site.save_liepin_first_page_continuation(
    source_run_id=request.source_run_id,
    logical_round_no=detail_open_claim_context.logical_round_no,
    query_instance_id=detail_open_claim_context.query_instance_id,
    keyword_query=request.query,
    visible_candidate_count=len(card_items),
    candidates=first_page_candidates,
)
```

On the claim-aware baseline path, freeze `baseline_candidates = tuple(first_page_candidates)` and use that immutable tuple as the only open/claim/mark queue for the rest of the request. Open each cached `detail_url`, claim its persisted `provider_candidate_key_hash`, and mark the persisted candidate's original rank; later search-state/card extractions may update readiness evidence but must never replace or re-rank this queue. Add a reorder regression where the visible page swaps A/B after the first open: baseline still opens and marks the original snapshot identities exactly once, and continuation state never assigns B's refreshed rank to A.

Add a compatibility regression calling `search_detail_backed_resumes()` without a claim context. It must still open its target and return no private continuation. Add a claim-aware regression proving the continuation uses the supplied `logical_round_no` and `query_instance_id`.

Wire the real store in `LiepinSiteAdapter`, not only the fake. Add a lazy private accessor that resolves the already-supplied run artifact root and keeps continuations under its protected child:

```python
def _first_page_continuation_store(self) -> LiepinFirstPageContinuationStore:
    root = self._site_config.artifact_root
    if root is None:
        raise OpenCliBrowserError("liepin_protected_artifact_root_missing")
    if self._continuation_store is None:
        self._continuation_store = LiepinFirstPageContinuationStore(root / "protected")
        self._continuation_store.delete_expired()
    return self._continuation_store
```

Initialize `_continuation_store` to `None` in `__init__`; implement save/load/mark/delete methods by delegating to this accessor. Production `build_liepin_opencli_worker_client()` already passes `settings.artifacts_path`, so no new setting is introduced. Add a real-adapter roundtrip test using `tmp_path` that verifies the file lives under `<artifact_root>/protected/pi-detail/...`, has mode `0600`, and is absent from the public artifact tree and serialized public payload.

Mark baseline candidates `opened`, `skipped_seen`, or `terminal_failed` immediately after each corresponding claim transition. Before returning the workflow envelope, attach `replace(private_continuation, initial_opened_count=opened)` under `_private_first_page_continuations` only when the claim-aware branch created it; otherwise attach an empty tuple. Add this Pydantic private attribute to `LiepinResumeSearchResponse`:

```python
_private_first_page_continuations: tuple[ProviderSearchContinuation, ...] = PrivateAttr(default=())
```

`_response_from_opencli_envelope()` must remove the private key before validating public fields, type-check every item as `ProviderSearchContinuation`, and assign the tuple to this `PrivateAttr`. Pydantic serialization therefore cannot emit it.

- [ ] **Step 5: Propagate the continuation through private result fields**

Add `private_first_page_continuations: tuple[ProviderSearchContinuation, ...] = ()` to `SourceLaneResult` and `RuntimeSourceLaneResult`. Add it to their merge functions but omit it from every public payload.

Map the workflow's private continuation into `SearchResult.private_continuations` in `opencli_retriever.py`; copy it in `client.py` and `_card_lane_result_from_search_result()`. Do not place it in `request_payload`.

Correct the detail-backed count contract at the same boundary:

```python
return LiepinResumeSearchResponse(
    resumes=resumes,
    exhausted=status == "succeeded",
    requestPayload=request_payload,
    raw_candidate_count=len(resumes),
)
```

In `_with_liepin_query_execution_outcome()`, stop deriving duplicates as `raw_candidate_count - len(candidate_store_updates)`. Sum the existing safe `detail_open_skipped_seen_count` workflow events for each target, then add only cross-target identity duplicates:

```python
pre_click_duplicate_count = sum(
    int(event.safe_counts.get("detail_open_skipped_seen_count", 0))
    for result in target_results
    for event in result.events
    if event.step_name == "finalize"
)
duplicate_candidate_count = pre_click_duplicate_count + cross_target_duplicate_candidate_count
```

Persist `pre_click_duplicate_count` separately as `pre_click_skipped_seen_count` on `SourceQueryExecutionOutcome` and copy it into a non-negative `QueryExecutionReceipt.pre_click_skipped_seen_count`. Keep `duplicate_candidate_count` on the source outcome/receipt for source-local observability, but Task 8's final logical-query allocator uses the separate pre-click field plus canonical post-merge duplicates so it never double-counts `cross_target_duplicate_candidate_count`. Do not infer unopened visible cards as duplicates. Add regressions for `30 visible / 3 captured / 0 skipped => raw=3, duplicates=0, first_page_visible=30` and a baseline cross-lane ledger skip that survives final logical outcome accounting exactly once.

- [ ] **Step 6: Run Gate B baseline tests**

Run:

```bash
uv run pytest -q \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_liepin_boundaries.py
```

Expected: PASS; each query opens only `3/2`, freezes up to 30 original cards, and leaks no continuation material publicly.

- [ ] **Step 7: Commit baseline first-page freezing**

```bash
git add \
  src/seektalent/runtime/source_query_intent.py \
  src/seektalent/source_adapters/query_policy.py \
  src/seektalent/providers/liepin/liepin_search_workflow.py \
  src/seektalent/providers/liepin/liepin_site_adapter.py \
  src/seektalent/providers/liepin/opencli_retriever.py \
  src/seektalent/providers/liepin/worker_contracts.py \
  src/seektalent/providers/liepin/client.py \
  src/seektalent/sources/liepin/runtime_lane.py \
  src/seektalent/source_contracts/contracts.py \
  src/seektalent/source_contracts/runtime_lanes.py \
  src/seektalent/runtime/source_round_dispatch.py \
  src/seektalent/models.py \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_liepin_boundaries.py
git commit -m "feat: freeze Liepin logical-query first pages"
```

### Task 6: Consume Or Discard A Continuation Without Searching Or Paging

**Files:**
- Create: `src/seektalent/runtime/source_expansion.py`
- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/liepin/opencli_worker_client.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/sources/liepin/runtime_lane.py`
- Modify: `src/seektalent/source_adapters/round_adapters.py`
- Modify: `src/seektalent/source_adapters/runtime_composition.py`
- Modify: `src/seektalent/runtime/composition.py`
- Modify: `src/seektalent/runtime/orchestrator.py:890-910`
- Modify: `src/seektalent/source_contracts/detail_open_claims.py`
- Test: `tests/test_liepin_search_workflow.py`
- Test: `tests/test_liepin_opencli_worker_client.py`
- Test: `tests/test_runtime_source_adapter_boundary.py`
- Test: `tests/test_liepin_detail_open_claims.py`

**Interfaces:**
- Consumes: `ProviderSearchContinuation` and the run-owned `DetailOpenClaimLedger`.
- Produces: `SourceFirstPageExpansionRequest`, `SourceFirstPageExpansionResult`, `SourceFirstPageExpansionError`, `SourceFirstPageExpander`, and `run_liepin_first_page_expansion()`; every browser attempt is checkpointed through the existing run callback.
- Task 7 sends every decision through the source-neutral expander provider with `action="expand"` or `action="discard"`, ensuring rejected continuations are deleted without browser work.

- [ ] **Step 1: Write failing no-search expansion tests**

Add `load_liepin_first_page_continuation()` to the Task 5 fake site and a baseline helper:

```python
def load_liepin_first_page_continuation(self, opaque_ref: str) -> LiepinFirstPageContinuation:
    assert self.continuation_store is not None
    return self.continuation_store.load(opaque_ref)


def _baseline_with_continuation(
    tmp_path: Path,
    *,
    visible: int,
    opened: int,
) -> tuple[FakeLiepinSearchWorkflowSite, ProviderSearchContinuation, DetailOpenClaimLedger]:
    site = _first_page_site(tmp_path, card_count=visible)
    ledger = DetailOpenClaimLedger({})
    workflow = LiepinSearchWorkflow(site=site)
    workflow._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=opened, max_cards=visible),
        detail_open_claim_context=_private_claim_context(ledger),
    )
    return site, replace(site.saved_continuations[0], initial_opened_count=opened), ledger
```

Add the no-search and duplicate tests:

```python
def test_expansion_consumes_every_remaining_snapshot_candidate_in_rank_order(tmp_path) -> None:
    site, continuation, ledger = _baseline_with_continuation(tmp_path, visible=6, opened=2)
    baseline_open_count = len(site.cached_opened_refs)
    envelope = LiepinSearchWorkflow(site=site).expand_first_page_continuation(
        continuation_ref=continuation.opaque_ref,
        detail_open_claim_context=_private_claim_context(ledger),
    )
    assert envelope["resumes_returned"] == 4
    assert site.cached_opened_refs[baseline_open_count:] == ["72", "73", "74", "75"]
    expansion_events = [
        event for event in site.events if event.get("action_kind") == "first_page_expansion_completed"
    ]
    assert expansion_events[0]["expansion_opened_count"] == 4
    assert site.calls.count("search_liepin_cards") == 1
    assert "next_page" not in site.calls


def test_expansion_skips_seen_and_continues_to_page_end(tmp_path) -> None:
    site, continuation, ledger = _baseline_with_continuation(tmp_path, visible=5, opened=2)
    stored = site.load_liepin_first_page_continuation(continuation.opaque_ref)
    rank_three = next(item for item in stored.candidates if item.rank == 3)
    assert ledger.try_claim(rank_three.provider_candidate_key_hash) is True
    envelope = LiepinSearchWorkflow(site=site).expand_first_page_continuation(
        continuation_ref=continuation.opaque_ref,
        detail_open_claim_context=_private_claim_context(ledger),
    )
    assert envelope["expansion_skipped_seen_count"] == 1
    assert site.cached_opened_refs[-2:] == ["73", "74"]
```

Add the failure case:

```python
def test_expansion_failure_preserves_baseline_and_returns_partial(tmp_path) -> None:
    site, continuation, ledger = _baseline_with_continuation(tmp_path, visible=5, opened=2)
    baseline_resumes = list(site.resumes)
    site.open_ok = False
    envelope = LiepinSearchWorkflow(site=site).expand_first_page_continuation(
        continuation_ref=continuation.opaque_ref,
        detail_open_claim_context=_private_claim_context(ledger),
    )
    assert envelope["status"] == "partial"
    assert envelope["safe_reason_code"] == "liepin_first_page_expansion_partial"
    assert site.resumes[:2] == baseline_resumes
    restored = site.load_liepin_first_page_continuation(continuation.opaque_ref)
    assert all(item.state == "terminal_failed" for item in restored.candidates[2:])


def test_expansion_releases_unattempted_claim_and_keeps_candidate_remaining(tmp_path) -> None:
    site, continuation, ledger = _baseline_with_continuation(tmp_path, visible=4, opened=2)
    site.search_states = [
        OpenCliBrowserResult(
            ok=False,
            action="state",
            safe_reason_code="liepin_opencli_results_not_ready",
        )
    ]
    envelope = LiepinSearchWorkflow(site=site).expand_first_page_continuation(
        continuation_ref=continuation.opaque_ref,
        detail_open_claim_context=_private_claim_context(ledger),
    )
    restored = site.load_liepin_first_page_continuation(continuation.opaque_ref)
    rank_three = next(item for item in restored.candidates if item.rank == 3)
    assert envelope["status"] == "partial"
    assert rank_three.state == "remaining"
    assert rank_three.provider_candidate_key_hash not in ledger.snapshot()


def test_discard_deletes_continuation_without_browser_action(tmp_path) -> None:
    site, continuation, _ledger = _baseline_with_continuation(tmp_path, visible=5, opened=2)
    calls_before = list(site.calls)
    site.discard_liepin_first_page_continuation(continuation.opaque_ref)
    assert site.calls == calls_before
    with pytest.raises(FileNotFoundError):
        site.load_liepin_first_page_continuation(continuation.opaque_ref)
```

- [ ] **Step 2: Run expansion tests and confirm missing route**

Run:

```bash
uv run pytest -q \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_runtime_source_adapter_boundary.py \
  -k 'expansion'
```

Expected: collection fails because no expansion request/result or provider route exists.

- [ ] **Step 3: Define the source-neutral expansion contract**

Create `src/seektalent/runtime/source_expansion.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.models import ResumeCandidate
from seektalent.source_contracts import RuntimeQueryCandidateAttribution, RuntimeSourceLaneResult

ExpansionStatus = Literal["completed", "partial", "blocked", "failed"]
ExpansionAction = Literal["expand", "discard"]


class SourceFirstPageExpansionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: ExpansionStatus,
        safe_reason_code: str,
        continuation_deleted: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.safe_reason_code = safe_reason_code
        self.continuation_deleted = continuation_deleted


@dataclass(frozen=True, kw_only=True)
class SourceFirstPageExpansionRequest:
    runtime_run_id: str
    round_no: int
    source_kind: str
    query_instance_id: str
    continuation_id: str
    continuation: ProviderSearchContinuation
    action: ExpansionAction


@dataclass(frozen=True, kw_only=True)
class SourceFirstPageExpansionResult:
    source_kind: str
    query_instance_id: str
    continuation_id: str
    status: ExpansionStatus
    candidates: tuple[ResumeCandidate, ...] = ()
    candidate_query_attributions: tuple[RuntimeQueryCandidateAttribution, ...] = ()
    lane_result: RuntimeSourceLaneResult | None = None
    first_page_visible_count: int = 0
    first_page_eligible_count: int = 0
    initial_opened_count: int = 0
    expansion_opened_count: int = 0
    expansion_skipped_seen_count: int = 0
    expansion_terminal_failure_count: int = 0
    safe_reason_code: str | None = None
    continuation_deleted: bool = False


SourceFirstPageExpander = Callable[
    [SourceFirstPageExpansionRequest],
    Awaitable[SourceFirstPageExpansionResult],
]
```

- [ ] **Step 4: Implement provider-private continuation consumption**

Add `expand_first_page_continuation()` to `LiepinSearchWorkflow` using the same claim/open/wait/capture transitions as baseline:

```python
continuation = self._site.load_liepin_first_page_continuation(continuation_ref)
ledger = detail_open_claim_context.detail_open_claim_ledger
initial_opened_count = sum(item.state == "opened" for item in continuation.candidates)
opened_ranks: set[int] = set()
skipped_seen = 0
terminal_failures = 0
last_reason: str | None = None
interrupted = False

def finish_failure(candidate: LiepinFirstPageCandidate, reason: str) -> bool:
    nonlocal terminal_failures, last_reason
    if ledger.has_browser_open_attempt(candidate.provider_candidate_key_hash):
        ledger.mark_terminal_failed(
            candidate.provider_candidate_key_hash,
            safe_reason_code=reason,
        )
        self._site.mark_liepin_first_page_candidate(
            opaque_ref=continuation_ref,
            rank=candidate.rank,
            state="terminal_failed",
        )
        terminal_failures += 1
        last_reason = reason
        return True
    ledger.release_unattempted(candidate.provider_candidate_key_hash)
    last_reason = reason
    return False

for candidate in continuation.candidates:
    if candidate.state != "remaining":
        continue
    candidate_key = candidate.provider_candidate_key_hash
    if not ledger.try_claim(candidate_key):
        self._site.mark_liepin_first_page_candidate(
            opaque_ref=continuation_ref,
            rank=candidate.rank,
            state="skipped_seen",
        )
        skipped_seen += 1
        continue

    open_result = self._open_detail_with_retry(
        source_run_id=continuation.source_run_id,
        ref=candidate.ref,
        rank=candidate.rank,
        cached_detail_url=candidate.detail_url,
        use_cached=True,
        before_browser_open_attempt=lambda key=candidate_key: ledger.record_browser_open_attempt(key),
    )
    if not open_result.ok:
        attempted = finish_failure(
            candidate,
            open_result.safe_reason_code or "liepin_opencli_detail_not_opened",
        )
        if not attempted:
            interrupted = True
            break
        continue
    wait_result = self._wait_detail_ready_transition(
        source_run_id=continuation.source_run_id,
        rank=candidate.rank,
    )
    if not wait_result.ok:
        finish_failure(candidate, wait_result.safe_reason_code or "liepin_opencli_detail_not_opened")
        continue
    capture_result = self._capture_detail_transition(
        source_run_id=continuation.source_run_id,
        rank=candidate.rank,
        require_ready=False,
        expected_provider_candidate_key_hash=candidate_key,
    )
    if not capture_result.ok:
        finish_failure(candidate, capture_result.safe_reason_code or "liepin_opencli_detail_not_opened")
        continue
    ledger.mark_opened(candidate_key)
    self._site.mark_liepin_first_page_candidate(
        opaque_ref=continuation_ref,
        rank=candidate.rank,
        state="opened",
    )
    opened_ranks.add(candidate.rank)

finalized = self._site.finalize_liepin_resumes(
    source_run_id=continuation.source_run_id,
    query=continuation.keyword_query,
    max_pages=1,
    max_cards=len(continuation.candidates),
    cards_seen=len(continuation.candidates),
    target_resumes=None,
)
expansion_resumes = [
    item
    for item in finalized.get("resumes", [])
    if isinstance(item, Mapping) and item.get("provider_rank") in opened_ranks
]
final_continuation = self._site.load_liepin_first_page_continuation(continuation_ref)
remaining_count = sum(item.state == "remaining" for item in final_continuation.candidates)
status = (
    "completed"
    if not interrupted and terminal_failures == 0 and remaining_count == 0
    else "partial"
)
return {
    **finalized,
    "status": status,
    "safe_reason_code": "liepin_first_page_expansion_partial" if status == "partial" else None,
    "resumes": expansion_resumes,
    "resumes_returned": len(expansion_resumes),
    "first_page_visible_count": continuation.visible_candidate_count,
    "first_page_eligible_count": len(continuation.candidates),
    "initial_opened_count": initial_opened_count,
    "expansion_opened_count": len(opened_ranks),
    "expansion_skipped_seen_count": skipped_seen,
    "expansion_terminal_failure_count": terminal_failures,
    "last_safe_reason_code": last_reason,
}
```

Wrap the loop in the existing OpenCLI safe-error boundary: action/timeout exhaustion returns the successful captures so far with `status="partial"` and a safe reason. The method must never call `search_liepin_cards()`, `restore_liepin_search_page()`, or a pagination action.

Track an explicit `interrupted` flag. If `finish_failure()` returns `False` because no browser attempt occurred, set `interrupted=True` before breaking; final status is `partial` whenever `interrupted` is true or any eligible row remains `remaining`, even when `terminal_failures == 0`. Only a page with no remaining eligible rows and no terminal failure is `completed`.

Expose `discard_liepin_first_page_continuation(opaque_ref)` on the same private site seam. Carry the source request `action` through provider adapter, worker client, retriever, and site adapter; no upper layer reaches into site storage directly. The site handles `action="discard"` by deleting the protected file and returning a zero-candidate completed result without invoking browser automation. For `action="expand"`, the provider route deletes the protected file in a `finally` block after constructing its terminal result, including `partial`, `blocked`, or `failed`. Add `continuation_deleted: bool` to the provider/source private result and `SourceFirstPageExpansionError`; only the provider boundary sets it true after delete/read-back confirms absence. A deletion failure returns or raises with `continuation_deleted=False` and a safe cleanup reason. The in-memory result must contain only mapped candidates and safe counts; it must not retain the URL-bearing continuation model. An unexpected process crash can leave an orphan, which Task 4's fixed seven-day startup cleanup removes; this plan does not claim full runtime resume of continuation work.

The per-candidate open/wait/capture sequence must also use this exact exception shape so a granted claim is never stranded:

```python
try:
    open_result = self._open_detail_with_retry(
        source_run_id=continuation.source_run_id,
        ref=candidate.ref,
        rank=candidate.rank,
        cached_detail_url=candidate.detail_url,
        use_cached=True,
        before_browser_open_attempt=lambda key=candidate_key: ledger.record_browser_open_attempt(key),
    )
except OpenCliBrowserError as exc:
    finish_failure(candidate, exc.safe_reason_code or "liepin_opencli_detail_not_opened")
    break
```

Use the same `finish_failure()` call in the safe-error boundary around wait/capture. Unattempted failures leave the continuation row `remaining`; attempted failures become `terminal_failed`.

- [ ] **Step 5: Carry expansion through the installed OpenCLI stack**

Add one explicit method at each boundary, preserving the same arguments. Provider adapter and OpenCLI worker client are asynchronous:

```python
async def handle_first_page_continuation_with_detail_open_claim_ledger(
    self,
    *,
    action: Literal["expand", "discard"],
    continuation: ProviderSearchContinuation,
    detail_open_claim_ledger: DetailOpenClaimLedger,
    logical_round_no: int,
    query_instance_id: str,
) -> ProviderFirstPageExpansionResult:
    raise NotImplementedError
```

Retriever and site adapter remain synchronous because the worker client already owns the `asyncio.to_thread()` boundary; give those two layers the same keyword-only signature with `def`, not `async def`. Every layer forwards `action` unchanged. The retriever returns a zero-candidate completed result for discard and maps captured resumes through the existing detail mapping for expand. It constructs `ProviderFirstPageExpansionResult`; the source adapter maps that object to `SourceFirstPageExpansionResult` and includes a `RuntimeSourceLaneResult` so candidate/source evidence follows the normal merge path. Counts remain typed fields, never arbitrary `request_payload` data. Add a contract test that rejects/flags a coroutine returned by the synchronous retriever seam.

Translate only expected provider-boundary failures (`ProviderSearchError`, `LiepinWorkerModeError`, and the existing safe OpenCLI boundary error) into `SourceFirstPageExpansionError` with a safe status/reason. Do not catch `Exception`: programmer errors and invariant violations must still fail loudly. Task 7 catches this source-neutral error per continuation so one failed lane cannot prevent the next lane from being discarded or expanded.

The OpenCLI worker client must route baseline search and first-page expansion through the same existing `threading.Lock`, `_OPENCLI_SEARCH_LOCK`. Match the baseline pattern: `asyncio.to_thread()` calls a synchronous helper, and that helper acquires the lock:

```python
async def handle_first_page_continuation_with_detail_open_claim_ledger(
    self,
    *,
    action: Literal["expand", "discard"],
    continuation: ProviderSearchContinuation,
    detail_open_claim_ledger: DetailOpenClaimLedger,
    logical_round_no: int,
    query_instance_id: str,
) -> ProviderFirstPageExpansionResult:
    return await asyncio.to_thread(
        self._handle_first_page_continuation_sync,
        action=action,
        continuation=continuation,
        detail_open_claim_ledger=detail_open_claim_ledger,
        logical_round_no=logical_round_no,
        query_instance_id=query_instance_id,
    )

def _handle_first_page_continuation_sync(
    self,
    *,
    action: Literal["expand", "discard"],
    continuation: ProviderSearchContinuation,
    detail_open_claim_ledger: DetailOpenClaimLedger,
    logical_round_no: int,
    query_instance_id: str,
) -> ProviderFirstPageExpansionResult:
    with _OPENCLI_SEARCH_LOCK:
        return self._retriever.handle_first_page_continuation_with_detail_open_claim_ledger(
            action=action,
            continuation=continuation,
            detail_open_claim_ledger=detail_open_claim_ledger,
            logical_round_no=logical_round_no,
            query_instance_id=query_instance_id,
        )
```

Add a concurrency regression that starts one baseline search and one expansion against a recording fake retriever and asserts `max_active_calls == 1`. This preserves the single shared Chrome/OpenCLI session invariant.

Add `continuation_deleted` to `ProviderFirstPageExpansionResult`; provider adapter sets it only after its `finally` delete plus absence check, source adapter propagates it unchanged, and expected boundary exceptions carry the same acknowledgement. At the provider adapter seam, add a parameterized regression for `completed`, `partial`, and `failed` results proving the file is absent and `continuation_deleted is True`. Add `action="discard"` coverage for acknowledgement plus zero browser calls, and a pre-provider failure with false acknowledgement. Direct workflow tests may inspect the continuation before the provider `finally`; lifecycle deletion is owned and proven at the provider seam.

Clarification: `action="discard"` short-circuits at the provider-private continuation store before worker construction, OpenCLI readiness, retriever, or browser startup. Only `action="expand"` crosses the worker/retriever/site stack. This guarantees that cleanup can still delete and acknowledge a continuation after an expand-side worker/provider startup failure.

- [ ] **Step 6: Checkpoint every attempted detail claim**

Add an optional callback to `DetailOpenClaimLedger`:

```python
DetailClaimCheckpoint = Callable[[], None]


class DetailOpenClaimLedger:
    def __init__(
        self,
        claims: MutableMapping[str, RuntimeDetailOpenClaim],
        *,
        checkpoint: DetailClaimCheckpoint | None = None,
    ) -> None:
        self._claims = claims
        self._lock = RLock()
        self._checkpoint_callback = checkpoint

    def _checkpoint(self) -> None:
        if self._checkpoint_callback is not None:
            self._checkpoint_callback()
```

Call `_checkpoint()` after leaving the lock in `record_browser_open_attempt()`, `mark_opened()`, and `mark_terminal_failed()`. Do not checkpoint a merely granted but unattempted claim. Add to `tests/test_liepin_detail_open_claims.py`:

```python
def test_attempt_and_terminal_claim_transitions_are_checkpointed() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    snapshots: list[dict[str, RuntimeDetailOpenClaim]] = []
    ledger: DetailOpenClaimLedger
    ledger = DetailOpenClaimLedger(claims, checkpoint=lambda: snapshots.append(ledger.snapshot()))
    assert ledger.try_claim("candidate-key") is True
    assert snapshots == []
    ledger.record_browser_open_attempt("candidate-key")
    ledger.mark_opened("candidate-key")
    assert [snapshot["candidate-key"].status for snapshot in snapshots] == ["claimed", "opened"]
    assert DetailOpenClaimLedger(snapshots[-1]).try_claim("candidate-key") is False
```

This proves checkpoint ordering for the current runtime-control state; it does not claim that `WorkflowRuntime` can resume an interrupted continuation.

Construct the run ledger in `WorkflowRuntime.run_async()` with a late-bound checkpoint callback:

```python
detail_open_claim_ledger: DetailOpenClaimLedger
detail_open_claim_ledger = DetailOpenClaimLedger(
    run_state.detail_open_claims_by_provider_key,
    checkpoint=lambda: self._refresh_runtime_candidate_checkpoint(
        runtime_checkpoint_callback=runtime_checkpoint_callback,
        tracer=tracer,
        run_state=run_state,
        detail_open_claim_ledger=detail_open_claim_ledger,
    ),
)
```

The existing optional callback preserves unit-test and non-Workbench callers.

- [ ] **Step 7: Register a Liepin source expander in runtime composition**

Define the provider beside the existing source-round adapter provider:

```python
RuntimeSourceFirstPageExpanderProvider = Callable[
    ["WorkflowRuntime", DetailOpenClaimLedger],
    Mapping[str, SourceFirstPageExpander],
]
```

Add `source_first_page_expander_provider: RuntimeSourceFirstPageExpanderProvider` to `RuntimeComposition`, pass it through `build_workflow_runtime()`, and set it in `build_runtime_composition()` to `default_source_first_page_expander_provider`. Implement the default provider in `source_adapters/round_adapters.py`:

```python
def default_source_first_page_expander_provider(
    runtime: WorkflowRuntime,
    detail_open_claim_ledger: DetailOpenClaimLedger,
) -> Mapping[str, SourceFirstPageExpander]:
    async def expand_liepin(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        return await run_liepin_first_page_expansion(
            settings=runtime.settings,
            request=request,
            detail_open_claim_ledger=detail_open_claim_ledger,
        )

    return {"liepin": expand_liepin}
```

CTS has no continuation and therefore no expander entry.
Add `source_first_page_expander_provider: RuntimeSourceFirstPageExpanderProvider | None = None` to the direct `WorkflowRuntime` constructor for test/custom-composition compatibility; the shipped `build_runtime_composition()` always supplies the provider, so this is dependency injection rather than a user-visible feature switch.

- [ ] **Step 8: Run provider expansion tests**

Run:

```bash
uv run pytest -q \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_liepin_boundaries.py \
  tests/test_liepin_detail_open_claims.py
```

Expected: PASS; expansion opens all remaining eligible snapshot candidates, skips duplicates before click, and sends no search or pagination action.

- [ ] **Step 9: Commit the expansion transport**

```bash
git add \
  src/seektalent/runtime/source_expansion.py \
  src/seektalent/providers/liepin/liepin_search_workflow.py \
  src/seektalent/providers/liepin/liepin_site_adapter.py \
  src/seektalent/providers/liepin/opencli_retriever.py \
  src/seektalent/providers/liepin/opencli_worker_client.py \
  src/seektalent/providers/liepin/adapter.py \
  src/seektalent/providers/liepin/client.py \
  src/seektalent/sources/liepin/runtime_lane.py \
  src/seektalent/source_adapters/round_adapters.py \
  src/seektalent/source_adapters/runtime_composition.py \
  src/seektalent/runtime/composition.py \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/source_contracts/detail_open_claims.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_liepin_boundaries.py \
  tests/test_liepin_detail_open_claims.py
git commit -m "feat: consume qualified Liepin first pages"
```

### Task 7: Add The Fixed Per-Lane Quality Gate

**Files:**
- Create: `src/seektalent/runtime/first_page_expansion.py`
- Modify: `src/seektalent/models.py:470-520`
- Modify: `src/seektalent/runtime/source_round_dispatch.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py:290-320`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

**Interfaces:**
- Consumes: baseline continuations, candidate-query attributions, scorecards, and fixed thresholds.
- Produces: `FirstPageExpansionDecision`, `ExpansionQueryMergeCounts`, `canonical_scorecards_by_identity_id()`, `select_qualified_first_page_expansions()`, `execute_first_page_decisions()`, `apply_first_page_expansion_to_receipts()`, and safe receipt counters.
- Task 8 invokes these functions from the round orchestrator.

- [ ] **Step 1: Write failing pure policy tests**

Import `FitBucket`, `ProviderSearchContinuation`, the three `SourceFirstPageExpansion*` types, and the Task 7 policy/executor functions, then add to `tests/test_runtime_state_flow.py`:

```python
def _expansion_score(
    resume_id: str,
    *,
    overall: int,
    must: int,
    risk: int | None = None,
    fit_bucket: FitBucket = "fit",
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket=fit_bucket,
        overall_score=overall,
        must_have_match_score=must,
        preferred_match_score=None,
        risk_score=risk,
        risk_flags=[],
        reasoning_summary="first-page quality fixture",
        evidence=[],
        confidence="high",
        matched_must_haves=[],
        missing_must_haves=[],
        matched_preferences=[],
        negative_signals=[],
        strengths=[],
        weaknesses=[],
        source_round=2,
    )


def _expansion_continuation(*, initial_opened_count: int = 3) -> ProviderSearchContinuation:
    return ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="query-exploit-target-1",
        opaque_ref="artifact://protected/pi-detail/query-exploit.json",
        source_kind="liepin",
        round_no=2,
        query_instance_id="query-exploit",
        visible_candidate_count=30,
        eligible_candidate_count=30,
        initial_opened_count=initial_opened_count,
    )


def _completed_query_receipt(*, requested_count: int) -> QueryExecutionReceipt:
    return QueryExecutionReceipt(
        round_no=2,
        source_kind="liepin",
        query_instance_id="query-exploit",
        query_fingerprint="fingerprint-exploit",
        term_group_key="term-group-exploit",
        non_anchor_term_family_ids=["domain.rag"],
        query_role="exploit",
        lane_type="exploit",
        query_terms=["AI Agent", "RAG"],
        keyword_query="AI Agent RAG",
        requested_count=requested_count,
        source_plan_version="1",
        status="completed",
        dispatch_started=True,
    )


def test_first_page_expansion_requires_every_baseline_candidate_to_be_high_quality() -> None:
    decision = decide_first_page_expansion(
        continuations=[_expansion_continuation()],
        requested_count=2,
        baseline_opened_count=2,
        baseline_identity_count=2,
        scorecards=[
            _expansion_score("r1", overall=90, must=80),
            _expansion_score("r2", overall=79, must=80),
        ],
    )
    assert decision.expand is False
    assert decision.reason_code == "baseline_quality_below_threshold"


def test_absent_risk_does_not_block_first_page_expansion() -> None:
    decision = decide_first_page_expansion(
        continuations=[_expansion_continuation()],
        requested_count=3,
        baseline_opened_count=3,
        baseline_identity_count=3,
        scorecards=[_expansion_score(f"r{index}", overall=85, must=75) for index in range(3)],
    )
    assert decision.expand is True


def test_applicable_high_risk_blocks_first_page_expansion() -> None:
    decision = decide_first_page_expansion(
        continuations=[_expansion_continuation()],
        requested_count=3,
        baseline_opened_count=3,
        baseline_identity_count=3,
        scorecards=[_expansion_score(f"r{index}", overall=85, must=75, risk=31) for index in range(3)],
    )
    assert decision.expand is False
    assert decision.reason_code == "baseline_risk_above_threshold"


def test_incomplete_baseline_target_does_not_expand() -> None:
    decision = decide_first_page_expansion(
        continuations=[_expansion_continuation()],
        requested_count=3,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_expansion_score("r1", overall=90, must=90)],
    )
    assert decision.expand is False
    assert decision.reason_code == "baseline_target_not_met"


def test_over_target_baseline_scores_every_canonical_identity_before_expanding() -> None:
    decision = decide_first_page_expansion(
        continuations=[_expansion_continuation()],
        requested_count=3,
        baseline_opened_count=4,
        baseline_identity_count=4,
        scorecards=[
            _expansion_score(f"r{index}", overall=90, must=90)
            for index in range(4)
        ],
    )
    assert decision.expand is True


def test_over_target_baseline_with_one_missing_score_does_not_expand() -> None:
    decision = decide_first_page_expansion(
        continuations=[_expansion_continuation()],
        requested_count=3,
        baseline_opened_count=4,
        baseline_identity_count=4,
        scorecards=[
            _expansion_score(f"r{index}", overall=90, must=90)
            for index in range(3)
        ],
    )
    assert decision.expand is False
    assert decision.reason_code == "baseline_scoring_incomplete"


def test_non_fit_baseline_never_expands_even_with_high_scores() -> None:
    decision = decide_first_page_expansion(
        continuations=[_expansion_continuation(initial_opened_count=1)],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_expansion_score("r1", overall=95, must=95, fit_bucket="not_fit")],
    )
    assert decision.expand is False
    assert decision.reason_code == "baseline_not_fit"


def test_quality_selector_uses_only_candidates_attributed_to_that_query() -> None:
    receipt = QueryExecutionReceipt(
        round_no=2,
        source_kind="liepin",
        query_instance_id="query-exploit",
        query_fingerprint="fingerprint-exploit",
        term_group_key="term-group-exploit",
        non_anchor_term_family_ids=["domain.rag"],
        query_role="exploit",
        lane_type="exploit",
        query_terms=["AI Agent", "RAG"],
        keyword_query="AI Agent RAG",
        requested_count=1,
        source_plan_version="1",
        status="completed",
        dispatch_started=True,
    )
    decisions = select_qualified_first_page_expansions(
        continuations=[_expansion_continuation(initial_opened_count=1)],
        receipts=[receipt],
        candidate_attributions=[
            RuntimeQueryCandidateAttribution(
                source_kind="liepin",
                query_instance_id="query-exploit",
                resume_id="r-good",
                dedup_key="r-good",
            )
        ],
        candidate_identity_by_resume_id={"r-good": "identity-good"},
        scorecards_by_identity_id={
            "identity-good": _expansion_score("r-good", overall=90, must=90),
            "identity-other-lane": _expansion_score("r-other-lane", overall=40, must=40),
        },
    )
    assert decisions[0].expand is True


def test_quality_selector_resolves_alias_attribution_to_canonical_scorecard() -> None:
    receipt = _completed_query_receipt(requested_count=1)
    decision = select_qualified_first_page_expansions(
        continuations=[_expansion_continuation(initial_opened_count=1)],
        receipts=[receipt],
        candidate_attributions=[
            RuntimeQueryCandidateAttribution(
                source_kind="liepin",
                query_instance_id="query-exploit",
                resume_id="provider-alias",
                dedup_key="same-person",
            )
        ],
        candidate_identity_by_resume_id={"provider-alias": "identity-1"},
        scorecards_by_identity_id={
            "identity-1": _expansion_score("canonical-resume", overall=90, must=90)
        },
    )[0]
    assert decision.expand is True


def test_two_physical_targets_for_one_query_form_one_quality_decision() -> None:
    first = _expansion_continuation(initial_opened_count=1)
    second = replace(
        first,
        continuation_id="query-exploit-target-2",
        opaque_ref="artifact://protected/pi-detail/query-exploit-target-2.json",
        visible_candidate_count=12,
        eligible_candidate_count=12,
        initial_opened_count=0,
    )
    receipt = QueryExecutionReceipt(
        round_no=2,
        source_kind="liepin",
        query_instance_id="query-exploit",
        query_fingerprint="fingerprint-exploit",
        term_group_key="term-group-exploit",
        non_anchor_term_family_ids=["domain.rag"],
        query_role="exploit",
        lane_type="exploit",
        query_terms=["AI Agent", "RAG"],
        keyword_query="AI Agent RAG",
        requested_count=1,
        source_plan_version="1",
        status="completed",
        dispatch_started=True,
    )
    decisions = select_qualified_first_page_expansions(
        continuations=[first, second],
        receipts=[receipt],
        candidate_attributions=[
            RuntimeQueryCandidateAttribution(
                source_kind="liepin",
                query_instance_id="query-exploit",
                resume_id="r-good",
                dedup_key="r-good",
            )
        ],
        candidate_identity_by_resume_id={"r-good": "identity-good"},
        scorecards_by_identity_id={
            "identity-good": _expansion_score("r-good", overall=90, must=90)
        },
    )
    assert len(decisions) == 1
    assert [item.continuation_id for item in decisions[0].continuations] == [
        "query-exploit-target-1",
        "query-exploit-target-2",
    ]


@pytest.mark.asyncio
async def test_expansion_executor_expands_or_discards_every_decision_in_order() -> None:
    qualified = decide_first_page_expansion(
        continuations=[_expansion_continuation(initial_opened_count=1)],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_expansion_score("r-good", overall=90, must=90)],
    )
    rejected_continuation = replace(
        _expansion_continuation(initial_opened_count=1),
        continuation_id="query-rejected-target-1",
        opaque_ref="artifact://protected/pi-detail/query-rejected.json",
        query_instance_id="query-rejected",
    )
    rejected = replace(
        qualified,
        query_instance_id="query-rejected",
        expand=False,
        reason_code="baseline_quality_below_threshold",
        continuations=(rejected_continuation,),
    )
    requests: list[SourceFirstPageExpansionRequest] = []

    async def expand(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        requests.append(request)
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            first_page_visible_count=request.continuation.visible_candidate_count,
            first_page_eligible_count=request.continuation.eligible_candidate_count,
            initial_opened_count=request.continuation.initial_opened_count,
        )

    results = await execute_first_page_decisions(
        runtime_run_id="run-1",
        round_no=2,
        decisions=[qualified, rejected],
        expanders={"liepin": expand},
    )
    assert [(item.continuation_id, item.action) for item in requests] == [
        ("query-exploit-target-1", "expand"),
        ("query-rejected-target-1", "discard"),
    ]
    assert [item.status for item in results] == ["completed", "completed"]


@pytest.mark.asyncio
async def test_expansion_boundary_failure_is_lane_local_and_next_decision_runs() -> None:
    first = decide_first_page_expansion(
        continuations=[_expansion_continuation(initial_opened_count=1)],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_expansion_score("r1", overall=90, must=90)],
    )
    second_continuation = replace(
        _expansion_continuation(initial_opened_count=1),
        continuation_id="query-second-target-1",
        opaque_ref="artifact://protected/pi-detail/query-second.json",
        query_instance_id="query-second",
    )
    second = replace(first, query_instance_id="query-second", continuations=(second_continuation,))
    calls: list[str] = []

    async def expand(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        calls.append(request.query_instance_id)
        if request.query_instance_id == "query-exploit":
            raise SourceFirstPageExpansionError(
                "provider fixture failure",
                status="failed",
                safe_reason_code="first_page_provider_failed",
            )
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
        )

    results = await execute_first_page_decisions(
        runtime_run_id="run-1",
        round_no=2,
        decisions=[first, second],
        expanders={"liepin": expand},
    )
    assert calls == ["query-exploit", "query-second"]
    assert [item.status for item in results] == ["failed", "completed"]
```

- [ ] **Step 2: Run policy tests and confirm missing module**

Run:

```bash
uv run pytest -q tests/test_runtime_state_flow.py -k 'first_page_expansion'
```

Expected: collection fails because `seektalent.runtime.first_page_expansion` does not exist.

- [ ] **Step 3: Implement the fixed quality policy**

Create `src/seektalent/runtime/first_page_expansion.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.models import QueryExecutionReceipt, RuntimeCanonicalResumeSelection, ScoredCandidate
from seektalent.runtime.source_expansion import (
    SourceFirstPageExpansionError,
    SourceFirstPageExpander,
    SourceFirstPageExpansionRequest,
    SourceFirstPageExpansionResult,
)
from seektalent.source_contracts import RuntimeQueryCandidateAttribution

MIN_OVERALL_SCORE = 80
MIN_MUST_HAVE_SCORE = 70
MAX_APPLICABLE_RISK_SCORE = 30


@dataclass(frozen=True)
class FirstPageExpansionDecision:
    source_kind: str
    query_instance_id: str
    expand: bool
    reason_code: str
    continuations: tuple[ProviderSearchContinuation, ...]


@dataclass(frozen=True)
class ExpansionQueryMergeCounts:
    source_kind: str
    query_instance_id: str
    unique_candidate_count: int
    duplicate_candidate_count: int


def canonical_scorecards_by_identity_id(
    *,
    scorecards_by_resume_id: Mapping[str, ScoredCandidate],
    candidate_identity_by_resume_id: Mapping[str, str],
    canonical_resume_by_identity_id: Mapping[str, RuntimeCanonicalResumeSelection],
) -> dict[str, ScoredCandidate]:
    grouped: dict[str, list[tuple[str, ScoredCandidate]]] = {}
    for resume_id, scorecard in scorecards_by_resume_id.items():
        identity_id = candidate_identity_by_resume_id.get(resume_id, resume_id)
        grouped.setdefault(identity_id, []).append((resume_id, scorecard))
    canonical: dict[str, ScoredCandidate] = {}
    for identity_id, rows in grouped.items():
        preferred_resume_id = getattr(
            canonical_resume_by_identity_id.get(identity_id),
            "canonical_resume_id",
            None,
        )
        rows.sort(key=lambda row: (row[0] != preferred_resume_id, row[0]))
        canonical[identity_id] = rows[0][1]
    return canonical


def decide_first_page_expansion(
    *,
    continuations: Sequence[ProviderSearchContinuation],
    requested_count: int,
    baseline_opened_count: int,
    baseline_identity_count: int,
    scorecards: Sequence[ScoredCandidate],
) -> FirstPageExpansionDecision:
    if not continuations:
        raise ValueError("first_page_continuation_group_empty")
    source_kind = continuations[0].source_kind
    query_instance_id = continuations[0].query_instance_id
    if any(
        item.source_kind != source_kind or item.query_instance_id != query_instance_id
        for item in continuations
    ):
        raise ValueError("first_page_continuation_group_mixed_query")
    if baseline_opened_count < requested_count or baseline_identity_count < requested_count:
        return FirstPageExpansionDecision(
            source_kind=source_kind,
            query_instance_id=query_instance_id,
            expand=False,
            reason_code="baseline_target_not_met",
            continuations=tuple(continuations),
        )
    if len(scorecards) != baseline_identity_count:
        return FirstPageExpansionDecision(
            source_kind=source_kind,
            query_instance_id=query_instance_id,
            expand=False,
            reason_code="baseline_scoring_incomplete",
            continuations=tuple(continuations),
        )
    if any(item.fit_bucket != "fit" for item in scorecards):
        reason = "baseline_not_fit"
    elif any(
        item.overall_score < MIN_OVERALL_SCORE or item.must_have_match_score < MIN_MUST_HAVE_SCORE
        for item in scorecards
    ):
        reason = "baseline_quality_below_threshold"
    elif any(
        item.risk_score is not None and item.risk_score > MAX_APPLICABLE_RISK_SCORE
        for item in scorecards
    ):
        reason = "baseline_risk_above_threshold"
    else:
        reason = "baseline_quality_gate_passed"
    return FirstPageExpansionDecision(
        source_kind=source_kind,
        query_instance_id=query_instance_id,
        expand=reason == "baseline_quality_gate_passed",
        reason_code=reason,
        continuations=tuple(continuations),
    )


def select_qualified_first_page_expansions(
    *,
    continuations: Sequence[ProviderSearchContinuation],
    receipts: Sequence[QueryExecutionReceipt],
    candidate_attributions: Sequence[RuntimeQueryCandidateAttribution],
    candidate_identity_by_resume_id: Mapping[str, str],
    scorecards_by_identity_id: Mapping[str, ScoredCandidate],
) -> list[FirstPageExpansionDecision]:
    receipts_by_key = {(item.source_kind, item.query_instance_id): item for item in receipts}
    continuations_by_key: dict[tuple[str, str], list[ProviderSearchContinuation]] = {}
    seen_continuation_ids: set[str] = set()
    for continuation in continuations:
        if continuation.continuation_id in seen_continuation_ids:
            raise ValueError("duplicate_first_page_continuation")
        seen_continuation_ids.add(continuation.continuation_id)
        continuations_by_key.setdefault(
            (continuation.source_kind, continuation.query_instance_id), []
        ).append(continuation)
    identity_ids_by_key: dict[tuple[str, str], list[str]] = {}
    for attribution in candidate_attributions:
        key = (attribution.source_kind, attribution.query_instance_id)
        identity_id = candidate_identity_by_resume_id.get(
            attribution.resume_id,
            attribution.resume_id,
        )
        identity_ids = identity_ids_by_key.setdefault(key, [])
        if identity_id not in identity_ids:
            identity_ids.append(identity_id)

    decisions: list[FirstPageExpansionDecision] = []
    for key, continuation_group in continuations_by_key.items():
        receipt = receipts_by_key.get(key)
        if receipt is None:
            raise ValueError("first_page_continuation_missing_receipt")
        if receipt.status != "completed" or not receipt.dispatch_started:
            decisions.append(
                FirstPageExpansionDecision(
                    source_kind=key[0],
                    query_instance_id=key[1],
                    expand=False,
                    reason_code="baseline_query_not_completed",
                    continuations=tuple(continuation_group),
                )
            )
            continue
        scorecards = [
            scorecards_by_identity_id[identity_id]
            for identity_id in identity_ids_by_key.get(key, [])
            if identity_id in scorecards_by_identity_id
        ]
        decisions.append(
            decide_first_page_expansion(
                continuations=continuation_group,
                requested_count=receipt.requested_count,
                baseline_opened_count=sum(
                    item.initial_opened_count for item in continuation_group
                ),
                baseline_identity_count=len(identity_ids_by_key.get(key, [])),
                scorecards=scorecards,
            )
        )
    return decisions


async def execute_first_page_decisions(
    *,
    runtime_run_id: str,
    round_no: int,
    decisions: Sequence[FirstPageExpansionDecision],
    expanders: Mapping[str, SourceFirstPageExpander],
) -> list[SourceFirstPageExpansionResult]:
    results: list[SourceFirstPageExpansionResult] = []
    for decision in decisions:
        expander = expanders.get(decision.source_kind)
        for continuation in decision.continuations:
            if expander is None:
                raise RuntimeSourceInvariantError("first_page_expander_unavailable")
            try:
                result = await expander(
                    SourceFirstPageExpansionRequest(
                        runtime_run_id=runtime_run_id,
                        round_no=round_no,
                        source_kind=decision.source_kind,
                        query_instance_id=decision.query_instance_id,
                        continuation_id=continuation.continuation_id,
                        continuation=continuation,
                        action="expand" if decision.expand else "discard",
                    )
                )
            except SourceFirstPageExpansionError as exc:
                result = SourceFirstPageExpansionResult(
                    source_kind=decision.source_kind,
                    query_instance_id=decision.query_instance_id,
                    continuation_id=continuation.continuation_id,
                    status=exc.status,
                    first_page_visible_count=continuation.visible_candidate_count,
                    first_page_eligible_count=continuation.eligible_candidate_count,
                    initial_opened_count=continuation.initial_opened_count,
                    safe_reason_code=exc.safe_reason_code,
                    continuation_deleted=exc.continuation_deleted,
                )
            results.append(result)
    return results


def apply_first_page_expansion_to_receipts(
    *,
    receipts: Sequence[QueryExecutionReceipt],
    decisions: Sequence[FirstPageExpansionDecision],
    outcomes: Sequence[SourceFirstPageExpansionResult],
    merge_counts: Sequence[ExpansionQueryMergeCounts],
    scoring_failure_counts: Mapping[tuple[str, str], int],
) -> list[QueryExecutionReceipt]:
    decisions_by_key = {(item.source_kind, item.query_instance_id): item for item in decisions}
    outcomes_by_key: dict[tuple[str, str], list[SourceFirstPageExpansionResult]] = {}
    for outcome in outcomes:
        outcomes_by_key.setdefault((outcome.source_kind, outcome.query_instance_id), []).append(outcome)
    merge_counts_by_key = {(item.source_kind, item.query_instance_id): item for item in merge_counts}
    updated: list[QueryExecutionReceipt] = []
    for receipt in receipts:
        key = (receipt.source_kind, receipt.query_instance_id)
        decision = decisions_by_key.get(key)
        if decision is None:
            updated.append(receipt)
            continue
        if not decision.expand:
            visible_count = sum(item.visible_candidate_count for item in decision.continuations)
            eligible_count = sum(item.eligible_candidate_count for item in decision.continuations)
            initial_opened_count = sum(item.initial_opened_count for item in decision.continuations)
            discard_outcomes = outcomes_by_key.get(key, [])
            discard_statuses = {item.status for item in discard_outcomes}
            discard_completed = (
                len(discard_outcomes) == len(decision.continuations)
                and discard_statuses == {"completed"}
            )
            if discard_completed:
                status = "not_qualified"
                reason_code = decision.reason_code
            elif "partial" in discard_statuses or "completed" in discard_statuses:
                status = "partial"
                reason_code = "first_page_continuation_discard_partial"
            elif discard_statuses == {"blocked"}:
                status = "blocked"
                reason_code = "first_page_continuation_discard_blocked"
            else:
                status = "failed"
                reason_code = "first_page_continuation_discard_failed"
            updated.append(
                receipt.model_copy(
                    update={
                        "first_page_visible_count": visible_count,
                        "first_page_eligible_count": eligible_count,
                        "initial_opened_count": initial_opened_count,
                        "first_page_expansion_qualified": False,
                        "first_page_expansion_status": status,
                        "first_page_expansion_reason_code": reason_code,
                    }
                )
            )
            continue

        query_outcomes = outcomes_by_key.get(key, [])
        if not query_outcomes:
            updated.append(
                receipt.model_copy(
                    update={
                        "first_page_visible_count": sum(
                            item.visible_candidate_count for item in decision.continuations
                        ),
                        "first_page_eligible_count": sum(
                            item.eligible_candidate_count for item in decision.continuations
                        ),
                        "initial_opened_count": sum(
                            item.initial_opened_count for item in decision.continuations
                        ),
                        "first_page_expansion_qualified": True,
                        "first_page_expansion_status": "failed",
                        "first_page_expansion_reason_code": "first_page_expansion_result_missing",
                    }
                )
            )
            continue

        merged = merge_counts_by_key.get(
            key,
            ExpansionQueryMergeCounts(
                source_kind=receipt.source_kind,
                query_instance_id=receipt.query_instance_id,
                unique_candidate_count=0,
                duplicate_candidate_count=0,
            ),
        )
        scoring_failures = scoring_failure_counts.get(key, 0)
        statuses = {item.status for item in query_outcomes}
        opened_count = sum(item.expansion_opened_count for item in query_outcomes)
        skipped_seen_count = sum(item.expansion_skipped_seen_count for item in query_outcomes)
        terminal_failure_count = sum(item.expansion_terminal_failure_count for item in query_outcomes)
        if statuses == {"completed"} and scoring_failures == 0:
            status = "completed"
        elif statuses == {"blocked"} and opened_count == 0:
            status = "blocked"
        elif statuses <= {"failed", "blocked"} and opened_count == 0:
            status = "failed"
        else:
            status = "partial"
        updated.append(
            receipt.model_copy(
                update={
                    "raw_candidate_count": receipt.raw_candidate_count + opened_count,
                    "unique_candidate_count": receipt.unique_candidate_count + merged.unique_candidate_count,
                    "duplicate_candidate_count": (
                        receipt.duplicate_candidate_count
                        + skipped_seen_count
                        + merged.duplicate_candidate_count
                    ),
                    "first_page_visible_count": sum(item.first_page_visible_count for item in query_outcomes),
                    "first_page_eligible_count": sum(item.first_page_eligible_count for item in query_outcomes),
                    "initial_opened_count": sum(item.initial_opened_count for item in query_outcomes),
                    "expansion_opened_count": opened_count,
                    "expansion_skipped_seen_count": skipped_seen_count,
                    "expansion_terminal_failure_count": terminal_failure_count,
                    "expansion_scoring_failure_count": scoring_failures,
                    "first_page_expansion_qualified": True,
                    "first_page_expansion_status": status,
                    "first_page_expansion_reason_code": next(
                        (item.safe_reason_code for item in query_outcomes if item.safe_reason_code),
                        decision.reason_code,
                    ),
                }
            )
        )
    return updated
```

- [ ] **Step 4: Add safe expansion fields to query receipts**

Add to `QueryExecutionReceipt` in `src/seektalent/models.py`:

```python
first_page_visible_count: int = Field(default=0, ge=0)
first_page_eligible_count: int = Field(default=0, ge=0)
initial_opened_count: int = Field(default=0, ge=0)
expansion_opened_count: int = Field(default=0, ge=0)
expansion_skipped_seen_count: int = Field(default=0, ge=0)
expansion_terminal_failure_count: int = Field(default=0, ge=0)
expansion_scoring_failure_count: int = Field(default=0, ge=0)
first_page_expansion_qualified: bool | None = None
first_page_expansion_status: Literal["not_qualified", "completed", "partial", "blocked", "failed"] | None = None
first_page_expansion_reason_code: str | None = None
```

These fields are counts/status only. Do not add continuation references or provider identities.

Retain Task 5's `pre_click_skipped_seen_count` as a separate receipt field. Add a receipt-update regression where one rejected lane's discard provider returns `failed`: its baseline receipt remains intact, `first_page_expansion_status == "failed"`, and `first_page_expansion_reason_code == "first_page_continuation_discard_failed"`; a following lane still executes. Add a qualified two-physical-target regression asserting summed `initial_opened_count`, one decision, and one receipt.

- [ ] **Step 5: Preserve continuations through source dispatch and retrieval results**

Add `private_first_page_continuations` to `SourceRoundAdapterResult`, `SourceRoundDispatchResult`, and `RetrievalExecutionResult`. A logical query may legitimately have multiple physical target searches after location/filter compilation, so preserve every continuation in dispatch order and group them later by `(source_kind, query_instance_id)`. Reject only duplicate `continuation_id` or duplicate `opaque_ref`, and verify that every continuation's source/query provenance matches a receipt. Add a two-target/one-query regression test proving both original pages survive and still produce one quality decision and one query receipt.

Use one validator while assembling `SourceRoundDispatchResult`:

```python
def _private_first_page_continuations(
    source_results: Sequence[SourceRoundAdapterResult],
    receipts: Sequence[QueryExecutionReceipt],
) -> tuple[ProviderSearchContinuation, ...]:
    receipt_keys = {(item.source_kind, item.query_instance_id) for item in receipts}
    seen_ids: set[str] = set()
    seen_refs: set[str] = set()
    continuations: list[ProviderSearchContinuation] = []
    for result in source_results:
        for continuation in result.private_first_page_continuations:
            if continuation.continuation_id in seen_ids or continuation.opaque_ref in seen_refs:
                raise RuntimeSourceInvariantError("duplicate_first_page_continuation")
            if (continuation.source_kind, continuation.query_instance_id) not in receipt_keys:
                raise RuntimeSourceInvariantError("first_page_continuation_missing_receipt")
            seen_ids.add(continuation.continuation_id)
            seen_refs.add(continuation.opaque_ref)
            continuations.append(continuation)
    return tuple(continuations)
```

- [ ] **Step 6: Run pure policy and dispatch tests**

Run:

```bash
uv run pytest -q \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  -k 'first_page_expansion or continuation'
```

Expected: PASS; quality decisions are per query and receipt augmentation never creates a second receipt.

- [ ] **Step 7: Commit expansion policy**

```bash
git add \
  src/seektalent/runtime/first_page_expansion.py \
  src/seektalent/models.py \
  src/seektalent/runtime/source_round_dispatch.py \
  src/seektalent/runtime/retrieval_runtime.py \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_multi_source_round_dispatch.py
git commit -m "feat: gate first-page expansion by lane quality"
```

### Task 8: Integrate Baseline Scoring, Expansion, And Reflection

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py:2170-2520`
- Modify: `src/seektalent/runtime/scoring_runtime.py`
- Modify: `src/seektalent/runtime/query_identity.py`
- Modify: `src/seektalent/runtime/first_page_expansion.py`
- Modify: `src/seektalent/runtime/reflection_context.py`
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/source_contracts/runtime_lanes.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `src/seektalent/source_adapters/runtime_composition.py`
- Modify: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent_workbench_v2/runtime_display.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_workbench_runtime_owned_execution.py`
- Test: `tests/test_agent_workbench_contract.py`
- Test: `tests/test_reflection_contract.py`

**Interfaces:**
- Consumes: source expanders, baseline continuations, per-query candidate attributions, and Task 7 decisions.
- Produces: one complete round containing baseline and expanded candidates before reflection, updated query receipts, and safe public expansion counts.
- Produces bounded per-query expansion evidence and expansion scoring failures in the same-round reflection context/prompt.
- Finalization and Workbench continue to consume the existing scorecard/candidate truth models.

- [ ] **Step 1: Write a failing integrated round test**

Add this recording critic and runtime test to `tests/test_runtime_state_flow.py`:

```python
class RecordingExpansionReflection(SequenceReflection):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[ReflectionContext] = []

    async def reflect(self, *, context: ReflectionContext) -> ReflectionAdvice:
        self.contexts.append(context)
        return await super().reflect(context=context)


def test_expansion_candidates_are_scored_and_visible_to_reflection_in_the_same_round(tmp_path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        provider_name="liepin",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    runtime_any = cast(Any, runtime)
    runtime_any._require_live_llm_config = lambda: None
    reflection = RecordingExpansionReflection()
    runtime_any.reflection_critic = reflection

    def source_round_adapters(runtime_instance: WorkflowRuntime, context: RuntimeSourceRoundContext):
        del runtime_instance

        async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            candidates: list[ResumeCandidate] = []
            outcomes: list[SourceQueryExecutionOutcome] = []
            attributions: list[RuntimeQueryCandidateAttribution] = []
            continuations: list[ProviderSearchContinuation] = []
            for intent in request.source_query_intents_by_source["liepin"]:
                for index in range(intent.requested_count):
                    candidate = _make_candidate(
                        f"baseline-{intent.query_instance_id}-{index}",
                        source_round=context.round_no,
                    )
                    candidates.append(candidate)
                    attributions.append(
                        RuntimeQueryCandidateAttribution(
                            source_kind="liepin",
                            query_instance_id=intent.query_instance_id,
                            resume_id=candidate.resume_id,
                            dedup_key=candidate.dedup_key,
                        )
                    )
                outcomes.append(
                    SourceQueryExecutionOutcome(
                        query_instance_id=intent.query_instance_id,
                        status="completed",
                        dispatch_started=True,
                        raw_candidate_count=intent.requested_count,
                        unique_candidate_count=intent.requested_count,
                    )
                )
                continuations.append(
                    ProviderSearchContinuation(
                        kind="first_page_detail_expansion",
                        continuation_id=f"target-{intent.query_instance_id}",
                        opaque_ref=f"artifact://protected/pi-detail/{intent.query_instance_id}.json",
                        source_kind="liepin",
                        round_no=context.round_no,
                        query_instance_id=intent.query_instance_id,
                        visible_candidate_count=6,
                        eligible_candidate_count=6,
                        initial_opened_count=intent.requested_count,
                    )
                )
            return SourceRoundAdapterResult(
                source="liepin",
                status="completed",
                candidates=tuple(candidates),
                raw_candidate_count=len(candidates),
                query_execution_outcomes=tuple(outcomes),
                candidate_query_attributions=tuple(attributions),
                private_first_page_continuations=tuple(continuations),
            )

        return {"liepin": liepin_adapter}

    expanded_query_ids: list[str] = []

    async def expand(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        if request.action == "discard":
            return SourceFirstPageExpansionResult(
                source_kind="liepin",
                query_instance_id=request.query_instance_id,
                continuation_id=request.continuation_id,
                status="completed",
            )
        expanded_query_ids.append(request.query_instance_id)
        candidate = _make_candidate(f"expanded-{request.query_instance_id}", source_round=request.round_no)
        return SourceFirstPageExpansionResult(
            source_kind="liepin",
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            candidates=(candidate,),
            candidate_query_attributions=(
                RuntimeQueryCandidateAttribution(
                    source_kind="liepin",
                    query_instance_id=request.query_instance_id,
                    resume_id=candidate.resume_id,
                    dedup_key=candidate.dedup_key,
                ),
            ),
            first_page_visible_count=request.continuation.visible_candidate_count,
            first_page_eligible_count=request.continuation.eligible_candidate_count,
            initial_opened_count=request.continuation.initial_opened_count,
            expansion_opened_count=1,
        )

    runtime_any.source_round_adapter_provider = source_round_adapters
    runtime_any.source_first_page_expander_provider = lambda _runtime, _ledger: {"liepin": expand}
    artifacts = runtime.run(
        source_kinds=["liepin"],
        job_title="AI Agent Engineer",
        jd="Build production agent systems.",
        notes="",
    )

    assert expanded_query_ids
    assert artifacts.run_state is not None
    expanded_resume_ids = {f"expanded-{query_id}" for query_id in expanded_query_ids}
    assert expanded_resume_ids <= set(artifacts.run_state.scorecards_by_resume_id)
    reflected_resume_ids = {
        item.resume_id
        for context in reflection.contexts
        for item in context.top_candidates
    }
    assert expanded_resume_ids <= reflected_resume_ids
```

Import `ReflectionContext`, `SourceRoundDispatchRequest`, `SourceFirstPageExpansionRequest`, and `SourceFirstPageExpansionResult` into the test module. This test remains fully local and performs no browser or network action.

- [ ] **Step 2: Run integrated tests and confirm expansion is not invoked**

Run:

```bash
uv run pytest -q tests/test_runtime_state_flow.py -k 'expansion_candidates_are_scored'
```

Expected: failures show the orchestrator goes directly from baseline scoring to reflection.

- [ ] **Step 3: Insert the expansion phase after baseline scoring**

Refactor the round body into this explicit sequence without adding a feature conditional:

Before source dispatch, build the source-expander registry and validate it against a source-plan capability `produces_private_first_page_continuations`. Add that boolean to the source-neutral lane plan; Liepin sets it true, other current sources false. If any selected capable source has no registered expander, fail before provider/browser invocation, so no continuation file can exist. The missing-expander regression asserts zero provider calls, zero browser calls, and zero protected files. The outer pending cleanup therefore handles only sources whose expander/discard transport was proven present before dispatch.

```python
# Immediately before the existing source-round execution call mutates run_state:
pre_round_top_ids = set(run_state.top_pool_ids)
pre_round_seen_resume_ids = set(run_state.seen_resume_ids)
source_expanders = (
    self.source_first_page_expander_provider(self, detail_open_claim_ledger)
    if self.source_first_page_expander_provider is not None
    else {}
)
assert_first_page_expanders_registered(
    source_plan=source_plan,
    expanders=source_expanders,
)

# Execute the existing source round unchanged, assigning retrieval_result.
# Immediately after that call returns:
query_dispatch_order = [item.query_instance_id for item in retrieval_result.query_outcomes]

baseline_scoring_result = await self._score_round(
    round_no=round_no,
    new_candidates=new_candidates,
    run_state=run_state,
    tracer=tracer,
    runtime_only_constraints=retrieval_plan.runtime_only_constraints,
    selected_source_kinds=tuple(lane.source for lane in source_plan),
    source_raw_targets=source_raw_targets,
    batch_kind="baseline",
    fail_on_scoring_error=True,
    finalize_pool=False,
)
baseline_intake_summary = run_state.latest_canonical_intake_summary
expansion_decisions = select_qualified_first_page_expansions(
    continuations=retrieval_result.private_first_page_continuations,
    receipts=retrieval_result.query_execution_receipts,
    candidate_attributions=retrieval_result.candidate_query_attributions,
    candidate_identity_by_resume_id=run_state.candidate_identity_by_resume_id,
    scorecards_by_identity_id=canonical_scorecards_by_identity_id(
        scorecards_by_resume_id=run_state.scorecards_by_resume_id,
        candidate_identity_by_resume_id=run_state.candidate_identity_by_resume_id,
        canonical_resume_by_identity_id=run_state.canonical_resume_by_identity_id,
    ),
)
expansion_results = await execute_first_page_decisions(
    runtime_run_id=tracer.run_id,
    round_no=round_no,
    decisions=expansion_decisions,
    expanders=source_expanders,
)
expansion_candidates, expansion_attributions, expansion_merge_counts = self._merge_expansion_candidates(
    results=expansion_results,
    run_state=run_state,
    source_plan=source_plan,
    round_no=round_no,
    tracer=tracer,
    seen_dedup_keys=seen_dedup_keys,
)
if expansion_candidates:
    expansion_scoring_result = await self._score_round(
        round_no=round_no,
        new_candidates=expansion_candidates,
        run_state=run_state,
        tracer=tracer,
        runtime_only_constraints=retrieval_plan.runtime_only_constraints,
        selected_source_kinds=tuple(lane.source for lane in source_plan),
        source_raw_targets=None,
        batch_kind="first_page_expansion",
        fail_on_scoring_error=False,
        finalize_pool=False,
    )
    expansion_intake_summary = run_state.latest_canonical_intake_summary
else:
    expansion_intake_summary = None
    expansion_scoring_result = ScoringRoundResult.empty()
run_state.latest_canonical_intake_summary = combine_round_intake_summaries(
    baseline=baseline_intake_summary,
    expansion=expansion_intake_summary,
)
current_top_candidates, pool_decisions, dropped_candidates = finalize_round_pool(
    round_no=round_no,
    run_state=run_state,
    tracer=tracer,
    previous_top_ids=pre_round_top_ids,
)
```

The pre-round top IDs and resume IDs must be captured immediately before the existing `_execute_source_round()` / source-dispatch call, not after it returns; otherwise baseline identities would be misclassified as duplicates. Snapshot resume IDs, not identity IDs: `rebuild_candidate_identities()` can rewrite identity IDs after alias merges. Immediately before the final post-merge allocator, map `pre_round_seen_resume_ids` through the final `candidate_identity_by_resume_id` to derive `identities_seen_before_round`. `query_dispatch_order` is captured only after `retrieval_result` exists and preserves the already-produced baseline logical outcome order. Save each batch's `latest_canonical_intake_summary` immediately after that score call because the next call overwrites it. `combine_round_intake_summaries()` restores complete round truth before reflection. `finalize_round_pool()` extracts the current `select_identity_top_candidates()`, `build_pool_decisions()`, final top-pool artifact write, and dropped-candidate calculation from `score_round()`. It is invoked exactly once after both score batches, so baseline candidates are `selected` relative to the prior round rather than incorrectly `retained` relative to the same round.

Add this private runtime helper (using the existing imports and source-order convention):

```python
def _merge_expansion_candidates(
    self,
    *,
    results: Sequence[SourceFirstPageExpansionResult],
    run_state: RunState,
    source_plan: tuple[RuntimeSourceLanePlan, ...],
    round_no: int,
    tracer: RunTracer,
    seen_dedup_keys: set[str],
) -> tuple[
    list[ResumeCandidate],
    list[RuntimeQueryCandidateAttribution],
    list[ExpansionQueryMergeCounts],
]:
    source_order = {lane.source: index for index, lane in enumerate(source_plan)}
    resume_ids_before_expansion = set(run_state.seen_resume_ids)
    candidates = [candidate for result in results for candidate in result.candidates]
    attributions = [
        attribution
        for result in results
        for attribution in result.candidate_query_attributions
    ]
    for result in results:
        if result.lane_result is not None:
            merge_source_lane_result_updates(
                run_state=run_state,
                result=result.lane_result,
                source_order=source_order,
                rebuild_identity=False,
            )
    for candidate in candidates:
        run_state.candidate_store[candidate.resume_id] = candidate
        if candidate.resume_id not in run_state.seen_resume_ids:
            run_state.seen_resume_ids.append(candidate.resume_id)
        if candidate.dedup_key:
            seen_dedup_keys.add(candidate.dedup_key)
    normalize_runtime_candidates(
        run_state=run_state,
        candidates=candidates,
        round_no=round_no,
        tracer=tracer,
    )
    rebuild_candidate_identities(run_state, source_order=source_order)
    identities_before = {
        run_state.candidate_identity_by_resume_id[resume_id]
        for resume_id in resume_ids_before_expansion
        if resume_id in run_state.candidate_identity_by_resume_id
    }

    counts_by_key: dict[tuple[str, str], list[int]] = {}
    claimed_new_identities: set[str] = set()
    new_candidates: list[ResumeCandidate] = []
    new_candidate_identities: set[str] = set()
    for candidate in candidates:
        identity_id = run_state.candidate_identity_by_resume_id.get(
            candidate.resume_id,
            candidate.resume_id,
        )
        if identity_id in identities_before or identity_id in new_candidate_identities:
            continue
        new_candidate_identities.add(identity_id)
        new_candidates.append(candidate)
    for attribution in attributions:
        key = (attribution.source_kind, attribution.query_instance_id)
        counts = counts_by_key.setdefault(key, [0, 0])
        identity_id = run_state.candidate_identity_by_resume_id.get(
            attribution.resume_id,
            attribution.resume_id,
        )
        if identity_id in identities_before or identity_id in claimed_new_identities:
            counts[1] += 1
        else:
            claimed_new_identities.add(identity_id)
            counts[0] += 1
    merge_counts = [
        ExpansionQueryMergeCounts(
            source_kind=source_kind,
            query_instance_id=query_instance_id,
            unique_candidate_count=counts[0],
            duplicate_candidate_count=counts[1],
        )
        for (source_kind, query_instance_id), counts in counts_by_key.items()
    ]
    return new_candidates, attributions, merge_counts
```

Checkpoint immediately after this helper and before expansion scoring. Only genuinely new canonical identities enter `expansion_candidates`; every attribution remains available for final query duplicate accounting. Run reflection only after this sequence finishes. Partial expansion results emit safe progress and continue.

Add regressions in which the final identity rebuild changes the winning identity ID after an alias merge: a pre-round candidate must remain previously seen in final round counts, and a baseline candidate must remain previously seen inside `_merge_expansion_candidates()`. Test `canonical_scorecards_by_identity_id()` for canonical-resume preference and deterministic alias fallback.

- [ ] **Step 4: Distinguish scoring batches without changing candidate truth**

Add these parameters to `score_round()` and `_score_round()`:

```python
batch_kind: Literal["baseline", "first_page_expansion"] = "baseline",
fail_on_scoring_error: bool = True,
finalize_pool: bool = True,
```

Replace the anonymous three-tuple return with one frozen `ScoringRoundResult(top_candidates, pool_decisions, dropped_candidates, scoring_failures)`. Provide `ScoringRoundResult.empty()` for a skipped expansion batch. Migrate every production call and test double found by `rg -n "_score_round\(|score_round\(" src tests`; existing callers read the first three named fields, while the integrated round persists `expansion_scoring_result.scoring_failures` on `RoundState`. Baseline remains fail-fast, so a successful baseline result has no failures.

Insert successful scorecards before applying the failure policy, and keep baseline fail-fast behavior:

```python
scored_candidates, scoring_failures = await resume_scorer.score_candidates_parallel(
    contexts=scoring_contexts,
    tracer=tracer,
)
for candidate in scored_candidates:
    if candidate.resume_id not in run_state.scorecards_by_resume_id:
        run_state.scorecards_by_resume_id[candidate.resume_id] = candidate
if scoring_failures and fail_on_scoring_error:
    raise run_stage_error("scoring", format_scoring_failure_message(scoring_failures))
```

Replace both existing `write_jsonl()` calls—input refs and scorecards—with row-by-row append operations so the second batch cannot overwrite the first:

```python
for item in normalized_scoring_pool:
    tracer.append_jsonl(
        f"round.{round_no:02d}.scoring.scoring_input_refs",
        {**scoring_input_ref(item), "batch_kind": batch_kind},
    )

for item in scored_candidates:
    tracer.append_jsonl(
        f"round.{round_no:02d}.scoring.scorecards",
        {**item.model_dump(mode="json"), "batch_kind": batch_kind},
    )
```

Write `batch_kind` into scoring progress as well. When `finalize_pool=False`, return the current top candidates with empty decision/dropped lists and do not write the top-pool snapshot. Move that finalization into `finalize_round_pool()` and keep `finalize_pool=True` as the compatibility default for existing direct callers. Add this focused test, importing `score_round as score_round_direct` from `runtime.scoring_runtime`:

Add this round-summary helper beside `score_round()`:

```python
def combine_round_intake_summaries(
    *,
    baseline: RuntimeCanonicalIntakeSummary | None,
    expansion: RuntimeCanonicalIntakeSummary | None,
) -> RuntimeCanonicalIntakeSummary | None:
    if baseline is None:
        return expansion
    if expansion is None:
        return baseline
    if baseline.round_no != expansion.round_no:
        raise ValueError("canonical_intake_round_mismatch")

    def add_counts(first: Mapping[str, int], second: Mapping[str, int]) -> dict[str, int]:
        keys = set(first) | set(second)
        return {key: first.get(key, 0) + second.get(key, 0) for key in sorted(keys)}

    return RuntimeCanonicalIntakeSummary(
        round_no=baseline.round_no,
        selected_source_kinds=tuple(
            dict.fromkeys((*baseline.selected_source_kinds, *expansion.selected_source_kinds))
        ),
        source_raw_targets=dict(baseline.source_raw_targets),
        raw_candidate_count=baseline.raw_candidate_count + expansion.raw_candidate_count,
        normalized_candidate_count=(
            baseline.normalized_candidate_count + expansion.normalized_candidate_count
        ),
        identity_count=baseline.identity_count + expansion.identity_count,
        auto_merged_duplicate_count=(
            baseline.auto_merged_duplicate_count + expansion.auto_merged_duplicate_count
        ),
        uncertain_conflict_count=(
            baseline.uncertain_conflict_count + expansion.uncertain_conflict_count
        ),
        skipped_already_scored_identity_count=(
            baseline.skipped_already_scored_identity_count
            + expansion.skipped_already_scored_identity_count
        ),
        scoring_candidate_count=(
            baseline.scoring_candidate_count + expansion.scoring_candidate_count
        ),
        canonical_resume_ids=tuple(
            dict.fromkeys((*baseline.canonical_resume_ids, *expansion.canonical_resume_ids))
        ),
        per_source_raw_counts=add_counts(
            baseline.per_source_raw_counts,
            expansion.per_source_raw_counts,
        ),
        per_source_normalized_counts=add_counts(
            baseline.per_source_normalized_counts,
            expansion.per_source_normalized_counts,
        ),
    )
```

This sum is valid because `_merge_expansion_candidates()` passes only identities that were new after baseline merge. Provider raw/duplicate opens remain receipt/observation metrics; this summary describes the canonical scoring intake. Add a focused helper test for ordered ID/source merging and a mismatch-round failure.

```python
def test_expansion_scoring_failure_keeps_successful_scores_and_does_not_raise(tmp_path) -> None:
    class PartialScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            successful = _scored_candidate(contexts[0].normalized_resume.resume_id, source_round=2)
            failure = ScoringFailure(
                resume_id=contexts[1].normalized_resume.resume_id,
                branch_id="expansion-failure",
                round_no=2,
                attempts=1,
                error_message="fixture scoring failure",
            )
            return [successful], [failure]

    run_state = _run_state_for_canonical_intake_tests()
    tracer = _noop_tracer(tmp_path)
    candidates = [
        _make_candidate("expanded-good", source_round=2),
        _make_candidate("expanded-failed", source_round=2),
    ]
    try:
        asyncio.run(
            score_round_direct(
                round_no=2,
                new_candidates=candidates,
                run_state=run_state,
                tracer=tracer,
                runtime_only_constraints=[],
                resume_scorer=PartialScorer(),
                format_scoring_failure_message=lambda failures: str(len(failures)),
                run_stage_error=lambda stage, message: RuntimeError(f"{stage}:{message}"),
                batch_kind="first_page_expansion",
                fail_on_scoring_error=False,
                finalize_pool=False,
            )
        )
    finally:
        tracer.close(status="completed")
    assert "expanded-good" in run_state.scorecards_by_resume_id
    assert "expanded-failed" not in run_state.scorecards_by_resume_id
```

Add a second artifact regression that invokes baseline and expansion batches against the same tracer, parses both `scoring_input_refs.jsonl` and `scorecards.jsonl`, and asserts the baseline and expansion resume IDs each occur exactly once with their respective `batch_kind`. This test must fail against the current `write_jsonl()` implementation because the expansion call overwrites baseline rows.

- [ ] **Step 5: Finalize receipts and round evidence before reflection**

Count expansion-only scoring failures per logical query from `expansion_attributions` and canonical identity ownership, then update receipts:

```python
failed_identity_ids = {
    run_state.candidate_identity_by_resume_id.get(failure.resume_id, failure.resume_id)
    for failure in expansion_scoring_result.scoring_failures
}
expansion_scoring_failure_counts: dict[tuple[str, str], int] = {}
counted_failure_keys: set[tuple[str, str, str]] = set()
for attribution in expansion_attributions:
    identity_id = run_state.candidate_identity_by_resume_id.get(
        attribution.resume_id,
        attribution.resume_id,
    )
    if identity_id not in failed_identity_ids:
        continue
    key = (attribution.source_kind, attribution.query_instance_id)
    failure_key = (*key, identity_id)
    if failure_key in counted_failure_keys:
        continue
    counted_failure_keys.add(failure_key)
    expansion_scoring_failure_counts[key] = expansion_scoring_failure_counts.get(key, 0) + 1

updated_receipts = apply_first_page_expansion_to_receipts(
    receipts=retrieval_result.query_execution_receipts,
    decisions=expansion_decisions,
    outcomes=expansion_results,
    merge_counts=expansion_merge_counts,
    scoring_failure_counts=expansion_scoring_failure_counts,
)
updated_by_key = {(item.source_kind, item.query_instance_id): item for item in updated_receipts}
run_state.retrieval_state.query_execution_ledger = [
    updated_by_key.get((item.source_kind, item.query_instance_id), item)
    for item in run_state.retrieval_state.query_execution_ledger
]
all_attributions = [
    *retrieval_result.candidate_query_attributions,
    *expansion_attributions,
]
identities_seen_before_round = {
    run_state.candidate_identity_by_resume_id[resume_id]
    for resume_id in pre_round_seen_resume_ids
    if resume_id in run_state.candidate_identity_by_resume_id
}
round_identity_ids = {
    run_state.candidate_identity_by_resume_id.get(
        attribution.resume_id,
        attribution.resume_id,
    )
    for attribution in all_attributions
}
final_round_new_identity_count = len(round_identity_ids - identities_seen_before_round)
combined_intake_summary = run_state.latest_canonical_intake_summary
if combined_intake_summary is not None:
    run_state.latest_canonical_intake_summary = combined_intake_summary.model_copy(
        update={
            "identity_count": len(round_identity_ids),
            "auto_merged_duplicate_count": max(
                0,
                combined_intake_summary.normalized_candidate_count - len(round_identity_ids),
            ),
        }
    )
allocated_query_outcomes = apply_post_merge_query_counts(
    outcomes=logical_outcomes_from_receipts(updated_receipts),
    candidate_attributions=all_attributions,
    candidate_identity_by_resume_id=run_state.candidate_identity_by_resume_id,
    dispatch_order=query_dispatch_order,
    identities_seen_before_round=identities_seen_before_round,
)
query_outcomes = add_pre_click_skips_to_query_outcomes(
    outcomes=allocated_query_outcomes,
    receipts=updated_receipts,
)
```

Add a regression where two physical continuations attribute aliases of the same canonical identity and that identity has one `ScoringFailure`: the logical query records exactly one expansion scoring failure.

Do not assign `logical_outcomes_from_receipts()` directly: it intentionally initializes unique/duplicate counts to zero. Re-running the existing post-merge allocator against the complete baseline-plus-expansion attribution set preserves canonical identity evidence. Then `add_pre_click_skips_to_query_outcomes()` adds, per logical query, `sum(receipt.pre_click_skipped_seen_count + receipt.expansion_skipped_seen_count)` to the allocator's duplicate count. It must not carry forward receipt `duplicate_candidate_count`, because that source-local field already includes attributed cross-target duplicates and would double-count them. Add a two-lane regression where lane B is skipped by the run-level ledger before click; the final lane-B logical duplicate count is exactly one.

Implement the helper in `runtime/query_identity.py` next to `apply_post_merge_query_counts()`:

```python
def add_pre_click_skips_to_query_outcomes(
    *,
    outcomes: Sequence[LogicalQueryOutcome],
    receipts: Sequence[QueryExecutionReceipt],
) -> list[LogicalQueryOutcome]:
    skipped_by_query: dict[str, int] = {}
    for receipt in receipts:
        skipped_by_query[receipt.query_instance_id] = (
            skipped_by_query.get(receipt.query_instance_id, 0)
            + receipt.pre_click_skipped_seen_count
            + receipt.expansion_skipped_seen_count
        )
    return [
        outcome.model_copy(
            update={
                "duplicate_candidate_count": (
                    outcome.duplicate_candidate_count
                    + skipped_by_query.get(outcome.query_instance_id, 0)
                )
            }
        )
        for outcome in outcomes
    ]
```

Update `SearchObservation` exactly once after expansion merge:

```python
expanded_raw_count = sum(item.expansion_opened_count for item in expansion_results)
qualified_results = [
    item
    for item in expansion_results
    if next(
        decision.expand
        for decision in expansion_decisions
        if decision.source_kind == item.source_kind
        and decision.query_instance_id == item.query_instance_id
    )
]
if any(item.status != "completed" for item in qualified_results):
    expansion_exhausted_reason = "first_page_expansion_partial"
elif qualified_results:
    expansion_exhausted_reason = "first_page_expansion_completed"
else:
    expansion_exhausted_reason = search_observation.exhausted_reason

search_observation = search_observation.model_copy(
    update={
        "raw_candidate_count": search_observation.raw_candidate_count + expanded_raw_count,
        "unique_new_count": final_round_new_identity_count,
        "shortage_count": max(
            0,
            search_observation.requested_count
            - final_round_new_identity_count,
        ),
        "fetch_attempt_count": search_observation.fetch_attempt_count,
        "exhausted_reason": expansion_exhausted_reason,
        "new_resume_ids": [
            *search_observation.new_resume_ids,
            *(item.resume_id for item in expansion_candidates),
        ],
        "new_candidate_summaries": [
            *search_observation.new_candidate_summaries,
            *(item.compact_summary() for item in expansion_candidates),
        ],
    }
)
retrieval_result = replace(
    retrieval_result,
    new_candidates=[*retrieval_result.new_candidates, *expansion_candidates],
    search_observation=search_observation,
    query_execution_receipts=updated_receipts,
    candidate_query_attributions=all_attributions,
    query_outcomes=query_outcomes,
)
```

Import `replace` from `dataclasses`; `RetrievalExecutionResult` is frozen, so no field may be assigned or list-mutated in place. Add an integrated regression that constructs the frozen result, runs the complete replacement path, and proves the original remains unchanged while the replacement contains baseline plus expansion candidates/evidence. Extend the identity-rewrite regression with an expansion alias that bridges two baseline identities: final `SearchObservation.unique_new_count`, `RuntimeCanonicalIntakeSummary.identity_count`, `auto_merged_duplicate_count`, and the logical-query allocator must all agree with the final rebuilt identity map rather than the earlier batch sums; actual scoring-candidate counts remain unchanged. `requested_count`, `fetch_attempt_count`, `search_attempts`, and city-search summaries remain unchanged because continuation consumption is neither a new query nor another page fetch. Rewrite the current round's receipt, query-outcome, observation, and ledger artifacts after this update, and build `RoundState` from the replacement.

Immediately after retrieval returns, track every private continuation ID as pending. Wrap the entire baseline score -> quality decision -> continuation execution -> expansion score -> receipt/observation replacement -> pool finalization sequence in one outer `try/finally`; remove an ID from pending only after its expand/discard boundary returns a terminal result. Add `discard_unconsumed_first_page_continuations()` to `runtime/first_page_expansion.py`: it groups pending carriers by source/query, sends `action="discard"` through the same source-neutral expander, performs no browser action, and returns safe cleanup outcomes. The outer `finally` calls it for every still-pending carrier, records cleanup failure without masking an already-active exception, then emits `_write_query_resume_hits()`, flywheel recording, and replay-snapshot exactly once from the best-known state. Normal handled failures must not rely on seven-day orphan cleanup; that window is only for process death. `QueryResumeHit` remains provider-search transport evidence: do not synthesize incomplete expansion hits because continuation candidates lack the original provider-rank fields required by that model. Expansion evidence is carried by `candidate_query_attributions`, augmented receipts, and final query outcomes; moving the writer merely lets existing baseline hits receive final score annotations. Keep the original `query_instance_id`; do not create another query package, receipt, sent-query record, search attempt, or term group. Add a baseline-scoring-failure regression asserting all protected continuations are discarded without browser calls and these audit artifacts still exist once. Add a cleanup-boundary-failure variant proving the original scoring exception remains primary while a safe cleanup failure is recorded.

Clarification for the pending set: terminal status alone never removes an ID. Remove it only on `continuation_deleted=True`; missing expander is an invariant error, and missing-expander/pre-provider-failure tests must run cleanup and prove the file is absent. False cleanup acknowledgement is recorded and blocks a successful round.

Extend the integrated test to assert the reflection context's `search_observation.raw_candidate_count` and `unique_new_count` include expansion, each reflected `query_outcome` retains non-zero final counts, and baseline plus expansion pool decisions are both `selected` when the prior-round top set was empty.

Add `scoring_failures: list[ScoringFailure]` to `RoundState`, populate it with the expansion batch's failures, and have `build_reflection_context()` copy it instead of hard-coding `[]`. Extend `reflection/critic.py::_safe_query_outcomes()` to aggregate each logical outcome's receipts into only these safe fields: `first_page_expansion_qualified`, `first_page_expansion_status`, `first_page_visible_count`, `first_page_eligible_count`, `initial_opened_count`, `expansion_opened_count`, `expansion_skipped_seen_count`, `expansion_terminal_failure_count`, `expansion_scoring_failure_count`, and `first_page_expansion_reason_code`. Never include continuation refs, browser refs, provider candidate keys, or URLs.

Add a reflection-context and rendered-prompt regression with two lanes: exploit expansion `completed`, expansion lane `partial` with one skipped-seen and one scoring failure. Assert the critic receives both distinct lane outcomes, the scoring failure, and safe counts/reasons; assert private marker strings are absent.

The new `RoundState` field uses `scoring_failures: list[ScoringFailure] = Field(default_factory=list)`, so existing constructors remain valid.

- [ ] **Step 6: Emit safe public progress**

Add public progress summaries with only these counts:

```python
{
    "stage": "first_page_expansion",
    "qualifiedLaneCount": qualified_count,
    "expandedCandidateCount": expanded_count,
    "skippedSeenCount": skipped_seen_count,
    "terminalFailureCount": terminal_failure_count,
    "scoringFailureCount": scoring_failure_count,
}
```

Register `first_page_expansion -> runtime_round_first_page_expansion` in `_RUNTIME_PUBLIC_EVENT_NAMES`, add the five keys above to `_PUBLIC_COUNT_KEYS`, and add the event name/stage label/count keys to `runtime_display.py`. Its summary is deterministic:

```python
if event_type == "runtime_round_first_page_expansion":
    return (
        f"{round_prefix}优质召回扩展完成：新增 {counts.get('expandedCandidateCount', 0)} 位，"
        f"跳过重复 {counts.get('skippedSeenCount', 0)} 位。"
    )
```

Reuse the existing query-group aggregate raw/new/duplicate fields after receipt augmentation. Do not expose continuation IDs, refs, URLs, provider IDs, or candidate hashes. Add a public-event contract test that serializes the event and asserts all private marker strings are absent.

- [ ] **Step 7: Run Gate C**

Run:

```bash
uv run pytest -q \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_workbench_runtime_owned_execution.py \
  tests/test_agent_workbench_contract.py \
  tests/test_runtime_control_projection.py
```

Expected: PASS; reflection receives expanded scorecards, dual lanes expand independently, and partial expansion preserves baseline truth.

- [ ] **Step 8: Commit integrated runtime expansion**

```bash
git add \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/runtime/scoring_runtime.py \
  src/seektalent/runtime/query_identity.py \
  src/seektalent/runtime/first_page_expansion.py \
  src/seektalent/runtime/reflection_context.py \
  src/seektalent/reflection/critic.py \
  src/seektalent/models.py \
  src/seektalent/source_contracts/runtime_lanes.py \
  src/seektalent/runtime/source_lanes.py \
  src/seektalent/source_adapters/runtime_composition.py \
  src/seektalent/runtime/public_events.py \
  src/seektalent_workbench_v2/runtime_display.py \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_workbench_runtime_owned_execution.py \
  tests/test_agent_workbench_contract.py \
  tests/test_reflection_contract.py \
  tests/test_runtime_control_projection.py
git commit -m "feat: expand high-quality Liepin first pages"
```

### Task 8.5: Repeat Independent Brooks Review Until CLEAR

**Scope:** Complete branch diff from the pre-plan base through Task 8, both approved documents, changed production/test files, public/private contracts, and the fresh full-suite evidence.

- [ ] **Step 0: Produce fresh full-suite evidence**

Before dispatching any reviewer, the execution owner runs:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ty check
scripts/verify-red-zone.sh
(cd apps/web-react && npm test && npm run check && npm run lint)
git diff --check
```

Save command outputs and changed-file scope for the reviewer. A first-pass `CLEAR` is invalid without this fresh evidence.

- [ ] **Step 1: Dispatch an independent read-only review**

Use a fresh subagent, not the execution owner and not a prior task implementer. Instruct it to use `brooks-review` for the full diff, `brooks-audit` for boundary/dependency changes, and `brooks-test` for the changed regression suite. The user requested GPT-5.6 Sol at highest reasoning for this gate; select that model/effort when the execution surface exposes those controls, otherwise use the strongest available reviewer and record the actual model/effort. The reviewer must inspect code and fresh test output, not only the plan or task summaries, and must return concrete Symptom -> Source -> Consequence -> Remedy findings or exactly `CLEAR`.

- [ ] **Step 2: Fix, verify, and re-review**

The single execution owner evaluates each finding with `superpowers:receiving-code-review`, adds/updates a failing regression before each behavior fix, implements only validated findings, and reruns the focused gate plus `uv run pytest -q`, frontend tests/check/lint, `git diff --check`, and boundary checks when relevant. Then dispatch a new independent review subagent over the updated complete diff. Do not let a review subagent edit the shared tree.

Repeat Step 2 without a fixed retry count until a fresh reviewer returns `CLEAR`. Save the final review scope, reviewer identity/model/effort, commands, results, prior finding resolutions, and the literal `CLEAR` verdict in the implementation record. If the reviewer cannot run or a finding cannot be resolved safely, Gate R is blocked; do not reinterpret silence or tool failure as approval.

### Task 9: Release Verification, Package Bump, And Domi Production Acceptance

**Files:**
- Modify: `pyproject.toml:3`
- Modify: `src/seektalent/version.py:3`
- Modify: `scripts/install-seektalent-domi.sh:17`
- Modify: `scripts/install-seektalent-domi.ps1:2,13`
- Modify: `uv.lock`
- Rebuild/stage: `src/seektalent_ui/static/workbench/**`
- Modify/finalize: `docs/superpowers/specs/2026-07-10-logical-query-execution-contract-design.md`
- Modify/finalize: `docs/superpowers/plans/2026-07-11-candidate-quality-first-page-expansion.md`
- Test: all focused and full suites below

**Interfaces:**
- Consumes: the complete behavior from Tasks 1–8.
- Produces: package version `0.7.40`, synchronized Domi bootstrap defaults, and evidence for one real production run.

- [ ] **Step 1: Run static checks before the version bump**

Run:

```bash
uv run ruff check src tests
uv run ty check
```

Expected: both commands exit 0. Fix only findings introduced by this plan; record unrelated pre-existing failures instead of editing unrelated files.

- [ ] **Step 2: Run focused Python regression suites**

Run:

```bash
uv run pytest -q \
  tests/test_scoring_cache.py \
  tests/test_reflection_contract.py \
  tests/test_context_builder.py \
  tests/test_candidate_feedback.py \
  tests/test_liepin_config.py \
  tests/test_liepin_detail_open_claims.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_state_flow.py \
  tests/test_workbench_v2_runtime_service.py \
  tests/test_workbench_runtime_owned_execution.py \
  tests/test_agent_workbench_contract.py \
  tests/test_liepin_boundaries.py
```

Expected: PASS.

- [ ] **Step 3: Run repository gates**

Run:

```bash
scripts/verify-red-zone.sh
scripts/verify-dev-workbench.sh
uv run pytest -q
cd apps/web-react
npm test -- --run src/components/workbench/ThinkingProcessRail.test.tsx src/components/workbench/ConversationScreenV2.test.tsx src/components/workbench/ConversationScreen.test.tsx
npm run check
npm run lint
cd ../..
```

Expected: every command exits 0. The full suite must not show an increase in skipped or xfailed tests attributable to this plan.

- [ ] **Step 4: Bump all active package/bootstrap versions together**

Change the four active version sources from `0.7.39` to `0.7.40`:

```toml
# pyproject.toml
version = "0.7.40"
```

```python
# src/seektalent/version.py
__version__ = "0.7.40"
```

```bash
# scripts/install-seektalent-domi.sh
local version="${1:-0.7.40}"
```

```powershell
# scripts/install-seektalent-domi.ps1
# top-level param block
[string]$Version = "0.7.40"

# Install-SeekTalentDomi function param block
[string]$Version = "0.7.40"
```

Run `uv lock` to update `uv.lock` mechanically, then prove all four active files agree and no old default remains:

```bash
test "$(rg -o '0\.7\.40' pyproject.toml src/seektalent/version.py scripts/install-seektalent-domi.sh scripts/install-seektalent-domi.ps1 | wc -l | tr -d ' ')" -ge 5
! rg -n '0\.7\.39' pyproject.toml src/seektalent/version.py scripts/install-seektalent-domi.sh scripts/install-seektalent-domi.ps1
```

- [ ] **Step 5: Verify package contents and Domi bootstrap tests**

Run:

```bash
python scripts/build_packaged_workbench.py
uv run pytest -q tests/test_build_packaged_workbench_script.py tests/test_cli_packaging.py tests/test_conversation_agent_schema_contract.py tests/test_react_workbench_desktop_only.py
uv build
uv run pytest -q tests/test_cli_basic_commands.py tests/test_cli.py tests/test_domi_bootstrap.py
/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python -m pip install \
  --upgrade --ignore-installed --no-cache-dir \
  --target "$HOME/.seektalent/python-prefix/0.7.40/site-packages" \
  dist/seektalent-0.7.40-py3-none-any.whl
PYTHONPATH="$HOME/.seektalent/python-prefix/0.7.40/site-packages" \
  /Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python \
  -m seektalent.cli version | tee /tmp/seektalent-0.7.40-version.txt
test "$(tr -d '\r\n' < /tmp/seektalent-0.7.40-version.txt)" = "0.7.40"

rm -rf /tmp/seektalent-0.7.40-wheel
python -m zipfile -e dist/seektalent-0.7.40-py3-none-any.whl /tmp/seektalent-0.7.40-wheel
test -f /tmp/seektalent-0.7.40-wheel/seektalent_ui/static/workbench/200.html
rg -q '主路径' /tmp/seektalent-0.7.40-wheel/seektalent_ui/static/workbench/_app
rg -q '扩展路径' /tmp/seektalent-0.7.40-wheel/seektalent_ui/static/workbench/_app
```

Expected: the tracked packaged frontend is freshly rebuilt from the changed React source, frontend/package tests pass, the wheel contains that bundle, and the version command prints exactly `0.7.40`. Review `git diff -- src/seektalent_ui/static/workbench` and stage the generated replacement/deletions in Step 8; do not leave stale hashed assets beside the new bundle.

- [ ] **Step 6: Execute one real Domi prod acceptance run**

First discover and fail fast on the two Domi runtimes; do not silently substitute system Python or Node:

```bash
DOMI_PYTHON=/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python
test -x "$DOMI_PYTHON" || { echo "domi_python_missing" >&2; exit 1; }

DOMI_NODE="${SEEKTALENT_DOMI_NODE:-${DOMI_NODE:-}}"
if [ -z "$DOMI_NODE" ]; then
  DOMI_NODE="$(
    PYTHONPATH="$HOME/.seektalent/python-prefix/0.7.40/site-packages" \
      "$DOMI_PYTHON" -c 'from seektalent.domi_bootstrap import resolve_domi_node; print(resolve_domi_node())'
  )"
fi
if [ ! -x "$DOMI_NODE" ]; then
  DOMI_NODE="$(find /Applications/Domi.app/Contents/Resources -type f -path '*/playwright/driver/node' -perm -111 -print -quit 2>/dev/null)"
fi
test -n "$DOMI_NODE" && test -x "$DOMI_NODE" \
  || { echo "domi_node_missing: set SEEKTALENT_DOMI_NODE or DOMI_NODE" >&2; exit 1; }
case "$(cd "$(dirname "$DOMI_NODE")" && pwd -P)/$(basename "$DOMI_NODE")" in
  /Applications/Domi.app/Contents/Resources/*) ;;
  *) echo "domi_node_not_domi_owned" >&2; exit 1 ;;
esac
"$DOMI_PYTHON" --version
"$DOMI_NODE" --version
export SEEKTALENT_DOMI_NODE="$DOMI_NODE"
```

If this preflight fails, Task 9 is blocked before prod acceptance; record the missing runtime and do not use system Node as false Domi evidence. After it succeeds, regenerate the user shim from the just-installed local wheel and launch that exact `0.7.40` target. Inject the user-provided JWT only into the process; never echo or persist it:

```bash
SITE_PACKAGES="$HOME/.seektalent/python-prefix/0.7.40/site-packages"
PYTHONPATH="$SITE_PACKAGES" "$DOMI_PYTHON" -m seektalent.domi_bootstrap \
  --package-version 0.7.40 \
  --python-path "$SITE_PACKAGES" \
  --domi-python "$DOMI_PYTHON" \
  --domi-node "$DOMI_NODE" \
  --bin-dir "$HOME/.seektalent/bin" \
  --print-json

if "$DOMI_PYTHON" -c 'import socket, sys; sock = socket.socket(); sock.settimeout(0.5); sys.exit(0 if sock.connect_ex(("127.0.0.1", 8011)) == 0 else 1)'; then
  echo "domi_workbench_port_in_use: 127.0.0.1:8011" >&2
  exit 1
fi
rm -f /tmp/seektalent-0.7.40-domi-workbench.pid

nohup env -i \
  HOME="$HOME" \
  PATH="$(dirname "$DOMI_PYTHON"):$(dirname "$DOMI_NODE"):/usr/bin:/bin" \
  PYTHONPATH="$SITE_PACKAGES" \
  SEEKTALENT_DOMI_NODE="$DOMI_NODE" \
  DOMI_NODE="$DOMI_NODE" \
  SEEKTALENT_DOMI_JWT="$SEEKTALENT_DOMI_JWT" \
  SEEKTALENT_DOMI_LLM_BASE_URL="https://api-domi.hewa.cn/api/v1/runtime/llm-proxy/v1" \
  SEEKTALENT_DOMI_LLM_CHANNEL="seek_talent" \
  SEEKTALENT_RUNTIME_MODE="prod" \
  SEEKTALENT_PROVIDER_NAME="liepin" \
  SEEKTALENT_LIEPIN_WORKER_MODE="opencli" \
  SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND="opencli" \
  "$DOMI_PYTHON" -m seektalent.domi_workbench --host 127.0.0.1 --port 8011 \
  > /tmp/seektalent-0.7.40-domi-workbench.log 2>&1 &
WORKBENCH_PID=$!
SERVER_PID=""
cleanup_acceptance_server() {
  if test -n "$SERVER_PID" && ps -p "$SERVER_PID" -o command= | rg -q 'seektalent_ui.server'; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
  if ps -p "$WORKBENCH_PID" -o command= | rg -q 'seektalent.domi_workbench'; then
    kill "$WORKBENCH_PID" 2>/dev/null || true
  fi
  wait "$SERVER_PID" 2>/dev/null || true
  wait "$WORKBENCH_PID" 2>/dev/null || true
  rm -f /tmp/seektalent-0.7.40-domi-workbench.pid
  for _ in $(seq 1 20); do
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 0.25
  done
  ! kill -0 "$SERVER_PID" 2>/dev/null
  ! kill -0 "$WORKBENCH_PID" 2>/dev/null
  ! "$DOMI_PYTHON" -c 'import socket, sys; s=socket.socket(); s.settimeout(0.2); sys.exit(0 if s.connect_ex(("127.0.0.1", 8011)) == 0 else 1)'
}
for _ in $(seq 1 60); do
  if test -z "$SERVER_PID"; then
    SERVER_PID="$(pgrep -P "$WORKBENCH_PID" -f 'seektalent_ui.server' | head -1 || true)"
  fi
  if ! kill -0 "$WORKBENCH_PID" 2>/dev/null; then
    cleanup_acceptance_server || true
    tail -200 /tmp/seektalent-0.7.40-domi-workbench.log >&2
    exit 1
  fi
  if test -n "$SERVER_PID" \
    && ps -p "$SERVER_PID" -o command= | rg -q 'seektalent_ui.server' \
    && curl -fsS http://127.0.0.1:8011/openapi.json >/dev/null; then
    break
  fi
  sleep 1
done
if test -z "$SERVER_PID" || ! curl -fsS http://127.0.0.1:8011/openapi.json >/dev/null; then
  cleanup_acceptance_server || true
  tail -200 /tmp/seektalent-0.7.40-domi-workbench.log >&2
  exit 1
fi
printf '%s\n%s\n' "$WORKBENCH_PID" "$SERVER_PID" > /tmp/seektalent-0.7.40-domi-workbench.pid
```

The pre-launch port check must fail rather than killing or trusting an unknown listener. Use Chrome only after both the owned wrapper and verified direct `seektalent_ui.server` child are recorded. After evidence, terminate child first, then wrapper, wait where possible, remove the PID file, and assert both PIDs are gone and port 8011 refuses connections. Early exit, timeout, and normal completion use this same ownership-proven cleanup; never leave the acceptance server running.

The acceptance evidence must satisfy all of these assertions:

```text
round 1 baseline target = 3
later dual-lane baseline targets = 3 and 2
no repeated term_group_key
after grouping multi-source receipts into attempted logical outcomes, every `non_anchor_term_family_ids` set is disjoint from all prior outcomes and same-round siblings; only the persisted compiler primary-anchor family repeats
if PRF executes, its explicit PRF family ID is persisted and consumed
deterministic exhaustion fixture with ordinary can-stop false makes zero controller/source calls and surfaces `query_family_exhausted` unchanged
duplicate detail candidates are skipped before browser open
at least one fixture or real high-quality lane records a quality-gate decision
every qualified lane opens every remaining eligible candidate in its frozen first-page snapshot
record Chrome tab count before/after expansion and perform the established manual cleanup; no unproven automatic tab close is introduced
no qualified expansion emits a second search submission or pagination action
ScoredCandidateDraft JSON schema contains no overall_score field
persisted scorecard overall_score equals deterministic weighted calculation
Workbench candidate list contains no matchScore below 60
Chrome DOM/screenshot: round 1 has exactly one `主路径` and no `扩展路径`; a real dual-lane round has exactly one `主路径` and one `扩展路径`
Chrome DOM: each path term appears once in that path and the keyword section contains no `关键词` heading, keywordQuery prose, lifecycle/status, source/provider, `原始`, `新增`, or `重复` metrics, cards, borders, backgrounds, or pills
deterministic integration fixture proves one partial lane does not prevent a successful round
```

If the live candidates do not naturally produce a qualified lane, keep the production run as source-integration evidence and use the deterministic 30-card OpenCLI fixture test as the expansion-positive evidence. Do not weaken the quality threshold to force a live expansion.

- [ ] **Step 7: Run security/privacy and diff checks**

Run the serialization/privacy tests, then grep only for the newly introduced continuation markers at public projection/UI boundaries:

```bash
uv run pytest -q \
  tests/test_liepin_boundaries.py \
  tests/test_runtime_control_projection.py \
  tests/test_agent_workbench_contract.py \
  -k 'continuation or private or public_event'

if rg -n "artifact://protected|opaque_ref|private_first_page_continuations" \
  src/seektalent_workbench_v2 \
  src/seektalent_ui \
  apps/web-react/src; then
  echo "private continuation marker reached a public boundary" >&2
  exit 1
fi
git diff --check
git status --short
```

Expected: serialization tests pass and the public-boundary grep returns no matches. Existing internal `provider_candidate_key_hash` storage and detail-open contracts are outside this change and are not falsely treated as new leakage. `git diff --check` exits 0. Before the release commit, `git status` may show only this plan's version/docs/generated-static changes plus the pre-existing `.gitignore`; after Step 8, only `.gitignore` remains unstaged.

- [ ] **Step 8: Commit the release-ready version bump**

```bash
git add \
  pyproject.toml \
  src/seektalent/version.py \
  scripts/install-seektalent-domi.sh \
  scripts/install-seektalent-domi.ps1 \
  uv.lock \
  src/seektalent_ui/static/workbench \
  docs/superpowers/specs/2026-07-10-logical-query-execution-contract-design.md \
  docs/superpowers/plans/2026-07-11-candidate-quality-first-page-expansion.md
if git diff --cached --name-only | rg -x '\.gitignore'; then
  echo "user-owned .gitignore was staged" >&2
  exit 1
fi
git commit -m "release: prepare 0.7.40 candidate quality expansion"
```

Do not stage `.gitignore`. Push, tag, PyPI publication, GitHub release creation, and CI monitoring require the user's separate release instruction after implementation verification.

---

## Plan Self-Review Checklist

- [x] Every requested behavior maps to at least one task: family novelty and minimal UI (Task 0), `3/2` (Task 3), deterministic total (Tasks 1–2), optional dimensions (Tasks 1–3), Workbench threshold (Task 3), fixed first-page expansion (Tasks 4–8), independent Brooks CLEAR (Task 8.5), and production proof (Task 9).
- [x] No task introduces a feature flag, new environment setting, search replay, pagination action, or public continuation reference.
- [x] Type names are consistent across tasks: `ProviderSearchContinuation`, `SourceFirstPageExpansionRequest`, `SourceFirstPageExpansionResult`, and `FirstPageExpansionDecision`.
- [x] Expansion augments existing query receipts and candidate attribution; it never creates another logical query receipt or term group.
- [x] All browser opens, including expansion, pass through `DetailOpenClaimLedger` first.
- [x] Low-score candidates remain available to controller/reflection and are filtered only by both Workbench summary projections.
- [x] Version bump occurs only after behavior, fresh full-suite evidence, and independent Brooks `CLEAR` pass.
