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

- [ ] **Step 1: Write the pure projection tests**

In `tests/test_query_plan.py`, add this import:

```python
from seektalent.retrieval.query_plan import project_round_legal_query_terms
```

Add these tests near `test_query_plan_rejects_secondary_title_anchor_after_round_one`:

```python
def test_project_query_terms_keeps_round_one_secondary_title_anchor() -> None:
    pool = [
        QueryTermCandidate(
            term="Backend",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            queryability="admitted",
            family="role.backend",
        ),
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=2,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="secondary_title_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="domain",
            priority=3,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.python",
        ),
    ]

    assert project_round_legal_query_terms(
        ["Backend", "Platform"],
        round_no=1,
        query_term_pool=pool,
    ) == ["Backend", "Platform"]


def test_project_query_terms_replaces_secondary_title_anchor_after_round_one() -> None:
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

    assert project_round_legal_query_terms(
        ["AI", "主观投资"],
        round_no=3,
        query_term_pool=pool,
    ) == ["AI", "模型部署"]
```

- [ ] **Step 2: Run the failing projection tests**

Run:

```bash
uv run pytest tests/test_query_plan.py::test_project_query_terms_keeps_round_one_secondary_title_anchor tests/test_query_plan.py::test_project_query_terms_replaces_secondary_title_anchor_after_round_one -q
```

Expected result before implementation: import failure for `project_round_legal_query_terms`.

- [ ] **Step 3: Implement the pure projection helper**

In `src/seektalent/retrieval/query_plan.py`, add this function after `canonicalize_controller_query_terms()`:

```python
def project_round_legal_query_terms(
    proposed_terms: list[str],
    *,
    round_no: int,
    query_term_pool: list[QueryTermCandidate],
) -> list[str]:
    clean_terms = unique_strings([normalize_term(term) for term in proposed_terms if normalize_term(term)])
    if round_no <= 1:
        return clean_terms

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
        return output

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

    return output[:3]
```

If `src/seektalent/retrieval/__init__.py` exports query-plan helpers, add:

```python
from seektalent.retrieval.query_plan import project_round_legal_query_terms
```

and include it in `__all__` if the module has an `__all__` list.

- [ ] **Step 4: Add controller decision projection helper**

In `src/seektalent/controller/react_controller.py`, import:

```python
from seektalent.retrieval import project_round_legal_query_terms
```

Add this helper near `validate_controller_decision()`:

```python
def project_controller_decision_if_round_legal(
    *,
    context: ControllerContext,
    decision: ControllerDecision,
    reason: str,
) -> ControllerDecision | None:
    if not isinstance(decision, SearchControllerDecision):
        return None
    if "secondary_title_anchor" not in reason:
        return None
    projected_terms = project_round_legal_query_terms(
        decision.proposed_query_terms,
        round_no=context.round_no,
        query_term_pool=context.query_term_pool,
    )
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

- [ ] **Step 5: Use the same projection in runtime sanitization**

In `src/seektalent/runtime/round_decision_runtime.py`, import `project_round_legal_query_terms` from `seektalent.retrieval`.

In `sanitize_controller_decision()`, compute projected terms before canonicalization:

```python
projected_terms = project_round_legal_query_terms(
    decision.proposed_query_terms,
    round_no=round_no,
    query_term_pool=run_state.retrieval_state.query_term_pool,
)
query_terms = canonicalize_controller_query_terms(
    projected_terms,
    round_no=round_no,
    title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
    query_term_pool=run_state.retrieval_state.query_term_pool,
    allowed_inactive_non_anchor_terms=allowed_inactive_terms,
)
```

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

In `tests/test_runtime_state_flow.py`, add a focused unit test for `sanitize_controller_decision()` or update the existing round-decision helper test so a round 3 decision with `["AI", "主观投资"]` is sanitized to `["AI", "模型部署"]`.

Use this assertion:

```python
assert sanitized.proposed_query_terms == ["AI", "模型部署"]
```

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
    LiepinTransition,
    LiepinTransitionRunner,
    TransitionResult,
)


def test_transition_runner_checks_pre_action_and_postcondition_order() -> None:
    calls: list[str] = []

    transition = LiepinTransition(
        name="apply_city_filter",
        precondition=lambda: calls.append("pre") or True,
        action=lambda: calls.append("action") or TransitionResult(ok=True),
        postcondition=lambda: calls.append("post") or True,
        retry_policy="none",
    )

    result = LiepinTransitionRunner().run(transition)

    assert result.ok is True
    assert result.reason is None
    assert calls == ["pre", "action", "post"]


def test_transition_runner_does_not_run_action_when_precondition_fails() -> None:
    calls: list[str] = []

    transition = LiepinTransition(
        name="wait_results_ready",
        precondition=lambda: False,
        action=lambda: calls.append("action") or TransitionResult(ok=True),
        postcondition=lambda: True,
        retry_policy="none",
    )

    result = LiepinTransitionRunner().run(transition)

    assert result.ok is False
    assert result.reason == "precondition_failed"
    assert calls == []


def test_transition_runner_does_not_repeat_toggle_when_postcondition_is_unknown() -> None:
    calls: list[str] = []

    transition = LiepinTransition(
        name="apply_school_type_filter",
        precondition=lambda: True,
        action=lambda: calls.append("action") or TransitionResult(ok=True),
        postcondition=lambda: False,
        retry_policy="no_repeat_toggle",
    )

    result = LiepinTransitionRunner().run(transition)

    assert result.ok is False
    assert result.reason == "postcondition_failed"
    assert calls == ["action"]
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

RetryPolicy = Literal["none", "refresh_state_once", "no_repeat_toggle"]


@dataclass(frozen=True, kw_only=True)
class TransitionResult:
    ok: bool
    reason: str | None = None
    event: dict[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class LiepinTransition:
    name: str
    precondition: Callable[[], bool]
    action: Callable[[], TransitionResult]
    postcondition: Callable[[], bool]
    retry_policy: RetryPolicy = "none"


class LiepinTransitionRunner:
    def run(self, transition: LiepinTransition) -> TransitionResult:
        if not transition.precondition():
            return TransitionResult(ok=False, reason="precondition_failed")
        result = transition.action()
        if not result.ok:
            return result
        if transition.postcondition():
            return result
        if transition.retry_policy == "refresh_state_once" and transition.postcondition():
            return result
        return TransitionResult(ok=False, reason="postcondition_failed", event=result.event)
```

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
    if result.reason:
        event["safe_reason_code"] = result.reason
    self._append_event(source_run_id, event)
