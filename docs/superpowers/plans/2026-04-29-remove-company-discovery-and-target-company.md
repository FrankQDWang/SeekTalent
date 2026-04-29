# Remove Company Discovery And Target Company Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the active company-discovery and target-company retrieval branch so new runs no longer execute company web rescue, no longer inject target-company query terms, and no longer carry company-discovery config, prompts, artifacts, or reporting behavior.

**Architecture:** Remove the branch mechanically from the outside in: first collapse active config and runtime vocabulary, then remove rescue/runtime wiring, then delete the `company_discovery` package and prompt surface, then replace old behavior tests with absence and legacy-read tests, and finally verify the benchmark smoke path no longer touches company discovery. Preserve `candidate_feedback` and `PRF v1.5` boundaries, including company-entity rejection, and keep historical runs readable in read-only paths.

**Tech Stack:** Python 3.12, Pydantic, existing SeekTalent runtime split modules, typed artifact registry/resolver, pytest, benchmark CLI, existing run artifacts.

---

## File Map

### Delete

- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/company_discovery_runtime.py`
  Purpose: Remove the company-discovery runtime branch instead of leaving a dormant module.

- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/__init__.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/bocha_provider.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/model_steps.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/models.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/page_reader.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/query_injection.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/scheduler.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/service.py`
  Purpose: Remove the entire domain package; no compatibility shell remains.

- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/company_discovery_plan.md`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/company_discovery_extract.md`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/company_discovery_reduce.md`
  Purpose: Remove prompt assets that no active runtime should load.

- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_company_discovery.py`
  Purpose: Remove behavior tests for a deleted package instead of turning them into no-op assertions.

### Modify

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py`
  Purpose: Remove active company-discovery and target-company settings while keeping stale dotenv values ignored.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/default.env`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/.env.example`
  Purpose: Remove checked-in company-discovery configuration examples.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/resources.py`
  Purpose: Remove company-discovery prompt names from the active required prompt registry.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
  Purpose: Remove active retrieval-state fields and active lane vocabulary that only exist for company discovery or target-company retrieval.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_router.py`
  Purpose: Remove `web_company_discovery` from active rescue selection and simplify inputs.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/round_decision_runtime.py`
  Purpose: Remove company-discovery execution hooks from round continuation.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
  Purpose: Remove `CompanyDiscoveryService`, target-company checks, company-specific prompt loading, run-config output, extra-model accounting, and company rescue calls.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_plan.py`
  Purpose: Remove `target_company`-specific candidate handling from query-term planning.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
  Purpose: Remove active diagnostics collection for company-discovery calls while staying tolerant of historical runs.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/tui.py`
  Purpose: Remove company-discovery-specific event rendering.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router_config.py`
  Purpose: Lock the new config defaults and removal of company-specific runtime state.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router.py`
  Purpose: Replace company-lane behavior tests with absence tests and simplified rescue-ordering tests.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
  Purpose: Remove company-runtime flow tests, add absence tests, and verify fallback goes from feedback directly to anchor-only / stop.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
  Purpose: Remove company prompt/artifact expectations from run-config and audit output while keeping read-only historical tolerance where needed.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_tui.py`
  Purpose: Assert the active TUI no longer renders company-discovery events.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py`
  Purpose: Remove active CLI expectations for company-discovery prompts.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py`
  Purpose: Replace checks that referenced `company_discovery_runtime.py` with absence/import-boundary checks.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py`
  Purpose: Replace “primary comparison disables company rescue” with “active runtime has no company rescue branch.”

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
  Purpose: Remove company-discovery outputs from active artifact documentation.

### Notes

- Do **not** modify `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/*` to remove `company_entity` or `ambiguous_company_or_product_entity` rejection logic.
- Do **not** change the defaults for `prf_v1_5_mode` or `prf_model_backend`.
- Do **not** mix in the current local benchmark-debug edits in `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py` and `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py` without first rebasing this plan on top of them carefully.

