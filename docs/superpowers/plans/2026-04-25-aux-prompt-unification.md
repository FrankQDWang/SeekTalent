# Auxiliary Prompt Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all runtime-used auxiliary system prompts onto the same prompt-file, prompt-hash, prompt-snapshot, and LLM-call-audit path as the primary runtime chain.

**Architecture:** Keep `WorkflowRuntime` as the single prompt assembly point. Auxiliary components should receive `LoadedPrompt` values just like the primary chain, while runtime emits prompt snapshots and `LLMCallSnapshot` artifacts for auxiliary calls without changing their business behavior.

**Tech Stack:** Python, PydanticAI, existing `PromptRegistry`, runtime tracer JSON artifacts, pytest

---

### Task 1: Add Auxiliary Prompt Files And Registry Coverage

**Files:**
- Create: `src/seektalent/prompts/tui_summary.md`
- Create: `src/seektalent/prompts/candidate_feedback.md`
- Create: `src/seektalent/prompts/company_discovery_plan.md`
- Create: `src/seektalent/prompts/company_discovery_extract.md`
- Create: `src/seektalent/prompts/company_discovery_reduce.md`
- Create: `src/seektalent/prompts/repair_requirements.md`
- Create: `src/seektalent/prompts/repair_controller.md`
- Create: `src/seektalent/prompts/repair_reflection.md`
- Modify: `src/seektalent/resources.py`
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing packaged-prompt coverage test**

```python
from seektalent.resources import REQUIRED_PROMPTS


def test_required_prompts_include_auxiliary_prompt_files() -> None:
    assert REQUIRED_PROMPTS == (
        "requirements",
        "controller",
        "scoring",
        "reflection",
        "finalize",
        "judge",
        "tui_summary",
        "candidate_feedback",
        "company_discovery_plan",
        "company_discovery_extract",
        "company_discovery_reduce",
        "repair_requirements",
        "repair_controller",
        "repair_reflection",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_required_prompts_include_auxiliary_prompt_files -v`
Expected: FAIL because `REQUIRED_PROMPTS` still lists only the six primary prompts.

- [ ] **Step 3: Add the auxiliary prompt files and expand required prompt inventory**

```python
# src/seektalent/resources.py
REQUIRED_PROMPTS = (
    "requirements",
    "controller",
    "scoring",
    "reflection",
    "finalize",
    "judge",
    "tui_summary",
    "candidate_feedback",
    "company_discovery_plan",
    "company_discovery_extract",
    "company_discovery_reduce",
    "repair_requirements",
    "repair_controller",
    "repair_reflection",
)
```

```md
<!-- src/seektalent/prompts/tui_summary.md -->
你是招聘业务助手。根据本轮已评分简历，写一段给非技术业务人员看的本轮简历质量短评。
要求：中文纯文本，不超过 80 字；概括整体质量、主要匹配点和明显风险；不要输出列表、Markdown、分数表；不要改变候选人评分或搜索决策。
```

```md
<!-- src/seektalent/prompts/candidate_feedback.md -->
Rank candidate-derived retrieval expansion terms. Only select terms from the provided candidate list.
Do not invent terms. Reject generic, company, school, location, degree, age, salary, and title-only terms.
```

```md
<!-- src/seektalent/prompts/company_discovery_plan.md -->
Generate bounded web search tasks for finding evidence-backed source companies.
```

```md
<!-- src/seektalent/prompts/company_discovery_extract.md -->
Extract target company candidates only when the provided evidence supports them.
```

```md
<!-- src/seektalent/prompts/company_discovery_reduce.md -->
Merge aliases, remove duplicates, and return a concise target company plan.
```

```md
<!-- src/seektalent/prompts/repair_requirements.md -->
Repair one RequirementExtractionDraft. Return complete JSON that preserves source intent and fixes the reported issue.
```

```md
<!-- src/seektalent/prompts/repair_controller.md -->
Repair one ControllerDecision. Return complete JSON that preserves intent and fixes the reported issue.
```

```md
<!-- src/seektalent/prompts/repair_reflection.md -->
Repair one ReflectionAdviceDraft. Return complete JSON that preserves intent and fixes the reported issue.
```

- [ ] **Step 4: Run the targeted tests**

Run: `uv run pytest tests/test_cli.py::test_required_prompts_include_auxiliary_prompt_files -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/prompts/*.md src/seektalent/resources.py tests/test_cli.py
git commit -m "Add auxiliary prompt files"
```

### Task 2: Rewire Auxiliary Components To Consume LoadedPrompt

