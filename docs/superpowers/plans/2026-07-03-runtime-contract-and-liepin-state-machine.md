# Runtime Contract And Liepin State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the current reflection/controller contract failure and harden Liepin browser operations with explicit verified transitions.

**Architecture:** Keep Pydantic AI structured output as the JSON shape contract, then add deterministic semantic projection where round-specific rules cannot be represented by JSON Schema. Keep OpenCLI generic and Liepin-specific logic inside the Liepin provider; add a small Liepin-only transition runner used by `LiepinSearchWorkflow` without changing scoring concurrency, UI shape, CTS behavior, or full-text deletion policy.

**Tech Stack:** Python 3.12, Pydantic, Pydantic AI, pytest, ruff, ty, existing OpenCLI browser wrapper, existing Liepin provider modules.

---

## Execution Preconditions

- This plan assumes the design spec is approved: `docs/superpowers/specs/2026-07-03-runtime-contract-and-liepin-state-machine-design.md`.
- Current worktree already has uncommitted Liepin changes in these files:
  - `src/seektalent/providers/liepin/liepin_site_adapter.py`
  - `src/seektalent/providers/liepin/liepin_site_parsing.py`
  - `src/seektalent/providers/liepin/opencli_filter_planning.py`
  - `tests/test_liepin_opencli_browser.py`
  - `tests/test_liepin_opencli_city_filter.py`
  - `tests/test_liepin_opencli_filter_planning.py`
- Implementation owner must preserve those changes. If using a fresh worktree, first decide whether those six files are part of the work to port. Do not overwrite them with a clean checkout by accident.

## File Structure

- Modify: `src/seektalent/prompts/reflection.md`
  - Remove stale permission for reflection to reuse `secondary_title_anchor`.
- Modify: `src/seektalent/reflection/critic.py`
  - Filter reflection keyword advice by both admitted-term status and round-later legal role.
- Modify: `src/seektalent/retrieval/query_plan.py`
  - Add pure query-term projection for the one known round-2+ title-support drift.
- Modify: `src/seektalent/retrieval/__init__.py`
  - Re-export the new query projection helper if query-plan helpers are currently exported there.
- Modify: `src/seektalent/controller/react_controller.py`
  - Add one narrow final projection after normal controller repair/retry.
- Modify: `src/seektalent/runtime/round_decision_runtime.py`
  - Use the same projection before canonicalizing runtime controller decisions.
- Create: `src/seektalent/providers/liepin/liepin_state_machine.py`
  - Define a small Liepin-only transition type and runner.
- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py`
  - Express search/filter/detail orchestration as named workflow phases backed by transitions.
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
  - Keep Liepin page operations and OpenCLI calls; make mutating page operations verify latest state before and after action.
- Modify: `src/seektalent/providers/liepin/opencli_filter_planning.py`
  - Keep deterministic native filter parsing helpers and add any missing postcondition predicate needed by transitions.
- Modify tests:
  - `tests/test_reflection_contract.py`
  - `tests/test_query_plan.py`
  - `tests/test_controller_contract.py`
  - `tests/test_runtime_state_flow.py`
  - `tests/test_liepin_state_machine.py`
  - `tests/test_liepin_opencli_browser.py`
  - `tests/test_liepin_opencli_city_filter.py`
  - `tests/test_liepin_opencli_filter_planning.py`

---

### Task 1: Project Reflection Advice To Later-Round Legal Terms

**Files:**
- Modify: `src/seektalent/prompts/reflection.md`
- Modify: `src/seektalent/reflection/critic.py`
- Test: `tests/test_reflection_contract.py`

- [ ] **Step 1: Write the failing reflection test**

Add this test near `test_materialized_reflection_drops_non_admitted_keyword_advice` in `tests/test_reflection_contract.py`:

```python
def test_materialized_reflection_drops_secondary_title_anchor_keyword_advice() -> None:
    context = _context(
        round_no=2,
        unique_new_count=5,
        query_term_pool=[
            QueryTermCandidate(
                term="AI",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                family="role.ai",
            ),
            QueryTermCandidate(
                term="主观投资",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Job title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                family="role.主观投资",
            ),
            QueryTermCandidate(
                term="模型部署",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="domain_context",
                family="domain.模型部署",
            ),
        ],
    )

    advice = materialize_reflection_advice(
        context=cast(Any, context),
        draft=ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(
                suggested_activate_terms=["主观投资", "模型部署"],
                suggested_keep_terms=["AI", "主观投资"],
                suggested_deprioritize_terms=["主观投资"],
                suggested_drop_terms=["主观投资"],
            ),
            filter_advice=ReflectionFilterAdviceDraft(),
            suggest_stop=False,
        ),
    )

    assert advice.keyword_advice.suggested_activate_terms == ["模型部署"]
    assert advice.keyword_advice.suggested_keep_terms == ["AI"]
    assert advice.keyword_advice.suggested_deprioritize_terms == []
    assert advice.keyword_advice.suggested_drop_terms == []
    assert "主观投资" not in advice.reflection_summary
    assert "模型部署" in advice.reflection_summary
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
uv run pytest tests/test_reflection_contract.py::test_materialized_reflection_drops_secondary_title_anchor_keyword_advice -q
```

Expected result before implementation: the test fails because `主观投资` still appears in materialized keyword advice or in `reflection_summary`.

- [ ] **Step 3: Add the reflection term-role filter**

In `src/seektalent/reflection/critic.py`, add `QueryTermCandidate` and `unique_strings` to the existing imports from `seektalent.models` if they are not already imported.

Add this helper near `_filter_to_admitted_terms`:

```python
def _filter_to_reflection_allowed_terms(
    terms: list[str],
    admitted_terms: dict[str, QueryTermCandidate],
) -> list[str]:
    output: list[str] = []
    for term in terms:
        candidate = admitted_terms.get(term.strip().casefold())
        if candidate is None:
            continue
        if candidate.retrieval_role == "secondary_title_anchor":
            continue
        output.append(candidate.term)
    return unique_strings(output)