## Task 1: Remove Active Config, Prompt Registry, And Runtime Vocabulary

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/default.env`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/.env.example`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/resources.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_router.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router_config.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router.py`

- [ ] **Step 1: Write the failing config and rescue-vocabulary tests**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router_config.py
import pytest
from pydantic import ValidationError

from seektalent.models import RetrievalState
from seektalent.runtime.rescue_router import RescueInputs, choose_rescue_lane
from tests.settings_factory import make_settings


def test_rescue_feature_defaults_remove_company_surface() -> None:
    settings = make_settings()

    assert settings.candidate_feedback_enabled is True
    assert not hasattr(settings, "target_company_enabled")
    assert not hasattr(settings, "company_discovery_enabled")
    assert not hasattr(settings, "bocha_api_key")


def test_stale_company_env_values_are_ignored_by_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_COMPANY_DISCOVERY_ENABLED", "1")
    monkeypatch.setenv("SEEKTALENT_TARGET_COMPANY_ENABLED", "1")

    settings = make_settings()

    assert not hasattr(settings, "company_discovery_enabled")
    assert not hasattr(settings, "target_company_enabled")


def test_retrieval_state_no_longer_accepts_company_fields() -> None:
    with pytest.raises(ValidationError):
        RetrievalState(company_discovery_attempted=True)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router.py
from seektalent.models import StopGuidance
from seektalent.runtime.rescue_router import RescueInputs, choose_rescue_lane


def _stop_guidance() -> StopGuidance:
    return StopGuidance(
        should_stop=False,
        can_stop=False,
        quality_gate_status="low_quality_exhausted",
        rationale="needs rescue",
        continue_reason="rescue path required",
    )


def test_candidate_feedback_is_selected_before_anchor_only() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_stop_guidance(),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=True,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "candidate_feedback"
    assert all(item.lane != "web_company_discovery" for item in decision.skipped_lanes)


def test_anchor_only_is_selected_after_feedback_branch_is_unavailable() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_stop_guidance(),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=False,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "anchor_only"
    assert {item.lane for item in decision.skipped_lanes} == {
        "reserve_broaden",
        "candidate_feedback",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router_config.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router.py`

Expected: FAIL because company settings and `web_company_discovery` still exist in active config and rescue-router models.

- [ ] **Step 3: Remove company config and active rescue vocabulary**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py
MODEL_FIELDS = (
    "requirements_model",
    "controller_model",
    "scoring_model",
    "finalize_model",
    "reflection_model",
    "structured_repair_model",
    "judge_model",
    "tui_summary_model",
    "candidate_feedback_model",
)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEEKTALENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    candidate_feedback_enabled: bool = True
    candidate_feedback_model: str = "openai-chat:qwen3.5-flash"
    candidate_feedback_reasoning_effort: ReasoningEffort = "off"
    prf_v1_5_mode: Literal["disabled", "shadow", "mainline"] = "shadow"
    prf_model_backend: Literal["legacy", "http_sidecar"] = "legacy"
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/resources.py
REQUIRED_PROMPTS = (
    "requirements",
    "controller",
    "scoring",
    "reflection",
    "finalize",
    "judge",
    "tui_summary",
    "candidate_feedback",
    "repair_requirements",
    "repair_controller",
    "repair_reflection",
)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py
class RetrievalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_plan_version: int = 0
    candidate_feedback_attempted: bool = False
    anchor_only_broaden_attempted: bool = False
    rescue_lane_history: list[dict[str, object]] = Field(default_factory=list)
    query_term_pool: list[QueryTermCandidate] = Field(default_factory=list)
    sent_query_history: list[SentQueryRecord] = Field(default_factory=list)
    reflection_keyword_advice_history: list[ReflectionKeywordAdvice] = Field(default_factory=list)
    reflection_filter_advice_history: list[ReflectionFilterAdvice] = Field(default_factory=list)
    last_projection_result: ConstraintProjectionResult | None = None
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_router.py
RescueLane = Literal[
    "reserve_broaden",
    "candidate_feedback",
    "anchor_only",
    "continue_controller",
    "allow_stop",
]


class RescueInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_guidance: StopGuidance
    has_untried_reserve_family: bool
    has_feedback_seed_resumes: bool
    candidate_feedback_enabled: bool
    candidate_feedback_attempted: bool
    anchor_only_broaden_attempted: bool


