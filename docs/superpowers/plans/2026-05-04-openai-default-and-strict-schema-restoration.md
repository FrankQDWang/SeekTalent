# OpenAI Default And Strict Schema Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore OpenAI Chat Completions-compatible as the repository default text-LLM protocol, bring strict native structured output back to the default structured stages, and replace the remaining `candidate_feedback_model_id` default from `qwen3.5-flash` to `deepseek-v4-flash` without changing active candidate-feedback rescue behavior.

**Architecture:** Keep the canonical dual-protocol surface from the April rollout, but flip the default protocol back to Bailian OpenAI Chat Completions-compatible and make structured-output behavior protocol-sensitive again. The work is deliberately narrow: update defaults, update the Bailian capability matrix, update the env templates/docs that expose these defaults, and prove the active rescue path still does not instantiate the dormant `CandidateFeedbackModelSteps` helper.

**Tech Stack:** Python 3.12, Pydantic Settings, pydantic-ai (`OpenAIChatModel`, `AnthropicModel`, `NativeOutput`, `PromptedOutput`), pytest

---

## File Map

- **Modify:** `src/seektalent/config.py`
  - Repository-default text-LLM protocol, endpoint kind, and `candidate_feedback_model_id`.
- **Modify:** `src/seektalent/llm.py`
  - Bailian capability matrix and protocol-sensitive structured-output behavior.
- **Modify:** `.env.example`
  - Checked-in starter environment template.
- **Modify:** `src/seektalent/default.env`
  - Packaged/default environment template.
- **Modify:** `.env`
  - Local development defaults in this workspace.
- **Modify:** `docs/configuration.md`
  - User-facing configuration table for candidate-feedback model config.
- **Modify:** `tests/test_llm_provider_config.py`
  - Default settings assertions and protocol-sensitive structured-output assertions.
- **Modify:** `tests/test_cli.py`
  - `init` template expectations.
- **Modify:** `tests/test_rescue_router_config.py`
  - Candidate-feedback config defaults.
- **Modify:** `tests/test_runtime_audit.py`
  - Public run-config serialization expectations.
- **Modify:** `tests/test_candidate_feedback.py`
  - Dormant helper stage-config expectations.
- **Modify:** `tests/test_runtime_state_flow.py`
  - Guard that active rescue flow still does not instantiate `CandidateFeedbackModelSteps`.

### Task 1: Flip Canonical Defaults And Sync Templates

**Files:**
- Modify: `tests/test_llm_provider_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_rescue_router_config.py`
- Modify: `tests/test_runtime_audit.py`
- Modify: `src/seektalent/config.py`
- Modify: `.env.example`
- Modify: `src/seektalent/default.env`
- Modify: `.env`

- [ ] **Step 1: Write the failing default-value tests**

```python
def test_canonical_text_llm_defaults_use_dual_protocol_surface() -> None:
    settings = make_settings()

    assert settings.text_llm_protocol_family == "openai_chat_completions_compatible"
    assert settings.text_llm_provider_label == "bailian"
    assert settings.text_llm_endpoint_kind == "bailian_openai_chat_completions"
    assert settings.text_llm_endpoint_region == "beijing"
    assert settings.candidate_feedback_model_id == "deepseek-v4-flash"


def test_rescue_feature_defaults() -> None:
    settings = make_settings()

    assert settings.candidate_feedback_enabled is True
    assert settings.candidate_feedback_model_id == "deepseek-v4-flash"
    assert settings.candidate_feedback_reasoning_effort == "off"
```

Also update the existing `test_init_writes_env_template` assertions in `tests/test_cli.py` and the run-config assertion in `tests/test_runtime_audit.py`:

```python
assert "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible" in text
assert "SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=bailian_openai_chat_completions" in text
assert run_config["settings"]["candidate_feedback_model_id"] == "deepseek-v4-flash"
```

