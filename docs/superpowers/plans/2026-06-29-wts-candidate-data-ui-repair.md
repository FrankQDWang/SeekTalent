# WTS Candidate Data UI Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the narrow WTS candidate data and UI slice so runtime candidate facts, scorecard match fields, OpenCLI source failures, and the React candidate list/detail drawer match the approved WTS design.

**Architecture:** Keep runtime as the owner of candidate facts, runtime-control as the durable projection, Workbench v2 BFF as the typed UI contract, and React as a pure renderer. Do not restore the old final LLM stage and do not rewrite the Workbench v2 transcript/conversation architecture.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, SQLite runtime-control store, pytest, React 19, TypeScript, Vitest, Testing Library, Playwright/browser acceptance.

---

## File Structure

Modify these focused backend files:

- `src/seektalent_runtime_control/candidates.py`  
  Add WTS-ready candidate evidence payload fields and scorecard projection. This remains a projection module only; it must not depend on Workbench v2.
- `tests/test_runtime_control_candidate_truth.py`  
  Cover scorecard projection and deterministic full-text parsing into WTS fields.
- `src/seektalent_workbench_v2/models.py`  
  Add typed WTS candidate summary/detail nested models while keeping existing `sections` for compatibility during migration.
- `src/seektalent_workbench_v2/runtime_service.py`  
  Convert runtime-control candidate evidence into typed WTS BFF payloads.
- `tests/test_workbench_v2_runtime_service.py`  
  Cover WTS summary/detail payloads and no placeholder leakage.
- `src/seektalent_workbench_v2/views.py`  
  Adjust strategy graph/thinking-process projection to show only backend-emitted keyword, observation, and reflection states.
- `tests/test_workbench_v2_service.py`  
  Cover no premature final node and strict keyword/observation/reflection timing.
- `src/seektalent/providers/liepin/liepin_site_adapter.py`  
  Add bounded stale-ref re-observe/retry behavior at the OpenCLI/Liepin boundary.
- `tests/test_liepin_opencli_boundary_wrappers.py`  
  Cover stale-ref retry and persistent stale-ref failure semantics.
- `src/seektalent/runtime/orchestrator.py` or existing source-lane degradation helpers, only if tests prove stale-ref still fails the whole run after adapter repair.  
  Preserve existing candidates when a later source action becomes partial/blocked.
- `tests/test_runtime_source_lanes.py` or `tests/test_runtime_state_flow.py`  
  Cover candidate preservation after late source failure if runtime changes are needed.

Modify these frontend files:

- `apps/web-react/src/lib/api/workbenchV2Types.ts`  
  Define explicit WTS candidate summary/detail fields for Workbench v2 instead of relying on old Agent Workbench types.
- `apps/web-react/src/lib/api/agentWorkbenchTypes.ts`  
  Keep shared candidate component compatibility by adding optional WTS fields to shared structural types where needed.
- `apps/web-react/src/lib/api/workbenchV2Client.ts`  
  Normalize new candidate fields and nested arrays.
- `apps/web-react/src/components/workbench/CandidateCard.tsx`
- `apps/web-react/src/components/workbench/CandidateQueue.tsx`
- `apps/web-react/src/components/workbench/CandidateQueue.css`
- `apps/web-react/src/components/workbench/CandidateDetailDrawer.tsx`
- `apps/web-react/src/components/workbench/CandidateDetailDrawer.css`
- `apps/web-react/src/components/workbench/ThinkingProcessRail.tsx`
- `apps/web-react/src/components/workbench/ConversationScreenV2.tsx`
- `apps/web-react/src/components/workbench/ConversationScreenV2.css`

Modify these frontend tests/fixtures:

- `apps/web-react/src/components/workbench/CandidateCard.test.tsx`
- `apps/web-react/src/components/workbench/CandidateDetailDrawer.test.tsx`
- `apps/web-react/src/components/workbench/CandidateQueue.test.tsx`
- `apps/web-react/src/components/workbench/ConversationScreenV2.test.tsx`
- `apps/web-react/src/lib/api/workbenchV2.test.ts`
- `apps/web-react/src/test/fixtures/agentWorkbenchBff.ts`

Do not modify unrelated Workbench v1 routes, old first-turn stores, memory, transcript persistence, or OpenAI/Bailian agent prompt logic in this plan.

---

### Task 1: Project WTS Candidate Truth From Runtime State

**Files:**
- Modify: `src/seektalent_runtime_control/candidates.py`
- Test: `tests/test_runtime_control_candidate_truth.py`

- [ ] **Step 1: Write a failing test for scorecard projection**

Add this test to `tests/test_runtime_control_candidate_truth.py` after `test_checkpoint_persists_compact_candidate_truth_without_artifacts`:

```python
def test_candidate_truth_projects_scorecard_match_fields() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    scorecard = run_state["scorecards_by_resume_id"]["resume_1"]
    assert isinstance(scorecard, dict)
    scorecard["strengths"] = ["候选人强项：有复杂推荐系统经验"]
    scorecard["weaknesses"] = ["候选人弱项：缺少本地招聘行业经验"]

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    match = truth.evidence[0].payload["match"]
    assert match == {
        "score": 92,
        "fitBucket": "fit",
        "reasoningSummary": "Strong platform engineering match.",
        "strengths": ["候选人强项：有复杂推荐系统经验"],
        "weaknesses": ["候选人弱项：缺少本地招聘行业经验"],
        "sourceRound": 1,
    }
```

- [ ] **Step 2: Run the failing scorecard test**

Run:

```bash
uv run pytest tests/test_runtime_control_candidate_truth.py::test_candidate_truth_projects_scorecard_match_fields -q
```

Expected: FAIL with `KeyError: 'match'`.

- [ ] **Step 3: Implement scorecard projection**

In `src/seektalent_runtime_control/candidates.py`, add this helper near `_safe_detail_payload`:

```python
def _match_payload(scorecard: Mapping[str, object]) -> dict[str, object]:
    return _compact_mapping(
        {
            "score": _int_or_none(scorecard.get("overall_score")),
            "fitBucket": _safe_text(scorecard.get("fit_bucket"), max_length=64),
            "reasoningSummary": _safe_text(scorecard.get("reasoning_summary"), max_length=1600),
            "strengths": _string_list(scorecard.get("strengths"))[:8],
            "weaknesses": _string_list(scorecard.get("weaknesses"))[:8],
            "sourceRound": _int_or_none(scorecard.get("source_round")),
        }
    )
```

Then update `_candidate_evidence` payload:

```python
    payload: dict[str, object] = {
        "providerRank": _int_or_none(evidence_payload.get("provider_rank")),
        "queryFingerprint": _safe_text(evidence_payload.get("query_fingerprint"), max_length=256),
        "reasonCode": _safe_text(evidence_payload.get("reason_code"), max_length=128),
        "safeReasonCodes": _string_list(evidence_payload.get("safe_reason_codes")),
        "candidateProfile": _candidate_profile_payload(candidate),
        "normalizedProfile": _normalized_profile_payload(normalized),
        "safeSummary": _safe_summary_payload(raw),
        "safeDetail": _safe_detail_payload(raw),
        "match": _match_payload(scorecard),
    }
```