def choose_rescue_lane(inputs: RescueInputs) -> RescueDecision:
    status = inputs.stop_guidance.quality_gate_status
    if status not in RESCUE_STATUSES:
        if inputs.stop_guidance.can_stop:
            return RescueDecision(selected_lane="allow_stop")
        return RescueDecision(selected_lane="continue_controller")

    skipped_lanes: list[SkippedRescueLane] = []

    if inputs.has_untried_reserve_family:
        return RescueDecision(selected_lane="reserve_broaden", skipped_lanes=skipped_lanes)
    skipped_lanes.append(SkippedRescueLane(lane="reserve_broaden", reason="no_untried_reserve_family"))

    if inputs.candidate_feedback_enabled and not inputs.candidate_feedback_attempted and inputs.has_feedback_seed_resumes:
        return RescueDecision(selected_lane="candidate_feedback", skipped_lanes=skipped_lanes)
    if not inputs.candidate_feedback_enabled:
        reason = "disabled"
    elif inputs.candidate_feedback_attempted:
        reason = "already_attempted"
    else:
        reason = "no_feedback_seed_resumes"
    skipped_lanes.append(SkippedRescueLane(lane="candidate_feedback", reason=reason))

    if not inputs.anchor_only_broaden_attempted:
        return RescueDecision(selected_lane="anchor_only", skipped_lanes=skipped_lanes)

    skipped_lanes.append(SkippedRescueLane(lane="anchor_only", reason="already_attempted"))
    return RescueDecision(selected_lane="allow_stop", skipped_lanes=skipped_lanes)
```

```dotenv
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/.env.example
# remove:
# SEEKTALENT_TARGET_COMPANY_ENABLED
# SEEKTALENT_COMPANY_DISCOVERY_ENABLED
# SEEKTALENT_COMPANY_DISCOVERY_PROVIDER
# SEEKTALENT_BOCHA_API_KEY
# SEEKTALENT_COMPANY_DISCOVERY_MODEL
# SEEKTALENT_COMPANY_DISCOVERY_REASONING_EFFORT
# SEEKTALENT_COMPANY_DISCOVERY_MAX_SEARCH_CALLS
# SEEKTALENT_COMPANY_DISCOVERY_MAX_RESULTS_PER_QUERY
# SEEKTALENT_COMPANY_DISCOVERY_MAX_OPEN_PAGES
# SEEKTALENT_COMPANY_DISCOVERY_TIMEOUT_SECONDS
# SEEKTALENT_COMPANY_DISCOVERY_ACCEPTED_COMPANY_LIMIT
# SEEKTALENT_COMPANY_DISCOVERY_MIN_CONFIDENCE
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router_config.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/default.env /Users/frankqdwang/Agents/SeekTalent-0.2.4/.env.example /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/resources.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_router.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router_config.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router.py
git commit -m "refactor: remove company rescue config surface"
```

## Task 2: Remove Runtime Routing, Query Injection, And Active Company Imports

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/round_decision_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_plan.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/company_discovery_runtime.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/query_injection.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py`