```

- [ ] **Step 4: Use the helper in `materialize_reflection_advice()`**

Replace the existing `keyword_advice = ReflectionKeywordAdvice` construction in `materialize_reflection_advice()` with:

```python
keyword_advice = ReflectionKeywordAdvice(
    suggested_activate_terms=_filter_to_reflection_allowed_terms(
        draft.keyword_advice.suggested_activate_terms,
        admitted_terms,
    ),
    suggested_keep_terms=_filter_to_reflection_allowed_terms(
        draft.keyword_advice.suggested_keep_terms,
        admitted_terms,
    ),
    suggested_deprioritize_terms=_filter_to_reflection_allowed_terms(
        draft.keyword_advice.suggested_deprioritize_terms,
        admitted_terms,
    ),
    suggested_drop_terms=_filter_to_reflection_allowed_terms(
        draft.keyword_advice.suggested_drop_terms,
        admitted_terms,
    ),
)
```

- [ ] **Step 5: Fix the reflection prompt**

In `src/seektalent/prompts/reflection.md`, replace:

```markdown
- You may suggest keeping or reusing `secondary_title_anchor` when it remains the best title-side support term already present in the term bank.
```

with:

```markdown
- Do not suggest activating, keeping, deprioritizing, or dropping `secondary_title_anchor`; it is a round-1-only title pairing term and reflection advice is only used by later rounds.
```

- [ ] **Step 6: Run reflection contract tests**

Run:

```bash
uv run pytest tests/test_reflection_contract.py -q
```

Expected result: all reflection contract tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent/prompts/reflection.md src/seektalent/reflection/critic.py tests/test_reflection_contract.py
git commit -m "fix: project reflection advice to legal query terms"
```

---

### Task 2: Add Narrow Controller Query Projection

**Files:**
- Modify: `src/seektalent/retrieval/query_plan.py`
- Modify: `src/seektalent/retrieval/__init__.py`
- Modify: `src/seektalent/controller/react_controller.py`
- Modify: `src/seektalent/runtime/round_decision_runtime.py`
- Test: `tests/test_query_plan.py`
- Test: `tests/test_controller_contract.py`
- Test: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write the narrow projection tests**

In `tests/test_query_plan.py`, add this import:

```python
from seektalent.retrieval.query_plan import try_project_secondary_title_anchor_after_round_one
```

Add these tests near `test_query_plan_rejects_secondary_title_anchor_after_round_one`:

```python
def test_try_project_secondary_title_anchor_after_round_one_replaces_only_secondary_anchor() -> None:
    pool = [
        QueryTermCandidate(
            term="AI",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="Job title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            family="role.ai",
        ),
        QueryTermCandidate(
            term="主观投资",
            source="job_title",
            category="role_anchor",
            priority=2,
            evidence="Job title",
            first_added_round=0,
            retrieval_role="secondary_title_anchor",
            family="role.主观投资",
        ),
        QueryTermCandidate(
            term="模型部署",
            source="jd",
            category="domain",
            priority=3,
            evidence="JD",
            first_added_round=0,
            retrieval_role="domain_context",
            family="domain.模型部署",
        ),
    ]

    assert try_project_secondary_title_anchor_after_round_one(
        ["AI", "主观投资"],
        round_no=3,
        query_term_pool=pool,
    ) == ["AI", "模型部署"]


def test_try_project_secondary_title_anchor_after_round_one_returns_none_for_round_one() -> None:
    assert try_project_secondary_title_anchor_after_round_one(
        ["Backend", "Platform"],
        round_no=1,
        query_term_pool=_projection_pool(),
    ) is None


def test_try_project_secondary_title_anchor_after_round_one_returns_none_without_secondary_anchor() -> None:
    assert try_project_secondary_title_anchor_after_round_one(
        ["AI", "模型部署"],
        round_no=3,
        query_term_pool=_projection_pool(),
    ) is None
```

Add controller-level negative tests so projection cannot swallow unrelated validation failures:

- `test_controller_projection_does_not_swallow_duplicate_terms`: proposed terms include duplicate terms and `secondary_title_anchor`; final validation must still fail and no projection result is returned.
- `test_controller_projection_does_not_swallow_too_many_terms`: proposed terms include four terms and `secondary_title_anchor`; final validation must still fail and no truncation is allowed.
- `test_controller_projection_does_not_swallow_missing_pool_term`: proposed terms include a pool-external term and `secondary_title_anchor`; final validation must still fail.

Define a local `_projection_pool()` test helper in `tests/test_query_plan.py` if the file does not already have one. It must include at least one `primary_role_anchor`, one `secondary_title_anchor`, and one admitted active non-title support candidate.

- [ ] **Step 2: Run the failing projection tests**

Run:

```bash
uv run pytest tests/test_query_plan.py::test_try_project_secondary_title_anchor_after_round_one_replaces_only_secondary_anchor tests/test_query_plan.py::test_try_project_secondary_title_anchor_after_round_one_returns_none_for_round_one tests/test_query_plan.py::test_try_project_secondary_title_anchor_after_round_one_returns_none_without_secondary_anchor -q
```

Expected result before implementation: import failure for `try_project_secondary_title_anchor_after_round_one`.

- [ ] **Step 3: Implement the pure projection helper**

In `src/seektalent/retrieval/query_plan.py`, add this function after `canonicalize_controller_query_terms()`:

```python
def try_project_secondary_title_anchor_after_round_one(
    proposed_terms: list[str],
    *,
    round_no: int,
    query_term_pool: list[QueryTermCandidate],
) -> list[str] | None:
    clean_terms = [normalize_term(term) for term in proposed_terms if normalize_term(term)]
    if round_no <= 1:
        return None

    term_index = _query_term_index(query_term_pool)
    output: list[str] = []
    removed_secondary_title = False
    used_families: set[str] = set()

    for term in clean_terms:
        candidate = term_index.get(term.casefold())
        if candidate is not None and candidate.retrieval_role == "secondary_title_anchor":
            removed_secondary_title = True
            continue
        output.append(term)
        if candidate is not None:
            used_families.add(candidate.family)

    if not removed_secondary_title:
        return None

    for candidate in sorted(query_term_pool, key=_non_anchor_sort_key):
        if candidate.queryability != "admitted":
            continue
        if not candidate.active:
            continue
        if _is_title_anchor_candidate(candidate):
            continue
        if candidate.family in used_families:
            continue
        output.append(candidate.term)
        used_families.add(candidate.family)
        if len(output) >= 2:
            break

    return output
```

Do not dedupe, truncate, or otherwise repair unrelated query-term problems in this helper. It only removes the secondary-title anchor and tries to fill the support slot.

If `src/seektalent/retrieval/__init__.py` exports query-plan helpers, add:

```python
from seektalent.retrieval.query_plan import try_project_secondary_title_anchor_after_round_one
```

and include it in `__all__` if the module has an `__all__` list.

- [ ] **Step 4: Add controller decision projection helper**

In `src/seektalent/controller/react_controller.py`, import:

```python
from seektalent.retrieval import try_project_secondary_title_anchor_after_round_one
```

Add this helper near `validate_controller_decision()`:

```python
_ROUND_SECONDARY_TITLE_ANCHOR_REASON = "rounds after 1 must not use secondary_title_anchor"


def project_controller_decision_if_round_legal(
    *,
    context: ControllerContext,
    decision: ControllerDecision,
    reason: str,
) -> ControllerDecision | None:
    if not isinstance(decision, SearchControllerDecision):
        return None
    if _ROUND_SECONDARY_TITLE_ANCHOR_REASON not in reason:
        return None
    projected_terms = try_project_secondary_title_anchor_after_round_one(
        decision.proposed_query_terms,
        round_no=context.round_no,
        query_term_pool=context.query_term_pool,
    )
    if projected_terms is None:
        return None
    if projected_terms == decision.proposed_query_terms:
        return None
    projected = decision.model_copy(update={"proposed_query_terms": projected_terms})
    if validate_controller_decision(context=context, decision=projected) is not None:
        return None
    return projected
```

In `ReActController.decide()`, immediately before `raise ValueError(retry_reason)`, add:

```python
projected = project_controller_decision_if_round_legal(
    context=context,
    decision=retried,
    reason=retry_reason,
)
if projected is not None:
    return projected
```

- [ ] **Step 5: Keep runtime sanitization strict**

Do not call the projection helper unconditionally from `sanitize_controller_decision()`. That path must stay a strict canonicalization/validation boundary.

If runtime sanitization needs the same recovery for already materialized controller decisions, wrap canonicalization narrowly:

```python
try:
    query_terms = canonicalize_controller_query_terms(
        decision.proposed_query_terms,
        round_no=round_no,
        title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
        query_term_pool=run_state.retrieval_state.query_term_pool,
        allowed_inactive_non_anchor_terms=allowed_inactive_terms,
    )
except ValueError as exc:
    if _ROUND_SECONDARY_TITLE_ANCHOR_REASON not in str(exc):
        raise
    projected_terms = try_project_secondary_title_anchor_after_round_one(
        decision.proposed_query_terms,
        round_no=round_no,
        query_term_pool=run_state.retrieval_state.query_term_pool,
    )
    if projected_terms is None:
        raise
    query_terms = canonicalize_controller_query_terms(
        projected_terms,
        round_no=round_no,
        title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
        query_term_pool=run_state.retrieval_state.query_term_pool,
        allowed_inactive_non_anchor_terms=allowed_inactive_terms,
    )
```

The second canonicalization must still enforce duplicates, max term count, pool membership, and inactive-term rules. Re-raise the original validation error when projection does not produce a fully valid decision.

- [ ] **Step 6: Add controller regression coverage**

In `tests/test_controller_contract.py`, add a test near the controller repair tests:

```python
def test_controller_projects_secondary_title_anchor_after_repair_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    controller = ReActController(
        make_settings(),
        LoadedPrompt(name="controller", path=Path("controller.md"), content="controller prompt", sha256="hash"),
        repair_prompt=LoadedPrompt(
            name="repair_controller",
            path=Path("repair_controller.md"),
            content="repair controller prompt",
            sha256="repair-hash",
        ),
    )
    context = _controller_context(
        round_no=3,
        query_term_pool=[
            QueryTermCandidate(
                term="AI",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                family="role.ai",
            ),
            QueryTermCandidate(
                term="主观投资",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                family="role.主观投资",
            ),
            QueryTermCandidate(
                term="模型部署",
                source="jd",
                category="domain",
                priority=3,
                evidence="jd",
                first_added_round=0,
                retrieval_role="domain_context",
                family="domain.模型部署",
            ),
        ],
        previous_reflection=ReflectionSummaryView(decision="continue", reflection_summary="Use a stronger support term."),
    )
    invalid = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Continue with the title-side support term.",
        proposed_query_terms=["AI", "主观投资"],
        proposed_filter_plan=ProposedFilterPlan(),
        response_to_reflection="Accepted reflection advice.",
    )
    calls = {"count": 0}

    async def fake_decide_live(
        *,
        context: ControllerContext,
        prompt_cache_key: str | None = None,
        source_user_prompt: str | None = None,
    ) -> ControllerDecision:
        del context, prompt_cache_key, source_user_prompt
        calls["count"] += 1
        return invalid

    async def fake_repair_controller_decision(
        settings, prompt, repair_prompt, source_user_prompt, decision, reason  # noqa: ANN001
    ) -> tuple[ControllerDecision, None, None]:
        del settings, prompt, repair_prompt, source_user_prompt, decision, reason
        return invalid, None, None

    monkeypatch.setattr(controller, "_decide_live", fake_decide_live)
    monkeypatch.setattr("seektalent.controller.react_controller.repair_controller_decision", fake_repair_controller_decision)

    result = asyncio.run(controller.decide(context=context))

    assert isinstance(result, SearchControllerDecision)
    assert result.proposed_query_terms == ["AI", "模型部署"]
    assert calls["count"] == 2
    assert controller.last_full_retry_count == 1
```