- [ ] **Step 2: Run the focused defaults tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/test_llm_provider_config.py::test_canonical_text_llm_defaults_use_dual_protocol_surface \
  tests/test_rescue_router_config.py::test_rescue_feature_defaults \
  tests/test_cli.py::test_init_writes_env_template \
  tests/test_runtime_audit.py::test_run_config_excludes_company_discovery_settings
```

Expected: FAIL because the repository still defaults to Anthropic-compatible and still emits `qwen3.5-flash`.

- [ ] **Step 3: Apply the minimal default/config changes**

Update `src/seektalent/config.py`:

```python
text_llm_protocol_family: TextLLMProtocolFamily = "openai_chat_completions_compatible"
text_llm_provider_label: TextLLMProviderLabel = "bailian"
text_llm_endpoint_kind: TextLLMEndpointKind = "bailian_openai_chat_completions"
text_llm_endpoint_region: TextLLMEndpointRegion = "beijing"
...
candidate_feedback_model_id: str = "deepseek-v4-flash"
```

Sync the same values in the env files:

```dotenv
SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible
SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=bailian_openai_chat_completions
SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=beijing
SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID=deepseek-v4-flash
```

- [ ] **Step 4: Re-run the focused defaults tests**

Run:

```bash
uv run pytest -q \
  tests/test_llm_provider_config.py::test_canonical_text_llm_defaults_use_dual_protocol_surface \
  tests/test_rescue_router_config.py::test_rescue_feature_defaults \
  tests/test_cli.py::test_init_writes_env_template \
  tests/test_runtime_audit.py::test_run_config_excludes_company_discovery_settings
```

Expected: PASS.

- [ ] **Step 5: Commit the defaults/template flip**

```bash
git add \
  src/seektalent/config.py \
  .env.example \
  src/seektalent/default.env \
  .env \
  tests/test_llm_provider_config.py \
  tests/test_cli.py \
  tests/test_rescue_router_config.py \
  tests/test_runtime_audit.py
git commit -m "feat: restore openai default text llm settings"
```

### Task 2: Restore Strict Native Output On The Default OpenAI Path

**Files:**
- Modify: `tests/test_llm_provider_config.py`
- Modify: `src/seektalent/llm.py`

- [ ] **Step 1: Write the failing structured-output tests**

Add targeted protocol-sensitive tests to `tests/test_llm_provider_config.py`:

```python
from pydantic_ai import NativeOutput, PromptedOutput


class _FakeStructuredModel:
    profile = type("Profile", (), {"supports_json_schema_output": True})()


def test_default_openai_structured_stages_use_native_strict_output() -> None:
    settings = make_settings()
    fake_model = _FakeStructuredModel()

    for stage_name in [
        "requirements",
        "controller",
        "reflection",
        "finalize",
        "judge",
        "structured_repair",
    ]:
        config = resolve_stage_model_config(settings, stage=stage_name)
        assert resolve_structured_output_mode(config) == "native_json_schema"
        assert isinstance(build_output_spec(config, fake_model, dict), NativeOutput)


def test_anthropic_structured_stages_remain_prompted_output() -> None:
    settings = make_settings(
        text_llm_protocol_family="anthropic_messages_compatible",
        text_llm_endpoint_kind="bailian_anthropic_messages",
        text_llm_endpoint_region="beijing",
    )
    fake_model = _FakeStructuredModel()

    for stage_name in [
        "requirements",
        "controller",
        "reflection",
        "finalize",
        "judge",
        "structured_repair",
    ]:
        config = resolve_stage_model_config(settings, stage=stage_name)
        assert resolve_structured_output_mode(config) == "prompted_json"
        assert isinstance(build_output_spec(config, fake_model, dict), PromptedOutput)
```

Also replace the old default expectation:

```python
def test_bailian_deepseek_v4_defaults_to_native_json_schema_mode() -> None:
    stage = resolve_stage_model_config(make_settings(), stage="controller")
    assert resolve_structured_output_mode(stage) == "native_json_schema"
```

- [ ] **Step 2: Run the structured-output tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/test_llm_provider_config.py::test_default_openai_structured_stages_use_native_strict_output \
  tests/test_llm_provider_config.py::test_anthropic_structured_stages_remain_prompted_output \
  tests/test_llm_provider_config.py::test_bailian_deepseek_v4_defaults_to_native_json_schema_mode
```

