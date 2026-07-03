# Liepin Structured Normalization And FullText Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Liepin whole-page text end-to-end, split CTS and Liepin normalization, score from allowlisted structured evidence, and delete the TUI-only `reflection_rationale` output.

**Architecture:** Remove Liepin `fullText`, `rawText`, and `page_text` at source boundaries first. Add full `StructuredResumeEvidence` for runtime/UI and separate `StructuredScoringEvidence` for LLM scoring, then migrate scoring, PRF, and runtime-control before deleting `raw_text_excerpt`. Keep scoring concurrency unchanged and keep OpenCLI generic behind a stable browser capability wrapper.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, existing SeekTalent source registry/runtime patterns, existing OpenCLI browser automation.

---

## Execution Rules

- [ ] Do not delete `NormalizedResume.raw_text_excerpt` until Tasks 4 and 5 have moved every consumer away from it.
- [ ] Do not put candidate identity, candidate name, `age`, `gender`, `sourceUrl`, education summary, education school, education degree, or education major into any scoring prompt section, including `RESUME CARD`.
- [ ] Do not change `score_candidates_parallel`, semaphore usage, or `asyncio.gather`.
- [ ] Do not add production compatibility paths that preserve Liepin `fullText`; old fixtures must be migrated or kept only as negative tests.
- [ ] Run scans against `src apps tests scripts tools`, not only `src/seektalent`.
- [ ] Do not rename or delete runtime-control requirement-review `ReviewItem.raw_text` / `reviewItems[].rawText`; it is job-requirement provenance, not resume full text or Liepin page text.

## Execution Ownership And Commit Boundaries

- [ ] Tasks 1-6 are serial and must share one execution owner because they move a single resume normalization/scoring contract.
- [ ] Task 8 must not run in parallel with Task 1 because both edit `liepin_site_adapter.py`, OpenCLI browser tests, and Liepin capture behavior.
- [ ] Task 7 may run separately only in an isolated worktree after Tasks 1-6 are merged because it touches shared models and UI/runtime schemas.
- [ ] Do not use broad `git add src`, `git add tests`, or `git add apps`; stage only the exact files changed for the active task.

## File Structure

- Create `src/seektalent/resume_normalizers/__init__.py` - source-normalizer public exports.
- Create `src/seektalent/resume_normalizers/registry.py` - source dispatch and normalizer protocol.
- Create `src/seektalent/resume_normalizers/cts.py` - CTS-owned normalization, moved from current shared implementation.
- Create `src/seektalent/resume_normalizers/liepin.py` - Liepin structured normalization with no whole-page text access.
- Create `src/seektalent/opencli_browser/client.py` - provider-agnostic browser capability protocol.
- Modify `src/seektalent/normalization.py` - compatibility facade over the registry.
- Modify `src/seektalent/models.py` - structured resume evidence, structured scoring evidence, final `raw_text_excerpt` removal, and `reflection_rationale` removal.
- Modify `src/seektalent/scoring/scorer.py` - render allowlisted structured scoring evidence.
- Modify `src/seektalent/candidate_feedback/llm_prf.py` - use structured evidence as PRF sources.
- Modify `src/seektalent_runtime_control/candidates.py` - stop emitting `rawTextExcerpt`.
- Modify `src/seektalent_runtime_control/commands.py` and `src/seektalent_runtime_control/service.py` only if scan shows pass-through of whole-page text.
- Modify `src/seektalent/providers/liepin/liepin_site_parsing.py` - remove DOM `fullText`.
- Modify `src/seektalent/providers/liepin/liepin_site_adapter.py` - stop writing `page_text` and remove direct OpenCLI coupling.
- Modify `src/seektalent/providers/liepin/opencli_retriever.py` - remove `payload.get("fullText")` fallback.
- Modify `src/seektalent/providers/liepin/mapper.py` - sanitize `ResumeCandidate.raw` and `ProviderSnapshot.raw_payload`.
- Modify `src/seektalent/providers/liepin/worker_contracts.py` - reject whole-page text keys at detail contract boundary.
- Modify `src/seektalent/corpus/runtime.py` only if raw provider snapshot artifact writing needs an additional sanitizer call after mapper sanitization.
- Modify `src/seektalent_ui/resume_snapshot_projection.py`, `src/seektalent_ui/workbench_candidate_graph.py`, `src/seektalent/mock_data.py` - remove UI/mock full-text assumptions.
- Modify reflection paths listed in Task 7, including generated `apps/web-react/src/lib/api/schema.d.ts`.

## Task 1: Hard-Delete Liepin Whole-Page Text At Source Boundaries

**Files:**
- Modify: `tests/test_liepin_provider_mapping.py`
- Modify: `tests/test_liepin_opencli_retriever.py`
- Modify: `tests/test_liepin_opencli_browser.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
- Modify: `src/seektalent/providers/liepin/mapper.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_parsing.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent_ui/resume_snapshot_projection.py`
- Modify: `src/seektalent/mock_data.py`
- Modify: `src/seektalent/corpus/runtime.py` only if mapper-level sanitization is not sufficient for corpus raw payload artifacts.

- [ ] **Step 1: Write failing contract and mapper tests**

In `tests/test_liepin_provider_mapping.py`, remove `"fullText"` from `ALLOWED_DETAIL_RAW_KEYS` and remove all top-level whole-page text aliases from `_worker_detail().payload`, including `"fullText"`, `"resumeText"`, `"detailBody"`, `"profile"`, and `"summary"`. Keep structured nested summaries such as `workExperienceList[].summary`. Add:

```python
WHOLE_PAGE_TEXT_ALIASES = (
    "fullText",
    "full_text",
    "rawText",
    "raw_text",
    "page_text",
    "pageText",
    "resumeText",
    "resume_text",
    "resume_free_text",
    "detailBody",
    "detail_body",
    "profile",
    "summary",
)


@pytest.mark.parametrize("field_name", WHOLE_PAGE_TEXT_ALIASES)
def test_worker_detail_rejects_whole_page_text_payload_aliases(field_name: str) -> None:
    payload = _worker_detail().model_dump(mode="json")
    payload["payload"][field_name] = "whole page resume text"

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateDetail.model_validate(payload)


def test_detail_mapping_sanitizes_provider_snapshot_payload() -> None:
    raw = _worker_detail().model_dump(mode="python")
    raw["payload"] = {
        **raw["payload"],
        "fullText": "whole page resume text that must not persist",
        "rawText": "raw whole page text that must not persist",
        "page_text": "captured page body that must not persist",
        "resumeText": "resume text alias that must not persist",
        "detailBody": "<html>detail body that must not persist</html>",
        "profile": "profile text alias that must not persist",
        "summary": "top-level summary text alias that must not persist",
        "workExperienceList": [{"company": "平安好医", "title": "用户体验设计专家", "summary": "structured work summary stays"}],
    }
    detail = LiepinWorkerCandidateDetail.model_construct(**raw)

    mapped = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    for key in WHOLE_PAGE_TEXT_ALIASES:
        assert key not in mapped.candidate.raw
        assert key not in mapped.provider_snapshot.raw_payload
    assert mapped.provider_snapshot.raw_payload["workExperienceList"][0]["summary"] == "structured work summary stays"
    serialized = json.dumps(mapped.provider_snapshot.raw_payload, ensure_ascii=False)
    assert "whole page resume text" not in serialized
    assert "detail body that must not persist" not in serialized
```

Change provider snapshot expectations in the existing card and detail snapshot tests:

```python
assert mapped.provider_snapshot.raw_payload == _sanitize_liepin_provider_payload(card.payload)
```

```python
assert mapped.provider_snapshot.raw_payload == _sanitize_liepin_provider_payload(detail.payload)
```

Import the helper from the mapper:

```python
from seektalent.providers.liepin.mapper import _sanitize_liepin_provider_payload
```

- [ ] **Step 2: Run the failing source-boundary tests**

Run:

```bash
pytest tests/test_liepin_provider_mapping.py::test_worker_detail_rejects_whole_page_text_payload_aliases \
  tests/test_liepin_provider_mapping.py::test_detail_mapping_sanitizes_provider_snapshot_payload -q
```

Expected: FAIL because the contract accepts the keys and mapper persists unsanitized payload in `ProviderSnapshot.raw_payload`.

- [ ] **Step 3: Reject whole-page text in worker contract**

In `src/seektalent/providers/liepin/worker_contracts.py`, add `model_validator` to the Pydantic imports:

```python
from pydantic import model_validator
```

Add to `LiepinWorkerCandidateDetail`:

```python
    @model_validator(mode="after")
    def reject_whole_page_text_fields(self) -> "LiepinWorkerCandidateDetail":
        prohibited = {
            "fullText",
            "full_text",
            "rawText",
            "raw_text",
            "page_text",
            "pageText",
            "resumeText",
            "resume_text",
            "resume_free_text",
            "detailBody",
            "detail_body",
            "profile",
            "summary",
        }
        present = sorted(key for key in prohibited if key in self.payload)
        if present:
            raise ValueError(f"Liepin detail payload must not include whole-page text fields: {', '.join(present)}")
        return self
