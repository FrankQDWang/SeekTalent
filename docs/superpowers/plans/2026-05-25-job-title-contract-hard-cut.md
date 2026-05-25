# Job Title Contract Hard Cut Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `role_title` from active SeekTalent runtime contracts and make `job_title` the only role-title field.

**Architecture:** This is a schema hard cut, not a compatibility migration. Core Pydantic models move to `job_title`; normalization derives it from `InputTruth.job_title`; downstream prompt/context/diagnostic/provider helpers follow the same field name. Old requirement-cache payloads are invalidated by a cache schema bump instead of being read through fallback code.

**Tech Stack:** Python 3.12, Pydantic, pydantic-ai, pytest, existing SeekTalent runtime and Workbench bridge modules.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-25-job-title-contract-hard-cut-design.md`

## Execution Notes

- Execute this in a clean worktree or clean Codex context.
- Do not run CEO review in this slice.
- Do not implement Workbench requirement-flow consolidation in this slice.
- Do not add `role_title` aliases, properties, validators, fallback readers, or dual-output fields.
- Do not support old requirement-cache payloads. Bump the cache key version.
- Do not delete UI or legacy APIs in this slice.
- Keep changes narrowly tied to `role_title` -> `job_title`.

## File Map

Modify core schemas and normalization:

- `src/seektalent/models.py`
  - Remove `RequirementExtractionDraft.role_title`.
  - Rename `RequirementSheet.role_title` to `job_title`.
  - Rename `RequirementDigest.role_title` to `job_title`.
  - Rename `ScoringPolicy.role_title` to `job_title`.

- `src/seektalent/requirements/normalization.py`
  - Derive `job_title` from the function argument.
  - Build requirement digest and scoring policy with `job_title`.

- `src/seektalent/requirements/extractor.py`
  - Bump requirements cache key version.
  - Use `job_title` in LLM output summaries.

- `src/seektalent/prompts/requirements.md`
  - Remove the instruction requiring `role_title`.
  - State that `job_title` is provided input truth and must not be re-output as `role_title`.

Modify active runtime and LLM input surfaces:

- `src/seektalent/controller/react_controller.py`
- `src/seektalent/scoring/scorer.py`
- `src/seektalent/reflection/critic.py`
- `src/seektalent/runtime/requirements_runtime.py`
- `src/seektalent/runtime/runtime_diagnostics.py`
- `src/seektalent/runtime/orchestrator.py`
- `src/seektalent/retrieval/query_identity.py`

Modify active provider and feedback helpers that currently use `role_title` to mean job title:

- `src/seektalent/providers/cts/filter_projection.py`
- `src/seektalent/runtime/source_filters.py`
- `src/seektalent/providers/liepin/card_policy.py`
- `src/seektalent/providers/liepin/runtime_lane.py`
- `src/seektalent/candidate_feedback/llm_prf.py`
- `src/seektalent/candidate_feedback/model_steps.py`
- `src/seektalent/candidate_feedback/llm_prf_bakeoff.py`

Modify direct core-model constructors outside core runtime:

- `src/seektalent_ui/runtime_bridge.py`

Update tests:

- `tests/test_requirement_extraction.py`
- `tests/test_controller_contract.py`
- `tests/test_reflection_contract.py`
- `tests/test_runtime_audit.py`
- `tests/test_llm_input_prompts.py`
- `tests/test_runtime_state_flow.py`
- `tests/test_candidate_feedback.py`
- `tests/test_query_identity.py`
- `tests/test_llm_prf.py`
- `tests/test_llm_prf_bakeoff.py`
- `tests/test_liepin_card_policy.py`
- `tests/test_filter_projection.py`
- `tests/test_runtime_source_adapter_boundary.py`
- `tests/test_workbench_api.py`
- Any additional test that fails only because it still constructs or asserts `role_title`.

---

### Task 1: Add Contract Tests That Fail On `role_title`

**Files:**
- Modify: `tests/test_requirement_extraction.py`
- Modify: `tests/test_llm_input_prompts.py`
- Test: `tests/test_requirement_extraction.py`
- Test: `tests/test_llm_input_prompts.py`

- [ ] **Step 1: Add model-schema hard-cut tests**

Add these tests near the top of `tests/test_requirement_extraction.py`, after `_valid_requirement_draft()` or near existing schema/normalization tests:

```python
def test_requirement_contract_uses_job_title_only() -> None:
    legacy_title_key = "_".join(("role", "title"))
    draft_schema = RequirementExtractionDraft.model_json_schema()
    sheet_schema = RequirementSheet.model_json_schema()

    assert legacy_title_key not in draft_schema.get("properties", {})
    assert legacy_title_key not in sheet_schema.get("properties", {})
    assert "job_title" in sheet_schema.get("properties", {})