If `_controller_context()` does not accept `query_term_pool`, extend that test helper locally in `tests/test_controller_contract.py` so it can override `query_term_pool` while preserving existing defaults.

- [ ] **Step 7: Add runtime sanitization coverage**

In `tests/test_runtime_state_flow.py`, add focused coverage for `sanitize_controller_decision()` only if that code path implements the narrow catch-and-project branch.

Use this success assertion for the exact secondary-title-anchor reason:

```python
assert sanitized.proposed_query_terms == ["AI", "模型部署"]
```

Also add negative assertions that duplicate terms, four terms, and missing-pool terms still raise.

- [ ] **Step 8: Run controller/query/runtime tests**

Run:

```bash
uv run pytest tests/test_query_plan.py tests/test_controller_contract.py tests/test_runtime_state_flow.py -q
```

Expected result: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/retrieval/query_plan.py src/seektalent/retrieval/__init__.py src/seektalent/controller/react_controller.py src/seektalent/runtime/round_decision_runtime.py tests/test_query_plan.py tests/test_controller_contract.py tests/test_runtime_state_flow.py
git commit -m "fix: project controller queries away from round-one support terms"
```

---

### Task 3: Add A Liepin-Only Transition Runner

**Files:**
- Create: `src/seektalent/providers/liepin/liepin_state_machine.py`
- Create: `tests/test_liepin_state_machine.py`

- [ ] **Step 1: Write transition runner tests**

Create `tests/test_liepin_state_machine.py`:

```python
from __future__ import annotations

from seektalent.providers.liepin.liepin_state_machine import (
    LiepinStateSnapshot,
    LiepinTransition,
    LiepinTransitionRunner,
    TransitionResult,
)


def test_transition_runner_observes_latest_state_before_and_after_action() -> None:
    calls: list[str] = []
    pre = LiepinStateSnapshot(ok=True, text="search input visible")
    post = LiepinStateSnapshot(ok=True, text="results visible")

    transition = LiepinTransition(
        name="apply_city_filter",
        phase="search",
        observe_pre_state=lambda: calls.append("observe_pre") or pre,
        precondition=lambda state: calls.append(f"pre:{state.text}") or True,
        action=lambda: calls.append("action") or TransitionResult(ok=True),
        observe_post_state=lambda: calls.append("observe_post") or post,
        postcondition=lambda state: calls.append(f"post:{state.text}") or True,
        retry_policy="none",
        safe_reason_code="liepin_opencli_filter_unapplied",
        trace_event="apply_native_filter",
    )

    result = LiepinTransitionRunner().run(transition)

    assert result.ok is True
    assert result.safe_reason_code is None
    assert calls == ["observe_pre", "pre:search input visible", "action", "observe_post", "post:results visible"]


def test_transition_runner_does_not_run_action_when_precondition_fails() -> None:
    calls: list[str] = []

    transition = LiepinTransition(
        name="wait_results_ready",
        phase="search",
        observe_pre_state=lambda: LiepinStateSnapshot(ok=True, text="login expired"),
        precondition=lambda state: False,
        action=lambda: calls.append("action") or TransitionResult(ok=True),
        observe_post_state=lambda: LiepinStateSnapshot(ok=True, text="unused"),
        postcondition=lambda state: True,
        retry_policy="none",
        safe_reason_code="liepin_opencli_results_not_ready",
        trace_event="observe_results",
    )

    result = LiepinTransitionRunner().run(transition)

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_results_not_ready"
    assert result.debug_reason == "precondition_failed"
    assert calls == []


def test_transition_runner_does_not_repeat_toggle_when_postcondition_is_unknown() -> None:
    calls: list[str] = []

    transition = LiepinTransition(
        name="apply_school_type_filter",
        phase="search",
        observe_pre_state=lambda: LiepinStateSnapshot(ok=True, text="985 visible"),
        precondition=lambda state: True,
        action=lambda: calls.append("action") or TransitionResult(ok=True),
        observe_post_state=lambda: LiepinStateSnapshot(ok=True, text="985 still unverified"),
        postcondition=lambda state: False,
        retry_policy="no_repeat_toggle",
        safe_reason_code="liepin_opencli_filter_unapplied",
        trace_event="apply_native_filter",
    )

    result = LiepinTransitionRunner().run(transition)

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_filter_unapplied"
    assert result.debug_reason == "postcondition_failed"
    assert calls == ["action"]
```

Add these matrix tests before implementation:

```python
def test_transition_runner_propagates_action_safe_reason_code() -> None:
    # action returns TransitionResult(ok=False, safe_reason_code="liepin_opencli_status_unavailable")
    # runner returns that exact safe reason without converting it