**Files:**
- Modify: `src/seektalent/resume_quality.py`
- Modify: `src/seektalent/candidate_feedback/model_steps.py`
- Modify: `src/seektalent/company_discovery/model_steps.py`
- Modify: `src/seektalent/company_discovery/service.py`
- Modify: `src/seektalent/repair.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Test: `tests/test_resume_quality.py`
- Test: `tests/test_candidate_feedback.py`
- Test: `tests/test_company_discovery.py`
- Test: `tests/test_llm_fail_fast.py`

- [ ] **Step 1: Write failing constructor and prompt-source tests**

```python
from pathlib import Path

from seektalent.prompting import LoadedPrompt
from seektalent.resume_quality import ResumeQualityCommenter
from tests.settings_factory import make_settings


def test_resume_quality_commenter_uses_loaded_prompt() -> None:
    prompt = LoadedPrompt(name="tui_summary", path=Path("tui_summary.md"), content="summary prompt", sha256="hash")
    commenter = ResumeQualityCommenter(make_settings(), prompt)
    assert commenter.prompt is prompt
```

```python
from pathlib import Path

from seektalent.candidate_feedback.model_steps import CandidateFeedbackModelSteps
from seektalent.prompting import LoadedPrompt
from tests.settings_factory import make_settings


def test_candidate_feedback_model_steps_store_loaded_prompt() -> None:
    prompt = LoadedPrompt(name="candidate_feedback", path=Path("candidate_feedback.md"), content="feedback prompt", sha256="hash")
    steps = CandidateFeedbackModelSteps(make_settings(), prompt)
    assert steps.prompt is prompt
```

```python
from pathlib import Path

from seektalent.company_discovery.model_steps import CompanyDiscoveryModelSteps
from seektalent.prompting import LoadedPrompt
from tests.settings_factory import make_settings


def test_company_discovery_model_steps_store_named_prompts() -> None:
    prompts = {
        "company_discovery_plan": LoadedPrompt("company_discovery_plan", Path("a.md"), "plan", "h1"),
        "company_discovery_extract": LoadedPrompt("company_discovery_extract", Path("b.md"), "extract", "h2"),
        "company_discovery_reduce": LoadedPrompt("company_discovery_reduce", Path("c.md"), "reduce", "h3"),
    }
    steps = CompanyDiscoveryModelSteps(make_settings(), prompts)
    assert steps.prompts == prompts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resume_quality.py tests/test_candidate_feedback.py tests/test_company_discovery.py -k "loaded_prompt or store_named_prompts" -v`
Expected: FAIL because constructors still accept only `settings`.

- [ ] **Step 3: Make the minimal constructor and prompt-consumption changes**

```python
# src/seektalent/resume_quality.py
class ResumeQualityCommenter:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _build_agent(self) -> Agent[None, str]:
        return Agent(
            model=build_model(self.settings.effective_tui_summary_model),
            output_type=str,
            system_prompt=self.prompt.content,
            retries=0,
            output_retries=0,
        )
```

```python
# src/seektalent/candidate_feedback/model_steps.py
class CandidateFeedbackModelSteps:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _agent(self) -> Agent[None, CandidateFeedbackModelRanking]:
        return Agent(
            ...,
            system_prompt=self.prompt.content,
            ...,
        )
```

```python
# src/seektalent/company_discovery/model_steps.py
class CompanyDiscoveryModelSteps:
    def __init__(self, settings: AppSettings, prompts: dict[str, LoadedPrompt]) -> None:
        self.settings = settings
        self.prompts = prompts

    def _agent(self, prompt_name: str, output_type: type[Any]) -> Agent[None, Any]:
        prompt = self.prompts[prompt_name]
        return Agent(
            ...,
            system_prompt=prompt.content,
            ...,
        )
```

```python
# src/seektalent/repair.py
async def repair_requirement_draft(
    settings: AppSettings,
    prompt: LoadedPrompt,
    repair_prompt: LoadedPrompt,
    input_truth: InputTruth,
    draft: RequirementExtractionDraft,
    reason: str,
) -> tuple[RequirementExtractionDraft, ProviderUsageSnapshot | None]:
    return await _repair_with_model(
        settings,
        output_type=RequirementExtractionDraft,
        system_prompt=repair_prompt.content,
        user_prompt=user_prompt,
    )