def test_requirement_draft_rejects_legacy_title_field() -> None:
    legacy_title_key = "_".join(("role", "title"))

    with pytest.raises(ValidationError):
        RequirementExtractionDraft.model_validate(
            {
                legacy_title_key: "Senior Python Engineer",
                "title_anchor_terms": ["Python"],
                "title_anchor_rationale": "title has one stable technical anchor",
                "jd_query_terms": ["retrieval"],
                "role_summary": "Build retrieval systems.",
                "must_have_capabilities": ["Python"],
                "scoring_rationale": "Prioritize Python retrieval experience.",
            }
        )


def test_normalize_requirement_draft_sets_job_title_from_input_truth() -> None:
    draft = RequirementExtractionDraft(
        title_anchor_terms=["Python"],
        title_anchor_rationale="title has one stable technical anchor",
        jd_query_terms=["retrieval"],
        role_summary="Build retrieval systems.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python retrieval experience.",
    )

    sheet = normalize_requirement_draft(draft, job_title="Senior Python Engineer")

    legacy_title_key = "_".join(("role", "title"))
    assert sheet.job_title == "Senior Python Engineer"
    assert legacy_title_key not in sheet.model_dump(mode="json")
```

Also add this import if it is not already present:

```python
from pydantic import ValidationError
```

- [ ] **Step 2: Add prompt hard-cut tests**

Add or update a requirements prompt test in `tests/test_llm_input_prompts.py`:

```python
def test_requirements_prompt_does_not_request_legacy_title_output() -> None:
    prompt = PromptRegistry(package_prompt_dir()).load("requirements").content

    legacy_title_key = "_".join(("role", "title"))
    assert legacy_title_key not in prompt
    assert "job_title" in prompt
    assert "title_anchor_terms" in prompt
```

If `PromptRegistry` or `package_prompt_dir` is already imported in that file, reuse the existing imports. If not, add:

```python
from seektalent.prompting import PromptRegistry
from seektalent.resources import package_prompt_dir
```

- [ ] **Step 3: Run the new tests and verify they fail for the right reason**

Run:

```bash
uv run pytest tests/test_requirement_extraction.py::test_requirement_contract_uses_job_title_only tests/test_requirement_extraction.py::test_requirement_draft_rejects_legacy_title_field tests/test_requirement_extraction.py::test_normalize_requirement_draft_sets_job_title_from_input_truth tests/test_llm_input_prompts.py::test_requirements_prompt_does_not_request_legacy_title_output -v
```

Expected before implementation:

- At least one failure because `RequirementExtractionDraft` still requires `role_title`.
- At least one failure because `RequirementSheet` still exposes `role_title`.
- The prompt test fails because `src/seektalent/prompts/requirements.md` still mentions `role_title`.

- [ ] **Step 4: Commit the failing contract tests if your workflow commits red first**

```bash
git add tests/test_requirement_extraction.py tests/test_llm_input_prompts.py
git commit -m "test: lock job title requirement contract"
```

If the execution workflow avoids red commits, keep the files unstaged and continue to Task 2.

---

### Task 2: Change Core Requirement Models, Normalization, Prompt, And Cache Key

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/requirements/normalization.py`
- Modify: `src/seektalent/requirements/extractor.py`
- Modify: `src/seektalent/prompts/requirements.md`
- Test: `tests/test_requirement_extraction.py`
- Test: `tests/test_llm_input_prompts.py`