- [ ] **Step 4: Run the scorecard test**

Run:

```bash
uv run pytest tests/test_runtime_control_candidate_truth.py::test_candidate_truth_projects_scorecard_match_fields -q
```

Expected: PASS.

- [ ] **Step 5: Write a failing test for deterministic Liepin fullText parsing**

Add this test to `tests/test_runtime_control_candidate_truth.py`:

```python
def test_candidate_truth_extracts_wts_fields_from_liepin_full_text() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    candidate_store = run_state["candidate_store"]
    assert isinstance(candidate_store, dict)
    resume = candidate_store["resume_1"]
    assert isinstance(resume, dict)
    resume["raw"] = {
        "fullText": (
            "潘**\n"
            "在职，看看新机会\n"
            "近30天内活跃 男 32岁 上海 本科 工作10年\n"
            "资深体验设计工程师 · 平安集团\n"
            "求职意向\n"
            "期望岗位：高端设计职位、设计经理/主管\n"
            "期望行业：互联网、其他\n"
            "期望地点：上海\n"
            "期望薪资：20-24k*14薪\n"
            "工作经历\n"
            "2019.06-至今（7年）\n"
            "平安好医｜用户体验设计专家\n"
            "工作内容：提供B端及C端体验设计方案。\n"
            "项目经历\n"
            "2020.05-至今（6年1个月）\n"
            "助力C端业务增长｜项目职务：-\n"
            "项目内容：通过设计调研提升转化率。\n"
            "教育经历\n"
            "2011.09-2014.07（2年10个月）\n"
            "华东师范大学 工业设计 硕士\n"
            "技能标签\n"
            "用户研究 交互设计 数据分析"
        )
    }
    run_state["normalized_store"] = {"resume_1": {}}

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    wts = truth.evidence[0].payload["wtsDetail"]
    assert wts["candidateName"] == "潘**"
    assert wts["activeStatus"] == "近30天内活跃"
    assert wts["jobStatus"] == "在职，看看新机会"
    assert wts["gender"] == "男"
    assert wts["age"] == 32
    assert wts["city"] == "上海"
    assert wts["education"] == "本科"
    assert wts["workYears"] == 10
    assert wts["currentTitle"] == "资深体验设计工程师"
    assert wts["currentCompany"] == "平安集团"
    assert wts["jobIntention"]["expectedCity"] == "上海"
    assert wts["workExperience"][0]["company"] == "平安好医"
    assert wts["projectExperience"][0]["name"] == "助力C端业务增长"
    assert wts["educationExperience"][0]["school"] == "华东师范大学"
    assert "交互设计" in wts["skills"]
```

- [ ] **Step 6: Run the failing fullText parsing test**

Run:

```bash
uv run pytest tests/test_runtime_control_candidate_truth.py::test_candidate_truth_extracts_wts_fields_from_liepin_full_text -q
```

Expected: FAIL with `KeyError: 'wtsDetail'`.

- [ ] **Step 7: Implement WTS detail extraction from raw structured fields and fullText**

In `src/seektalent_runtime_control/candidates.py`, add helper functions below `_safe_detail_payload`. Keep them local to this projection module:

```python
def _wts_detail_payload(raw: Mapping[str, object], candidate: Mapping[str, object], normalized: Mapping[str, object]) -> dict[str, object]:
    full_text = _safe_text(raw.get("fullText"), max_length=20_000) or _safe_text(raw.get("page_text"), max_length=20_000)
    parsed = _parse_liepin_full_text(full_text or "")
    structured = _compact_mapping(
        {
            "candidateName": _safe_text(raw.get("candidate_name"), max_length=120)
            or _safe_text(normalized.get("candidate_name"), max_length=120)
            or parsed.get("candidateName"),
            "activeStatus": _safe_text(candidate.get("active_status"), max_length=120) or parsed.get("activeStatus"),
            "jobStatus": _safe_text(candidate.get("job_state"), max_length=120) or parsed.get("jobStatus"),
            "gender": _safe_text(candidate.get("gender"), max_length=24) or parsed.get("gender"),
            "age": _int_or_none(candidate.get("age")) or parsed.get("age"),
            "city": _safe_text(candidate.get("now_location"), max_length=120) or parsed.get("city"),
            "education": _first(_string_list(candidate.get("education_summaries"))) or parsed.get("education"),
            "workYears": _int_or_none(candidate.get("work_year")) or parsed.get("workYears"),
            "currentTitle": _safe_text(raw.get("currentTitle"), max_length=180)
            or _safe_text(normalized.get("current_title"), max_length=180)
            or parsed.get("currentTitle"),
            "currentCompany": _safe_text(raw.get("currentCompany"), max_length=180)
            or _safe_text(normalized.get("current_company"), max_length=180)
            or parsed.get("currentCompany"),
            "jobIntention": parsed.get("jobIntention"),
            "workExperience": _wts_timeline_items(raw.get("workExperienceList"), fallback=parsed.get("workExperience")),
            "projectExperience": _wts_timeline_items(raw.get("projectExperienceList"), fallback=parsed.get("projectExperience")),
            "educationExperience": _wts_timeline_items(raw.get("educationList"), fallback=parsed.get("educationExperience")),
            "skills": _string_list(raw.get("skills"))[:24] or _string_list(raw.get("skillTags"))[:24] or parsed.get("skills"),
            "sourceUrl": _safe_text(raw.get("sourceUrl"), max_length=500),
        }
    )
    return structured
```

Add deterministic parser helpers. The parser should be conservative: parse clear label/value lines and clear timeline blocks; return an empty mapping when text does not match:

```python
def _parse_liepin_full_text(text: str) -> dict[str, object]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {}
    result: dict[str, object] = {}
    result["candidateName"] = lines[0]
    for line in lines[:8]:
        if "在职" in line or "新机会" in line or "离职" in line:
            result["jobStatus"] = line
        if "近" in line and "活跃" in line:
            result["activeStatus"] = line.split()[0]
        age = _extract_number_before(line, "岁")
        years = _extract_number_after(line, "工作")
        if age is not None:
            result["age"] = age
        if years is not None:
            result["workYears"] = years
        for gender in ("男", "女"):
            if f" {gender} " in f" {line} ":
                result["gender"] = gender
        for education in ("博士", "硕士", "本科", "大专"):
            if education in line:
                result["education"] = education
        for city in ("北京", "上海", "杭州", "深圳", "广州", "成都", "南京", "苏州"):
            if city in line:
                result["city"] = city
    for line in lines[:10]:
        if " · " in line:
            title, company = line.split(" · ", 1)
            result["currentTitle"] = title.strip()
            result["currentCompany"] = company.strip()
            break
    result["jobIntention"] = _parse_labeled_section(lines, "求职意向")
    result["workExperience"] = _parse_timeline_section(lines, "工作经历")
    result["projectExperience"] = _parse_timeline_section(lines, "项目经历")
    result["educationExperience"] = _parse_timeline_section(lines, "教育经历")
    result["skills"] = _parse_skill_section(lines)
    return _compact_mapping(result)
```

Use small helper functions for `_extract_number_before`, `_extract_number_after`, `_parse_labeled_section`, `_parse_timeline_section`, `_parse_skill_section`, and `_wts_timeline_items`. Keep them deterministic and covered by the test above. Do not add LLM calls.