Expected: FAIL because the current Bailian DeepSeek V4 capability map still resolves both protocol families to `prompted_json`.

- [ ] **Step 3: Update the Bailian capability matrix**

In `src/seektalent/llm.py`, change only the OpenAI-compatible DeepSeek V4 entries:

```python
(
    "bailian",
    "openai_chat_completions_compatible",
    "bailian_openai_chat_completions",
    "beijing",
    "deepseek-v4-pro",
): TextLLMCapability(
    structured_output_mode="native_json_schema",
    supports_thinking=True,
    supports_reasoning_effort=True,
    allowed_reasoning_efforts=frozenset({"high", "max"}),
),
(
    "bailian",
    "openai_chat_completions_compatible",
    "bailian_openai_chat_completions",
    "beijing",
    "deepseek-v4-flash",
): TextLLMCapability(
    structured_output_mode="native_json_schema",
    supports_thinking=True,
    supports_reasoning_effort=True,
    allowed_reasoning_efforts=frozenset({"high", "max"}),
),
```

Keep the Anthropic-compatible entries as:

```python
structured_output_mode="prompted_json"
```

Do not add new protocol families, new toggles, or stage-specific exceptions here.

- [ ] **Step 4: Re-run the structured-output tests**

Run:

```bash
uv run pytest -q \
  tests/test_llm_provider_config.py::test_default_openai_structured_stages_use_native_strict_output \
  tests/test_llm_provider_config.py::test_anthropic_structured_stages_remain_prompted_output \
  tests/test_llm_provider_config.py::test_bailian_deepseek_v4_defaults_to_native_json_schema_mode
```

Expected: PASS.

- [ ] **Step 5: Commit the structured-output restoration**

```bash
git add src/seektalent/llm.py tests/test_llm_provider_config.py
git commit -m "feat: restore strict schema on default openai path"
```

### Task 3: Keep Candidate Feedback Behavior Unchanged While Updating Its Config Surface

**Files:**
- Modify: `tests/test_candidate_feedback.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_runtime_audit.py`

- [ ] **Step 1: Write the failing drift guards**

Update the dormant-helper config expectation in `tests/test_candidate_feedback.py`:

```python
stage_config = ResolvedTextModelConfig(
    stage="candidate_feedback",
    protocol_family="openai_chat_completions_compatible",
    provider_label="bailian",
    endpoint_kind="bailian_openai_chat_completions",
    endpoint_region="beijing",
    base_url="https://example.com/v1",
    api_key="test-key",
    model_id="deepseek-v4-flash",
    structured_output_mode="native_json_schema",
    thinking_mode=False,
    reasoning_effort="off",
    openai_prompt_cache_enabled=False,
    openai_prompt_cache_retention=None,
)
```

Add a runtime drift test to `tests/test_runtime_state_flow.py`:

```python
def test_candidate_feedback_lane_does_not_instantiate_model_steps(monkeypatch, tmp_path: Path) -> None:
    class _Boom:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("CandidateFeedbackModelSteps should stay dormant on the active rescue path.")

    monkeypatch.setattr(
        "seektalent.candidate_feedback.model_steps.CandidateFeedbackModelSteps",
        _Boom,
    )

    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        candidate_feedback_enabled=True,
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = {
            "fit-1": _fit_scorecard(
                "fit-1",
                overall_score=90,
                must_have_match_score=82,
                risk_score=15,
                reasoning_summary="Built LangGraph workflow orchestration.",
                evidence=["LangGraph workflow orchestration and tool calling."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph", "tool calling"],
            ),
            "fit-2": _fit_scorecard(
                "fit-2",
                overall_score=88,
                must_have_match_score=80,
                risk_score=18,
                reasoning_summary="Used LangGraph for Agent workflow.",
                evidence=["LangGraph and RAG workflow implementation."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph"],
            ),
        }
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=lambda _: None))
    finally:
        tracer.close()

    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "candidate_feedback"
```