```

```python
# src/seektalent/runtime/orchestrator.py
prompt_map = self.prompts.load_many([
    "requirements",
    "controller",
    "scoring",
    "reflection",
    "finalize",
    "judge",
    "tui_summary",
    "candidate_feedback",
    "company_discovery_plan",
    "company_discovery_extract",
    "company_discovery_reduce",
    "repair_requirements",
    "repair_controller",
    "repair_reflection",
])
self.resume_quality_commenter = ResumeQualityCommenter(settings, prompt_map["tui_summary"])
```

- [ ] **Step 4: Run the focused component tests**

Run: `uv run pytest tests/test_resume_quality.py tests/test_candidate_feedback.py tests/test_company_discovery.py tests/test_llm_fail_fast.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/resume_quality.py src/seektalent/candidate_feedback/model_steps.py src/seektalent/company_discovery/model_steps.py src/seektalent/company_discovery/service.py src/seektalent/repair.py src/seektalent/runtime/orchestrator.py tests/test_resume_quality.py tests/test_candidate_feedback.py tests/test_company_discovery.py tests/test_llm_fail_fast.py
git commit -m "Wire auxiliary prompts through registry"
```

### Task 3: Add Runtime Prompt Hashes, Snapshots, And Auxiliary LLM Call Artifacts

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/company_discovery/service.py`
- Modify: `src/seektalent/candidate_feedback/model_steps.py`
- Modify: `src/seektalent/resume_quality.py`
- Modify: `src/seektalent/repair.py`
- Test: `tests/test_runtime_audit.py`

- [ ] **Step 1: Write failing runtime-audit tests for auxiliary prompt visibility**

```python
def test_run_config_prompt_hashes_include_auxiliary_prompts(tmp_path, monkeypatch) -> None:
    artifacts = _run_fixture_runtime(tmp_path, monkeypatch)
    run_config = json.loads((artifacts.run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert "tui_summary" in run_config["prompt_hashes"]
    assert "candidate_feedback" in run_config["prompt_hashes"]
    assert "company_discovery_plan" in run_config["prompt_hashes"]
    assert "repair_controller" in run_config["prompt_hashes"]
```

```python
def test_runtime_writes_auxiliary_prompt_snapshots(tmp_path, monkeypatch) -> None:
    artifacts = _run_fixture_runtime(tmp_path, monkeypatch)
    assert (artifacts.run_dir / "prompts" / "tui_summary.md").exists()
    assert (artifacts.run_dir / "prompts" / "candidate_feedback.md").exists()
    assert (artifacts.run_dir / "prompts" / "company_discovery_plan.md").exists()
```

```python
def test_round_artifacts_include_tui_summary_call_snapshot(tmp_path, monkeypatch) -> None:
    artifacts = _run_fixture_runtime(tmp_path, monkeypatch)
    call = json.loads((artifacts.run_dir / "rounds" / "round_01" / "tui_summary_call.json").read_text(encoding="utf-8"))
    assert call["prompt_hash"]
    assert call["prompt_snapshot_path"] == "prompts/tui_summary.md"
    assert call["stage"] == "tui_summary"
```

- [ ] **Step 2: Run the targeted runtime tests to verify they fail**

Run: `uv run pytest tests/test_runtime_audit.py -k "auxiliary_prompts or tui_summary_call_snapshot" -v`
Expected: FAIL because auxiliary prompts are not in `prompt_hashes`, snapshot output, or round call artifacts.

- [ ] **Step 3: Add minimal runtime tracing for auxiliary calls**

```python
# src/seektalent/runtime/orchestrator.py
self._write_prompt_snapshots(tracer)

tracer.write_json(
    f"rounds/round_{round_no:02d}/tui_summary_call.json",
    self._build_llm_call_snapshot(
        stage="tui_summary",
        call_id=f"tui-summary-r{round_no:02d}",
        model_id=self.settings.effective_tui_summary_model,
        prompt_name="tui_summary",
        user_payload={"ROUND_RESUME_QUALITY_CONTEXT": payload},
        user_prompt_text=user_prompt_text,
        input_artifact_refs=[f"rounds/round_{round_no:02d}/scored_candidates.json"],
        output_artifact_refs=[],
        started_at=started_at,
        latency_ms=latency_ms,
        status="succeeded",
        structured_output=comment,
        input_summary=f"round={round_no}; candidates={len(payload['candidates'])}",
        output_summary=comment,
        provider_usage=provider_usage,
    ),
)
```

```python
# src/seektalent/company_discovery/service.py
return CompanyDiscoveryResult(
    ...,
    model_call_audit={
        "plan": plan_call_snapshot,
        "extract": extract_call_snapshot,
        "reduce": reduce_call_snapshot,
    },
)
```