Then update `_candidate_evidence` payload:

```python
        "wtsDetail": _wts_detail_payload(raw, candidate, normalized),
```

- [ ] **Step 8: Run candidate truth tests**

Run:

```bash
uv run pytest tests/test_runtime_control_candidate_truth.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```bash
git add src/seektalent_runtime_control/candidates.py tests/test_runtime_control_candidate_truth.py
git commit -m "feat: project WTS candidate truth"
```

---

### Task 2: Expose Typed WTS Candidate Views From Workbench v2 BFF

**Files:**
- Modify: `src/seektalent_workbench_v2/models.py`
- Modify: `src/seektalent_workbench_v2/runtime_service.py`
- Test: `tests/test_workbench_v2_runtime_service.py`

- [ ] **Step 1: Write a failing BFF detail test**

In `tests/test_workbench_v2_runtime_service.py`, update `CandidateFactStore.list_candidate_evidence()` payload to include:

```python
                    "match": {
                        "score": 92,
                        "fitBucket": "fit",
                        "reasoningSummary": "可独立主导 0-1 产品体验搭建。",
                        "strengths": ["擅长通过定量和定性调研挖掘真实痛点。"],
                        "weaknesses": ["AI 产品体验设计项目未在简历中明确体现。"],
                        "sourceRound": 1,
                    },
                    "wtsDetail": {
                        "candidateName": "吴所谓",
                        "activeStatus": "近30天内活跃",
                        "jobStatus": "在职，看看新机会",
                        "gender": "男",
                        "age": 32,
                        "city": "上海",
                        "education": "本科",
                        "workYears": 10,
                        "currentTitle": "资深体验设计工程师",
                        "currentCompany": "平安集团",
                        "jobIntention": {
                            "expectedRole": "高端设计职位",
                            "expectedIndustry": "互联网、其他",
                            "expectedCity": "上海",
                            "expectedSalary": "20-24k*14薪",
                        },
                        "workExperience": [
                            {
                                "dateRange": "2019.06-至今（7年）",
                                "company": "平安好医",
                                "title": "用户体验设计专家",
                                "description": "提供 B 端及 C 端体验设计方案。",
                            }
                        ],
                        "projectExperience": [
                            {
                                "dateRange": "2020.05-至今（6年1个月）",
                                "name": "助力C端业务增长",
                                "role": "-",
                                "description": "通过设计调研提升转化率。",
                            }
                        ],
                        "educationExperience": [
                            {
                                "dateRange": "2011.09-2014.07（2年10个月）",
                                "school": "华东师范大学",
                                "major": "工业设计",
                                "degree": "硕士",
                            }
                        ],
                        "skills": ["用户研究", "交互设计"],
                        "sourceUrl": "https://h.liepin.com/resume/showresumedetail/?res_id_encode=test",
                    },
```

Add assertions to `test_runtime_service_candidate_detail_projects_wts_profile_fields()`:

```python
    assert detail["avatarLabel"] == "吴"
    assert detail["match"] == {
        "summary": "可独立主导 0-1 产品体验搭建。",
        "strengths": ["擅长通过定量和定性调研挖掘真实痛点。"],
        "weaknesses": ["AI 产品体验设计项目未在简历中明确体现。"],
        "score": 92,
        "fitBucket": "fit",
    }
    assert detail["jobIntention"]["expectedSalary"] == "20-24k*14薪"
    assert detail["workExperience"][0]["company"] == "平安好医"
    assert detail["projectExperience"][0]["name"] == "助力C端业务增长"
    assert detail["educationExperience"][0]["school"] == "华东师范大学"
    assert detail["skills"] == ["用户研究", "交互设计"]
    assert detail["sourceUrl"].startswith("https://h.liepin.com/resume/showresumedetail/")
```

- [ ] **Step 2: Run the failing BFF detail test**

Run:

```bash
uv run pytest tests/test_workbench_v2_runtime_service.py::test_runtime_service_candidate_detail_projects_wts_profile_fields -q
```

Expected: FAIL because `avatarLabel`, `match`, typed experiences, and `sourceUrl` are missing.

- [ ] **Step 3: Add typed Pydantic models**

In `src/seektalent_workbench_v2/models.py`, add these classes before `WorkbenchV2CandidateSummaryView`:

```python
class WorkbenchV2CandidateMatchView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    score: int | None = Field(default=None, ge=0, le=100)
    fitBucket: str | None = None


class WorkbenchV2CandidateJobIntentionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRole: str | None = None
    expectedIndustry: str | None = None
    expectedCity: str | None = None
    expectedSalary: str | None = None


class WorkbenchV2CandidateTimelineItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dateRange: str | None = None
    title: str | None = None
    company: str | None = None
    school: str | None = None
    major: str | None = None
    degree: str | None = None
    name: str | None = None
    role: str | None = None
    description: str | None = None
```

Extend `WorkbenchV2CandidateSummaryView`:

```python
    avatarLabel: str | None = None
    avatarColorKey: str | None = None
    sourceLabel: str | None = None
```

Extend `WorkbenchV2CandidateDetailView`:

```python
    avatarLabel: str | None = None
    avatarColorKey: str | None = None
    match: WorkbenchV2CandidateMatchView | None = None
    jobIntention: WorkbenchV2CandidateJobIntentionView | None = None
    workExperience: list[WorkbenchV2CandidateTimelineItemView] = Field(default_factory=list)
    projectExperience: list[WorkbenchV2CandidateTimelineItemView] = Field(default_factory=list)
    educationExperience: list[WorkbenchV2CandidateTimelineItemView] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    sourceUrl: str | None = None
```

- [ ] **Step 4: Implement BFF candidate mapping helpers**

In `src/seektalent_workbench_v2/runtime_service.py`, add helpers near existing candidate helpers:

```python
def _candidate_avatar_label(display_name: str) -> str:
    clean = display_name.strip()
    if not clean:
        return "候"
    if clean.startswith("候选人 "):
        return "候"
    return clean[0]


def _candidate_avatar_color_key(identity_id: str) -> str:
    bucket = sum(ord(character) for character in identity_id) % 6
    return f"avatar-{bucket}"