- [ ] **Step 1: Write the failing runtime absence tests**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py
def test_runtime_falls_back_to_anchor_only_when_candidate_feedback_has_no_safe_term(tmp_path: Path) -> None:
    settings = make_settings()
    runtime = WorkflowRuntime(
        make_settings(
            runs_dir=str(tmp_path / "runs"),
            mock_cts=True,
            min_rounds=1,
            max_rounds=10,
            candidate_feedback_enabled=True,
        )
    )
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = _python_feedback_seed_scorecards()
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer))
    finally:
        tracer.close()

    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "anchor_only"
    assert {"lane": "candidate_feedback", "reason": "no_safe_feedback_term"} in rescue_decision["skipped_lanes"]
    assert all(item["lane"] != "web_company_discovery" for item in rescue_decision["skipped_lanes"])
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py
def test_active_runtime_has_no_company_rescue_branch() -> None:
    source = Path("/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py").read_text(encoding="utf-8")
    assert "CompanyDiscoveryService" not in source
    assert "web_company_discovery" not in source
    assert "target_company_enabled" not in source
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py
def test_company_discovery_runtime_module_is_removed() -> None:
    assert not (ROOT / "src/seektalent/runtime/company_discovery_runtime.py").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py -k company /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py`

Expected: FAIL because runtime still imports and executes company-discovery paths.

- [ ] **Step 3: Remove runtime company branch and target-company query logic**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/round_decision_runtime.py
async def continue_after_controller_decision(
    *,
    run_state: RunState,
    rescue_decision: RescueDecision,
    force_candidate_feedback_decision: Callable[..., SearchControllerDecision | None],
    # remove:
    # force_company_discovery_decision
    # select_anchor_only_after_failed_company_discovery
    ...
) -> tuple[RescueDecision, SearchControllerDecision | None]:
    if rescue_decision.selected_lane == "candidate_feedback":
        feedback_decision = force_candidate_feedback_decision(...)
        if feedback_decision is not None:
            return rescue_decision, feedback_decision
        rescue_decision = rescue_decision.model_copy(
            update={
                "selected_lane": "anchor_only",
                "skipped_lanes": [
                    *rescue_decision.skipped_lanes,
                    SkippedRescueLane(lane="candidate_feedback", reason="no_safe_feedback_term"),
                ],
            }
        )

    return rescue_decision, None
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_plan.py
def _duplicate_families(candidates: list[QueryTermCandidate]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in candidates:
        if item.family in seen:
            duplicates.append(item.family)
            continue
        seen.add(item.family)
    return unique_strings(duplicates)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py
# remove imports from seektalent.company_discovery and seektalent.runtime.company_discovery_runtime

def _choose_rescue_lane(...):
    return choose_rescue_lane(
        RescueInputs(
            stop_guidance=controller_context.stop_guidance,
            has_untried_reserve_family=has_untried_reserve_family,
            has_feedback_seed_resumes=has_feedback_seed_resumes,
            candidate_feedback_enabled=self.settings.candidate_feedback_enabled,
            candidate_feedback_attempted=run_state.retrieval_state.candidate_feedback_attempted,
            anchor_only_broaden_attempted=run_state.retrieval_state.anchor_only_broaden_attempted,
        )
    )


async def _continue_after_empty_feedback(...):
    return await round_decision_runtime.continue_after_controller_decision(
        run_state=run_state,
        rescue_decision=rescue_decision,
        force_candidate_feedback_decision=self._force_candidate_feedback_decision,
        ...
    )


def _collect_extra_model_specs(...) -> list[tuple[str, str | None, str | None]]:
    extra_model_specs: list[tuple[str, str | None, str | None]] = []
    if self.settings.candidate_feedback_enabled:
        extra_model_specs.append((self.settings.candidate_feedback_model, None, None))
    return extra_model_specs
```

- [ ] **Step 4: Run runtime absence tests to verify they pass**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py -k 'feedback or company' /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py`

Expected: PASS, and the active runtime no longer references `CompanyDiscoveryService`, `web_company_discovery`, or `target_company`.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/round_decision_runtime.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_plan.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py
git rm /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/company_discovery_runtime.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/query_injection.py
git commit -m "refactor: remove active company rescue runtime"
```

## Task 3: Delete The Company Discovery Package And Prompt Surface