- [ ] **Step 1: Update `RequirementExtractionDraft`**

In `src/seektalent/models.py`, change `RequirementExtractionDraft` so it starts like this. Do not leave `role_title` in the class.

```python
class RequirementExtractionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_anchor_terms: list[str] = Field(
        min_length=1,
        max_length=2,
        description="One or two stable searchable title anchors extracted from the job title.",
    )
    title_anchor_rationale: str = Field(
        min_length=1,
        description="Short explanation for why these title anchors best capture the searchable job title.",
    )
    jd_query_terms: list[str] = Field(
        default_factory=list,
        description="High-signal searchable terms extracted from the JD only, excluding all title anchors.",
    )
    notes_query_terms: list[str] = Field(
        default_factory=list,
        description="High-signal searchable terms extracted from the notes only, excluding all title anchors.",
    )
    role_summary: str = Field(min_length=1, description="Concise business summary of the role scope.")
    must_have_capabilities: list[str] = Field(default_factory=list, description="Critical capabilities required for fit.")
    preferred_capabilities: list[str] = Field(default_factory=list, description="Nice-to-have capabilities that strengthen fit.")
    exclusion_signals: list[str] = Field(default_factory=list, description="Signals that make the candidate unsuitable.")
```

Keep the existing remaining fields after `exclusion_signals`.

- [ ] **Step 2: Update requirement sheet, digest, and scoring policy fields**

In `src/seektalent/models.py`, change these field names:

```python
class RequirementSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    title_anchor_terms: list[str] = Field(min_length=1, max_length=2)
    title_anchor_rationale: str = Field(min_length=1)
    role_summary: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    preferred_capabilities: list[str] = Field(default_factory=list)
    exclusion_signals: list[str] = Field(default_factory=list)
    hard_constraints: HardConstraintSlots = Field(default_factory=HardConstraintSlots)
    preferences: PreferenceSlots = Field(default_factory=PreferenceSlots)
    initial_query_term_pool: list[QueryTermCandidate] = Field(default_factory=list)
    scoring_rationale: str
```

```python
class RequirementDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    role_summary: str
    top_must_have_capabilities: list[str] = Field(default_factory=list)
    top_preferences: list[str] = Field(default_factory=list)
    hard_constraint_summary: list[str] = Field(default_factory=list)
```

```python
class ScoringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    role_summary: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    preferred_capabilities: list[str] = Field(default_factory=list)
    exclusion_signals: list[str] = Field(default_factory=list)
    hard_constraints: HardConstraintSlots = Field(default_factory=HardConstraintSlots)
    preferences: PreferenceSlots = Field(default_factory=PreferenceSlots)
    scoring_rationale: str
```

Do not add this kind of compatibility property:

```python
@property
def role_title(self) -> str:
    return self.job_title
```

- [ ] **Step 3: Update normalization to write `job_title`**

In `src/seektalent/requirements/normalization.py`, replace local `role_title` naming with `normalized_job_title`:

```python
def normalize_requirement_draft(draft: RequirementExtractionDraft, *, job_title: str) -> RequirementSheet:
    normalized_job_title = _clean_text(job_title)
    title_anchor_terms = _normalize_title_anchor_terms(draft.title_anchor_terms)
    title_anchor_rationale = _clean_text(draft.title_anchor_rationale)
    role_summary = _clean_text(draft.role_summary)
    scoring_rationale = _clean_text(draft.scoring_rationale)
    if not normalized_job_title:
        raise ValueError("job_title must not be empty after normalization")
```