def test_transition_runner_stops_when_pre_state_is_terminal() -> None:
    # observe_pre_state returns ok=False with safe_reason_code="liepin_opencli_terminal_state"
    # action and post observation are not called
```

- [ ] **Step 2: Run failing transition tests**

Run:

```bash
uv run pytest tests/test_liepin_state_machine.py -q
```

Expected result before implementation: import failure for `seektalent.providers.liepin.liepin_state_machine`.

- [ ] **Step 3: Implement transition runner**

Create `src/seektalent/providers/liepin/liepin_state_machine.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

RetryPolicy = Literal["none", "no_repeat_toggle"]


@dataclass(frozen=True, kw_only=True)
class LiepinStateSnapshot:
    ok: bool
    text: str
    url: str | None = None
    safe_reason_code: str | None = None
    observation: dict[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class TransitionResult:
    ok: bool
    safe_reason_code: str | None = None
    debug_reason: str | None = None
    event: dict[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class LiepinTransition:
    name: str
    phase: str
    observe_pre_state: Callable[[], LiepinStateSnapshot]
    precondition: Callable[[LiepinStateSnapshot], bool]
    action: Callable[[], TransitionResult]
    observe_post_state: Callable[[], LiepinStateSnapshot]
    postcondition: Callable[[LiepinStateSnapshot], bool]
    safe_reason_code: str
    trace_event: str
    retry_policy: RetryPolicy = "none"


class LiepinTransitionRunner:
    def run(self, transition: LiepinTransition) -> TransitionResult:
        pre_state = transition.observe_pre_state()
        if not pre_state.ok:
            return TransitionResult(
                ok=False,
                safe_reason_code=pre_state.safe_reason_code or transition.safe_reason_code,
                debug_reason="pre_state_failed",
            )
        if not transition.precondition(pre_state):
            return TransitionResult(
                ok=False,
                safe_reason_code=transition.safe_reason_code,
                debug_reason="precondition_failed",
            )
        result = transition.action()
        if not result.ok:
            return result
        post_state = transition.observe_post_state()
        if not post_state.ok:
            return TransitionResult(
                ok=False,
                safe_reason_code=post_state.safe_reason_code or transition.safe_reason_code,
                debug_reason="post_state_failed",
                event=result.event,
            )
        if transition.postcondition(post_state):
            return result
        return TransitionResult(
            ok=False,
            safe_reason_code=transition.safe_reason_code,
            debug_reason="postcondition_failed",
            event=result.event,
        )
```

Do not add `refresh_state_once` in this slice. Fresh state is already mandatory before and after every action. Future retry behavior must be modeled as a separate named transition or an explicit policy with its own state observation and tests.

- [ ] **Step 4: Run transition tests**

Run:

```bash
uv run pytest tests/test_liepin_state_machine.py -q
```

Expected result: all transition tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/liepin/liepin_state_machine.py tests/test_liepin_state_machine.py
git commit -m "feat: add Liepin transition runner"
```

---

### Task 4: Express Liepin Search And Filter Operations As Verified Phases

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/providers/liepin/opencli_filter_planning.py`
- Test: `tests/test_liepin_opencli_browser.py`
- Test: `tests/test_liepin_opencli_city_filter.py`
- Test: `tests/test_liepin_opencli_filter_planning.py`

**State-machine contract:**

Every search/filter browser phase must run through `LiepinTransitionRunner.run(transition)`. Do not merely wrap existing calls in private methods.

| Transition | `action_kind` | Pre-state source | Action | Postcondition | Public safe reason code |
| --- | --- | --- | --- | --- | --- |
| Open search | `open_search` | OpenCLI tab/url state | Open or select search route | Search URL or search surface is active | `liepin_opencli_search_not_ready` |
| Wait search ready | `wait_search_ready` | OpenCLI state/readiness probe | Wait/observe search page | Search input and button are visible | `liepin_opencli_search_not_ready` |
| Clear filters | `clear_native_filters` | OpenCLI state | Clear is available and not already done for workflow | Click clear once | Filter summaries clear or clear action disappears | `liepin_opencli_filter_unapplied` |
| Fill keyword | `fill_search` | OpenCLI state | Fill keyword input | Input/search surface remains valid | `liepin_opencli_search_input_missing` |
| Click search | `click_search` | OpenCLI state | Click search | Loading, results, or empty state observed | `liepin_opencli_search_submit_unconfirmed` |
| Observe results | `observe_results` | OpenCLI state/readiness probe | Wait/observe results | Result list or empty state classified | `liepin_opencli_results_not_ready` |
| Apply native filter | `apply_native_filter` | OpenCLI state | One deterministic click/fill/confirm sequence | Selected state or summary verifies target | `liepin_opencli_filter_unapplied` |
| Extract cards | `extract_structured_cards` | OpenCLI state plus read-only structured probe | Extract structured cards | Card payload validates | `liepin_opencli_results_not_ready` or `liepin_opencli_malformed_state` |

`action_kind` values must stay snake_case and must be wired through both `action-trace.json.events` and final provider `workflow_steps`. If this slice adds or renames an action kind, update `opencli_workflow._ACTION_TO_STEP_EVENT` and the client safe allowlist in the same commit.

- [ ] **Step 1: Preserve existing filter-state regression tests**

Confirm these tests exist or add them with the current helper style:

```python
def test_search_liepin_cards_does_not_retry_school_type_toggle_after_unverified_click(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[30]<label>院校要求：</label>
[31]<label>211</label>
[32]<label>985</label>
王** 男 34岁 工作5年 硕士 上海
"""
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_after_search,
                state_after_search,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "AI"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "32"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-school-toggle",
        query="AI",
        max_pages=1,
        max_cards=10,
        native_filters={"schoolTypes": "985"},
    )

    assert envelope["status"] == "succeeded"
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "32")) == 1
```

If a current test uses a different filter key shape, keep the current working shape and preserve the final click-count assertion.

- [ ] **Step 2: Preserve existing city precheck regression tests**

Confirm `tests/test_liepin_opencli_city_filter.py` has a test equivalent to:

```python
def test_search_liepin_cards_keeps_visible_expected_city_without_picker_retry(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_expected_city = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[22]<label>上海</label>
[23]<label>其他</label>
[50]<label title=期望城市 />
  <span>上海</span>
王** 男 34岁 工作5年 硕士 上海
求职期望：上海 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_expected_city,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-city-precheck",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "上海"}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "23") not in commands.calls
```

- [ ] **Step 3: Add workflow phase helpers**

In `src/seektalent/providers/liepin/liepin_search_workflow.py`, import:

```python
from seektalent.providers.liepin.liepin_state_machine import (
    LiepinTransition,
    LiepinTransitionRunner,
    TransitionResult,
)
```

Add a runner in `LiepinSearchWorkflow.__init__()`:

```python
self._runner = LiepinTransitionRunner()
```

Add this helper:

```python
def _append_transition_event(
    self,
    source_run_id: str,
    *,
    name: str,
    route_kind: str,
    result: TransitionResult,
) -> None:
    event: dict[str, object] = {
        "action_kind": name,
        "route_kind": route_kind,
        "ok": result.ok,
    }
    if result.safe_reason_code:
        event["safe_reason_code"] = result.safe_reason_code
    self._append_event(source_run_id, event)
```

Keep `debug_reason` internal to runner logs or unit-test assertions. Do not put it into provider envelopes, `workflow_steps`, or UI-visible trace events.

- [ ] **Step 4: Express search-card phases through transitions**

Inside `search_detail_backed_resumes()`, each documented search phase must call the transition runner:

```python
result = self._runner.run(
    LiepinTransition(
        name="fill_keyword",
        phase="search",
        observe_pre_state=self._site.observe_liepin_search_state,
        precondition=lambda state: has_search_input(state.text),
        action=lambda: self._site.fill_liepin_search_keyword(request.query),
        observe_post_state=self._site.observe_liepin_search_state,
        postcondition=lambda state: has_search_surface(state.text),
        retry_policy="none",
        safe_reason_code="liepin_opencli_search_input_missing",
        trace_event="fill_search",
    )
)
```

Use the same pattern for `open_search`, `wait_search_ready`, `clear_native_filters`, `click_search`, `observe_results`, `apply_native_filter`, and `extract_structured_cards`. Preserve the existing public envelope shape, but build blocked envelopes from `TransitionResult.safe_reason_code`, not internal debug labels.

Add tests that assert `LiepinTransitionRunner.run` is called for the search phases. A direct call from `search_detail_backed_resumes()` to a mutating adapter method without a transition is a test failure.

- [ ] **Step 5: Guard native filter mutation in the adapter**

In `src/seektalent/providers/liepin/liepin_site_adapter.py`, ensure `_select_liepin_native_filter()` follows this concrete sequence:

```python
state_before_click = self.state()
if not state_before_click.ok:
    return state_before_click
state_text = _opencli_result_text(state_before_click)
if native_filter_selection_applied(state_text, section=section, label=label):
    return state_before_click
self._click_native_filter_option(label, state_text=state_text, section=section)
state_after_click = self.state()
if not state_after_click.ok:
    return state_after_click
state_text_after_click = _opencli_result_text(state_after_click)
if native_filter_selection_applied(state_text_after_click, section=section, label=label):
    return state_after_click
return OpenCliBrowserResult(
    ok=False,
    action="apply_liepin_native_filter",
    safe_reason_code="liepin_opencli_filter_unapplied",
    observation={"filter": filter_name, "section": section, "value": label},
)
```

Keep the current richer implementation if it already has these semantics. Do not add any modal-closing path.

- [ ] **Step 6: Add command-order and reason-code coverage**

Add FakeCommands tests that fail if any mutating OpenCLI command (`fill`, `click`, `tab new`, `tab select`) runs without a preceding `state` or explicit readiness probe in the same transition.

Add transition-matrix tests for:

- every transition in the table has at least success, precondition-blocked, action-failed, and postcondition-failed coverage;
- pre-state terminal failure uses the concrete transition `safe_reason_code`;
- status unavailable remains terminal unless a transition explicitly declares a safe local recovery path;
- action failure propagates the action `safe_reason_code`;
- postcondition failure uses the concrete transition `safe_reason_code`;
- stale result/card refs fail before the action and do not click a stale ref;
- `no_repeat_toggle` never runs the same toggle action twice;
- `action-trace.json.events` and final `workflow_steps` contain the same canonical snake_case action kind.

- [ ] **Step 7: Run focused filter tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_city_filter.py tests/test_liepin_opencli_filter_planning.py -q
```

Expected result: all focused filter/browser tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/seektalent/providers/liepin/liepin_search_workflow.py src/seektalent/providers/liepin/liepin_site_adapter.py src/seektalent/providers/liepin/opencli_filter_planning.py tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_city_filter.py tests/test_liepin_opencli_filter_planning.py
git commit -m "refactor: verify Liepin search filters by transition"
```

---

### Task 5: Express Liepin Detail Operations As Verified Phases

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Test: `tests/test_liepin_opencli_browser.py`

**State-machine contract:**

Every detail browser phase must run through `LiepinTransitionRunner.run(transition)`.

| Transition | `action_kind` | Pre-state source | Action | Postcondition | Public safe reason code |
| --- | --- | --- | --- | --- | --- |
| Select candidate | `detail_candidate_selected` | Latest structured card state | Choose next target card/ref | Target identity is traceable | `liepin_opencli_results_not_ready` |
| Open detail | `open_detail` | OpenCLI state | Open card ref or cached detail URL | Active tab is detail route or pending detail state | `liepin_opencli_detail_not_opened` |
| Wait detail ready | `wait_detail_ready` | OpenCLI state/readiness probe | Wait/observe detail page | Detail resume state is ready | `liepin_opencli_detail_not_opened` |
| Observe detail | `observe_detail` | OpenCLI state plus read-only structured probe | Extract structured detail payload | Payload validates before artifact write | `liepin_opencli_detail_not_opened` or `liepin_opencli_malformed_state` |
| Capture detail | `capture_detail_succeeded` | Valid structured detail payload | Persist normalized structured detail artifact | Artifact has structured fields and no fullText aliases | `liepin_opencli_malformed_state` |
| Restore search | `return_to_search_after_capture` | OpenCLI tab/url state | Select/restore search page | Search page active or cached-detail mode enabled | `liepin_opencli_search_restore_failed` |
| Refresh visible cards | `visible_cards_refreshed_after_return` | OpenCLI state plus read-only structured probe | Refresh card refs | Cards validate or cached-detail mode continues | `liepin_opencli_results_not_ready` |

- [ ] **Step 1: Preserve detail-ready regression coverage**

Confirm `tests/test_liepin_opencli_browser.py` has `test_capture_liepin_detail_resume_waits_until_detail_page_is_ready`. If the test is absent, add the version already used by the current test helper pattern:

```python
def test_capture_liepin_detail_resume_waits_until_detail_page_is_ready(tmp_path: Path) -> None:
    class DetailReadyCommands(FakeCommands):
        def __init__(self) -> None:
            super().__init__(
                outputs={
                    ("opencli", "browser", "seektalent-liepin", "state"): [
                        "URL: about:blank url: about:blank title: viewport: 1512x707 --- interactive: 0",
                        detail_state,
                    ],
                    ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
                    ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
                    ),
                }
            )
            self.detail_ready = False

        def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
            call = tuple(argv)
            if len(call) >= 4 and call[3] == "eval":
                del timeout
                self.calls.append(call)
                self.envs.append(env)
                if not self.detail_ready:
                    return json.dumps({"ok": False, "safeReasonCode": "liepin_opencli_detail_not_opened"})
                return _liepin_detail_payload_json(summary_text=detail_state)
            output = super().run(argv, timeout=timeout, env=env)
            if call == ("opencli", "browser", "seektalent-liepin", "state") and "当前职位" in output:
                self.detail_ready = True
            return output

    commands = DetailReadyCommands()
    captured = _runner(commands, lease_dir=tmp_path).capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is True
    assert commands.calls.index(("opencli", "browser", "seektalent-liepin", "wait", "time", "2")) < next(
        index for index, call in enumerate(commands.calls) if len(call) >= 4 and call[3] == "eval"
    )
```

- [ ] **Step 2: Preserve cached detail URL regression coverage**

Confirm `tests/test_liepin_opencli_browser.py` has `test_search_liepin_resumes_uses_cached_detail_urls_when_refresh_after_return_loses_cards`. If it is absent, restore it from the current branch before refactoring detail phases. The assertion must verify all of these:

```python
assert envelope["status"] == "succeeded"
assert len(envelope["resumes"]) == 2
assert any(event["action_kind"] == "detail_urls_cached" for event in trace["events"])
assert any(event["action_kind"] == "return_to_search_after_capture" and event["ok"] is False for event in trace["events"])
```

- [ ] **Step 3: Express detail phases through transitions**

In `src/seektalent/providers/liepin/liepin_search_workflow.py`, implement each detail phase as an explicit transition:

```python
result = self._runner.run(
    LiepinTransition(
        name="open_detail",
        phase="detail",
        observe_pre_state=self._site.observe_liepin_search_state,
        precondition=lambda state: card_ref_is_still_valid(state.text, ref) or cached_detail_url is not None,
        action=lambda: self._site.open_liepin_detail_or_cached_url(
            source_run_id=source_run_id,
            ref=ref,
            rank=rank,
            cached_detail_url=cached_detail_url,
        ),
        observe_post_state=self._site.observe_liepin_detail_state,
        postcondition=lambda state: detail_route_or_pending(state.text, state.url),
        retry_policy="none",
        safe_reason_code="liepin_opencli_detail_not_opened",
        trace_event="open_detail",
    )
)
```

Use the same pattern for `wait_detail_ready`, `observe_detail`, `capture_detail_succeeded`, `return_to_search_after_capture`, and `visible_cards_refreshed_after_return`.

If `return_to_search_after_capture` fails after a successful capture, record a failed transition event with `safe_reason_code="liepin_opencli_search_restore_failed"` and enter cached-detail-URL mode only if cached URLs were captured before leaving the search page. Cached mode must still respect the target resume budget and all detail payload validation.

Add transition-matrix tests for each detail transition:

- success;
- precondition-blocked stale ref or missing cached URL;
- action failure propagating the concrete action `safe_reason_code`;
- postcondition failure using the transition's concrete `safe_reason_code`;
- status unavailable and terminal states do not retry;
- restore failure enters cached-detail-URL mode only after a successful capture and only when cached URLs exist.

Add tests that assert the detail loop cannot call `open_liepin_detail`, `capture_liepin_detail_resume`, or `restore_liepin_search_page` without a corresponding transition event:

```python
assert any(event["action_kind"] == "open_detail" for event in trace["events"])
assert any(event["action_kind"] == "wait_detail_ready" for event in trace["events"])
assert any(event["action_kind"] == "observe_detail" for event in trace["events"])
assert any(
    event["action_kind"] == "return_to_search_after_capture"
    and event.get("safe_reason_code") == "liepin_opencli_search_restore_failed"
    for event in trace["events"]
)
```

Add command-order coverage for detail transitions:

```python
mutating_ops = {"click", "tab", "fill"}
for index, call in enumerate(commands.calls):
    if len(call) >= 4 and call[3] in mutating_ops:
        assert any(previous[3] in {"state", "eval"} for previous in commands.calls[max(0, index - 3):index])
```

If a test needs a broader window than three commands because of existing `get url` calls, use a helper that scopes to the current transition event instead of loosening the requirement globally.

- [ ] **Step 4: Keep detail payload fail-closed**

Before committing, run this focused test:

```bash
uv run pytest tests/test_liepin_opencli_browser.py::test_capture_liepin_detail_resume_rejects_whole_page_text_aliases_before_artifact_write -q
```

Expected result: test passes. If it fails, restore the pre-artifact-write rejection in `capture_liepin_detail_resume()` before continuing.

- [ ] **Step 5: Run detail/browser tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_browser.py -q
```

Expected result: all browser tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/providers/liepin/liepin_search_workflow.py src/seektalent/providers/liepin/liepin_site_adapter.py tests/test_liepin_opencli_browser.py
git commit -m "refactor: verify Liepin detail capture phases"
```

---

### Task 6: Final Verification

**Files:**
- No planned production edits.
- Modify tests only if focused verification exposes stale fixtures caused by the planned changes.

- [ ] **Step 1: Run runtime contract tests**

```bash
uv run pytest tests/test_reflection_contract.py tests/test_query_plan.py tests/test_controller_contract.py tests/test_runtime_state_flow.py -q
```

Expected result: all tests pass.

- [ ] **Step 2: Run Liepin focused tests**

```bash
uv run pytest tests/test_liepin_state_machine.py tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_city_filter.py tests/test_liepin_opencli_filter_planning.py tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_runtime_source_adapter_boundary.py -q
```

Expected result: all tests pass.

- [ ] **Step 3: Run ruff on touched files**

```bash
uv run ruff check src/seektalent/reflection/critic.py src/seektalent/retrieval/query_plan.py src/seektalent/retrieval/__init__.py src/seektalent/controller/react_controller.py src/seektalent/runtime/round_decision_runtime.py src/seektalent/providers/liepin/liepin_state_machine.py src/seektalent/providers/liepin/liepin_search_workflow.py src/seektalent/providers/liepin/liepin_site_adapter.py src/seektalent/providers/liepin/opencli_filter_planning.py tests/test_reflection_contract.py tests/test_query_plan.py tests/test_controller_contract.py tests/test_runtime_state_flow.py tests/test_liepin_state_machine.py tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_city_filter.py tests/test_liepin_opencli_filter_planning.py
```

Expected result: `All checks passed!`

- [ ] **Step 4: Run ty on touched Python files**

```bash
uv run --group dev ty check src/seektalent/reflection/critic.py src/seektalent/retrieval/query_plan.py src/seektalent/retrieval/__init__.py src/seektalent/controller/react_controller.py src/seektalent/runtime/round_decision_runtime.py src/seektalent/providers/liepin/liepin_state_machine.py src/seektalent/providers/liepin/liepin_search_workflow.py src/seektalent/providers/liepin/liepin_site_adapter.py src/seektalent/providers/liepin/opencli_filter_planning.py tests/test_reflection_contract.py tests/test_query_plan.py tests/test_controller_contract.py tests/test_runtime_state_flow.py tests/test_liepin_state_machine.py tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_city_filter.py tests/test_liepin_opencli_filter_planning.py
```

Expected result: `All checks passed!`

- [ ] **Step 5: Run full-text guard**

```bash
rg -n "fullText|rawText|wholePageText|visible_text|normalized_card_text|normalizedCardText" src/seektalent/providers/liepin src/seektalent/sources/liepin src/seektalent/resume_normalizers/liepin.py tests
```

Expected result: matches are limited to denylist constants, negative tests, parser rejection tests, and migration comments. No production path may read those fields as candidate evidence.

- [ ] **Step 6: Commit verification-only fixture fixes if needed**

If verification required only test fixture updates:

```bash
git add tests
git commit -m "test: align runtime contract and Liepin transition coverage"
```

If no files changed during final verification, do not create a commit.

---

## Implementation Notes

- Keep `secondary_title_anchor` in `QueryRetrievalRole`; it remains valid for round 1.
- Do not widen reflection output schema.
- Do not change scoring parallelism.
- Do not collect, store, or fallback to full text.
- Do not add modal-closing behavior.
- Do not build a generic workflow engine for future sites.
- Keep `LiepinOpenCliResumeRetriever` as the stable entrypoint.
- Keep OpenCLI and Liepin decoupled: OpenCLI exposes browser primitives, Liepin provider owns Liepin-specific selectors, validation, and workflow semantics.

## Self-Review

- Spec coverage: Tasks 1 and 2 cover the current runtime failure; Tasks 3 through 5 cover Liepin transition hardening; Task 6 covers verification and text-tail guardrails.
- Unspecified work scan: every task lists exact files, commands, expected outcomes, and concrete code or assertions.
- Type consistency: helper names are stable across tasks: `_filter_to_reflection_allowed_terms`, `try_project_secondary_title_anchor_after_round_one`, `project_controller_decision_if_round_legal`, `TransitionResult`, `LiepinTransition`, and `LiepinTransitionRunner`.