- [ ] **Step 2: Run the candidate-feedback drift tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/test_candidate_feedback.py::test_candidate_feedback_model_steps_use_resolved_stage_config \
  tests/test_runtime_state_flow.py::test_candidate_feedback_lane_does_not_instantiate_model_steps \
  tests/test_runtime_audit.py::test_run_config_excludes_company_discovery_settings
```

Expected: at least one failure because the helper test still expects Anthropic/Qwen defaults.

- [ ] **Step 3: Update only the expectations, not the rescue behavior**

Keep the active rescue path untouched. Only align the tests and serialized config expectations with the new defaults:

```python
assert run_config["settings"]["candidate_feedback_model_id"] == "deepseek-v4-flash"
```

Do not import or instantiate `CandidateFeedbackModelSteps` from the active rescue runtime.

- [ ] **Step 4: Re-run the candidate-feedback drift tests**

Run:

```bash
uv run pytest -q \
  tests/test_candidate_feedback.py::test_candidate_feedback_model_steps_use_resolved_stage_config \
  tests/test_runtime_state_flow.py::test_candidate_feedback_lane_does_not_instantiate_model_steps \
  tests/test_runtime_audit.py::test_run_config_excludes_company_discovery_settings
```

Expected: PASS.

- [ ] **Step 5: Commit the candidate-feedback config cleanup**

```bash
git add \
  tests/test_candidate_feedback.py \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_audit.py
git commit -m "test: lock candidate feedback behavior during config cleanup"
```

### Task 4: Update User-Facing Docs And Run The Regression Slice

**Files:**
- Modify: `docs/configuration.md`

- [ ] **Step 1: Update the configuration docs**

Replace the stale candidate-feedback row:

```md
| `SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID` | `deepseek-v4-flash` | Reserved for dormant model-ranked candidate feedback steps; the active rescue lane remains deterministic. |
```

Do not reintroduce `SEEKTALENT_CANDIDATE_FEEDBACK_MODEL` or old provider-prefixed defaults into docs.

- [ ] **Step 2: Run the focused regression suite**

Run:

```bash
uv run pytest -q \
  tests/test_llm_provider_config.py \
  tests/test_cli.py \
  tests/test_rescue_router_config.py \
  tests/test_runtime_audit.py \
  tests/test_candidate_feedback.py \
  tests/test_runtime_state_flow.py \
  tests/test_requirement_extraction.py \
  tests/test_controller_contract.py \
  tests/test_reflection_contract.py \
  tests/test_finalizer_contract.py \
  tests/test_evaluation.py
```

Expected: PASS.

- [ ] **Step 3: Run syntax and diff hygiene checks**

Run:

```bash
uv run python -m py_compile src/seektalent/config.py src/seektalent/llm.py
git diff --check
```

Expected: both commands succeed with no output.

- [ ] **Step 4: Commit the docs sync and verification-ready state**

```bash
git add docs/configuration.md
git commit -m "docs: sync openai defaults and candidate feedback model id"
```

## Self-Review

- **Spec coverage:** This plan covers the default protocol flip, env sync, strict-schema restoration on the default OpenAI path, Anthropic prompted preservation, `candidate_feedback_model_id` cleanup, and an explicit guard that active candidate-feedback rescue behavior stays unchanged.
- **Placeholder scan:** No `TODO`, `TBD`, “handle appropriately”, or “similar to above” placeholders remain.
- **Type consistency:** The plan uses the current canonical names from the codebase: `text_llm_protocol_family`, `text_llm_endpoint_kind`, `candidate_feedback_model_id`, `ResolvedTextModelConfig`, `NativeOutput`, and `PromptedOutput`.

## Notes

- Keep the diff surgical. This plan intentionally does **not** reopen the April dual-protocol design, remove Anthropic support, or reconnect dormant candidate-feedback model steps.
- Preserve the hard-cut config surface from the April rollout. Do not restore any `openai-chat:` or `openai-responses:` config path.