In the `RequirementSheet(...)` construction, use:

```python
        job_title=normalized_job_title,
```

In the query compiler call, use:

```python
            job_title=normalized_job_title,
```

Update `build_requirement_digest()`:

```python
    return RequirementDigest(
        job_title=requirement_sheet.job_title,
        role_summary=requirement_sheet.role_summary,
        top_must_have_capabilities=requirement_sheet.must_have_capabilities[:4],
        top_preferences=top_preferences[:4],
        hard_constraint_summary=summary,
    )
```

Update `build_scoring_policy()`:

```python
            "job_title": requirement_sheet.job_title,
```

- [ ] **Step 4: Update requirements extractor cache key and output summaries**

In `src/seektalent/requirements/extractor.py`, bump the cache key schema:

```python
"requirement_extraction_draft.v3",
```

Update output-summary code in runtime if it references `role_title` output. The requirements draft no longer has any title field, but the job title is still important diagnostic context. Summaries that include the job title must read it from the LLM call input payload / `InputTruth.job_title`, not from the `RequirementExtractionDraft` output.

```python
return f"job_title={job_title!r}; title_anchors={len(output.get('title_anchor_terms') or [])}; jd_terms={len(output.get('jd_query_terms') or [])}"
```

- [ ] **Step 5: Update the requirements prompt**

In `src/seektalent/prompts/requirements.md`, remove:

```text
- Set `role_title` to the normalized job title.
```

Add this rule under Hard Rules without spelling the old field name in the prompt text:

```text
- Treat the provided `job_title` as the canonical job title input. Do not output a separate title field.
```

Keep existing title-anchor guidance intact.

- [ ] **Step 6: Update requirement-extraction tests and fixtures**

In `tests/test_requirement_extraction.py`, update every `RequirementExtractionDraft(...)` fixture to remove `role_title=...`.

Example before:

```python
RequirementExtractionDraft(
    role_title="Senior Python Engineer",
    title_anchor_terms=["Python"],
    title_anchor_rationale="title has one stable technical anchor",
    jd_query_terms=["Retrieval Systems"],
    role_summary="Build retrieval and ranking capabilities.",
    must_have_capabilities=["Python"],
    scoring_rationale="Prioritize Python and retrieval depth.",
)
```

After:

```python
RequirementExtractionDraft(
    title_anchor_terms=["Python"],
    title_anchor_rationale="title has one stable technical anchor",
    jd_query_terms=["Retrieval Systems"],
    role_summary="Build retrieval and ranking capabilities.",
    must_have_capabilities=["Python"],
    scoring_rationale="Prioritize Python and retrieval depth.",
)
```

Update `RequirementSheet(...)` fixtures:

```python
RequirementSheet(
    job_title="Senior Python Engineer",
    title_anchor_terms=["Python"],
    title_anchor_rationale="title has one stable technical anchor",
    role_summary="Build retrieval systems.",
    must_have_capabilities=["Python"],
    scoring_rationale="Prioritize Python retrieval experience.",
)
```

Update assertions:

```python
assert requirement_sheet.job_title == "Senior Python Engineer"
```

- [ ] **Step 7: Run focused requirement and prompt tests**

Run:

```bash
uv run pytest tests/test_requirement_extraction.py tests/test_llm_input_prompts.py -q
```

Expected: failures are now limited to downstream files still reading `requirement_sheet.role_title` or `ScoringPolicy.role_title`. Continue to Task 3.

---

### Task 3: Rename Runtime LLM Input Surfaces And Diagnostics

**Files:**
- Modify: `src/seektalent/controller/react_controller.py`
- Modify: `src/seektalent/scoring/scorer.py`
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/runtime/requirements_runtime.py`
- Modify: `src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/retrieval/query_identity.py`
- Test: `tests/test_controller_contract.py`
- Test: `tests/test_reflection_contract.py`
- Test: `tests/test_runtime_audit.py`
- Test: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Update controller, scoring, and reflection prompt renderers**

In `src/seektalent/controller/react_controller.py`, change the requirements block from role wording to job-title wording:

```python
                "REQUIREMENTS\n"
                f"- Job Title: {sheet.job_title}\n"