def _candidate_match(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> dict[str, object] | None:
    match_payload = _first_mapping_from_payloads(evidence, "match")
    summary = (
        _text_from_mapping(match_payload, "reasoningSummary")
        if match_payload is not None
        else None
    ) or _clean_text(identity.summary)
    strengths = _list_texts_from_mapping(match_payload, "strengths") if match_payload is not None else []
    weaknesses = _list_texts_from_mapping(match_payload, "weaknesses") if match_payload is not None else []
    score = _candidate_score(identity, evidence)
    fit_bucket = _text_from_mapping(match_payload, "fitBucket") if match_payload is not None else identity.fit_bucket
    payload = {
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "score": score,
        "fitBucket": fit_bucket,
    }
    return {key: value for key, value in payload.items() if value not in (None, [], "")} or None
```

Add WTS detail readers:

```python
def _candidate_wts_detail(evidence: Sequence[RuntimeControlCandidateEvidence]) -> dict[str, object]:
    return _first_mapping_from_payloads(evidence, "wtsDetail") or {}


def _candidate_job_intention(evidence: Sequence[RuntimeControlCandidateEvidence]) -> dict[str, object] | None:
    wts = _candidate_wts_detail(evidence)
    intention = wts.get("jobIntention")
    return intention if isinstance(intention, dict) and intention else None


def _candidate_timeline(evidence: Sequence[RuntimeControlCandidateEvidence], key: str) -> list[dict[str, object]]:
    wts = _candidate_wts_detail(evidence)
    value = wts.get(key)
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _candidate_skills(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    wts = _candidate_wts_detail(evidence)
    value = wts.get("skills")
    if not isinstance(value, list):
        return _skill_items(evidence)
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
```

Add `_first_mapping_from_payloads` and `_list_texts_from_mapping`:

```python
def _first_mapping_from_payloads(
    evidence: Sequence[RuntimeControlCandidateEvidence],
    key: str,
) -> dict[str, object] | None:
    for item in evidence:
        value = item.payload.get(key)
        if isinstance(value, dict) and value:
            return {str(field): field_value for field, field_value in value.items() if isinstance(field, str)}
    return None


def _list_texts_from_mapping(payload: dict[str, object] | None, key: str) -> list[str]:
    if payload is None:
        return []
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
```

Update `list_candidate_summaries()` to include:

```python
                    "avatarLabel": _candidate_avatar_label(display_name),
                    "avatarColorKey": _candidate_avatar_color_key(identity.identity_id),
                    "sourceLabel": "猎聘" if "liepin" in source_kinds else "CTS 实验" if "cts" in source_kinds else None,
```

Update `get_candidate_detail()` to compute `display_name` once by calling `_candidate_display_name(identity, evidence, fallback="候选人")` and return:

```python
            "avatarLabel": _candidate_avatar_label(display_name),
            "avatarColorKey": _candidate_avatar_color_key(identity.identity_id),
            "match": _candidate_match(identity, evidence),
            "jobIntention": _candidate_job_intention(evidence),
            "workExperience": _candidate_timeline(evidence, "workExperience"),
            "projectExperience": _candidate_timeline(evidence, "projectExperience"),
            "educationExperience": _candidate_timeline(evidence, "educationExperience"),
            "skills": _candidate_skills(evidence),
            "sourceUrl": _text_from_mapping(_candidate_wts_detail(evidence), "sourceUrl"),
```

- [ ] **Step 5: Run BFF tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_runtime_service.py::test_runtime_service_candidate_detail_projects_wts_profile_fields tests/test_workbench_v2_runtime_service.py::test_runtime_service_does_not_claim_source_without_evidence -q
```

Expected: PASS.

- [ ] **Step 6: Run model/route contract tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_routes.py tests/test_workbench_v2_service.py::test_conversation_view_includes_live_candidate_summaries -q
```

Expected: PASS. If the named service test does not exist in this checkout, run:

```bash
uv run pytest tests/test_workbench_v2_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/seektalent_workbench_v2/models.py src/seektalent_workbench_v2/runtime_service.py tests/test_workbench_v2_runtime_service.py
git commit -m "feat: expose WTS candidate views"
```

---

### Task 3: Make Strategy Graph And Thinking Process Event-Truthful

**Files:**
- Modify: `src/seektalent_workbench_v2/views.py`
- Test: `tests/test_workbench_v2_service.py`

- [ ] **Step 1: Add failing tests for no premature final node and round cards**

In `tests/test_workbench_v2_service.py`, add or update v2 view tests with this shape:

```python
def test_v2_strategy_graph_does_not_show_final_shortlist_before_runtime_result() -> None:
    record = _conversation_record_with_events(
        runtime_state="running",
        runtime_run_id="rtrun_truthful",
        events=[
            _runtime_progress_event(
                event_id="evt_query",
                step=1,
                payload={
                    "runtimeEventType": "runtime_round_query_ready",
                    "runtimeEventSeq": 10,
                    "roundNo": 1,
                    "stage": "round_query",
                    "state": "completed",
                    "status": "completed",
                    "details": {"keywordQuery": "交互设计 用户研究", "queryTerms": ["交互设计", "用户研究"]},
                    "summary": "第 1 轮查询策略已生成。",
                },
            ),
            _runtime_progress_event(
                event_id="evt_scoring",
                step=2,
                payload={
                    "runtimeEventType": "runtime_round_scoring_completed",
                    "runtimeEventSeq": 20,
                    "roundNo": 1,
                    "stage": "scoring",
                    "state": "completed",
                    "status": "completed",
                    "details": {"resumeQualityComment": "候选人整体匹配用户研究和复杂项目经验。"},
                    "summary": "第 1 轮评分完成。",
                },
            ),
        ],
    )

    view = conversation_record_to_view(record)

    assert [node.label for node in view.strategyGraph.nodes] == [
        "需求拆解",
        "第 1 轮 · 关键词",
        "第 1 轮 · observation",
    ]
    assert not any(node.kind == "final" for node in view.strategyGraph.nodes)
    assert [card.title for card in view.thinkingProcess.rounds[0].cards] == ["关键词", "observation"]
```

Also add:

```python
def test_v2_strategy_graph_adds_reflection_only_after_reflection_event() -> None:
    record = _conversation_record_with_events(
        runtime_state="running",
        runtime_run_id="rtrun_reflection",
        events=[
            _runtime_progress_event(
                event_id="evt_query",
                step=1,
                payload={
                    "runtimeEventType": "runtime_round_query_ready",
                    "runtimeEventSeq": 10,
                    "roundNo": 1,
                    "stage": "round_query",
                    "state": "completed",
                    "status": "completed",
                    "details": {"keywordQuery": "交互设计 用户研究", "queryTerms": ["交互设计", "用户研究"]},
                    "summary": "第 1 轮查询策略已生成。",
                },
            ),
            _runtime_progress_event(
                event_id="evt_reflection",
                step=2,
                payload={
                    "runtimeEventType": "runtime_round_feedback_completed",
                    "runtimeEventSeq": 30,
                    "roundNo": 1,
                    "stage": "reflection",
                    "state": "completed",
                    "status": "completed",
                    "details": {"reflectionSummary": "下一轮降低行业限制，扩大 B 端体验关键词。"},
                    "summary": "第 1 轮复盘完成。",
                },
            ),
        ],
    )

    view = conversation_record_to_view(record)

    assert [card.title for card in view.thinkingProcess.rounds[0].cards] == ["关键词", "反思和下一轮变更"]
    assert view.strategyGraph.nodes[-1].label == "第 1 轮 · 反思"
```

Use existing helper style in `tests/test_workbench_v2_service.py`. If `_conversation_record_with_events` or `_runtime_progress_event` does not exist, create small local helpers in the test file that construct `WorkbenchV2ConversationRecord` and `WorkbenchV2TranscriptEvent`.

- [ ] **Step 2: Run the failing graph tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_service.py -k "strategy_graph_does_not_show_final_shortlist or strategy_graph_adds_reflection" -q
```

Expected: FAIL because labels currently use `查询包`, `Top Pool`, and `下一轮策略`.

- [ ] **Step 3: Update v2 graph labels and phases**

In `src/seektalent_workbench_v2/views.py`, change graph node creation:

```python
        if event_type in {"runtime_round_query_ready", "runtime_search_started"} or stage == "round_query":
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-keywords",
                kind="phase",
                label=f"第 {round_no} 轮 · 关键词",
                summary=_keyword_query_from_payload(event.payload) or summary,
                roundNo=round_no,
                phase="keywords",
                stage="round_query",
                status=status,
                sourceKind="all",
            )
```

Replace scoring/top-pool node creation with observation:

```python
        elif event_type in {
            "runtime_round_scoring_completed",
        } or stage == "scoring":
            observation = _observation_text_from_payload(event.payload)
            if observation is not None:
                _upsert_graph_node(
                    node_order,
                    node_specs,
                    f"v2-round-{round_no}-observation",
                    kind="phase",
                    label=f"第 {round_no} 轮 · observation",
                    summary=observation,
                    roundNo=round_no,
                    phase="observation",
                    stage="scoring",
                    status=status,
                    sourceKind="all",
                )
```

Replace feedback node label:

```python
                f"v2-round-{round_no}-reflection",
                kind="phase",
                label=f"第 {round_no} 轮 · 反思",
                summary=_reflection_text_from_payload(event.payload) or summary,
                roundNo=round_no,
                phase="reflection",
```

Keep final node creation only for `runtime_result`, `runtime_finalization_completed`, or `runtime_run_completed`.

- [ ] **Step 4: Run graph and service tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_service.py -q
```

Expected: PASS. Update old assertions that intentionally expected `查询包`, `Top Pool`, or `下一轮策略` to the confirmed WTS terms.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/seektalent_workbench_v2/views.py tests/test_workbench_v2_service.py
git commit -m "fix: align v2 runtime graph with WTS event timing"
```

---

### Task 4: Recover From OpenCLI Stale References Without Losing Existing Candidates

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Test: `tests/test_liepin_opencli_boundary_wrappers.py`
- Modify if required by failing tests: `src/seektalent/runtime/orchestrator.py`
- Test if runtime change is required: `tests/test_runtime_source_lanes.py`

- [ ] **Step 1: Write a failing adapter retry test**

Add this test to `tests/test_liepin_opencli_boundary_wrappers.py` near the existing public boundary test:

```python
def test_liepin_site_adapter_reobserves_and_retries_stale_ref_once(tmp_path: Path) -> None:
    from seektalent.opencli_browser.contracts import OpenCliBrowserConfig, OpenCliBrowserError, OpenCliBrowserResult
    from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
    from seektalent.providers.liepin.liepin_site_adapter import LiepinOpenCliSiteConfig, LiepinSiteAdapter

    class Automation:
        commands = object()
        window_counter = object()
        blank_window_closer = object()
        current_tab_opener = object()

        def __init__(self) -> None:
            self.click_calls = 0
            self.state_calls = 0

        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="status")

        def run_browser_command(self, command: str, args: tuple[str, ...]) -> str:
            if command == "state":
                self.state_calls += 1
                return "猎聘 搜索结果 [ref=44] 查看详情"
            if command == "get" and args == ("url",):
                return LIEPIN_RECRUITER_SEARCH_URL
            return ""

        def click_ref(self, ref: str) -> str:
            assert ref == "44"
            self.click_calls += 1
            if self.click_calls == 1:
                raise OpenCliBrowserError("opencli_stale_ref")
            return "clicked"

    automation = Automation()
    adapter = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-liepin",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("www.liepin.com", "h.liepin.com"),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
            lease_dir=tmp_path,
        ),
        automation=automation,  # type: ignore[arg-type]
    )

    adapter._click_liepin_detail_ref("44")

    assert automation.click_calls == 2
    assert automation.state_calls == 1
```

- [ ] **Step 2: Run the failing adapter retry test**

Run:

```bash
uv run pytest tests/test_liepin_opencli_boundary_wrappers.py::test_liepin_site_adapter_reobserves_and_retries_stale_ref_once -q
```

Expected: FAIL because `_click_liepin_detail_ref` currently raises on stale ref.

- [ ] **Step 3: Implement one retry for stale ref at the fixed-action boundary**

In `src/seektalent/providers/liepin/liepin_site_adapter.py`, add:

```python
    def _run_stale_ref_retry_once(self, call: Callable[[], str]) -> str:
        try:
            return self._run_opencli_call(call)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_stale_ref":
                raise
            self.state()
            return self._run_opencli_call(call)
```

Use this helper only for idempotent fixed-target actions:

```python
    def _click_liepin_detail_ref(self, ref: str) -> None:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._run_stale_ref_retry_once(lambda: self._automation.click_ref(ref))
        self._touch_lease()
```

Also update `_click_native_filter_ref` if tests show stale refs occur there:

```python
    def _click_native_filter_ref(self, ref: str) -> None:
        self._run_stale_ref_retry_once(lambda: self._automation.click_ref(ref))
        self._touch_lease()
```

Do not retry broad search commands or network failures.

- [ ] **Step 4: Run adapter tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_boundary_wrappers.py -q
```

Expected: PASS.

- [ ] **Step 5: Verify existing runtime source degradation behavior still passes**

The adapter retry is expected to fix the observed stale-ref failure at the source boundary. Runtime already has source degradation coverage, so first verify those guardrails without changing orchestration code.

Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_runtime_source_degradation.py -q
```

Expected: PASS.

- [ ] **Step 6: Inspect runtime behavior only if Step 5 fails**

If Step 5 fails after the adapter retry change, inspect the failing assertion and limit the runtime fix to preserving existing `candidate_store`, `normalized_store`, `candidate_identities`, and `scorecards_by_resume_id` when a later source lane result is `partial` or `failed`. Do not change first-round/no-candidate failure behavior.

The expected runtime invariant after any such fix is:

```python
assert run_state.candidate_store
assert run_state.normalized_store
assert run_state.candidate_identities
assert run_state.scorecards_by_resume_id
```

- [ ] **Step 7: Commit Task 4**

```bash
git add src/seektalent/providers/liepin/liepin_site_adapter.py tests/test_liepin_opencli_boundary_wrappers.py src/seektalent/runtime/orchestrator.py tests/test_runtime_source_lanes.py
git commit -m "fix: recover stale OpenCLI refs"
```

If `orchestrator.py` and `tests/test_runtime_source_lanes.py` were not changed, omit them from `git add`.

---

### Task 5: Render WTS Candidate List And Detail Drawer From Typed Fields

**Files:**
- Modify: `apps/web-react/src/lib/api/workbenchV2Types.ts`
- Modify: `apps/web-react/src/lib/api/agentWorkbenchTypes.ts`
- Modify: `apps/web-react/src/lib/api/workbenchV2Client.ts`
- Modify: `apps/web-react/src/lib/api/workbenchV2.test.ts`
- Modify: `apps/web-react/src/components/workbench/CandidateCard.tsx`
- Modify: `apps/web-react/src/components/workbench/CandidateQueue.tsx`
- Modify: `apps/web-react/src/components/workbench/CandidateQueue.css`
- Modify: `apps/web-react/src/components/workbench/CandidateDetailDrawer.tsx`
- Modify: `apps/web-react/src/components/workbench/CandidateDetailDrawer.css`
- Test: `apps/web-react/src/components/workbench/CandidateCard.test.tsx`
- Test: `apps/web-react/src/components/workbench/CandidateDetailDrawer.test.tsx`
- Test: `apps/web-react/src/components/workbench/CandidateQueue.test.tsx`
- Fixture: `apps/web-react/src/test/fixtures/agentWorkbenchBff.ts`

- [ ] **Step 1: Update failing frontend tests for WTS card requirements**

In `apps/web-react/src/components/workbench/CandidateCard.test.tsx`, replace the first test name and assertions:

```typescript
it("renders a WTS candidate card with surname avatar, chips, and detail action", () => {
  expect.hasAssertions();

  render(
    <CandidateCard
      candidate={{
        ...candidateFixture,
        avatarLabel: "吴",
        avatarColorKey: "avatar-2",
        displayName: "吴所谓",
        headline: "资深体验设计工程师",
        company: "平安集团",
        status: "fit",
      }}
    />,
  );

  const article = screen.getByRole("article", { name: "吴所谓" });
  expect(within(article).getByText("吴")).toBeVisible();
  expect(within(article).getByText("猎聘")).toBeVisible();
  expect(within(article).getByText("资深体验设计工程师 · 平安集团")).toBeVisible();
  expect(within(article).getByText("32岁")).toBeVisible();
  expect(within(article).getByText("上海")).toBeVisible();
  expect(within(article).getByText("本科")).toBeVisible();
  expect(within(article).getByText("工作10年")).toBeVisible();
  expect(within(article).getByRole("button", { name: "查看详情" })).toBeEnabled();
});
```

Remove expectations that require a visible score badge if the WTS asset does not show it on the card.

- [ ] **Step 2: Update failing drawer test for typed sections**

In `apps/web-react/src/components/workbench/CandidateDetailDrawer.test.tsx`, add a fixture detail object with typed fields:

```typescript
const wtsDetail = {
  ...agentWorkbenchCandidateDetailFixture,
  accessState: "allowed",
  activeStatus: "近30天内活跃",
  age: 32,
  avatarLabel: "吴",
  avatarColorKey: "avatar-2",
  company: "平安集团",
  detailAvailability: "available",
  displayName: "吴所谓",
  education: "本科",
  evidence: [],
  evidenceLevel: "detail",
  experienceYears: 10,
  gender: "男",
  headline: "资深体验设计工程师",
  jobStatus: "在职，看看新机会",
  location: "上海",
  match: {
    summary: "可独立主导 0-1 产品体验搭建。",
    strengths: ["擅长通过定量和定性调研挖掘真实痛点。"],
    weaknesses: ["AI 产品体验设计项目未在简历中明确体现。"],
    score: 92,
    fitBucket: "fit",
  },
  jobIntention: {
    expectedRole: "高端设计职位",
    expectedIndustry: "互联网、其他",
    expectedCity: "上海",
    expectedSalary: "20-24k*14薪",
  },
  workExperience: [
    {
      dateRange: "2019.06-至今（7年）",
      company: "平安好医",
      title: "用户体验设计专家",
      description: "提供 B 端及 C 端体验设计方案。",
    },
  ],
  projectExperience: [
    {
      dateRange: "2020.05-至今（6年1个月）",
      name: "助力C端业务增长",
      role: "-",
      description: "通过设计调研提升转化率。",
    },
  ],
  educationExperience: [
    {
      dateRange: "2011.09-2014.07（2年10个月）",
      school: "华东师范大学",
      major: "工业设计",
      degree: "硕士",
    },
  ],
  skills: ["用户研究", "交互设计"],
  sections: [],
  sourceKinds: ["liepin"],
  sourceUrl: "https://h.liepin.com/resume/showresumedetail/?res_id_encode=test",
};
```

Update assertions:

```typescript
expect(screen.getByText("匹配程度")).toBeVisible();
expect(screen.getByText("推荐理由：可独立主导 0-1 产品体验搭建。")).toBeVisible();
expect(screen.getByText("候选人强项：擅长通过定量和定性调研挖掘真实痛点。")).toBeVisible();
expect(screen.getByText("候选人弱项：AI 产品体验设计项目未在简历中明确体现。")).toBeVisible();
expect(screen.getByText("求职意向")).toBeVisible();
expect(screen.getByText("期望薪资：20-24k*14薪")).toBeVisible();
expect(screen.getByText("工作经历")).toBeVisible();
expect(screen.getByText("平安好医｜用户体验设计专家")).toBeVisible();
expect(screen.getByText("项目经历")).toBeVisible();
expect(screen.getByText("助力C端业务增长")).toBeVisible();
expect(screen.getByText("教育经历")).toBeVisible();
expect(screen.getByText("华东师范大学｜工业设计｜硕士")).toBeVisible();
expect(screen.getByText("技能标签")).toBeVisible();
expect(screen.queryByText("读取完整详情前需要审批")).not.toBeInTheDocument();
expect(screen.queryByText("安全摘要")).not.toBeInTheDocument();
expect(screen.queryByText("脱敏")).not.toBeInTheDocument();
```

- [ ] **Step 3: Run failing frontend candidate tests**

Run:

```bash
cd apps/web-react
pnpm test -- CandidateCard.test.tsx CandidateDetailDrawer.test.tsx CandidateQueue.test.tsx
```

Expected: FAIL where typed fields are not normalized/rendered yet.

- [ ] **Step 4: Add frontend candidate types**

In `apps/web-react/src/lib/api/agentWorkbenchTypes.ts`, extend the structural candidate types:

```typescript
export type WorkbenchCandidateMatch = {
  summary?: string | null;
  strengths: string[];
  weaknesses: string[];
  score?: number | null;
  fitBucket?: string | null;
};

export type WorkbenchCandidateJobIntention = {
  expectedRole?: string | null;
  expectedIndustry?: string | null;
  expectedCity?: string | null;
  expectedSalary?: string | null;
};

export type WorkbenchCandidateTimelineItem = {
  dateRange?: string | null;
  title?: string | null;
  company?: string | null;
  school?: string | null;
  major?: string | null;
  degree?: string | null;
  name?: string | null;
  role?: string | null;
  description?: string | null;
};
```

Extend `AgentWorkbenchCandidateSummary`:

```typescript
  avatarColorKey?: string | null;
  avatarLabel?: string | null;
  sourceLabel?: string | null;
```

Extend `AgentWorkbenchCandidateDetailResponse`:

```typescript
  avatarColorKey?: string | null;
  avatarLabel?: string | null;
  jobIntention?: WorkbenchCandidateJobIntention | null;
  match?: WorkbenchCandidateMatch | null;
  workExperience?: WorkbenchCandidateTimelineItem[];
  projectExperience?: WorkbenchCandidateTimelineItem[];
  educationExperience?: WorkbenchCandidateTimelineItem[];
  skills?: string[];
  sourceUrl?: string | null;
```

- [ ] **Step 5: Normalize new fields in Workbench v2 client**

In `apps/web-react/src/lib/api/workbenchV2Types.ts`, change `candidates?: AgentWorkbenchCandidateSummary[];` to keep using the extended shared type. In `apps/web-react/src/lib/api/workbenchV2Client.ts`, ensure detail normalizer returns defaults:

```typescript
return {
  ...response,
  evidence: response.evidence ?? [],
  sections: (response.sections ?? []).map((section) => ({
    ...section,
    items: section.items ?? [],
  })),
  sourceKinds: response.sourceKinds ?? [],
  match: response.match
    ? {
        ...response.match,
        strengths: response.match.strengths ?? [],
        weaknesses: response.match.weaknesses ?? [],
      }
    : null,
  workExperience: response.workExperience ?? [],
  projectExperience: response.projectExperience ?? [],
  educationExperience: response.educationExperience ?? [],
  skills: response.skills ?? [],
};
```

- [ ] **Step 6: Render WTS card**

In `CandidateCard.tsx`:

- Use `candidate.avatarLabel ?? candidate.displayName.slice(0, 1)` for avatar.
- Put `data-avatar-color={candidate.avatarColorKey ?? "avatar-0"}` on the card or avatar.
- Render source badge as `candidate.sourceLabel ?? candidateSourceLabel(candidate.sourceKinds)`.
- Keep card hierarchy matching WTS: name/source in header, headline line, chips, detail button.
- Remove default copy `候选人安全摘要`.

Implementation snippet:

```tsx
const avatarLabel = candidate.avatarLabel ?? candidate.displayName.slice(0, 1);
const sourceLabel = candidate.sourceLabel ?? candidateSourceLabel(candidate.sourceKinds);
const headlineText = candidateHeadline(candidate);

return (
  <span
    className="candidate-card__avatar"
    data-avatar-color={candidate.avatarColorKey ?? "avatar-0"}
    aria-hidden="true"
  >
    {avatarLabel}
  </span>
);
```

- [ ] **Step 7: Render typed WTS detail drawer**

In `CandidateDetailDrawer.tsx`, replace `CandidateDetailBody` section rendering with typed section builders:

```tsx
function CandidateDetailBody({ detail }: { detail: AgentWorkbenchCandidateDetailResponse }) {
  if (detail.accessState !== "allowed") {
    return <CandidateDetailError message="候选人详情暂时不可用。" />;
  }
  return (
    <div aria-label="候选人详情内容" className="candidate-detail-drawer__body" tabIndex={0}>
      <MatchSection match={detail.match} />
      <JobIntentionSection intention={detail.jobIntention} />
      <TimelineSection title="工作经历" items={detail.workExperience ?? []} type="work" />
      <TimelineSection title="项目经历" items={detail.projectExperience ?? []} type="project" />
      <TimelineSection title="教育经历" items={detail.educationExperience ?? []} type="education" />
      <SkillSection skills={detail.skills ?? []} />
    </div>
  );
}
```

Add helper components that hide empty sections:

```tsx
function MatchSection({ match }: { match?: AgentWorkbenchCandidateDetailResponse["match"] | null }) {
  if (!match || (!match.summary && match.strengths.length === 0 && match.weaknesses.length === 0)) {
    return null;
  }
  return (
    <section className="candidate-detail-section">
      <h3>匹配程度</h3>
      <div className="candidate-detail-section__paragraphs">
        {match.summary ? <p>推荐理由：{match.summary}</p> : null}
        {match.strengths.map((item) => <p key={`strength-${item}`}>候选人强项：{item}</p>)}
        {match.weaknesses.map((item) => <p key={`weakness-${item}`}>候选人弱项：{item}</p>)}
      </div>
    </section>
  );
}
```

Use `TimelineSection` to render WTS timeline rows with date dot/line. Do not render evidence sections in the WTS drawer.

- [ ] **Step 8: Update CSS to match WTS assets**

In `CandidateQueue.css`:

- Right card border stays light purple.
- Border radius should be close to WTS asset, not oversized.
- Avatar colors come from `[data-avatar-color]`.
- Remove visible score prominence if it conflicts with asset.

Use:

```css
.candidate-card__avatar[data-avatar-color="avatar-0"] { background: #6d77ff; }
.candidate-card__avatar[data-avatar-color="avatar-1"] { background: #ff9f1c; }
.candidate-card__avatar[data-avatar-color="avatar-2"] { background: #2fb4ce; }
.candidate-card__avatar[data-avatar-color="avatar-3"] { background: #0fbf92; }
.candidate-card__avatar[data-avatar-color="avatar-4"] { background: #7c5cff; }
.candidate-card__avatar[data-avatar-color="avatar-5"] { background: #19b6a3; }
```

In `CandidateDetailDrawer.css`:

- Keep right-side fixed drawer.
- Remove grey centered modal semantics.
- Keep header light blue and body white with rounded top-left inner panel.
- Add timeline styling for work/project/education sections.

- [ ] **Step 9: Run frontend candidate tests**

Run:

```bash
cd apps/web-react
pnpm test -- CandidateCard.test.tsx CandidateDetailDrawer.test.tsx CandidateQueue.test.tsx workbenchV2.test.ts
```

Expected: PASS.

- [ ] **Step 10: Commit Task 5**

```bash
git add apps/web-react/src/lib/api/workbenchV2Types.ts apps/web-react/src/lib/api/agentWorkbenchTypes.ts apps/web-react/src/lib/api/workbenchV2Client.ts apps/web-react/src/lib/api/workbenchV2.test.ts apps/web-react/src/components/workbench/CandidateCard.tsx apps/web-react/src/components/workbench/CandidateQueue.tsx apps/web-react/src/components/workbench/CandidateQueue.css apps/web-react/src/components/workbench/CandidateDetailDrawer.tsx apps/web-react/src/components/workbench/CandidateDetailDrawer.css apps/web-react/src/components/workbench/CandidateCard.test.tsx apps/web-react/src/components/workbench/CandidateDetailDrawer.test.tsx apps/web-react/src/components/workbench/CandidateQueue.test.tsx apps/web-react/src/test/fixtures/agentWorkbenchBff.ts
git commit -m "feat: render WTS candidate drawer"
```

---

### Task 6: Restore Right Rail Layout And WTS Thinking Surface

**Files:**
- Modify: `apps/web-react/src/components/workbench/ConversationScreenV2.tsx`
- Modify: `apps/web-react/src/components/workbench/ConversationScreenV2.css`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.tsx`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.css`
- Test: `apps/web-react/src/components/workbench/ConversationScreenV2.test.tsx`

- [ ] **Step 1: Add failing right-rail tests**

In `ConversationScreenV2.test.tsx`, add:

```typescript
it("keeps the right WTS rail visible for workflow conversations", () => {
  expect.hasAssertions();

  render(<ConversationScreenV2 view={conversationViewWithWorkflowSurface()} />);

  expect(screen.getByRole("region", { name: "对话" })).toBeVisible();
  expect(screen.getByRole("region", { name: "策略图面板" })).toBeVisible();
});
```

Add or update side rail test:

```typescript
it("renders only candidate and thinking tabs in the right rail", () => {
  expect.hasAssertions();

  render(
    <ConversationScreenV2Side
      view={conversationViewWithWorkflowSurface({
        candidates: [
          {
            candidateId: "identity_1",
            rank: 1,
            displayName: "吴所谓",
            avatarLabel: "吴",
            avatarColorKey: "avatar-2",
            headline: "资深体验设计工程师",
            company: "平安集团",
            age: 32,
            location: "上海",
            education: "本科",
            experienceYears: 10,
            sourceKinds: ["liepin"],
            sourceLabel: "猎聘",
            matchScore: 92,
            matchSummary: "可独立主导 0-1 产品体验搭建。",
            status: "fit",
            detailAvailability: "available",
            accessState: "allowed",
            evidenceLevel: "detail",
          },
        ],
      })}
    />,
  );

  expect(screen.getByRole("tab", { name: /候选人/ })).toBeVisible();
  expect(screen.getByRole("tab", { name: /思考过程/ })).toBeVisible();
  expect(screen.queryByText("运行状态")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run failing layout tests**

Run:

```bash
cd apps/web-react
pnpm test -- ConversationScreenV2.test.tsx
```

Expected: FAIL only if current UI still exposes wrong rail/status behavior.

- [ ] **Step 3: Fix layout and labels**

In `ConversationScreenV2.tsx`:

- Keep `ConversationScreenV2Side` active whenever `hasWorkbenchV2WorkflowSurface(view)` is true.
- Do not add a generic “运行状态” replacement in the side rail.
- Keep default tab as candidates only if candidates exist, otherwise thinking.

In `ThinkingProcessRail.tsx`:

- Keep tab labels exactly `候选人` and `思考过程`.
- Do not render source-status content in this rail.
- When thinking is empty, render `思考过程尚未生成`.

In CSS:

- Ensure the right rail width matches WTS assets and scrolls independently.
- Ensure composer remains bottom-fixed in the chat panel and no large blank white area appears under content.

- [ ] **Step 4: Run layout tests**

Run:

```bash
cd apps/web-react
pnpm test -- ConversationScreenV2.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add apps/web-react/src/components/workbench/ConversationScreenV2.tsx apps/web-react/src/components/workbench/ConversationScreenV2.css apps/web-react/src/components/workbench/ThinkingProcessRail.tsx apps/web-react/src/components/workbench/ThinkingProcessRail.css apps/web-react/src/components/workbench/ConversationScreenV2.test.tsx
git commit -m "fix: keep WTS right rail aligned"
```

---

### Task 7: Generate API Schema If Backend Models Changed

**Files:**
- Modify if generated: `apps/web-react/src/lib/api/schema.d.ts`

- [ ] **Step 1: Start backend for OpenAPI generation**

Run:

```bash
./scripts/start-dev-workbench.sh
```

Expected: backend available at `http://127.0.0.1:8012` and React available at `http://127.0.0.1:5178`. If port 8012 is already in use, stop the old server with:

```bash
lsof -nP -iTCP:8012 -sTCP:LISTEN
```

Then stop the stale process you own and restart the script.

- [ ] **Step 2: Generate frontend OpenAPI types**

In another terminal:

```bash
cd apps/web-react
SEEKTALENT_OPENAPI_URL=http://127.0.0.1:8012/openapi.json pnpm api:gen
```

Expected: `apps/web-react/src/lib/api/schema.d.ts` updates with the new `WorkbenchV2Candidate*` fields.

- [ ] **Step 3: Run TypeScript check**

Run:

```bash
cd apps/web-react
pnpm check
```

Expected: PASS.

- [ ] **Step 4: Commit generated schema if changed**

Run:

```bash
git status --short apps/web-react/src/lib/api/schema.d.ts
```

If it changed:

```bash
git add apps/web-react/src/lib/api/schema.d.ts
git commit -m "chore: refresh workbench v2 API schema"
```

If it did not change, record that no generated schema commit was needed.

---

### Task 8: Full Verification And Browser Acceptance

**Files:**
- No code changes expected.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
uv run pytest \
  tests/test_runtime_control_candidate_truth.py \
  tests/test_workbench_v2_runtime_service.py \
  tests/test_workbench_v2_service.py \
  tests/test_liepin_opencli_boundary_wrappers.py \
  tests/test_runtime_source_lanes.py \
  tests/test_runtime_source_degradation.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend focused tests**

Run:

```bash
cd apps/web-react
pnpm test -- \
  CandidateCard.test.tsx \
  CandidateDetailDrawer.test.tsx \
  CandidateQueue.test.tsx \
  ConversationScreenV2.test.tsx \
  workbenchV2.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run frontend build/type check**

Run:

```bash
cd apps/web-react
pnpm check
```

Expected: PASS.

- [ ] **Step 4: Start dev workbench**

Run:

```bash
./scripts/start-dev-workbench.sh
```

Expected:

- Backend: `http://127.0.0.1:8012`
- React Workbench: `http://127.0.0.1:5178`
- No port conflict.
- No backend startup exception.

- [ ] **Step 5: Browser acceptance with logged-in Liepin/OpenCLI environment**

Use browser automation against `http://127.0.0.1:5178/conversations/new`:

1. Send a pure chat message such as `你好`.  
   Expected: transcript shows the user message immediately and assistant response later; no runtime starts.
2. Create a new conversation and paste a JD that includes title and long description.  
   Expected: transcript remains scrollable; requirement form appears in transcript; no screen replacement.
3. Toggle at least one requirement checkbox off and add one extra requirement.  
   Expected: checkbox can be cancelled; extra requirement appears in form; old form does not visually duplicate as active.
4. Confirm requirements.  
   Expected: chat area contracts with transition; strategy graph appears; right rail appears with `候选人 / 思考过程`.
5. Watch runtime events.  
   Expected: graph nodes appear only when backend events arrive: keywords first, observation only after scoring/quality comment, reflection only after reflection event.
6. Wait until candidates appear.  
   Expected: right rail candidate cards match WTS card hierarchy: round surname avatar, name, source badge, title/company, chips, detail button.
7. Click `查看详情`.  
   Expected: right-side drawer opens; it is not a centered modal; it shows header, match section, job intention, work experience, project experience, education, skills.
8. Ask in chat `现在进度如何？`.  
   Expected: assistant answer reflects current backend runtime state, not a fabricated state.
9. Add a next-round requirement mid-run.  
   Expected: transcript records it and backend response says which future round it applies to.

- [ ] **Step 6: Visual acceptance against WTS assets**

Compare browser screenshots with:

- `/Users/frankqdwang/Agents/SeekTalent-0.2.4/WTS/候选人列表页面.png`
- `/Users/frankqdwang/Agents/SeekTalent-0.2.4/WTS/简历详情完整内容.png`

Expected visual checks:

- Right rail placement and tab labels match asset.
- Candidate card hierarchy and spacing are close to asset.
- Avatar is surname/initial circle with varied color.
- Detail drawer is right-side, scrollable, and section order matches asset.
- No `安全摘要`, `脱敏`, `证据`, `运行状态` copy appears in the WTS candidate/detail UI.

- [ ] **Step 7: Final status report**

Prepare a short final report:

- Commits created.
- Tests passed.
- Browser acceptance result.
- Any remaining visual deviation, with screenshot path.
- Whether stale-ref recovery was verified with unit tests only or also observed in a real Liepin/OpenCLI run.

Do not claim the work is complete unless Steps 1-6 passed or unresolved failures are explicitly listed.