```

- [ ] **Step 4: Sanitize mapper output and provider snapshots**

In `src/seektalent/providers/liepin/mapper.py`, add:

```python
PROHIBITED_LIEPIN_WHOLE_PAGE_TEXT_KEYS = frozenset(
    {
        "fullText",
        "full_text",
        "rawText",
        "raw_text",
        "page_text",
        "pageText",
        "resumeText",
        "resume_text",
        "resume_free_text",
        "detailBody",
        "detail_body",
        "profile",
        "summary",
    }
)


def _sanitize_liepin_provider_payload(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in PROHIBITED_LIEPIN_WHOLE_PAGE_TEXT_KEYS}
```

In `_map_candidate`, replace the first two lines with:

```python
    provider_payload = _sanitize_liepin_provider_payload(worker_candidate.payload)
    snapshot_hash = sha256_json(provider_payload)
```

Pass `provider_payload` into `ProviderSnapshot(raw_payload=...)`:

```python
        raw_payload=provider_payload,
```

In `_copy_safe_detail_payload_fields`, remove `"fullText"`, `"rawText"`, `"profile"`, and `"summary"` from the copied key tuple. The tuple must keep structured keys:

```python
        "candidate_name",
        "candidateName",
        "activeStatus",
        "jobStatus",
        "gender",
        "age",
        "city",
        "education",
        "workYears",
        "currentTitle",
        "currentCompany",
        "jobIntention",
        "workExperienceList",
        "projectExperienceList",
        "educationList",
        "skills",
        "skillTags",
        "tags",
        "keywords",
        "locations",
        "sourceUrl",