```

In `src/seektalent/scoring/scorer.py`, change scoring policy rendering:

```python
                "SCORING POLICY\n"
                f"- Job Title: {policy.job_title}\n"
```

In `src/seektalent/reflection/critic.py`, change requirements rendering:

```python
                "REQUIREMENTS\n"
                f"- Job Title: {context.requirement_sheet.job_title}\n"
```

- [ ] **Step 2: Update requirements runtime progress and event payloads**

In `src/seektalent/runtime/requirements_runtime.py`, replace completion summaries and payload keys:

```python
        summary=requirement_sheet.job_title,
```

```python
        f"岗位需求解析完成：{requirement_sheet.job_title}",
```

```python
            "job_title": requirement_sheet.job_title,
```

Do not emit `role_title` in the public/progress payload.

- [ ] **Step 3: Update runtime diagnostics and query identity**

In `src/seektalent/retrieval/query_identity.py`, rename the function parameter and serialized key:

```python
def build_job_intent_fingerprint(
    *,
    job_title: str,
    must_haves: list[str],
    preferred_terms: list[str],
    hard_filters: dict[str, object],
    location_preferences: list[str],
    normalized_intent_hash: str,
    intent_schema_version: str,
) -> str:
```

The payload inside that function should contain:

```python
"job_title": normalize_term(job_title),
```

In `src/seektalent/runtime/orchestrator.py`, update its call:

```python
            job_title=requirement_sheet.job_title,
```

In `src/seektalent/runtime/runtime_diagnostics.py`, update function calls and payloads so requirement-sheet JSON has `job_title`.

- [ ] **Step 4: Update orchestrator summaries and input refs**

In `src/seektalent/runtime/orchestrator.py`, update `_build_llm_call_snapshot()` so `_llm_output_summary()` can see the call input payload:

```python
            output_summary=self._llm_output_summary(
                stage=stage,
                output=structured_output,
                input_payload=user_payload,
            ),
```

Then update `_llm_output_summary()` to read the job title from input truth, not from the draft output:

```python
    def _llm_output_summary(
        self,
        *,
        stage: str,
        output: Any | None,
        input_payload: dict[str, Any] | None = None,
    ) -> str | None:
        if output is None:
            return None
        if stage == "requirements" and isinstance(output, dict):
            truth = (input_payload or {}).get("INPUT_TRUTH", {})
            job_title = truth.get("job_title", "") if isinstance(truth, dict) else ""
            return (
                f"job_title={job_title!r}; "
                f"title_anchors={len(output.get('title_anchor_terms') or [])}; "
                f"jd_terms={len(output.get('jd_query_terms') or [])}"
            )
```

For `repair_requirements`, avoid reading `role_title` from the draft output. Use:

```python
        if stage == "repair_requirements" and isinstance(output, dict):
            return f"jd_terms={len(output.get('jd_query_terms') or [])}"
```

Rename `_input_text_refs()` arguments and payload:

```python
    def _input_text_refs(self, *, job_title: str, jd: str, notes: str) -> dict[str, object]:
        return {
            "input_truth_ref": "input_truth.json",
            "job_title": job_title,
            "jd_sha256": hashlib.sha256(jd.encode("utf-8")).hexdigest(),
            "notes_sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
            "jd_chars": len(jd),
            "notes_chars": len(notes),
        }