**Files:**
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/*.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/company_discovery_*.md`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_tui.py`
- Delete: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_company_discovery.py`

- [ ] **Step 1: Write the failing prompt and package absence tests**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py
def test_required_prompts_exclude_company_discovery_names() -> None:
    assert "company_discovery_plan" not in REQUIRED_PROMPTS
    assert "company_discovery_extract" not in REQUIRED_PROMPTS
    assert "company_discovery_reduce" not in REQUIRED_PROMPTS
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_tui.py
def test_tui_does_not_render_company_discovery_event_branch() -> None:
    source = Path("/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/tui.py").read_text(encoding="utf-8")
    assert "company_discovery_completed" not in source
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py
def test_run_config_prompt_hashes_exclude_company_discovery_prompts(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    runtime.requirement_extractor = StubRequirementExtractor()
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
    finally:
        tracer.close()

    run_config = _read_json(_runtime_artifact(tracer.run_dir, "run_config"))

    assert "company_discovery_plan" not in run_config["prompt_hashes"]
    assert "company_discovery_extract" not in run_config["prompt_hashes"]
    assert "company_discovery_reduce" not in run_config["prompt_hashes"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py -k prompt /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_tui.py -k company_discovery /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py -k company_discovery`

Expected: FAIL because prompt registry, TUI, and runtime audit still mention company-discovery prompts and events.

- [ ] **Step 3: Remove package, prompt files, and active audit/UI references**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/tui.py
def _render_progress_lines(event: ProgressEvent) -> list[str]:
    payload = event.payload or {}
    if event.type == "round_completed":
        return _render_round_completed(event, payload)
    if event.type in {"requirements_started", "controller_started", "reflection_started", "finalizer_started"}:
        return [_thinking_line(event)]
    if event.type == "run_completed":
        return [f"[dim]业务 trace 完成：{escape(event.message)}[/]"]
    if event.type == "run_failed":
        return [f"[dim]·[/] 运行失败：{escape(event.message)}"]
    if event.type == "rescue_lane_completed":
        return _render_rescue_lane_completed(payload)
    if event.type == "search_started":
        return _render_search_progress(event, payload, query_key="planned_queries", trim_message=True)
    if event.type == "search_completed":
        return _render_search_progress(event, payload, query_key="executed_queries", trim_message=False)
    ...
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py
for logical_name in [
    "round.*.controller.controller_call",
    "round.*.scoring.tui_summary_call",
    "round.*.controller.repair_controller_call",
    "round.*.reflection.repair_reflection_call",
    "round.*.reflection.reflection_call",
]:
    for path in resolver.resolve_many(logical_name):
        ...
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py
assert "company_discovery_plan" not in run_config["settings"]
assert "company_discovery_model" not in run_config["settings"]
assert "has_bocha_key" not in run_config["settings"]
assert "company_discovery_plan" not in run_config["prompt_hashes"]
```

- [ ] **Step 4: Run tests to verify the active prompt and UI surface is clean**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py -k prompt /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_tui.py -k company_discovery /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py -k company_discovery`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/resources.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/tui.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_tui.py
git rm /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/__init__.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/bocha_provider.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/model_steps.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/models.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/page_reader.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/scheduler.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/company_discovery/service.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/company_discovery_plan.md /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/company_discovery_extract.md /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/company_discovery_reduce.md /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_company_discovery.py
git commit -m "refactor: delete company discovery package and prompts"
```

## Task 4: Preserve Historical Read Tolerance And PRF Company-Entity Rejection

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback_span_models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`

- [ ] **Step 1: Write the failing historical-tolerance and PRF-preservation tests**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py
def test_historical_company_artifacts_are_ignored_in_read_only_diagnostics(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    session.write_json("runtime.requirements_call", {"stage": "requirements", "prompt_name": "requirements"})
    session.register_path(
        "round.02.retrieval.company_discovery_plan_call",
        "rounds/02/retrieval/company_discovery_plan_call.json",
        content_type="application/json",
    )
    session.write_json("round.02.retrieval.company_discovery_plan_call", {"stage": "company_discovery_plan"})

    pressure = collect_llm_schema_pressure(session.root)

    assert isinstance(pressure, list)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback_span_models.py
def test_company_entity_rejection_still_exists_after_company_runtime_removal() -> None:
    family = PhraseFamily(
        family_id="company.bytedance",
        canonical_surface="ByteDance",
        candidate_term_type="company_entity",
        source_span_ids=["span-1"],
        positive_seed_support_count=1,
        negative_support_count=0,
        reject_reasons=["company_entity_rejected"],
    )

    assert "company_entity_rejected" in family.reject_reasons
```

- [ ] **Step 2: Run tests to verify they fail or expose active coupling**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py -k 'historical or company' /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback_span_models.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py -k company`

Expected: FAIL until diagnostics stop assuming company-discovery prompts are active while PRF company rejection remains untouched.

- [ ] **Step 3: Keep read-only tolerance and keep PRF company rejection**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py
for logical_name in [
    "round.*.controller.controller_call",
    "round.*.scoring.tui_summary_call",
    "round.*.controller.repair_controller_call",
    "round.*.reflection.repair_reflection_call",
    "round.*.reflection.reflection_call",
]:
    for path in resolver.resolve_many(logical_name):
        if not path.exists():
            continue
        pressure.append(_llm_schema_pressure_item(json.loads(path.read_text(encoding="utf-8"))))

# no special handling is needed for legacy company artifacts;
# the active collector simply stops requesting them.
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback_span_models.py
# keep the existing company-entity reject expectations intact
assert family.reject_reasons == ["company_entity_rejected"]
```

- [ ] **Step 4: Run tests to verify history is still readable and PRF semantics are unchanged**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py -k 'historical or company' /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback_span_models.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py -k company`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback_span_models.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py
git commit -m "test: preserve historical tolerance and prf company rejection"
```

## Task 5: Final Absence Checks, Documentation, And Benchmark Smoke Verification

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py`

- [ ] **Step 1: Add final absence and default-preservation tests**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py
FORBIDDEN_ACTIVE_PATTERNS = (
    "CompanyDiscoveryService",
    "web_company_discovery",
    "target_company_enabled",
    "company_discovery_enabled",
    'retrieval_role == "target_company"',
)


def test_active_runtime_has_no_company_discovery_references() -> None:
    checked_files = [
        ROOT / "src/seektalent/runtime/orchestrator.py",
        ROOT / "src/seektalent/runtime/rescue_router.py",
        ROOT / "src/seektalent/runtime/round_decision_runtime.py",
        ROOT / "src/seektalent/tui.py",
    ]
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_ACTIVE_PATTERNS:
            assert pattern not in text
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py
from seektalent.config import AppSettings


def test_company_removal_does_not_change_prf_defaults() -> None:
    settings = AppSettings()
    assert settings.prf_v1_5_mode == "shadow"
    assert settings.prf_model_backend == "legacy"
```

- [ ] **Step 2: Run absence tests to verify they fail**

Run: `uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py -k 'company or prf'`

Expected: FAIL until all active references are removed and PRF defaults remain intact.

- [ ] **Step 3: Update docs and final absence checks**

```markdown
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md
- Remove all references to company-discovery prompt call artifacts, company-discovery result artifacts, and company-discovery decision artifacts from the active output contract.
- Keep historical examples, if any remain, clearly labeled as legacy or archive-only.
```

- [ ] **Step 4: Run the regression suite and benchmark smoke verification**

Run:

```bash
uv run pytest -q /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router_config.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_rescue_router.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_tui.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback_span_models.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py
```

Expected: PASS.

Then run one smoke benchmark from each benchmark family again:

```bash
uv run seektalent benchmark --jds-file /tmp/seektalent-benchmark-smoke/agent_sample1.jsonl --env-file .env --benchmark-max-concurrency 1 --benchmark-run-retries 0 --benchmark-upload-retries 0 --disable-eval --json
uv run seektalent benchmark --jds-file /tmp/seektalent-benchmark-smoke/bigdata_sample1.jsonl --env-file .env --benchmark-max-concurrency 1 --benchmark-run-retries 0 --benchmark-upload-retries 0 --disable-eval --json
uv run seektalent benchmark --jds-file /tmp/seektalent-benchmark-smoke/llm_training_sample1.jsonl --env-file .env --benchmark-max-concurrency 1 --benchmark-run-retries 0 --benchmark-upload-retries 0 --disable-eval --json
```

Expected:

- no run enters `web_company_discovery`
- no run fails because of company-discovery web redirects or anti-bot pages
- benchmark summaries and child-artifact links remain intact

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md
git commit -m "refactor: finish company discovery removal"
```

## Self-Review

### Spec Coverage

- Remove active company runtime branch: covered by Tasks 1-3.
- Remove explicit target-company retrieval: covered by Tasks 1-2.
- Remove config/prompt/artifact/reporting surface: covered by Tasks 1 and 3.
- Preserve historical readability: covered by Task 4.
- Preserve `candidate_feedback` / `PRF v1.5` boundaries: covered by Tasks 4-5.
- Re-run benchmark smoke without company-discovery branch: covered by Task 5.

### Completeness Scan

- No unfinished markers or hand-wavy “fill this in later” instructions remain.
- Each task includes explicit files, concrete test code, exact commands, and commit commands.

### Type Consistency

- Active rescue lane values in the plan are consistent: `reserve_broaden`, `candidate_feedback`, `anchor_only`, `continue_controller`, `allow_stop`.
- Removed values are treated consistently as forbidden active vocabulary: `web_company_discovery`, `target_company`, `company_rescue`.
- `PRF v1.5` defaults remain `shadow + legacy` throughout the plan.