```

- [ ] **Step 5: Remove DOM and adapter collection paths**

In `src/seektalent/providers/liepin/liepin_site_parsing.py`, remove the `fullText` entry from `_liepin_detail_resume_payload_probe_script`.

In `src/seektalent/providers/liepin/liepin_site_adapter.py`, inside `capture_liepin_detail_resume`, delete the `detail_text = self._detail_state_text_until_resume_ready()` assignment and remove `"page_text": ...` from the raw artifact payload.

Replace any use of:

```python
str(payload["fullText"])
```

with:

```python
_structured_detail_text(payload)
```

Add a local helper in `liepin_site_adapter.py`:

```python
def _structured_detail_text(payload: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in ("candidate_name", "candidateName", "currentTitle", "currentCompany", "city", "education"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for key in ("skills", "skillTags", "tags", "keywords"):
        values = payload.get(key)
        if isinstance(values, list):
            parts.extend(str(item).strip() for item in values[:16] if str(item).strip())
    for list_key in ("workExperienceList", "projectExperienceList", "educationList"):
        values = payload.get(list_key)
        if not isinstance(values, list):
            continue
        for item in values[:6]:
            if not isinstance(item, Mapping):
                continue
            for field in ("company", "title", "name", "duration", "summary", "description", "school"):
                value = item.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
    return " ".join(parts)
```

- [ ] **Step 6: Remove OpenCLI retriever fallback**

In `src/seektalent/providers/liepin/opencli_retriever.py`, replace:

```python
    normalized_text = str(resume.get("normalized_text") or payload.get("fullText") or "")
```

with:

```python
    normalized_text = str(resume.get("normalized_text") or _structured_detail_text(payload))
```

Add the same `_structured_detail_text(payload: Mapping[str, object]) -> str` helper to this module. Keep it local so OpenCLI stays generic and Liepin owns source-specific semantics.

- [ ] **Step 7: Remove UI/mock full-text paths**

In `src/seektalent_ui/resume_snapshot_projection.py`, remove `"fullText"`, `"rawText"`, and `"page_text"` from `_LIEPIN_TEXT_FIELD_KEYS` and remove their display labels.

In `src/seektalent/mock_data.py`, replace any `fullText` or `rawText` fixture fields with structured `workExperienceList`, `projectExperienceList`, `educationList`, and `skills`.

- [ ] **Step 8: Update source-boundary fixtures**

Run:

```bash
rg -n "fullText|full_text|rawText|raw_text|page_text|pageText|resumeText|resume_text|resume_free_text|detailBody|detail_body|payload\\.get\\(\"fullText\"\\)|normalized_text.*fullText" \
  src/seektalent/providers/liepin src/seektalent_ui/resume_snapshot_projection.py src/seektalent/mock_data.py tests
```

For tests with positive fixtures, replace whole-page text with structured fields. Keep whole-page aliases only in negative tests that assert rejection or sanitization.

- [ ] **Step 9: Add transition tests for old consumers**

Add focused tests proving the committed Task 1 state still works before `raw_text_excerpt` is removed:

```python
def test_liepin_detail_without_full_text_still_produces_legacy_excerpt() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-transition-1",
        dedup_key="liepin-transition-1",
        search_text="用户体验设计 用户研究",
        raw={
            "provider": "liepin",
            "currentTitle": "资深体验设计工程师",
            "currentCompany": "平安集团",
            "workExperienceList": [{"company": "平安好医", "title": "用户体验设计专家", "summary": "负责体验设计。"}],
            "skills": ["用户研究", "交互设计"],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.raw_text_excerpt
    assert "平安好医" in normalized.raw_text_excerpt
    assert "fullText" not in normalized.raw_text_excerpt
```

```python
def test_scoring_prompt_accepts_liepin_without_full_text() -> None:
    context = ScoringContext(
        round_no=1,
        scoring_policy=ScoringPolicy(
            job_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["用户研究"],
            preferred_capabilities=["交互设计"],
            exclusion_signals=[],
            hard_constraints=HardConstraintSlots(),
            preferences=PreferenceSlots(),
            scoring_rationale="Score structured Liepin detail.",
        ),
        normalized_resume=NormalizedResume(
            resume_id="liepin-transition-1",
            dedup_key="liepin-transition-1",
            source_provider="liepin",
            candidate_name="潘**",
            current_title="资深体验设计工程师",
            current_company="平安集团",
            skills=["用户研究", "交互设计"],
            recent_experiences=[
                NormalizedExperience(
                    title="用户体验设计专家",
                    company="平安好医",
                    duration="2019.06-至今",
                    summary="负责体验设计。",
                )
            ],
            raw_text_excerpt="平安好医 用户体验设计专家 负责体验设计。",
            completeness_score=90,
        ),
        requirement_sheet_sha256="requirement-sheet-hash",
    )

    prompt = render_scoring_prompt(context)

    assert "平安好医" in prompt
    assert "fullText" not in prompt
```

In the existing `tests/test_runtime_control_candidate_truth.py::test_candidate_truth_projects_wts_fields_from_structured_liepin_detail_payload`, add:

```python
serialized = json.dumps(truth.model_dump(mode="json"), ensure_ascii=False)
assert "平安好医" in serialized
assert "fullText" not in serialized
assert "page_text" not in serialized
```

- [ ] **Step 10: Run focused source-boundary and transition tests**

Run:

```bash
pytest tests/test_liepin_provider_mapping.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_browser.py \
  tests/test_workbench_v2_runtime_service.py::test_runtime_service_candidate_detail_projects_wts_profile_fields \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_normalization.py::test_liepin_detail_without_full_text_still_produces_legacy_excerpt \
  tests/test_llm_input_prompts.py::test_scoring_prompt_accepts_liepin_without_full_text \
  tests/test_runtime_control_candidate_truth.py::test_candidate_truth_projects_wts_fields_from_structured_liepin_detail_payload -q
```

Expected: PASS. Scoring, PRF, and `raw_text_excerpt` consumers may still exist after this task.

- [ ] **Step 11: Commit Task 1**

```bash
git add src/seektalent/providers/liepin/worker_contracts.py \
  src/seektalent/providers/liepin/mapper.py \
  src/seektalent/providers/liepin/liepin_site_parsing.py \
  src/seektalent/providers/liepin/liepin_site_adapter.py \
  src/seektalent/providers/liepin/opencli_retriever.py \
  src/seektalent_ui/resume_snapshot_projection.py \
  src/seektalent/mock_data.py \
  tests/test_liepin_provider_mapping.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_browser.py \
  tests/test_workbench_api.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_normalization.py \
  tests/test_llm_input_prompts.py \
  tests/test_runtime_control_candidate_truth.py
git commit -m "fix: remove Liepin whole-page text capture"
```

## Task 2: Add Structured Resume And Scoring Evidence Models

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `tests/test_normalization.py`

- [ ] **Step 1: Write evidence model tests**

Append to `tests/test_normalization.py`:

```python
def test_structured_resume_evidence_derives_scoring_evidence_without_protected_fields() -> None:
    evidence = StructuredResumeEvidence(
        identity={"candidateName": "吴**", "age": 32, "gender": "男"},
        current_role={"title": "资深体验设计工程师", "company": "平安集团", "workYears": 10},
        job_intention={"expectedRole": "体验设计", "expectedCity": "上海", "expectedSalary": "20-24k"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                title="用户体验设计专家",
                duration="2019.06-至今",
                summary="负责 B 端和 C 端体验设计。",
            )
        ],
        project_experience=[StructuredResumeTimelineItem(name="增长项目", summary="通过用户研究优化转化。")],
        education_experience=[StructuredResumeTimelineItem(school="华东师范大学", degree="硕士", major="设计学")],
        skills=["用户研究", "交互设计"],
        source_metadata={"sourceUrl": "https://h.liepin.com/resume/showresumedetail/abc"},
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "平安好医" in serialized
    assert "增长项目" in serialized
    assert "用户研究" in serialized
    assert "吴**" not in serialized
    assert '"name":' not in serialized
    assert "age" not in serialized
    assert "gender" not in serialized
    assert "sourceUrl" not in serialized
    assert "华东师范大学" not in serialized
    assert "硕士" not in serialized
    assert "设计学" not in serialized
```

Add imports if missing:

```python
import json
from seektalent.models import StructuredResumeEvidence, StructuredResumeTimelineItem
```

- [ ] **Step 2: Run evidence model test and verify failure**

Run:

```bash
pytest tests/test_normalization.py::test_structured_resume_evidence_derives_scoring_evidence_without_protected_fields -q
```

Expected: FAIL because structured evidence models do not exist.

- [ ] **Step 3: Add evidence models without deleting `raw_text_excerpt`**

In `src/seektalent/models.py`, add after `NormalizedExperience`:

```python
class StructuredResumeTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = ""
    title: str = ""
    name: str = ""
    school: str = ""
    major: str = ""
    degree: str = ""
    duration: str = ""
    summary: str = ""


class StructuredScoringRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    company: str = ""
    work_years: int | None = None


class StructuredScoringJobIntention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_role: str = ""
    expected_industry: str = ""
    expected_city: str = ""
    expected_salary: str = ""


class StructuredScoringWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = ""
    title: str = ""
    duration: str = ""
    summary: str = ""


class StructuredScoringProjectItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = ""
    duration: str = ""
    summary: str = ""


class StructuredScoringEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_role: StructuredScoringRole = Field(default_factory=StructuredScoringRole)
    job_intention: StructuredScoringJobIntention = Field(default_factory=StructuredScoringJobIntention)
    work_experience: list[StructuredScoringWorkItem] = Field(default_factory=list)
    project_experience: list[StructuredScoringProjectItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class StructuredResumeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: dict[str, str | int] = Field(default_factory=dict)
    current_role: dict[str, str | int] = Field(default_factory=dict)
    status: dict[str, str | int] = Field(default_factory=dict)
    job_intention: dict[str, str | int] = Field(default_factory=dict)
    work_experience: list[StructuredResumeTimelineItem] = Field(default_factory=list)
    project_experience: list[StructuredResumeTimelineItem] = Field(default_factory=list)
    education_experience: list[StructuredResumeTimelineItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    source_metadata: dict[str, str | int] = Field(default_factory=dict)

    def to_scoring_evidence(self) -> StructuredScoringEvidence:
        return StructuredScoringEvidence(
            current_role=StructuredScoringRole(
                title=_text_value(self.current_role.get("title")),
                company=_text_value(self.current_role.get("company")),
                work_years=_int_value(self.current_role.get("workYears") or self.current_role.get("work_years")),
            ),
            job_intention=StructuredScoringJobIntention(
                expected_role=_text_value(self.job_intention.get("expectedRole") or self.job_intention.get("expected_role")),
                expected_industry=_text_value(self.job_intention.get("expectedIndustry") or self.job_intention.get("expected_industry")),
                expected_city=_text_value(self.job_intention.get("expectedCity") or self.job_intention.get("expected_city")),
                expected_salary=_text_value(self.job_intention.get("expectedSalary") or self.job_intention.get("expected_salary")),
            ),
            work_experience=[_work_item_for_scoring(item) for item in self.work_experience],
            project_experience=[_project_item_for_scoring(item) for item in self.project_experience],
            skills=self.skills[:24],
        )


def _text_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int_value(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _work_item_for_scoring(item: StructuredResumeTimelineItem) -> StructuredScoringWorkItem:
    return StructuredScoringWorkItem(
        company=item.company,
        title=item.title,
        duration=item.duration,
        summary=item.summary,
    )


def _project_item_for_scoring(item: StructuredResumeTimelineItem) -> StructuredScoringProjectItem:
    return StructuredScoringProjectItem(
        project_name=item.name,
        duration=item.duration,
        summary=item.summary,
    )
```

Add to `NormalizedResume`:

```python
    structured_evidence: StructuredResumeEvidence = Field(default_factory=StructuredResumeEvidence)
```

Keep this field in this task:

```python
    raw_text_excerpt: str = ""
```

- [ ] **Step 4: Run evidence tests**

Run:

```bash
pytest tests/test_normalization.py::test_structured_resume_evidence_derives_scoring_evidence_without_protected_fields -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/seektalent/models.py tests/test_normalization.py
git commit -m "feat: add structured resume evidence models"
```

## Task 3: Split CTS And Liepin Normalization Through Registry

**Files:**
- Create: `src/seektalent/resume_normalizers/__init__.py`
- Create: `src/seektalent/resume_normalizers/registry.py`
- Create: `src/seektalent/resume_normalizers/cts.py`
- Create: `src/seektalent/resume_normalizers/liepin.py`
- Modify: `src/seektalent/normalization.py`
- Modify: `tests/test_normalization.py`

- [ ] **Step 1: Write dispatch and Liepin normalization tests**

Append to `tests/test_normalization.py`:

```python
def test_normalization_dispatches_liepin_to_liepin_normalizer(monkeypatch) -> None:
    from seektalent.resume_normalizers import registry

    called: list[str] = []

    def fake_liepin(candidate: ResumeCandidate):
        called.append(candidate.resume_id)
        return registry._legacy_normalize_resume(candidate).model_copy(update={"source_provider": "liepin"})

    monkeypatch.setitem(registry.NORMALIZERS, "liepin", fake_liepin)

    normalized = normalize_resume(
        ResumeCandidate(
            resume_id="liepin-dispatch-1",
            dedup_key="liepin-dispatch-1",
            search_text="用户体验设计",
            raw={"provider": "liepin", "currentTitle": "AI Agent Engineer"},
        )
    )

    assert called == ["liepin-dispatch-1"]
    assert normalized.source_provider == "liepin"


def test_normalization_dispatches_cts_to_cts_normalizer(monkeypatch) -> None:
    from seektalent.resume_normalizers import registry

    called: list[str] = []

    def fake_cts(candidate: ResumeCandidate):
        called.append(candidate.resume_id)
        return registry._legacy_normalize_resume(candidate).model_copy(update={"source_provider": "cts"})

    monkeypatch.setitem(registry.NORMALIZERS, "cts", fake_cts)

    normalized = normalize_resume(
        ResumeCandidate(
            resume_id="cts-dispatch-1",
            dedup_key="cts-dispatch-1",
            search_text="AI Infra Engineer",
            raw={"provider": "cts", "current_title": "AI Infra Engineer"},
        )
    )

    assert called == ["cts-dispatch-1"]
    assert normalized.source_provider == "cts"


def test_liepin_normalization_uses_structured_evidence_without_whole_page_text() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-detail-structured-1",
        dedup_key="liepin-detail-structured-1",
        search_text="用户体验设计 用户研究 交互设计",
        raw={
            "provider": "liepin",
            "candidate_name": "吴**",
            "activeStatus": "近30天内活跃",
            "jobStatus": "在职，看看新机会",
            "age": 32,
            "gender": "男",
            "city": "上海",
            "education": "本科",
            "workYears": 10,
            "currentTitle": "资深体验设计工程师",
            "currentCompany": "平安集团",
            "jobIntention": {"expectedSalary": "20-24k*14薪", "expectedCity": "上海"},
            "workExperienceList": [{"company": "平安好医", "title": "用户体验设计专家", "summary": "负责 B 端和 C 端体验设计。"}],
            "projectExperienceList": [{"name": "增长项目", "summary": "通过用户研究优化转化。"}],
            "educationList": [{"school": "华东师范大学", "degree": "硕士", "major": "设计学"}],
            "skills": ["用户研究", "交互设计"],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider == "liepin"
    assert normalized.structured_evidence.current_role["title"] == "资深体验设计工程师"
    assert normalized.structured_evidence.work_experience[0].company == "平安好医"
    assert normalized.structured_evidence.project_experience[0].name == "增长项目"
    assert normalized.structured_evidence.education_experience[0].school == "华东师范大学"
    assert normalized.raw_text_excerpt
    assert "fullText" not in normalized.raw_text_excerpt
    assert normalized.completeness_score == 100


def test_liepin_normalization_rejects_whole_page_text_keys() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-bad-text-1",
        dedup_key="liepin-bad-text-1",
        search_text="用户体验设计",
        raw={"provider": "liepin", "fullText": "whole page text"},
    )

    with pytest.raises(ValueError, match="whole-page text"):
        normalize_resume(candidate)


def test_unknown_source_with_liepin_text_alias_rejects_instead_of_cts_fallback() -> None:
    candidate = ResumeCandidate(
        resume_id="unknown-liepin-bad-text-1",
        dedup_key="unknown-liepin-bad-text-1",
        search_text="用户体验设计",
        raw={"fullText": "old Liepin whole page text"},
    )

    with pytest.raises(ValueError, match="Unsupported or unmigrated Liepin-shaped resume payload"):
        normalize_resume(candidate)


def test_old_liepin_fixture_without_provider_must_be_migrated() -> None:
    candidate = ResumeCandidate(
        resume_id="old-liepin-fixture-1",
        dedup_key="old-liepin-fixture-1",
        search_text="用户体验设计",
        raw={"currentTitle": "资深体验设计工程师", "workExperienceList": [{"company": "平安好医"}]},
    )

    with pytest.raises(ValueError, match="Unsupported or unmigrated Liepin-shaped resume payload"):
        normalize_resume(candidate)
```

- [ ] **Step 2: Run dispatch tests and verify failure**

Run:

```bash
pytest tests/test_normalization.py::test_normalization_dispatches_liepin_to_liepin_normalizer \
  tests/test_normalization.py::test_normalization_dispatches_cts_to_cts_normalizer \
  tests/test_normalization.py::test_liepin_normalization_uses_structured_evidence_without_whole_page_text \
  tests/test_normalization.py::test_liepin_normalization_rejects_whole_page_text_keys \
  tests/test_normalization.py::test_unknown_source_with_liepin_text_alias_rejects_instead_of_cts_fallback \
  tests/test_normalization.py::test_old_liepin_fixture_without_provider_must_be_migrated -q
```

Expected: FAIL because the registry package and Liepin normalizer do not exist.

- [ ] **Step 3: Create source-normalizer registry**

Create `src/seektalent/resume_normalizers/__init__.py`:

```python
from seektalent.resume_normalizers.registry import normalize_resume, normalizer_key_for_candidate

__all__ = ["normalize_resume", "normalizer_key_for_candidate"]
```

Create `src/seektalent/resume_normalizers/registry.py`:

```python
from __future__ import annotations

from collections.abc import Callable

from seektalent.models import NormalizedResume, ResumeCandidate

ResumeNormalizer = Callable[[ResumeCandidate], NormalizedResume]
NORMALIZERS: dict[str, ResumeNormalizer] = {}


def normalizer_key_for_candidate(candidate: ResumeCandidate) -> str:
    provider = candidate.raw.get("provider") or candidate.raw.get("source") or candidate.raw.get("source_provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip().casefold()
    if candidate.source_resume_id and str(candidate.source_resume_id).startswith("liepin"):
        return "liepin"
    if _has_liepin_shape(candidate.raw):
        raise ValueError("Unsupported or unmigrated Liepin-shaped resume payload")
    return "cts"


def _has_liepin_shape(raw: dict[str, object]) -> bool:
    liepin_text_aliases = {
        "fullText",
        "full_text",
        "rawText",
        "raw_text",
        "page_text",
        "pageText",
        "resumeText",
        "resume_text",
        "resume_free_text",
        "detailBody",
        "detail_body",
    }
    liepin_structured_keys = {
        "currentTitle",
        "currentCompany",
        "workExperienceList",
        "projectExperienceList",
        "educationList",
        "jobIntention",
        "activeStatus",
        "jobStatus",
    }
    return bool(set(raw) & (liepin_text_aliases | liepin_structured_keys))


def normalize_resume(candidate: ResumeCandidate) -> NormalizedResume:
    key = normalizer_key_for_candidate(candidate)
    normalizer = NORMALIZERS.get(key)
    if normalizer is None:
        raise ValueError(f"Unsupported resume normalizer source: {key}")
    return normalizer(candidate)


def _legacy_normalize_resume(candidate: ResumeCandidate) -> NormalizedResume:
    from seektalent.resume_normalizers.cts import normalize_cts_resume

    return normalize_cts_resume(candidate)


from seektalent.resume_normalizers.cts import normalize_cts_resume
from seektalent.resume_normalizers.liepin import normalize_liepin_resume

NORMALIZERS.update({"cts": normalize_cts_resume, "liepin": normalize_liepin_resume})
```

- [ ] **Step 4: Move existing normalizer to CTS**

Create `src/seektalent/resume_normalizers/cts.py` by moving the current implementation from `src/seektalent/normalization.py`. Rename the public function to:

```python
def normalize_cts_resume(candidate: ResumeCandidate) -> NormalizedResume:
```

Keep current CTS behavior green. Do not import or special-case Liepin in this file.

Replace `src/seektalent/normalization.py` with:

```python
from seektalent.resume_normalizers.registry import normalize_resume
from seektalent.resume_normalizers.cts import normalize_locations

__all__ = ["normalize_resume", "normalize_locations"]
```

If existing imports need additional helper names, re-export only the exact names reported by:

```bash
rg -n "from seektalent.normalization import" src tests
```

- [ ] **Step 5: Create Liepin normalizer**

Create `src/seektalent/resume_normalizers/liepin.py`:

```python
from __future__ import annotations

from collections.abc import Mapping

from seektalent.models import (
    NormalizedExperience,
    NormalizedResume,
    ResumeCandidate,
    StructuredResumeEvidence,
    StructuredResumeTimelineItem,
    stable_fallback_resume_id,
    unique_strings,
)
from seektalent.resume_normalizers.cts import normalize_locations

PROHIBITED_LIEPIN_TEXT_KEYS = frozenset(
    {
        "fullText",
        "full_text",
        "rawText",
        "raw_text",
        "page_text",
        "pageText",
        "resumeText",
        "resume_text",
        "resume_free_text",
        "detailBody",
        "detail_body",
        "profile",
        "summary",
    }
)


def normalize_liepin_resume(candidate: ResumeCandidate) -> NormalizedResume:
    raw = candidate.raw
    prohibited = sorted(key for key in PROHIBITED_LIEPIN_TEXT_KEYS if key in raw)
    if prohibited:
        raise ValueError(f"Liepin raw payload must not include whole-page text fields: {', '.join(prohibited)}")

    candidate_name = _first_text(raw.get("candidate_name"), raw.get("candidateName"))
    current_title = _first_text(raw.get("currentTitle"), raw.get("current_title"))
    current_company = _first_text(raw.get("currentCompany"), raw.get("current_company"))
    work_years = _int_or_none(raw.get("workYears")) or candidate.work_year
    locations = normalize_locations([raw.get("city"), candidate.now_location, candidate.expected_location, *_string_list(raw.get("locations"))])[:4]
    work_items = _timeline_items(raw.get("workExperienceList"))
    project_items = _timeline_items(raw.get("projectExperienceList"))
    education_items = _timeline_items(raw.get("educationList"))
    skills = unique_strings([*_string_list(raw.get("skills")), *_string_list(raw.get("skillTags")), *_string_list(raw.get("tags")), *_string_list(raw.get("keywords"))])[:24]
    recent_experiences = [
        NormalizedExperience(title=item.title, company=item.company, duration=item.duration, summary=item.summary)
        for item in work_items[:4]
    ]
    structured = StructuredResumeEvidence(
        identity=_compact({"candidateName": candidate_name, "age": _int_or_none(raw.get("age")), "gender": _text(raw.get("gender"))}),
        current_role=_compact({"title": current_title, "company": current_company, "workYears": work_years}),
        status=_compact({"activeStatus": _text(raw.get("activeStatus")), "jobStatus": _text(raw.get("jobStatus"))}),
        job_intention=_job_intention(raw.get("jobIntention")),
        work_experience=work_items,
        project_experience=project_items,
        education_experience=education_items,
        skills=skills,
        source_metadata=_compact({"sourceUrl": _text(raw.get("sourceUrl")), "scoreEvidenceSource": _text(raw.get("score_evidence_source"))}),
    )
    resume_id = candidate.resume_id
    if candidate.used_fallback_id and not resume_id.startswith("fallback-"):
        resume_id = stable_fallback_resume_id(
            {
                "candidate_name": candidate_name,
                "current_title": current_title,
                "current_company": current_company,
                "locations": locations,
                "recent_experiences": [item.model_dump(mode="json") for item in recent_experiences[:2]],
            }
        )
    missing_fields = [
        name
        for name, present in {
            "candidate_name": bool(candidate_name),
            "current_title": bool(current_title),
            "current_company": bool(current_company),
            "locations": bool(locations),
            "skills": bool(skills),
            "recent_experiences": bool(recent_experiences),
        }.items()
        if not present
    ]
    completeness_score = max(0, 100 - len(missing_fields) * 12)
    return NormalizedResume(
        resume_id=resume_id,
        dedup_key=candidate.dedup_key,
        used_fallback_id=candidate.used_fallback_id,
        source_provider="liepin",
        candidate_name=candidate_name,
        headline=current_title,
        current_title=current_title,
        current_company=current_company,
        years_of_experience=work_years,
        locations=locations,
        education_summary=_education_summary(raw, education_items),
        skills=skills,
        industry_tags=[],
        language_tags=[],
        recent_experiences=recent_experiences,
        key_achievements=[item.summary for item in [*work_items, *project_items] if item.summary][:4],
        structured_evidence=structured,
        raw_text_excerpt=_structured_text_for_legacy_consumers(structured),
        completeness_score=completeness_score,
        missing_fields=missing_fields,
        normalization_notes=["Normalized from Liepin structured detail."],
        source_round=candidate.source_round,
        score_evidence_source=_text(raw.get("score_evidence_source")),
        card_scorecard_ref=_text(raw.get("card_scorecard_ref")),
        detail_scorecard_ref=_text(raw.get("detail_scorecard_ref")),
        score_delta=_int_or_none(raw.get("score_delta")),
        detail_open_reason=_text(raw.get("detail_open_reason")),
        detail_open_policy_version=_text(raw.get("detail_open_policy_version")),
    )


def _timeline_items(value: object) -> list[StructuredResumeTimelineItem]:
    items: list[StructuredResumeTimelineItem] = []
    if not isinstance(value, list):
        return items
    for raw_item in value[:8]:
        if not isinstance(raw_item, Mapping):
            continue
        item = StructuredResumeTimelineItem(
            company=_first_text(raw_item.get("company"), raw_item.get("companyName")),
            title=_first_text(raw_item.get("title"), raw_item.get("position"), raw_item.get("positionName")),
            name=_first_text(raw_item.get("name"), raw_item.get("projectName")),
            school=_first_text(raw_item.get("school"), raw_item.get("schoolName")),
            major=_first_text(raw_item.get("major"), raw_item.get("majorName"), raw_item.get("speciality")),
            degree=_first_text(raw_item.get("degree"), raw_item.get("education"), raw_item.get("educationLevel")),
            duration=_first_text(raw_item.get("duration"), raw_item.get("time"), raw_item.get("dateRange"), raw_item.get("startEndTime")),
            summary=_first_text(raw_item.get("summary"), raw_item.get("description"), raw_item.get("workContent"), raw_item.get("content")),
        )
        if any(item.model_dump(mode="json").values()):
            items.append(item)
    return items


def _structured_text_for_legacy_consumers(evidence: StructuredResumeEvidence) -> str:
    parts: list[str] = []
    parts.extend(str(value) for value in evidence.current_role.values() if str(value).strip())
    parts.extend(evidence.skills)
    for item in [*evidence.work_experience, *evidence.project_experience]:
        parts.extend(part for part in [item.company, item.title, item.name, item.duration, item.summary] if part)
    return " ".join(parts)[:4000]


def _education_summary(raw: Mapping[str, object], education_items: list[StructuredResumeTimelineItem]) -> str:
    if education_items:
        first = education_items[0]
        return " ".join(part for part in [first.school, first.major, first.degree] if part)
    return _text(raw.get("education"))


def _job_intention(value: object) -> dict[str, str | int]:
    if not isinstance(value, Mapping):
        return {}
    return _compact(
        {
            "expectedRole": _first_text(value.get("expectedRole"), value.get("expectedTitle")),
            "expectedIndustry": _text(value.get("expectedIndustry")),
            "expectedCity": _first_text(value.get("expectedCity"), value.get("expectedLocation")),
            "expectedSalary": _text(value.get("expectedSalary")),
        }
    )


def _compact(value: Mapping[str, object | None]) -> dict[str, str | int]:
    return {key: item for key, item in value.items() if isinstance(item, str | int) and (not isinstance(item, str) or item.strip())}


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _string_list(value: object) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed
    return None
```

- [ ] **Step 6: Run normalization tests**

Run:

```bash
pytest tests/test_normalization.py -q
```

Expected: PASS. `raw_text_excerpt` still exists at this point and is derived from structured fields only.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/seektalent/models.py \
  src/seektalent/normalization.py \
  src/seektalent/resume_normalizers/__init__.py \
  src/seektalent/resume_normalizers/registry.py \
  src/seektalent/resume_normalizers/cts.py \
  src/seektalent/resume_normalizers/liepin.py \
  tests/test_normalization.py
git commit -m "feat: split resume normalization by source"
```

## Task 4: Switch Scoring To Allowlisted Structured Evidence

**Files:**
- Modify: `src/seektalent/scoring/scorer.py`
- Modify: `tests/test_llm_input_prompts.py`
- Modify: `tests/test_llm_lifecycle.py`
- Modify: `tests/test_scoring_cache.py`
- Modify: `tests/test_runtime_audit.py`

- [ ] **Step 1: Write scoring prompt tests**

In `tests/test_llm_input_prompts.py`, add:

```python
def test_scoring_prompt_uses_allowlisted_structured_evidence() -> None:
    context = _scoring_context()
    context = context.model_copy(
        update={
            "normalized_resume": context.normalized_resume.model_copy(
                update={
                    "candidate_name": "吴**",
                    "education_summary": "华东师范大学 硕士 设计学",
                    "structured_evidence": StructuredResumeEvidence(
                        identity={"candidateName": "吴**", "age": 32, "gender": "男"},
                        current_role={"title": "资深体验设计工程师", "company": "平安集团", "workYears": 10},
                        job_intention={"expectedSalary": "20-24k"},
                        work_experience=[StructuredResumeTimelineItem(company="平安好医", title="用户体验设计专家", summary="负责体验设计。")],
                        project_experience=[StructuredResumeTimelineItem(name="增长项目", summary="优化转化。")],
                        education_experience=[StructuredResumeTimelineItem(school="华东师范大学", degree="硕士", major="设计学")],
                        skills=["用户研究", "交互设计"],
                        source_metadata={"sourceUrl": "https://h.liepin.com/resume/showresumedetail/abc"},
                    )
                }
            )
        }
    )

    prompt = render_scoring_prompt(context)

    assert "STRUCTURED_RESUME_EVIDENCE" in prompt
    assert "RAW EXCERPT" not in prompt
    assert "RESUME_RAW_EXCERPT" not in prompt
    assert "平安好医" in prompt
    assert "用户研究" in prompt
    assert "吴**" not in prompt
    assert "Candidate identity: (excluded from LLM scoring)" in prompt
    assert "age" not in prompt
    assert "gender" not in prompt
    assert "sourceUrl" not in prompt
    assert "华东师范大学" not in prompt
    assert "硕士" not in prompt
    assert "设计学" not in prompt
```

Add imports if missing:

```python
from seektalent.models import StructuredResumeEvidence, StructuredResumeTimelineItem
```

- [ ] **Step 2: Run scoring prompt test and verify failure**

Run:

```bash
pytest tests/test_llm_input_prompts.py::test_scoring_prompt_uses_allowlisted_structured_evidence -q
```

Expected: FAIL because the prompt still renders `RAW EXCERPT`.

- [ ] **Step 3: Render structured scoring evidence**

In `src/seektalent/scoring/scorer.py`, change the prompt-safety import:

```python
from seektalent.prompt_safety import render_template_version_block, render_untrusted_json_block, render_untrusted_text_block
```

Add:

```python
def _structured_scoring_evidence_payload(resume: NormalizedResume) -> dict[str, object]:
    return resume.structured_evidence.to_scoring_evidence().model_dump(mode="json", exclude_none=True)
```

In `render_scoring_prompt`, replace the whole `RAW EXCERPT` block with:

```python
            "STRUCTURED RESUME EVIDENCE\n"
            + render_untrusted_json_block(
                "STRUCTURED_RESUME_EVIDENCE",
                _structured_scoring_evidence_payload(resume),
            ),
```

In `resume_card_text`, replace the current name line with an explicit exclusion marker:

```python
        "- Candidate identity: (excluded from LLM scoring)\n"
```

Keep the existing education exclusion line and do not render `resume.education_summary` anywhere in the scoring prompt. Do not modify `ResumeScorer.score_candidates_parallel`, `_score_candidate_with_cache`, semaphore usage, or `asyncio.gather`.

- [ ] **Step 4: Add concurrency guard**

In `tests/test_llm_lifecycle.py`, add:

```python
def test_scoring_parallel_implementation_keeps_gather_and_semaphore() -> None:
    source = Path("src/seektalent/scoring/scorer.py").read_text(encoding="utf-8")

    assert "asyncio.Semaphore(self.settings.scoring_max_concurrency)" in source
    assert "await asyncio.gather" in source
    assert "score_candidates_parallel" in source
```

Add:

```python
from pathlib import Path
```

- [ ] **Step 5: Update scoring prompt expectations**

Run:

```bash
rg -n "RAW EXCERPT|RESUME_RAW_EXCERPT|raw_text_excerpt" tests/test_llm_input_prompts.py tests/test_scoring_cache.py tests/test_runtime_audit.py tests/test_llm_lifecycle.py src/seektalent/scoring
```

Replace scoring prompt assertions with `STRUCTURED_RESUME_EVIDENCE`. Leave PRF and runtime-control references for Task 5.

- [ ] **Step 6: Run scoring tests**

Run:

```bash
pytest tests/test_llm_input_prompts.py tests/test_llm_lifecycle.py tests/test_scoring_cache.py tests/test_runtime_audit.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/seektalent/scoring/scorer.py tests/test_llm_input_prompts.py tests/test_llm_lifecycle.py tests/test_scoring_cache.py tests/test_runtime_audit.py
git commit -m "feat: score from allowlisted structured evidence"
```

## Task 5: Move PRF And Runtime-Control Off Raw Excerpts

**Files:**
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `src/seektalent_runtime_control/candidates.py`
- Modify: `src/seektalent_runtime_control/commands.py`
- Modify: `src/seektalent_runtime_control/service.py`
- Modify: `src/seektalent_ui/workbench_candidate_graph.py`
- Modify: `tests/test_llm_prf.py`
- Modify: `tests/test_llm_prf_bakeoff.py`
- Modify: `tests/test_runtime_control_candidate_truth.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_workbench_v2_runtime_service.py`

- [ ] **Step 1: Write runtime-control absence test**

In `tests/test_runtime_control_candidate_truth.py`, add or update the Liepin detail payload test to assert:

```python
normalized_profile = payload["normalizedProfile"]
assert "rawTextExcerpt" not in normalized_profile
assert "raw_text_excerpt" not in normalized_profile
structured = normalized_profile["structuredEvidence"]
assert structured["work_experience"][0]["company"] == "平安好医"
serialized = json.dumps(normalized_profile, ensure_ascii=False)
assert "fullText" not in serialized
assert "page_text" not in serialized
```

- [ ] **Step 2: Write PRF source test**

In `tests/test_llm_prf.py`, add:

```python
def test_llm_prf_uses_structured_resume_evidence_sources() -> None:
    resume = _normalized_resume(
        structured_evidence=StructuredResumeEvidence(
            work_experience=[StructuredResumeTimelineItem(company="Acme", title="Agent Engineer", summary="Built retrieval agents.")],
            project_experience=[StructuredResumeTimelineItem(name="Agent Workflow", summary="Implemented evaluation workflows.")],
            skills=["Python", "RAG"],
        ),
        raw_text_excerpt="legacy text that should not be used",
    )

    sources = _normalized_resume_text_sources(resume, source_kind="grounding_eligible")
    serialized = json.dumps([source.model_dump(mode="json") for source in sources], ensure_ascii=False)

    assert "Built retrieval agents" in serialized
    assert "legacy text that should not be used" not in serialized
    assert "raw_text_excerpt" not in serialized
```

Add imports if missing:

```python
import json
from seektalent.models import StructuredResumeEvidence, StructuredResumeTimelineItem
```

- [ ] **Step 3: Replace PRF raw excerpt source section**

In `src/seektalent/candidate_feedback/llm_prf.py`, change the `LLMPRFSourceSection` literal:

```python
    "structured_resume_evidence",
```

Remove:

```python
    "raw_text_excerpt",
```

In `_SOURCE_SECTION_ORDER`, replace `"raw_text_excerpt"` with `"structured_resume_evidence"`.

Where `_normalized_resume_text_sources` appends from `resume.raw_text_excerpt`, append bounded structured evidence text:

```python
    structured_texts = _structured_resume_source_texts(resume)
    for index, text in enumerate(structured_texts):
        source_texts.append(
            _build_source_text(
                resume_id=resume.resume_id,
                source_section="structured_resume_evidence",
                original_field_path=f"structured_evidence.{index}",
                text=text,
                source_kind=source_kind,
                source_text_index=len(source_texts),
                rank_reason="structured_resume_evidence",
            )
        )
```

Add:

```python
def _structured_resume_source_texts(resume: NormalizedResume) -> list[str]:
    evidence = resume.structured_evidence
    texts: list[str] = []
    for item in [*evidence.work_experience, *evidence.project_experience]:
        text = " ".join(part for part in [item.company, item.title, item.name, item.duration, item.summary] if part)
        if text:
            texts.append(text[:LLM_PRF_MAX_SOURCE_TEXT_CHARS])
    for skill in evidence.skills[:12]:
        if skill:
            texts.append(skill[:LLM_PRF_MAX_SOURCE_TEXT_CHARS])
    return texts[:LLM_PRF_MAX_SOURCE_TEXTS_PER_SEED_RESUME]
```

- [ ] **Step 4: Make runtime-control payloads explicit**

In `src/seektalent_runtime_control/candidates.py`, remove reads or emits of `raw_text_excerpt` and `rawTextExcerpt`. `_normalized_profile_payload` receives `NormalizedResume.model_dump(...)` as a mapping. Keep it mapping-based, remove `rawTextExcerpt`, and add explicit structured evidence:

```python
return {
    "resumeId": normalized.get("resume_id"),
    "sourceProvider": normalized.get("source_provider"),
    "candidateName": normalized.get("candidate_name"),
    "headline": normalized.get("headline"),
    "currentTitle": normalized.get("current_title"),
    "currentCompany": normalized.get("current_company"),
    "yearsOfExperience": normalized.get("years_of_experience"),
    "locations": _list(normalized.get("locations")),
    "skills": _list(normalized.get("skills")),
    "recentExperiences": _list(normalized.get("recent_experiences")),
    "structuredEvidence": _mapping(normalized.get("structured_evidence")),
    "completenessScore": normalized.get("completeness_score"),
    "missingFields": _list(normalized.get("missing_fields")),
}
```

Run this scan and update `commands.py`, `service.py`, and `workbench_candidate_graph.py` only where it reports pass-through:

```bash
rg -n "rawTextExcerpt|raw_text_excerpt|fullText|page_text" src/seektalent_runtime_control src/seektalent_ui/workbench_candidate_graph.py
rg -n "\"rawText\"" src/seektalent_runtime_control src/seektalent_ui/workbench_candidate_graph.py
```

Expected: no candidate evidence/profile/WTS Liepin resume-text hits. Leave `rawText` hits in `src/seektalent_runtime_control/commands.py`, `src/seektalent_runtime_control/service.py`, and next-round requirement tests unchanged unless a separate requirement-review contract migration is explicitly approved.

- [ ] **Step 5: Run PRF and runtime-control tests**

Run:

```bash
pytest tests/test_llm_prf.py tests/test_llm_prf_bakeoff.py tests/test_runtime_control_candidate_truth.py tests/test_workbench_api.py tests/test_workbench_v2_runtime_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/seektalent/candidate_feedback/llm_prf.py \
  src/seektalent_runtime_control/candidates.py \
  src/seektalent_runtime_control/commands.py \
  src/seektalent_runtime_control/service.py \
  src/seektalent_ui/workbench_candidate_graph.py \
  tests/test_llm_prf.py \
  tests/test_llm_prf_bakeoff.py \
  tests/test_runtime_control_candidate_truth.py \
  tests/test_workbench_api.py \
  tests/test_workbench_v2_runtime_service.py
git commit -m "fix: move runtime consumers to structured resume evidence"
```

## Task 6: Delete `raw_text_excerpt` And Legacy Raw Excerpt Contract

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/normalization.py`
- Modify: `src/seektalent/resume_normalizers/cts.py`
- Modify: `src/seektalent/resume_normalizers/liepin.py`
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `src/seektalent_runtime_control/candidates.py`
- Modify tests reported by the scan in Step 1.

- [ ] **Step 1: Confirm remaining old-field references**

Run:

```bash
rg -n "raw_text_excerpt|rawTextExcerpt|RESUME_RAW_EXCERPT|RAW EXCERPT" src apps tests
```

Expected before this task: remaining hits are model fields, normalizer construction, old tests, and negative assertions. There must be no scoring, PRF, or runtime-control positive dependency after Tasks 4 and 5.

- [ ] **Step 2: Remove field and constructor arguments**

In `src/seektalent/models.py`, delete:

```python
    raw_text_excerpt: str = ""
```

Remove `self.raw_text_excerpt` from `NormalizedResume.scoring_text`.

If `scoring_text` still has callers after Task 4, make it prompt-safe by excluding `candidate_name`, `education_summary`, and any raw excerpt. If it has no callers, delete the property and update tests to assert the scorer uses `StructuredScoringEvidence`.

In `src/seektalent/resume_normalizers/cts.py` and `src/seektalent/resume_normalizers/liepin.py`, remove `raw_text_excerpt=` from `NormalizedResume(...)` construction.

In `src/seektalent/normalization.py`, keep only facade exports; do not reintroduce raw text helpers.

- [ ] **Step 3: Update tests**

Run:

```bash
rg -n "raw_text_excerpt|rawTextExcerpt|RESUME_RAW_EXCERPT|RAW EXCERPT" tests
```

For old positive assertions, replace with structured evidence assertions. Keep only negative assertions that prove the strings are absent from serialized prompts or payloads.

- [ ] **Step 4: Run old-field absence tests**

Run:

```bash
pytest tests/test_normalization.py tests/test_llm_input_prompts.py tests/test_llm_prf.py tests/test_runtime_control_candidate_truth.py tests/test_workbench_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add src/seektalent/models.py \
  src/seektalent/normalization.py \
  src/seektalent/resume_normalizers/cts.py \
  src/seektalent/resume_normalizers/liepin.py \
  src/seektalent/candidate_feedback/llm_prf.py \
  src/seektalent_runtime_control/candidates.py
# Then stage only the exact test files reported by the Step 3 scan.
git commit -m "fix: delete raw resume excerpt contract"
```

## Task 7: Delete `reflection_rationale` End-To-End

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/prompts/reflection.md`
- Modify: `src/seektalent/controller/react_controller.py`
- Modify: `src/seektalent/runtime/reflection_runtime.py`
- Modify: `src/seektalent/runtime/context_views.py`
- Modify: `src/seektalent/runtime/runtime_reports.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent_workbench_v2/runtime_display.py`
- Modify: `src/seektalent_workbench_v2/views.py`
- Modify: `src/seektalent/tui.py`
- Modify: `src/seektalent_ui/agent_workbench_models.py`
- Modify: `src/seektalent_ui/agent_workbench_response.py`
- Modify: `src/seektalent_ui/agent_workbench_rounds.py`
- Modify: `src/seektalent_ui/runtime_graph.py`
- Modify: `src/seektalent_runtime_control/stage_outputs.py`
- Modify: `apps/web-react/src/lib/api/schema.d.ts`
- Modify: `tests/test_reflection_contract.py`
- Modify: `tests/test_llm_input_prompts.py`
- Modify: `tests/test_agent_workbench_contract.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_runtime_audit.py`
- Modify: `tests/test_workbench_runtime_graph.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_workbench_v2_service.py`
- Modify: `tests/test_tui.py`
- Modify: `tests/test_controller_contract.py`
- Modify: `tests/test_llm_fail_fast.py`

- [ ] **Step 1: Write absence test**

Append to `tests/test_reflection_contract.py`:

```python
def test_reflection_models_do_not_have_rationale_fields() -> None:
    assert "reflection_rationale" not in ReflectionAdvice.model_fields
    assert "reflection_rationale" not in ReflectionAdviceDraft.model_fields
```

- [ ] **Step 2: Run absence test and verify failure**

Run:

```bash
pytest tests/test_reflection_contract.py::test_reflection_models_do_not_have_rationale_fields -q
```

Expected: FAIL because the models still expose `reflection_rationale`.

- [ ] **Step 3: Remove reflection rationale from models and prompt**

In `src/seektalent/models.py`, remove `REFLECTION_RATIONALE_MAX_CHARS` if unused, and remove `reflection_rationale` from `ReflectionAdvice`, `ReflectionAdviceDraft`, and `ReflectionSummaryView`.

In `src/seektalent/prompts/reflection.md`, remove any instruction asking for `reflection_rationale`.

In `src/seektalent/reflection/critic.py`, remove materialization of `reflection_rationale`, remove `_public_reflection_reason` if it has no callers, and set stop reason deterministically:

```python
suggested_stop_reason = "reflection_stop" if suggest_stop else None
```

- [ ] **Step 4: Remove public/runtime/UI fields**

Run:

```bash
rg -n "reflection_rationale|reflectionRationale" src apps tests
```

For production files, remove the field. Keep `reflection_summary`, `keyword_advice`, `filter_advice`, `suggest_stop`, and `suggested_stop_reason`.

In `src/seektalent_workbench_v2/views.py`, make `_reflection_text_from_payload` return only `reflectionSummary`:

```python
def _reflection_text_from_payload(payload: dict[str, object]) -> str | None:
    details = payload.get("details")
    if not isinstance(details, dict):
        return None
    value = details.get("reflectionSummary")
    return value if isinstance(value, str) and value.strip() else None
```

Do not change `_observation_text_from_payload`; `resumeQualityComment` remains the Workbench thinking-process observation.

After removing `reflectionRationale` from the FastAPI/Pydantic source models, regenerate `apps/web-react/src/lib/api/schema.d.ts` from the live OpenAPI schema; do not hand-edit generated schema. Run the Workbench API locally and execute:

```bash
cd apps/web-react && SEEKTALENT_OPENAPI_URL=http://127.0.0.1:<port>/openapi.json pnpm api:gen
```

- [ ] **Step 5: Update tests**

Run:

```bash
rg -n "reflection_rationale|reflectionRationale" tests
```

For every `ReflectionAdviceDraft(...)` and `ReflectionAdvice(...)`, remove the rationale argument. Replace positive rationale assertions with `reflection_summary`, `suggest_stop`, or `suggested_stop_reason` assertions. Keep one negative assertion in `tests/test_llm_input_prompts.py`:

```python
assert "reflection_rationale" not in prompt
```

- [ ] **Step 6: Run reflection/UI tests**

Run:

```bash
pytest tests/test_reflection_contract.py \
  tests/test_llm_input_prompts.py \
  tests/test_agent_workbench_contract.py \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_audit.py \
  tests/test_workbench_runtime_graph.py \
  tests/test_workbench_api.py \
  tests/test_workbench_v2_service.py::test_v2_strategy_graph_does_not_show_final_shortlist_before_runtime_result \
  tests/test_workbench_v2_service.py::test_v2_strategy_graph_adds_reflection_only_after_reflection_event \
  tests/test_workbench_v2_service.py::test_v2_feedback_observation_without_reflection_emits_only_observation \
  tests/test_tui.py \
  tests/test_controller_contract.py \
  tests/test_llm_fail_fast.py -q
```

Then run:

```bash
rg -n "reflectionRationale" apps/web-react/src/lib/api/schema.d.ts
```

Expected: pytest PASS and no generated schema hits.

- [ ] **Step 7: Commit Task 7**

```bash
git add src/seektalent/models.py \
  src/seektalent/reflection/critic.py \
  src/seektalent/prompts/reflection.md \
  src/seektalent/controller/react_controller.py \
  src/seektalent/runtime/reflection_runtime.py \
  src/seektalent/runtime/context_views.py \
  src/seektalent/runtime/runtime_reports.py \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/runtime/public_events.py \
  src/seektalent_workbench_v2/runtime_display.py \
  src/seektalent_workbench_v2/views.py \
  src/seektalent/tui.py \
  src/seektalent_ui/agent_workbench_models.py \
  src/seektalent_ui/agent_workbench_response.py \
  src/seektalent_ui/agent_workbench_rounds.py \
  src/seektalent_ui/runtime_graph.py \
  src/seektalent_runtime_control/stage_outputs.py \
  apps/web-react/src/lib/api/schema.d.ts
# Then stage only the exact test files changed in Task 7.
git commit -m "fix: delete reflection rationale output"
```

## Task 8: Add Stable OpenCLI Browser Capability Boundary

**Files:**
- Create: `src/seektalent/opencli_browser/client.py`
- Modify: `src/seektalent/opencli_browser/automation.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/providers/liepin/opencli_browser_cli.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `tests/test_liepin_opencli_browser.py`
- Modify: `tests/test_liepin_provider_adapter.py`
- Modify: `tests/test_liepin_opencli_browser_window_policy.py`
- Create: `tests/test_liepin_opencli_boundary_wrappers.py`

- [ ] **Step 1: Write boundary tests**

In `tests/test_liepin_opencli_browser.py`, add:

```python
from types import SimpleNamespace


class FakeBrowserClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def status(self):
        self.calls.append("status")
        return SimpleNamespace(ok=True, safe_reason_code=None)

    def open_tab(self, url: str):
        self.calls.append(("open_tab", url))
        return True

    def click_ref(self, ref: str):
        self.calls.append(("click_ref", ref))
        return "{}"

    def find_css(self, selector: str, *, limit: int, text_max: int):
        self.calls.append(("find_css", selector, limit, text_max))
        return "[]"

    def readonly_eval(self, script: str):
        self.calls.append(("readonly_eval", script))
        return "{}"

    def run_browser_command(self, command: str, args: tuple[str, ...]):
        self.calls.append(("run_browser_command", command, args))
        if command == "tab" and args == ("list",):
            return "[]"
        if command == "tab" and args[:1] == ("new",):
            return json.dumps({"page": "page-1", "url": args[1]})
        return "{}"

    def wait_time(self, *, seconds: int):
        self.calls.append(("wait_time", seconds))
        return SimpleNamespace(ok=True)

    def close_blank_window(self):
        self.calls.append(("close_blank_window",))
        return True

    def count_windows(self) -> int | None:
        self.calls.append(("count_windows",))
        return 1


def test_liepin_site_adapter_does_not_access_opencli_internal_components() -> None:
    source = Path("src/seektalent/providers/liepin/liepin_site_adapter.py").read_text(encoding="utf-8")

    forbidden_tokens = (
        "self._automation.commands",
        "self._automation.window_counter",
        "self._automation.blank_window_closer",
        "self._automation.current_tab_opener",
        "self._automation._pace_before_action",
    )
    for token in forbidden_tokens:
        assert token not in source


def test_liepin_site_adapter_uses_generic_browser_client_for_recovery(tmp_path: Path) -> None:
    browser = FakeBrowserClient()
    browser_config = OpenCliBrowserConfig(command=("opencli",), session="seektalent-liepin", timeout_seconds=10)
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=("www.liepin.com", "h.liepin.com"),
        allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        lease_dir=tmp_path,
        artifact_root=tmp_path,
        cleanup_worker_enabled=False,
    )
    adapter = LiepinSiteAdapter(browser_config=browser_config, site_config=site_config, browser=browser)

    result = adapter.recover_connection()

    assert result.ok
    assert "status" in browser.calls
    assert ("open_tab", LIEPIN_RECRUITER_SEARCH_URL) in browser.calls
    assert ("count_windows",) in browser.calls


def test_liepin_site_adapter_uses_generic_browser_client_for_tab_open(tmp_path: Path) -> None:
    browser = FakeBrowserClient()
    browser_config = OpenCliBrowserConfig(command=("opencli",), session="seektalent-liepin", timeout_seconds=10)
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=("www.liepin.com", "h.liepin.com"),
        allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        lease_dir=tmp_path,
        artifact_root=tmp_path,
        cleanup_worker_enabled=False,
    )
    adapter = LiepinSiteAdapter(browser_config=browser_config, site_config=site_config, browser=browser)

    result = adapter.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)

    assert result.ok
    assert ("run_browser_command", "tab", ("list",)) in browser.calls
    assert ("run_browser_command", "tab", ("new", LIEPIN_RECRUITER_SEARCH_URL)) in browser.calls
    assert not any(call[0] == "open_tab" for call in browser.calls if isinstance(call, tuple))


def test_liepin_detail_capture_does_not_use_opencli_internals_or_whole_page_text() -> None:
    source = Path("src/seektalent/providers/liepin/liepin_site_adapter.py").read_text(encoding="utf-8")
    detail_block = source.split("def capture_liepin_detail_resume", 1)[1].split("def search_liepin_resumes", 1)[0]

    assert "self._automation" not in detail_block
    assert "_detail_state_text_until_resume_ready" not in detail_block
    assert '"page_text"' not in detail_block
    assert 'payload["fullText"]' not in detail_block


def test_capture_liepin_detail_resume_does_not_persist_whole_page_text(tmp_path: Path) -> None:
    payload = json.loads(_liepin_detail_payload_json())
    payload["fullText"] = "must not persist"
    payload["rawText"] = "must not persist"

    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=json.dumps(payload, ensure_ascii=False),
        outputs={("opencli", "browser", "seektalent-liepin", "get", "url"): LIEPIN_SEARCH_URL},
    )

    result = _runner(commands, lease_dir=tmp_path).capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert result.ok is True
    serialized = json.dumps(
        json.loads((tmp_path / "protected" / "pi-detail" / "run-1" / "collected-resumes.json").read_text()),
        ensure_ascii=False,
    )
    for token in ("fullText", "rawText", "page_text", "must not persist"):
        assert token not in serialized
```

- [ ] **Step 2: Run boundary tests and verify failure**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py \
  tests/test_liepin_provider_adapter.py \
  tests/test_liepin_opencli_browser_window_policy.py \
  tests/test_liepin_opencli_boundary_wrappers.py -q
```

Expected: FAIL because `LiepinSiteAdapter` still reaches into OpenCLI internals directly.

- [ ] **Step 3: Create generic browser protocol**

Create `src/seektalent/opencli_browser/client.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from seektalent.opencli_browser.contracts import OpenCliBrowserResult


class BrowserAutomationClient(Protocol):
    def status(self) -> OpenCliBrowserResult: ...
    def get_url(self) -> OpenCliBrowserResult: ...
    def find(self, *, query: str) -> OpenCliBrowserResult: ...
    def fill(self, *, target_args: tuple[str, ...], text_size: int) -> OpenCliBrowserResult: ...
    def click(self, *, target_args: tuple[str, ...]) -> OpenCliBrowserResult: ...
    def scroll(self, *, direction: str) -> OpenCliBrowserResult: ...
    def wait_time(self, *, seconds: int) -> OpenCliBrowserResult: ...
    def click_ref(self, ref: str) -> str: ...
    def find_css(self, selector: str, *, limit: int, text_max: int) -> str: ...
    def readonly_eval(self, script: str) -> str: ...
    def run_browser_command(self, command: str, args: Sequence[str]) -> str: ...
    def open_tab(self, url: str) -> bool: ...
    def close_blank_window(self) -> bool: ...
    def count_windows(self) -> int | None: ...
```

- [ ] **Step 4: Adapt OpenCLI automation to the protocol**

In `src/seektalent/opencli_browser/automation.py`, add methods that delegate existing internals:

```python
    def open_tab(self, url: str):
        return self.current_tab_opener.open_tab(url)

    def close_blank_window(self):
        return self.blank_window_closer.close_blank()

    def count_windows(self) -> int | None:
        return self.window_counter.count()
```

Do not add `recover_connection` to `BrowserAutomationClient`. Recovery is Liepin-owned because it opens `LIEPIN_RECRUITER_SEARCH_URL`. Do not move Liepin-specific pacing actions such as `apply_liepin_filters` or `open_liepin_detail` into the generic browser client; keep those in `LiepinSiteAdapter`.

- [ ] **Step 5: Make Liepin adapter depend on the protocol**

In `src/seektalent/providers/liepin/liepin_site_adapter.py`, type the automation dependency as:

```python
from seektalent.opencli_browser.client import BrowserAutomationClient
```

Replace direct uses of:

```python
self._automation.commands
self._automation.window_counter
self._automation.blank_window_closer
self._automation.current_tab_opener
```

with protocol methods:

```python
self._browser.run_browser_command(...)
self._browser.count_windows()
self._browser.close_blank_window()
self._browser.open_tab(url)
```

Name the field `_browser` in the adapter constructor so provider code no longer communicates that it owns OpenCLI internals.
Update real construction sites in `src/seektalent/providers/liepin/opencli_browser_cli.py` and `src/seektalent/providers/liepin/client.py` from `automation=...` to `browser=...`.

- [ ] **Step 6: Run boundary tests**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py \
  tests/test_liepin_provider_adapter.py \
  tests/test_liepin_opencli_browser_window_policy.py \
  tests/test_liepin_opencli_boundary_wrappers.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 8**

```bash
git add src/seektalent/opencli_browser/client.py \
  src/seektalent/opencli_browser/automation.py \
  src/seektalent/providers/liepin/liepin_site_adapter.py \
  src/seektalent/providers/liepin/opencli_browser_cli.py \
  src/seektalent/providers/liepin/client.py \
  tests/test_liepin_opencli_browser.py \
  tests/test_liepin_provider_adapter.py \
  tests/test_liepin_opencli_browser_window_policy.py \
  tests/test_liepin_opencli_boundary_wrappers.py
git commit -m "refactor: isolate Liepin from OpenCLI browser internals"
```

## Task 9: Full Verification And Artifact Scan

**Files:**
- Read/verify only unless scan reports misses.

- [ ] **Step 1: Run focused regression suite**

Run:

```bash
pytest tests/test_liepin_provider_mapping.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_browser.py \
  tests/test_liepin_provider_adapter.py \
  tests/test_liepin_opencli_browser_window_policy.py \
  tests/test_liepin_opencli_boundary_wrappers.py \
  tests/test_normalization.py \
  tests/test_llm_input_prompts.py \
  tests/test_llm_lifecycle.py \
  tests/test_scoring_cache.py \
  tests/test_llm_prf.py \
  tests/test_runtime_control_candidate_truth.py \
  tests/test_workbench_api.py \
  tests/test_workbench_v2_runtime_service.py \
  tests/test_reflection_contract.py \
  tests/test_agent_workbench_contract.py \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_audit.py \
  tests/test_workbench_runtime_graph.py \
  tests/test_tui.py \
  tests/test_controller_contract.py \
  tests/test_llm_fail_fast.py -q
```

Expected: PASS.

- [ ] **Step 2: Run production-path forbidden-field scan**

Run:

```bash
rg -n "fullText|full_text|rawText|raw_text|page_text|pageText|resumeText|resume_text|resume_free_text|detailBody|detail_body|rawTextExcerpt|raw_text_excerpt|RESUME_RAW_EXCERPT|RAW EXCERPT|reflection_rationale|reflectionRationale" src apps tests scripts tools
```

Expected: no production-path hits. Allowed hits are exact test literals only in named negative tests for worker-contract rejection, mapper/provider-snapshot sanitizer behavior, and prompt/payload absence assertions. Runtime-control requirement-review `ReviewItem.raw_text` / `reviewItems[].rawText` is allowed only in its existing requirement-amendment contract paths and tests; it is not Liepin resume full text.

- [ ] **Step 3: Run source-boundary artifact scan**

Run:

```bash
rg -n "raw_payload=worker_candidate\\.payload|payload\\.get\\(\"fullText\"\\)|text\\(document\\.querySelector\\(\"#resume-detail-single\"\\)|_LIEPIN_TEXT_FIELD_KEYS|sourceUrl.*STRUCTURED_RESUME_EVIDENCE|resumeText|detailBody" src apps tests scripts tools
```

Expected: no hits.

- [ ] **Step 4: Verify generated Workbench schema**

Run either the full dev workbench verification script or the equivalent OpenAPI generation diff check:

```bash
scripts/verify-dev-workbench.sh
```

Expected: PASS, including committed generated schema changes.

- [ ] **Step 5: Run type and whitespace checks**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 6: Inspect scoring concurrency**

Run:

```bash
rg -n "Semaphore\\(self\\.settings\\.scoring_max_concurrency\\)|asyncio\\.gather|score_candidates_parallel" src/seektalent/scoring/scorer.py
```

Expected: all three patterns remain present.

- [ ] **Step 7: Final commit if fixes were needed during verification**

If Steps 1-5 required edits, commit them:

Stage only the exact files changed during verification fixes, then commit:

```bash
git commit -m "test: verify structured Liepin resume pipeline"
```

If Steps 1-5 required no edits, do not create an empty commit.

## Self-Review

- Spec coverage: Tasks 1 and 9 cover hard Liepin whole-page text deletion across source, artifact, UI, and tests. Tasks 2 through 6 cover structured evidence, source-specific normalization, scoring allowlist, runtime consumer migration, and final old-field deletion. Task 7 removes `reflection_rationale` across backend, UI, runtime-control, generated schema, and tests. Task 8 enforces the OpenCLI/Liepin wrapper boundary.
- Protected field boundary: Task 2 introduces typed `StructuredScoringEvidence`; Task 4 verifies scoring prompt absence of candidate identity, age, gender, source URL, and education summary/school/degree/major.
- Intermediate-state safety: Tasks 2 and 3 keep `raw_text_excerpt`; Tasks 4 and 5 move consumers; Task 6 deletes the old contract after consumers have moved.
- Parallelism: Task 4 and Task 9 explicitly preserve and scan `score_candidates_parallel`, semaphore, and `asyncio.gather`.
- Fixture policy: Task 1 migrates positive fixtures and allows whole-page text only in negative tests that prove rejection or sanitization.