```

Update all call sites to pass `job_title=...`.

- [ ] **Step 5: Update tests that build `ScoringPolicy` or assert runtime artifacts**

In `tests/test_controller_contract.py`, `tests/test_runtime_state_flow.py`, and `tests/test_llm_input_prompts.py`, replace:

```python
ScoringPolicy(
    role_title=requirement_sheet.role_title,
```

with:

```python
ScoringPolicy(
    job_title=requirement_sheet.job_title,
```

Update artifact assertions:

```python
assert judge_packet["requirements"]["requirement_sheet"]["job_title"] == "Senior Python Engineer"
legacy_title_key = "_".join(("role", "title"))
assert legacy_title_key not in judge_packet["requirements"]["requirement_sheet"]
```

- [ ] **Step 6: Run focused runtime contract tests**

Run:

```bash
uv run pytest tests/test_controller_contract.py tests/test_reflection_contract.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_query_identity.py -q
```

Expected: remaining failures point to provider, candidate-feedback, Workbench bridge, or stale fixtures still using `role_title`.

- [ ] **Step 7: Commit runtime surface changes**

```bash
git add src/seektalent/controller/react_controller.py src/seektalent/scoring/scorer.py src/seektalent/reflection/critic.py src/seektalent/runtime/requirements_runtime.py src/seektalent/runtime/runtime_diagnostics.py src/seektalent/runtime/orchestrator.py src/seektalent/retrieval/query_identity.py tests/test_controller_contract.py tests/test_reflection_contract.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_llm_input_prompts.py tests/test_query_identity.py
git commit -m "refactor: use job_title across runtime LLM surfaces"
```

---

### Task 4: Rename Provider, Candidate-Feedback, And Bridge Call Sites

**Files:**
- Modify: `src/seektalent/providers/cts/filter_projection.py`
- Modify: `src/seektalent/runtime/source_filters.py`
- Modify: `src/seektalent/providers/liepin/card_policy.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `src/seektalent/candidate_feedback/model_steps.py`
- Modify: `src/seektalent/candidate_feedback/llm_prf_bakeoff.py`
- Modify: `src/seektalent_ui/runtime_bridge.py`
- Test: `tests/test_candidate_feedback.py`
- Test: provider/runtime tests selected by failures

- [ ] **Step 1: Update CTS and runtime source filter helpers**

In `src/seektalent/providers/cts/filter_projection.py` and `src/seektalent/runtime/source_filters.py`, change return sites that read:

```python
return requirement_sheet.role_title or None
```

to:

```python
return requirement_sheet.job_title or None
```

- [ ] **Step 2: Update Liepin card policy naming**

In `src/seektalent/providers/liepin/card_policy.py`, rename parameters and local variables from `role_title` to `job_title`.

The public reason string must not contain `role_title`. Change:

```python
reasons.append("matched_role_title")
return score, tuple(reason for reason in reasons if reason != "matched_role_title")
```

to:

```python
reasons.append("matched_job_title")
return score, tuple(reason for reason in reasons if reason != "matched_job_title")
```

Also rename `compact_role_title`:

```python
compact_job_title = _compact_cjk(job_title)
```

- [ ] **Step 3: Update Liepin runtime lane**

In `src/seektalent/providers/liepin/runtime_lane.py`, update calls into card policy:

```python
role_title=request.job_title,
```

becomes:

```python
job_title=request.job_title,
```

Rename helper parameters from `role_title` to `job_title` where they mean the request job title.

- [ ] **Step 4: Update candidate-feedback PRF fields**

In `src/seektalent/candidate_feedback/llm_prf.py`, rename model fields and function parameters:

```python
job_title: str = ""
```

Because `LLMPRFInput` is written as a runtime artifact and loaded by bakeoff/live-validation fixtures, bump the PRF input schema version when the payload key changes:

```python
LLM_PRF_SCHEMA_VERSION = "llm-prf-v2"
```

Update every `Literal["llm-prf-v1"]` field in `src/seektalent/candidate_feedback/llm_prf.py` that defaults to `LLM_PRF_SCHEMA_VERSION` so the shared constant and model field types agree on `llm-prf-v2`.

Any payload that currently serializes:

```python
"role_title": role_title,
```

must serialize:

```python
"job_title": job_title,
```

In `src/seektalent/candidate_feedback/model_steps.py`, rename function parameters and payload keys to `job_title`.

In `src/seektalent/candidate_feedback/llm_prf_bakeoff.py`, rename the bakeoff case field:

```python
job_title: str
```

and pass:

```python
job_title=case.job_title,
```

Update the PRF tests and fixtures that load or assert this payload shape:

- `tests/test_llm_prf.py`
- `tests/test_llm_prf_bakeoff.py`
- `tests/fixtures/llm_prf_bakeoff/cases.jsonl`
- `tests/fixtures/llm_prf_live_validation/cases.jsonl`

- [ ] **Step 5: Update Workbench bridge direct core-model construction**

In `src/seektalent_ui/runtime_bridge.py`, update `_requirement_draft_from_approved_triage(...)`. Remove the `role_title` argument from the `RequirementExtractionDraft(...)` constructor:

```python
    return RequirementExtractionDraft(
        title_anchor_terms=title_anchor_terms,
        title_anchor_rationale=f"Title anchors derived from job_title: {context.session.job_title}",
        jd_query_terms=jd_query_terms,
        notes_query_terms=notes_query_terms,
        role_summary=_role_summary_from_triage(context),
        must_have_capabilities=triage.must_haves,
        preferred_capabilities=triage.nice_to_haves,
        exclusion_signals=triage.exclusions,
        locations=structured_defaults.locations,
        school_type_requirement=structured_defaults.school_type_requirement,
        degree_requirement=structured_defaults.degree_requirement,
        experience_requirement=structured_defaults.experience_requirement,
        age_requirement=structured_defaults.age_requirement,
        scoring_rationale=_scoring_rationale_from_triage(context),
    )
```

Keep this as a constructor fix only. Do not remove `_seed_approved_requirement_cache()` in this slice.

Update Workbench bridge/cache tests that assert the seeded requirement payload:

- `tests/test_workbench_api.py`

Expected seeded cache payload:

```python
assert cached_requirement["jd_query_terms"]
legacy_title_key = "_".join(("role", "title"))
assert legacy_title_key not in cached_requirement
```

- [ ] **Step 6: Update provider and feedback tests**

Run:

```bash
uv run pytest tests/test_candidate_feedback.py -q
uv run pytest tests/test_llm_prf.py tests/test_llm_prf_bakeoff.py -q
uv run pytest tests/test_workbench_api.py -q
```

If failures show payload assertions containing `role_title`, update them to `job_title`.

Run a targeted provider selection based on existing files:

```bash
uv run pytest tests -k "liepin or source_filter or filter_projection" -q
```

Expected: no failures caused by old `role_title` names.

- [ ] **Step 7: Commit provider, feedback, and bridge changes**

```bash
git add src/seektalent/providers/cts/filter_projection.py src/seektalent/runtime/source_filters.py src/seektalent/providers/liepin/card_policy.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/candidate_feedback/llm_prf.py src/seektalent/candidate_feedback/model_steps.py src/seektalent/candidate_feedback/llm_prf_bakeoff.py src/seektalent_ui/runtime_bridge.py tests/test_candidate_feedback.py tests/test_llm_prf.py tests/test_llm_prf_bakeoff.py tests/test_liepin_card_policy.py tests/test_filter_projection.py tests/test_workbench_api.py tests/fixtures/llm_prf_bakeoff/cases.jsonl tests/fixtures/llm_prf_live_validation/cases.jsonl
git commit -m "refactor: use job_title in source and feedback boundaries"
```

---

### Task 5: Remove Remaining Active `role_title` References And Verify Hard Cut

**Files:**
- Modify: any active file still reported by `rg -n "\\brole_title\\b" src/seektalent src/seektalent_ui tests`
- Test: focused tests plus grep acceptance

- [ ] **Step 1: Run the hard-cut grep**

Run:

```bash
rg -n "\\brole_title\\b" src/seektalent src/seektalent_ui tests
```

Expected after Tasks 2-4: no output.

If there is output, fix each active runtime occurrence by renaming the field, local variable, payload key, prompt label, or test assertion to `job_title`. Do not add compatibility wrappers.

- [ ] **Step 2: Check constructors and payload literals outside core runtime**

Run:

```bash
rg -n "role_title=|\"role_title\"|'role_title'|matched_role_title|compact_role_title" src/seektalent src/seektalent_ui tests
```

Expected: no active occurrences. If matches remain in docs under `docs/superpowers` from this plan/spec, ignore them. Active code, UI bridge code, tests, and fixtures must be clean.

- [ ] **Step 3: Run the required focused tests**

Run:

```bash
uv run pytest tests/test_requirement_extraction.py
uv run pytest tests/test_controller_contract.py
uv run pytest tests/test_reflection_contract.py
uv run pytest tests/test_runtime_audit.py
uv run pytest tests/test_llm_input_prompts.py
uv run pytest tests/test_runtime_state_flow.py
uv run pytest tests/test_candidate_feedback.py
uv run pytest tests/test_query_identity.py
uv run pytest tests/test_llm_prf.py
uv run pytest tests/test_llm_prf_bakeoff.py
uv run pytest tests/test_liepin_card_policy.py
uv run pytest tests/test_filter_projection.py
uv run pytest tests/test_runtime_source_adapter_boundary.py
uv run pytest tests/test_workbench_api.py
```

Expected: all selected tests pass.

- [ ] **Step 4: Run static checks used by the repository**

Run:

```bash
uv run ruff check src tests
```

Expected: no new lint failures.

Run:

```bash
uv run ty check src
```

Expected: no new type-check failures. If existing unrelated type-check observations exist, record the exact output in the final report and confirm no `job_title`/`role_title` change caused them.

- [ ] **Step 5: Inspect the final diff for compatibility backdoors**

Run:

```bash
git diff -- src/seektalent src/seektalent_ui tests
```

Confirm the diff does not contain any of these active-code patterns:

```text
def role_title
role_title: str
alias="role_title"
validation_alias="role_title"
Field(..., alias=
data.get("role_title")
legacy role title
backward compatible role_title
```

Expected: none of those patterns are present in active implementation code.

- [ ] **Step 6: Record acceptance evidence**

Run:

```bash
rg -n "\\brole_title\\b" src/seektalent src/seektalent_ui tests || true
rg -n "role_title=|\"role_title\"|'role_title'|matched_role_title|compact_role_title" src/seektalent src/seektalent_ui tests || true
```

Expected:

- Both broad `src/seektalent src/seektalent_ui tests` commands print no matches.
- Matches in the new spec/plan are outside these active paths and do not affect runtime.

- [ ] **Step 7: Commit hard-cut completion**

```bash
git add src tests
git commit -m "refactor: hard cut role_title from active runtime"
```

---

## Self-Review Checklist For The Implementer

- `RequirementExtractionDraft` has no `role_title`.
- `RequirementSheet`, `RequirementDigest`, and `ScoringPolicy` use `job_title`.
- Requirements prompt does not mention `role_title`.
- Requirements cache key schema version changed to `requirement_extraction_draft.v3`.
- PRF input artifact schema version changed for the `job_title` payload shape.
- No compatibility property, alias, fallback reader, or old-cache reader was added.
- Runtime LLM call summaries that include job title read it from input truth, not requirement draft output.
- `rg -n "\\brole_title\\b" src/seektalent src/seektalent_ui tests` returns no matches.
- Workbench bridge still imports and constructs `RequirementExtractionDraft` without `role_title`.
- The selected pytest commands pass or any failure is documented as unrelated to this hard cut.