```python
# src/seektalent/runtime/orchestrator.py
tracer.write_json(f"{prefix}/company_discovery_plan_call.json", result.model_call_audit["plan"])
tracer.write_json(f"{prefix}/company_discovery_extract_call.json", result.model_call_audit["extract"])
tracer.write_json(f"{prefix}/company_discovery_reduce_call.json", result.model_call_audit["reduce"])
```

```python
# src/seektalent/repair.py
return repaired_output, usage, LLMCallSnapshot(
    stage="controller_repair",
    call_id="controller-repair-r01",
    model_id=model_id,
    prompt_hash=repair_prompt.sha256,
    prompt_snapshot_path="prompts/repair_controller.md",
    ...,
)
```

- [ ] **Step 4: Run the runtime audit subset**

Run: `uv run pytest tests/test_runtime_audit.py -k "auxiliary_prompts or tui_summary_call_snapshot or company_discovery_plan_call or repair_call" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/company_discovery/service.py src/seektalent/candidate_feedback/model_steps.py src/seektalent/resume_quality.py src/seektalent/repair.py tests/test_runtime_audit.py
git commit -m "Trace auxiliary prompt calls"
```

### Task 4: Tighten Regression Coverage And Run Final Verification

**Files:**
- Modify: `tests/test_requirement_extraction.py`
- Modify: `tests/test_controller_contract.py`
- Modify: `tests/test_reflection_contract.py`
- Modify: `tests/test_resume_quality.py`
- Modify: `tests/test_candidate_feedback.py`
- Modify: `tests/test_company_discovery.py`
- Modify: `tests/test_runtime_audit.py`

- [ ] **Step 1: Add regression tests for repair prompt routing**

```python
async def fake_repair(settings, prompt, repair_prompt, input_truth, draft, reason):  # noqa: ANN001
    assert prompt.name == "requirements"
    assert repair_prompt.name == "repair_requirements"
    return draft, None, None
```

```python
async def fake_repair_controller_decision(settings, prompt, repair_prompt, source_user_prompt, decision, reason):  # noqa: ANN001
    assert prompt.name == "controller"
    assert repair_prompt.name == "repair_controller"
    return decision, None, None
```

```python
async def fake_repair_reflection_draft(settings, prompt, repair_prompt, source_user_prompt, draft, reason):  # noqa: ANN001
    assert prompt.name == "reflection"
    assert repair_prompt.name == "repair_reflection"
    return draft, None, None
```

- [ ] **Step 2: Run the regression slice and verify it fails first where signatures changed**

Run: `uv run pytest tests/test_requirement_extraction.py tests/test_controller_contract.py tests/test_reflection_contract.py -k "repair_prompt" -v`
Expected: FAIL until tests and call signatures align.

- [ ] **Step 3: Update the tests and finish any remaining signature or artifact mismatches**

```python
assert call["prompt_snapshot_path"] == "prompts/repair_reflection.md"
assert call["prompt_hash"] == runtime.prompts.prompt_hashes()["repair_reflection"]
assert call["model_id"] == settings.structured_repair_model
```

- [ ] **Step 4: Run the full verification set**

Run: `uv run pytest tests/test_cli.py tests/test_resume_quality.py tests/test_candidate_feedback.py tests/test_company_discovery.py tests/test_requirement_extraction.py tests/test_controller_contract.py tests/test_reflection_contract.py tests/test_runtime_audit.py tests/test_llm_fail_fast.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py tests/test_resume_quality.py tests/test_candidate_feedback.py tests/test_company_discovery.py tests/test_requirement_extraction.py tests/test_controller_contract.py tests/test_reflection_contract.py tests/test_runtime_audit.py tests/test_llm_fail_fast.py
git commit -m "Cover auxiliary prompt unification"
```

## Self-Review

Spec coverage:
- Prompt files: Task 1
- `PromptRegistry` wiring: Task 2
- runtime prompt hashes and snapshots: Task 3
- auxiliary `LLMCallSnapshot` coverage: Task 3
- repair prompt unification and regression safety: Task 4

Placeholder scan:
- No `TODO`, `TBD`, or deferred implementation markers remain.

Type consistency:
- All tasks use the same prompt names:
  `tui_summary`, `candidate_feedback`, `company_discovery_plan`, `company_discovery_extract`, `company_discovery_reduce`, `repair_requirements`, `repair_controller`, `repair_reflection`.
- Repair helpers consistently receive both the source prompt and the repair prompt.