```

- [ ] **Step 4: Name search-card phases without changing the public envelope**

Inside `search_detail_backed_resumes()`, split the existing search/card segment into private methods:

```python
def _search_cards_phase(self, request: LiepinSearchWorkflowRequest) -> dict[str, object]:
    return self._site.search_liepin_cards(
        source_run_id=request.source_run_id,
        query=request.query,
        max_pages=request.max_pages,
        max_cards=request.max_cards,
        native_filters=request.native_filters,
    )


def _extract_cards_phase(
    self,
    *,
    source_run_id: str,
    max_cards: int,
) -> OpenCliBrowserResult:
    return self._site.extract_structured_liepin_cards(
        source_run_id=source_run_id,
        max_cards=max_cards,
    )
```

Call those methods from `search_detail_backed_resumes()`. Preserve the existing blocked envelope behavior.

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

- [ ] **Step 6: Run focused filter tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_city_filter.py tests/test_liepin_opencli_filter_planning.py -q
```

Expected result: all focused filter/browser tests pass.

- [ ] **Step 7: Commit**

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

- [ ] **Step 3: Split detail phases in `LiepinSearchWorkflow`**

In `src/seektalent/providers/liepin/liepin_search_workflow.py`, add these private methods:

```python
def _open_detail_phase(
    self,
    *,
    source_run_id: str,
    ref: str,
    rank: int,
    cached_detail_url: str | None,
    use_cached_detail_url: bool,
) -> OpenCliBrowserResult:
    if use_cached_detail_url and cached_detail_url is not None:
        return self._site.open_liepin_detail_cached_url(
            source_run_id=source_run_id,
            ref=ref,
            rank=rank,
            detail_url=cached_detail_url,
        )
    return self._site.open_liepin_detail(source_run_id=source_run_id, ref=ref, rank=rank)


def _capture_detail_phase(
    self,
    *,
    source_run_id: str,
    rank: int,
) -> OpenCliBrowserResult:
    return self._site.capture_liepin_detail_resume(source_run_id=source_run_id, rank=rank)


def _restore_search_phase(self, *, source_run_id: str, rank: int) -> str | None:
    restored_page_id = self._site.restore_liepin_search_page()
    self._append_event(
        source_run_id,
        {
            "action_kind": "return_to_search_after_capture",
            "route_kind": "search",
            "ok": restored_page_id is not None,
            "rank": rank,
        },
    )
    return restored_page_id
```

Use these methods in the existing detail loop. Do not change the public return envelope.

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
- Type consistency: helper names are stable across tasks: `_filter_to_reflection_allowed_terms`, `project_round_legal_query_terms`, `project_controller_decision_if_round_legal`, `TransitionResult`, `LiepinTransition`, and `LiepinTransitionRunner`.
