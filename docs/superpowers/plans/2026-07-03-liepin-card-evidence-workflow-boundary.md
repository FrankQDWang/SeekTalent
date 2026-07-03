# Liepin Card Evidence And Workflow Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Liepin list/card evidence structured-only and split Liepin page capabilities from the detail-backed search workflow.

**Architecture:** Keep `OpenCliBrowserAutomation` generic. Make `LiepinSiteAdapter` expose Liepin page operations and structured DOM extraction. Move the detail-backed candidate loop into `LiepinSearchWorkflow`, with `LiepinOpenCliResumeRetriever` calling a workflow-style runner contract.

**Tech Stack:** Python 3.12, Pydantic, dataclasses, pytest, existing OpenCLI browser wrapper, existing Liepin provider modules.

---

## File Structure

- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
  - Add card preview models and the structured card evidence model.
  - Keep `LiepinSafeCardSummary` as the current public field type name, backed by the richer structured model.
- Modify: `src/seektalent/providers/liepin/card_policy.py`
  - Remove `normalized_card_text` from policy inputs.
  - Score from structured fields and preview entries only.
- Modify: `src/seektalent/providers/liepin/liepin_site_parsing.py`
  - Add safe parsing and validation for structured card evidence probe output.
  - Add a fixed readonly-eval script for Liepin result card evidence.
  - Remove `normalized_card_text` from card summary output.
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
  - Add `extract_structured_liepin_cards()`.
  - Keep `extract_visible_liepin_cards()` as a delegating compatibility entry for CLI action compatibility, but make it return structured evidence with no `visible_text`.
  - Add a private workflow-facing wrapper object instead of expanding the public adapter method surface.
  - Replace the old `search_liepin_resumes()` body with a workflow delegate.
- Modify: `src/seektalent/providers/liepin/liepin_site_payloads.py`
  - Store structured card evidence artifacts with no raw card text keys.
- Modify: `src/seektalent/providers/liepin/mapper.py`
  - Keep card compatibility `search_text` derived from structured card evidence only.
- Modify: `src/seektalent/sources/liepin/runtime_lane.py`
  - Build card-policy input only from `safe_card_summary`, never from `candidate.search_text`.
- Modify: `src/seektalent/resume_normalizers/liepin.py`
  - Build card fallback timeline items from structured previews, never from `normalized_card_text`.
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
  - Keep the stable runner method unless implementation discovers a safe private runner wrapper; do not require a public `LiepinSiteAdapter` method expansion.
- Create: `src/seektalent/providers/liepin/liepin_search_workflow.py`
  - Own the detail-backed search loop and workflow events.
- Modify tests:
  - `tests/test_liepin_opencli_browser.py`
  - `tests/test_liepin_opencli_browser_window_policy.py`
  - `tests/test_liepin_card_policy.py`
  - `tests/test_liepin_provider_mapping.py`
  - `tests/test_liepin_opencli_retriever.py`
  - `tests/test_liepin_provider_source_composition.py`
  - `tests/test_liepin_browser_boundaries.py`
  - `tests/test_normalization.py`
  - `tests/test_liepin_drift_smoke.py`

---

### Task 1: Add Structured Card Evidence Contracts

**Files:**
- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
- Modify: `src/seektalent/providers/liepin/card_policy.py`
- Test: `tests/test_liepin_provider_mapping.py`
- Test: `tests/test_liepin_card_policy.py`

- [ ] **Step 1: Write failing worker-contract tests**

Append these tests near the existing safe-card-summary tests in `tests/test_liepin_provider_mapping.py`:

```python
def test_worker_card_accepts_structured_card_evidence_preview_fields() -> None:
    card = _worker_card().model_copy(
        update={
            "safe_card_summary": LiepinSafeCardSummary(
                current_or_recent_company="北京思图场景数据科技服务有限公司",
                current_or_recent_title="AI算法工程师",
                skill_tags=("Python", "MySQL"),
                experience_preview=(
                    {
                        "company": "北京思图场景数据科技服务有限公司",
                        "title": "AI算法工程师",
                        "date_range": "2021.04-至今",
                        "duration": "6年3个月",
                        "is_current": True,
                    },
                ),
                education_preview=(
                    {
                        "school": "齐齐哈尔大学",
                        "major": "计算机科学与技术",
                        "degree": "本科",
                        "recruitment_type": "统招",
                        "date_range": "2017.08-2021.07",
                    },
                ),
                masked_name=True,
            )
        }
    )

    mapped = map_liepin_worker_card(card, raw_payload_artifact_ref="worker://cards/candidate-1.json")

    summary = mapped.candidate.raw["safe_card_summary"]
    assert summary["experience_preview"] == [
        {
            "company": "北京思图场景数据科技服务有限公司",
            "title": "AI算法工程师",
            "date_range": "2021.04-至今",
            "duration": "6年3个月",
            "is_current": True,
        }
    ]
    assert summary["education_preview"] == [
        {
            "school": "齐齐哈尔大学",
            "major": "计算机科学与技术",
            "degree": "本科",
            "recruitment_type": "统招",
            "date_range": "2017.08-2021.07",
        }
    ]


def test_worker_card_rejects_card_text_tail_fields() -> None:
    payload = _worker_card().model_dump(mode="json")
    payload["safeCardSummary"] = {
        "current_or_recent_title": "Backend Engineer",
        "visible_text": "raw visible card text",
    }

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateCard.model_validate(payload)

    payload["safeCardSummary"] = {
        "current_or_recent_title": "Backend Engineer",
        "normalized_card_text": "legacy card text",
    }

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateCard.model_validate(payload)
```

- [ ] **Step 2: Write failing card-policy tests**

Replace the helper in `tests/test_liepin_card_policy.py` with structured fields. The helper must not accept free card text:

```python
def _summary(
    candidate_id: str,
    provider_rank: int,
    *,
    title: str | None = None,
    company: str | None = None,
    city: str | None = None,
    skills: tuple[str, ...] = (),
    experience: tuple[dict[str, object], ...] = (),
) -> LiepinCardSummary:
    return LiepinCardSummary(
        candidate_resume_id=candidate_id,
        provider_rank=provider_rank,
        current_or_recent_company=company,
        current_or_recent_title=title,
        city=city,
        skill_tags=skills,
        experience_preview=experience,
    )
```

Update the existing test inputs so query terms come from structured fields:

```python
_summary(
    "rank-1",
    1,
    title="Backend Engineer",
    company="Ranking Platform",
    skills=("FastAPI", "ranking"),
)
```

Add this assertion test:

```python
def test_card_policy_has_no_normalized_card_text_field() -> None:
    fields = LiepinCardSummary.__dataclass_fields__

    assert "normalized_card_text" not in fields
```

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
pytest tests/test_liepin_provider_mapping.py::test_worker_card_accepts_structured_card_evidence_preview_fields \
  tests/test_liepin_provider_mapping.py::test_worker_card_rejects_card_text_tail_fields \
  tests/test_liepin_card_policy.py::test_card_policy_has_no_normalized_card_text_field -q
```

Expected: FAIL because preview models do not exist and `LiepinCardSummary` still has `normalized_card_text`.

- [ ] **Step 4: Add structured card models**

In `src/seektalent/providers/liepin/worker_contracts.py`, replace `LiepinSafeCardSummary` with this model block:

```python
class LiepinCardExperiencePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str | None = None
    title: str | None = None
    date_range: str | None = None
    duration: str | None = None
    is_current: bool | None = None


class LiepinCardEducationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school: str | None = None
    major: str | None = None
    degree: str | None = None
    recruitment_type: str | None = None
    date_range: str | None = None


class LiepinStructuredCardEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    display_title: str | None = None
    current_or_recent_company: str | None = None
    current_or_recent_title: str | None = None
    work_years: int | None = None
    age: int | None = None
    gender: str | None = None
    city: str | None = None
    expected_city: str | None = None
    education_level: str | None = None
    school_names: tuple[str, ...] = ()
    major_names: tuple[str, ...] = ()
    skill_tags: tuple[str, ...] = ()
    job_intention: str | None = None
    active_status: str | None = None
    badges: tuple[str, ...] = ()
    experience_preview: tuple[LiepinCardExperiencePreview, ...] = ()
    education_preview: tuple[LiepinCardEducationPreview, ...] = ()
    masked_name: bool = False


class LiepinSafeCardSummary(LiepinStructuredCardEvidence):
    """Compatibility name for the structured card evidence payload."""
```

Keep `LiepinWorkerCandidateCard.safe_card_summary` typed as `LiepinSafeCardSummary | None`.

- [ ] **Step 5: Update card-policy dataclasses and tokenization**

In `src/seektalent/providers/liepin/card_policy.py`, add these dataclasses before `LiepinCardSummary`:

```python
@dataclass(frozen=True, kw_only=True)
class LiepinCardExperiencePreview:
    company: str | None = None
    title: str | None = None
    date_range: str | None = None
    duration: str | None = None
    is_current: bool | None = None


@dataclass(frozen=True, kw_only=True)
class LiepinCardEducationPreview:
    school: str | None = None
    major: str | None = None
    degree: str | None = None
    recruitment_type: str | None = None
    date_range: str | None = None
```

Replace `LiepinCardSummary` with:

```python
@dataclass(frozen=True, kw_only=True)
class LiepinCardSummary:
    candidate_resume_id: str
    provider_rank: int
    display_title: str | None = None
    current_or_recent_company: str | None = None
    current_or_recent_title: str | None = None
    work_years: int | None = None
    age: int | None = None
    gender: str | None = None
    city: str | None = None
    expected_city: str | None = None
    education_level: str | None = None
    school_names: tuple[str, ...] = ()
    major_names: tuple[str, ...] = ()
    skill_tags: tuple[str, ...] = ()
    job_intention: str | None = None
    active_status: str | None = None
    badges: tuple[str, ...] = ()
    experience_preview: tuple[dict[str, object], ...] = ()
    education_preview: tuple[dict[str, object], ...] = ()
    masked_name: bool = False
```

Add this helper:

```python
def _card_text_values(card: LiepinCardSummary) -> tuple[str, ...]:
    values: list[str] = [
        value
        for value in (
            card.display_title,
            card.current_or_recent_company,
            card.current_or_recent_title,
            card.city,
            card.expected_city,
            card.education_level,
            card.job_intention,
            card.active_status,
            *card.badges,
            *card.school_names,
            *card.major_names,
            *card.skill_tags,
        )
        if value
    ]
    for item in card.experience_preview:
        values.extend(str(item.get(key) or "") for key in ("company", "title", "date_range", "duration"))
    for item in card.education_preview:
        values.extend(str(item.get(key) or "") for key in ("school", "major", "degree", "recruitment_type", "date_range"))
    return tuple(value for value in values if value)
```

Replace both tuple constructions in `_card_tokens()` and `_compact_card_text()` with:

```python
" ".join(_card_text_values(card))
```

- [ ] **Step 6: Run contract and card-policy tests**

Run:

```bash
pytest tests/test_liepin_provider_mapping.py::test_worker_card_accepts_structured_card_evidence_preview_fields \
  tests/test_liepin_provider_mapping.py::test_worker_card_rejects_card_text_tail_fields \
  tests/test_liepin_card_policy.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent/providers/liepin/worker_contracts.py \
  src/seektalent/providers/liepin/card_policy.py \
  tests/test_liepin_provider_mapping.py \
  tests/test_liepin_card_policy.py
git commit -m "feat: add structured Liepin card evidence contract"
```

---

### Task 2: Replace Visible Card Extraction With Structured Evidence

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_site_parsing.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/providers/liepin/opencli_browser_cli.py`
- Modify: `src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts`
- Test: `tests/test_liepin_opencli_browser.py`
- Test: `tests/test_liepin_opencli_browser_window_policy.py`
- Test: `tests/test_liepin_browser_boundaries.py`

- [ ] **Step 1: Write failing structured extraction tests**

In `tests/test_liepin_opencli_browser.py`, replace `test_extract_visible_liepin_cards_returns_structured_safe_cards` with:

```python
def test_extract_structured_liepin_cards_returns_structured_evidence_without_card_text(tmp_path: Path) -> None:
    structured_cards = {
        "ok": True,
        "schema_version": "seektalent.liepin_structured_cards_probe.v1",
        "cards": [
            {
                "provider_rank": 1,
                "ref": "70",
                "masked_name": True,
                "gender": "男",
                "age": 40,
                "work_years": 14,
                "city": "上海",
                "expected_city": "上海",
                "education_level": "硕士",
                "current_or_recent_company": "某科技公司",
                "current_or_recent_title": "大数据开发工程师",
                "job_intention": "数据开发专家",
                "active_status": "今天活跃",
                "badges": ["金领"],
                "skill_tags": ["数据仓库", "数据治理", "Python", "Hive"],
                "experience_preview": [
                    {
                        "company": "某科技公司",
                        "title": "大数据开发工程师",
                        "date_range": "2022.08-至今",
                        "duration": "3年9个月",
                        "is_current": True,
                    }
                ],
                "education_preview": [
                    {"school": "沈阳工业大学", "degree": "本科"}
                ],
            }
        ],
    }
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): "王** 40岁 工作14年 硕士 上海",
            ("opencli", "browser", "seektalent-liepin", "eval", ANY_STRUCTURED_CARD_PROBE): json.dumps(
                structured_cards,
                ensure_ascii=False,
            ),
        },
    )

    result = _runner(commands, lease_dir=tmp_path).extract_structured_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["schema_version"] == "seektalent.opencli_liepin_structured_cards.v1"
    first = payload["cards"][0]
    assert first["provider_rank"] == 1
    assert first["ref"] == "70"
    assert first["current_or_recent_company"] == "某科技公司"
    assert first["experience_preview"][0]["title"] == "大数据开发工程师"
    encoded = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("visible_text", "normalized_card_text", "raw_html", "inner_text", "fullText", "rawText"):
        assert forbidden not in encoded
```

Use the repository's existing command fake pattern for matching eval scripts. If the fake does not support wildcard script matching, add a small helper in the test file:

```python
ANY_STRUCTURED_CARD_PROBE = "__structured_card_probe__"
```

and update the fake command class so `readonly_eval` calls containing `"seektalent.liepin_structured_cards_probe.v1"` use this key.

- [ ] **Step 2: Update ref-binding test to assert no visible text**

In `tests/test_liepin_opencli_browser.py`, update `test_extract_visible_liepin_cards_binds_ref_to_same_card_summary` so the method call is:

```python
result = _runner(commands, lease_dir=tmp_path).extract_structured_liepin_cards(source_run_id="run-1", max_cards=10)
```

Replace the old visible-text assertions with:

```python
assert card["ref"] == "71"
assert card["current_or_recent_company"] == "杭州科技公司"
assert card["current_or_recent_title"].startswith("实时数仓工程师")
assert "visible_text" not in card
assert "normalized_card_text" not in card
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py::test_extract_structured_liepin_cards_returns_structured_evidence_without_card_text \
  tests/test_liepin_opencli_browser.py::test_extract_visible_liepin_cards_binds_ref_to_same_card_summary -q
```

Expected: FAIL because `extract_structured_liepin_cards()` does not exist.

- [ ] **Step 4: Add card evidence probe parsing**

In `src/seektalent/providers/liepin/liepin_site_parsing.py`, add these functions near the detail probe helpers:

```python
FORBIDDEN_CARD_EVIDENCE_KEYS = frozenset(
    {
        "raw_html",
        "inner_html",
        "inner_text",
        "visible_text",
        "normalized_card_text",
        "fullText",
        "rawText",
        "page_text",
    }
)


def _safe_structured_cards_from_probe_output(output: str, *, max_cards: int) -> tuple[dict[str, object], ...]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
    if not isinstance(parsed, Mapping) or parsed.get("ok") is False:
        reason = parsed.get("safeReasonCode") if isinstance(parsed, Mapping) else None
        raise OpenCliBrowserError(str(reason or "liepin_opencli_malformed_state"))
    cards = parsed.get("cards")
    if not isinstance(cards, list):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    result: list[dict[str, object]] = []
    for item in cards[:max_cards]:
        if not isinstance(item, Mapping):
            continue
        card = _sanitize_structured_card_mapping(item)
        if card:
            result.append(card)
    return tuple(result)


def _sanitize_structured_card_mapping(item: Mapping[str, object]) -> dict[str, object]:
    for key in item:
        if str(key) in FORBIDDEN_CARD_EVIDENCE_KEYS:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
    provider_rank = _positive_int(item.get("provider_rank"), default=0)
    ref = str(item.get("ref") or "").strip()
    if provider_rank < 1 or not _is_safe_page_id(ref):
        return {}
    result: dict[str, object] = {
        "provider_rank": provider_rank,
        "ref": ref,
        "masked_name": bool(item.get("masked_name", True)),
    }
    for key in (
        "display_title",
        "current_or_recent_company",
        "current_or_recent_title",
        "gender",
        "city",
        "expected_city",
        "education_level",
        "job_intention",
        "active_status",
    ):
        value = _optional_bounded_card_text(item.get(key), max_chars=160)
        if value is not None:
            result[key] = value
    for key in ("age", "work_years"):
        value = _positive_int_or_none(item.get(key))
        if value is not None:
            result[key] = value
    for key in ("badges", "skill_tags", "school_names", "major_names"):
        result[key] = _bounded_text_tuple(item.get(key), max_items=20, max_chars=80)
    result["experience_preview"] = _sanitize_card_preview_list(
        item.get("experience_preview"),
        keys=("company", "title", "date_range", "duration"),
        bool_keys=("is_current",),
    )
    result["education_preview"] = _sanitize_card_preview_list(
        item.get("education_preview"),
        keys=("school", "major", "degree", "recruitment_type", "date_range"),
        bool_keys=(),
    )
    return result
```

Add the helpers used above:

```python
def _optional_bounded_card_text(value: object, *, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = _bounded_public_text(value, max_chars=max_chars)
    return clean or None


def _bounded_text_tuple(value: object, *, max_items: int, max_chars: int) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        clean = _optional_bounded_card_text(item, max_chars=max_chars)
        if clean is None or clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        result.append(clean)
        if len(result) >= max_items:
            break
    return tuple(result)


def _sanitize_card_preview_list(
    value: object,
    *,
    keys: tuple[str, ...],
    bool_keys: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list | tuple):
        return ()
    result: list[dict[str, object]] = []
    for item in value[:8]:
        if not isinstance(item, Mapping):
            continue
        preview: dict[str, object] = {}
        for key in keys:
            clean = _optional_bounded_card_text(item.get(key), max_chars=180)
            if clean is not None:
                preview[key] = clean
        for key in bool_keys:
            raw = item.get(key)
            if isinstance(raw, bool):
                preview[key] = raw
        if preview:
            result.append(preview)
    return tuple(result)
```

- [ ] **Step 5: Add fixed structured-card readonly probe**

In `src/seektalent/providers/liepin/liepin_site_parsing.py`, add a selector-first probe builder. The probe may use per-card `innerText` internally, but it must not return raw text fields. It must parse education per line, collect Chinese and Latin skill chips, avoid a hard-coded city-only allowlist, and use Chinese-safe gender matching.

```python
def _liepin_structured_cards_payload_probe_script(*, max_cards: int) -> str:
    if max_cards < 1 or max_cards > 50:
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    return rf"""
(() => {{
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const lines = (node) => String((node && node.innerText) || "")
    .split(/\n+/)
    .map(clean)
    .filter(Boolean);
  const unique = (items) => Array.from(new Set(items.filter(Boolean)));
  const intFrom = (value, pattern) => {{
    const match = clean(value).match(pattern);
    return match ? Number(match[1]) : null;
  }};
  const firstLine = (items, pattern) => items.find((line) => pattern.test(line)) || "";
  const textOf = (root, selectors) => {{
    for (const selector of selectors) {{
      const value = clean(root.querySelector(selector)?.textContent);
      if (value) return value;
    }}
    return "";
  }};
  const tagTexts = (root) => unique(
    Array.from(root.querySelectorAll(".tag, .skill-tag, [class*=tag], [class*=skill]"))
      .map((node) => clean(node.textContent))
      .filter((value) => /^[A-Za-z][A-Za-z0-9+#./-]{{1,20}}$/.test(value) || /^[\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9+#./-]{{1,20}}$/.test(value))
  ).slice(0, 20);
  const parseExperience = (line) => {{
    const match = clean(line).match(/^(.+?)\s*·\s*(.+?)\s+(\d{{4}}[./-]\d{{2}}[^ ]*)?/);
    if (!match) return null;
    return {{
      company: clean(match[1]).slice(0, 160),
      title: clean(match[2]).replace(/\d{{4}}[./-].*$/, "").slice(0, 160),
      date_range: clean(match[3] || ""),
      is_current: /至今/.test(line),
    }};
  }};
  const parseEducation = (line) => {{
    const parts = clean(line).split("·").map(clean).filter(Boolean);
    const school = parts.find((part) => /(大学|学院|学校)$/.test(part)) || "";
    if (!school) return null;
    return {{
      school: school.slice(0, 160),
      major: (parts.find((part) => !/(大学|学院|学校|本科|硕士|博士|大专|统招)/.test(part)) || "").slice(0, 160),
      degree: (parts.find((part) => /^(本科|硕士|博士|大专)$/.test(part)) || "").slice(0, 80),
      recruitment_type: (parts.find((part) => /统招/.test(part)) || "").slice(0, 80),
      date_range: (line.match(/\d{{4}}[./-]\d{{2}}[^ ]*/) || [""])[0],
    }};
  }};
  const cards = Array.from(document.querySelectorAll("#resultList .detail-resume-card-wrap")).slice(0, {max_cards});
  const payloadCards = cards.map((card, index) => {{
    const cardLines = lines(card);
    const text = cardLines.join(" ");
    const ref = card.getAttribute("data-opencli-ref") || card.dataset.opencliRef || String(index + 1);
    const profile = firstLine(cardLines, /\d{{2}}\s*岁|工作\s*\d+\s*年/);
    const intention = firstLine(cardLines, /求职期望/);
    const experienceLines = cardLines.filter((line) => /·/.test(line) && /\d{{4}}[./-]\d{{2}}/.test(line));
    const educationLines = cardLines.filter((line) => /(大学|学院|本科|硕士|博士|大专|统招)/.test(line));
    const experiencePreview = experienceLines.map(parseExperience).filter(Boolean).slice(0, 3);
    const educationPreview = educationLines.map(parseEducation).filter(Boolean).slice(0, 2);
    const firstExperience = experiencePreview[0] || {{}};
    const explicitCity = textOf(card, ["[class*=city]", "[class*=area]", "[class*=location]"]);
    const profileCity = (profile.match(/(?:^|\s)([\u4e00-\u9fa5]{{2,8}}(?:区|市)?)(?:$|\s)/) || [null, null])[1];
    return {{
      provider_rank: index + 1,
      ref,
      masked_name: /[\u4e00-\u9fa5A-Za-z][*＊]{{1,3}}|[*＊][\u4e00-\u9fa5A-Za-z]/.test(text),
      gender: /(?:^|\s|，|,|·)男(?:\s|，|,|·|$)/.test(text) ? "男" : (/(?:^|\s|，|,|·)女(?:\s|，|,|·|$)/.test(text) ? "女" : null),
      age: intFrom(profile, /(\d{{2}})\s*岁/),
      work_years: intFrom(profile, /工作\s*(\d{{1,2}})\s*年/),
      city: explicitCity || profileCity,
      expected_city: (intention.match(/求职期望[:：]?\s*([\u4e00-\u9fa5]{{2,8}})/) || [null, null])[1],
      education_level: (text.match(/(博士|硕士|本科|大专)/) || [null, null])[1],
      current_or_recent_company: firstExperience.company || null,
      current_or_recent_title: firstExperience.title || null,
      job_intention: intention ? clean(intention.replace(/^求职期望[:：]?\s*/, "")).slice(0, 160) : null,
      active_status: (text.match(/(今天活跃|近\d+天活跃|隐藏活跃状态)/) || [null, null])[1],
      badges: Array.from(new Set(cardLines.filter((line) => /金领|热度/.test(line)).slice(0, 8))),
      skill_tags: tagTexts(card),
      experience_preview: experiencePreview,
      education_preview: educationPreview,
    }};
  }});
  return JSON.stringify({{
    ok: true,
    schema_version: "seektalent.liepin_structured_cards_probe.v1",
    cards: payloadCards,
  }});
}})()
"""
```

- [ ] **Step 6: Add `extract_structured_liepin_cards()` to the adapter**

In `src/seektalent/providers/liepin/liepin_site_adapter.py`, import the new parser helpers:

```python
from seektalent.providers.liepin.liepin_site_parsing import (
    _liepin_structured_cards_payload_probe_script,
    _safe_structured_cards_from_probe_output,
)
```

Add this method near `extract_visible_liepin_cards()`:

```python
def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
    try:
        if max_cards < 1 or max_cards > 50:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        state = self.state()
        if not state.ok:
            return state
        script = _liepin_structured_cards_payload_probe_script(max_cards=max_cards)
        output = self._run_opencli_call(lambda: self._automation.readonly_eval(script))
        cards = list(_safe_structured_cards_from_probe_output(output, max_cards=max_cards))
        payload = {
            "schema_version": "seektalent.opencli_liepin_structured_cards.v1",
            "source_run_id": source_run_id,
            "cards": cards,
            "card_count": len(cards),
        }
        return OpenCliBrowserResult(
            ok=True,
            action="extract_structured_liepin_cards",
            counts={"cards": len(cards)},
            observation=payload,
            private_output=json.dumps(payload, ensure_ascii=False),
        )
    except OpenCliBrowserError as exc:
        return OpenCliBrowserResult(
            ok=False,
            action="extract_structured_liepin_cards",
            safe_reason_code=exc.safe_reason_code,
        )
```

Replace the old `extract_visible_liepin_cards()` body with a compatibility delegate:

```python
def extract_visible_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
    result = self.extract_structured_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)
    return OpenCliBrowserResult(
        ok=result.ok,
        action="extract_visible_liepin_cards",
        safe_reason_code=result.safe_reason_code,
        counts=result.counts,
        observation=result.observation,
        private_output=result.private_output,
    )
```

- [ ] **Step 7: Make `search_liepin_cards()` use the same structured evidence**

In `src/seektalent/providers/liepin/liepin_site_adapter.py`, replace the post-search card parsing path inside `search_liepin_cards()`. After the final search state is ready, do not call `extract_liepin_card_summaries(state_text, max_cards=max_cards)` and do not use `normalized_card_text` for fingerprints. Call the structured card extractor and pass those structured card mappings to `cards_envelope()`:

```python
structured_cards = self.extract_structured_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)
if not structured_cards.ok:
    return self._blocked_cards_envelope(
        source_run_id=source_run_id,
        query=query,
        safe_reason_code=structured_cards.safe_reason_code,
        safe_run_id=safe_run_id,
        pages_visited=pages_visited,
        events=events,
    )
raw_cards = structured_cards.observation.get("cards") if isinstance(structured_cards.observation, Mapping) else None
cards = raw_cards if isinstance(raw_cards, list) else []
events.append({"action_kind": "visible_cards_observed", "route_kind": "search", "visible_cards": len(cards)})
return self._cards_envelope(
    source_run_id=source_run_id,
    query=query,
    safe_run_id=safe_run_id,
    pages_visited=pages_visited,
    events=events,
    state_text=final_state_text,
    cards=[card for card in cards if isinstance(card, Mapping)],
)
```

Update `tests/test_liepin_opencli_browser.py::test_search_liepin_cards_runs_bounded_opencli_flow_and_writes_valid_artifacts` so it proves the public card-search envelope and the written `public-summary/pi-card/...json` artifact contain structured fields and do not contain text-tail fields:

```python
summary_path = tmp_path / "public-summary" / "pi-card" / "run-1" / "1.json"
summary = json.loads(summary_path.read_text(encoding="utf-8"))
assert summary["current_or_recent_company"] == "海光集成电路"
encoded_summary = json.dumps(summary, ensure_ascii=False)
assert "visible_text" not in encoded_summary
assert "normalized_card_text" not in encoded_summary
```

- [ ] **Step 8: Update CLI and extension action names**

In `src/seektalent/providers/liepin/opencli_browser_cli.py`, add an action branch:

```python
if action == "extract_structured_liepin_cards":
    return runner.extract_structured_liepin_cards(
        source_run_id=str(payload.get("source_run_id") or "manual"),
        max_cards=_positive_int(payload.get("max_cards"), default=10),
    )
```

Keep the existing `extract_visible_liepin_cards` branch as compatibility.

In `src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts`, update every action gate that knows about the old extractor:

1. Add `"seektalent_opencli_extract_structured_liepin_cards"` to `capabilitiesPayload().capabilities.tools`.
2. In `updateStateFromPayload()`, treat both extractor actions identically:

   ```ts
   if (action === "extract_visible_liepin_cards" || action === "extract_structured_liepin_cards") {
     stateReady = parsed.ok === true && parsed.observation?.terminal !== true;
     if (stateReady) {
       terminalReason = null;
     } else {
       terminalReason =
         parsed.observation?.terminal === true && typeof parsed.safeReasonCode === "string"
           ? parsed.safeReasonCode
           : null;
     }
   }
   ```

3. Add `"extract_structured_liepin_cards"` to the terminal bypass action list next to `"extract_visible_liepin_cards"`.
4. Add `"extract_structured_liepin_cards"` to the budget-exempt action list next to `"extract_visible_liepin_cards"`.
5. Register the new structured-card tool:

   ```ts
   pi.registerTool({
     name: "seektalent_opencli_extract_structured_liepin_cards",
     label: "Read structured Liepin cards",
     description: "Read structured Liepin result card evidence from the current search results page without clicking or opening details.",
     parameters: Type.Object({
       sourceRunId: Type.String(),
       maxCards: Type.Optional(Type.Number()),
     }),
     async execute(_toolCallId: string, params: ToolParams) {
       return textResult(await runAction("extract_structured_liepin_cards", params));
     },
   });
   ```

- [ ] **Step 9: Update browser boundary tests**

In `tests/test_liepin_browser_boundaries.py`, update the expected extension action/tool allowlist to include:

```python
"extract_structured_liepin_cards"
```

Keep the old `extract_visible_liepin_cards` assertions only where the compatibility action is intentionally checked.

- [ ] **Step 10: Run focused extraction tests**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py::test_extract_structured_liepin_cards_returns_structured_evidence_without_card_text \
  tests/test_liepin_opencli_browser.py::test_extract_visible_liepin_cards_binds_ref_to_same_card_summary \
  tests/test_liepin_opencli_browser_window_policy.py \
  tests/test_liepin_browser_boundaries.py -q
```

Expected: PASS after updating assertions away from `visible_text`.

- [ ] **Step 11: Commit**

```bash
git add src/seektalent/providers/liepin/liepin_site_parsing.py \
  src/seektalent/providers/liepin/liepin_site_adapter.py \
  src/seektalent/providers/liepin/opencli_browser_cli.py \
  src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts \
  tests/test_liepin_opencli_browser.py \
  tests/test_liepin_opencli_browser_window_policy.py \
  tests/test_liepin_browser_boundaries.py
git commit -m "feat: extract structured Liepin card evidence"
```

---

### Task 3: Remove Card Text From Payloads, Mapping, And Normalization

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_site_payloads.py`
- Modify: `src/seektalent/providers/liepin/mapper.py`
- Modify: `src/seektalent/providers/liepin/card_policy.py`
- Modify: `src/seektalent/sources/liepin/runtime_lane.py`
- Modify: `src/seektalent/resume_normalizers/liepin.py`
- Modify: `tests/test_normalization.py`
- Modify: `tests/test_liepin_opencli_browser.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `tests/test_liepin_provider_mapping.py`

- [ ] **Step 1: Write failing artifact and normalization assertions**

In `tests/test_liepin_opencli_browser.py`, update `test_search_liepin_cards_runs_bounded_opencli_flow_and_writes_valid_artifacts` to assert:

```python
encoded = json.dumps(envelope, ensure_ascii=False)
assert "visible_text" not in encoded
assert "normalized_card_text" not in encoded
assert "raw_html" not in encoded
assert "inner_text" not in encoded
```

In `tests/test_normalization.py`, replace any Liepin fixture use of:

```python
"normalized_card_text": "数据开发 数据仓库 数据治理 Python Java 大规模数据处理"
```

with:

```python
"experience_preview": [
    {
        "company": "业务线科技公司",
        "title": "高级数据开发工程师",
        "date_range": "2022.08-至今",
    }
],
"skill_tags": ["Python", "Java", "数据仓库", "数据治理"]
```

Add this assertion to the Liepin card normalization test:

```python
encoded = json.dumps(normalized.model_dump(mode="json"), ensure_ascii=False)
assert "normalized_card_text" not in encoded
assert "visible_text" not in encoded
```

In `tests/test_liepin_runtime_source_lane.py`, add a test that proves runtime card policy input does not read `ResumeCandidate.search_text`:

```python
def test_liepin_card_summary_for_candidate_ignores_candidate_search_text() -> None:
    from seektalent.core.retrieval.provider_contract import ResumeCandidate
    from seektalent.sources.liepin.runtime_lane import _card_summary_for_candidate

    candidate = ResumeCandidate(
        resume_id="liepin-card-1",
        source_resume_id=None,
        snapshot_sha256="sha",
        dedup_key="dedup",
        search_text="SENTINEL raw-ish compatibility text",
        raw={
            "safe_card_summary": {
                "current_or_recent_company": "北京思图场景数据科技服务有限公司",
                "current_or_recent_title": "AI算法工程师",
                "skill_tags": ["Python", "数据仓库"],
                "experience_preview": [
                    {
                        "company": "北京思图场景数据科技服务有限公司",
                        "title": "AI算法工程师",
                        "date_range": "2021.04-至今",
                    }
                ],
            }
        },
    )

    summary = _card_summary_for_candidate(candidate=candidate, provider_rank=1)

    assert "normalized_card_text" not in summary.__dataclass_fields__
    assert summary.current_or_recent_title == "AI算法工程师"
    assert summary.experience_preview[0]["title"] == "AI算法工程师"
    assert "SENTINEL" not in repr(summary)
```

In `tests/test_normalization.py`, add a focused normalizer test:

```python
def test_liepin_normalizer_uses_card_experience_preview_not_normalized_card_text() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-card-1",
        source_resume_id=None,
        snapshot_sha256="sha",
        dedup_key="dedup",
        search_text="ignored",
        raw={
            "source": "liepin",
            "safe_card_summary": {
                "current_or_recent_title": "AI算法工程师",
                "normalized_card_text": "SENTINEL legacy card text",
                "experience_preview": [
                    {
                        "company": "北京思图场景数据科技服务有限公司",
                        "title": "AI算法工程师",
                        "date_range": "2021.04-至今",
                    }
                ],
            },
        },
    )

    normalized = normalize_resume(candidate)

    encoded = json.dumps(normalized.model_dump(mode="json"), ensure_ascii=False)
    assert "AI算法工程师" in encoded
    assert "SENTINEL" not in encoded
```

In `tests/test_liepin_provider_mapping.py`, add a mapper sentinel test:

```python
def test_card_mapping_ignores_worker_normalized_text_when_safe_card_summary_exists() -> None:
    card = _worker_card().model_copy(
        update={
            "normalized_text": "SENTINEL raw-ish card text",
            "safe_card_summary": LiepinSafeCardSummary(
                current_or_recent_company="北京思图场景数据科技服务有限公司",
                current_or_recent_title="AI算法工程师",
                skill_tags=("Python",),
            ),
        }
    )

    mapped = map_liepin_worker_card(card)

    assert "AI算法工程师" in mapped.candidate.search_text
    assert "SENTINEL" not in mapped.candidate.search_text
    assert "SENTINEL" not in mapped.provider_snapshot.normalized_text
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py::test_search_liepin_cards_runs_bounded_opencli_flow_and_writes_valid_artifacts \
  tests/test_normalization.py::test_liepin_safe_card_summary_feeds_normalized_resume -q
```

Expected: FAIL while payload and normalization code still know `normalized_card_text`.

- [ ] **Step 3: Update card envelopes**

In `src/seektalent/providers/liepin/liepin_site_payloads.py`, change `cards_envelope()` so it sanitizes structured evidence before digest calculation, public summary writing, protected snapshot writing, and envelope construction:

```python
FORBIDDEN_CARD_SUMMARY_KEYS = {
    "visible_text",
    "normalized_card_text",
    "raw_html",
    "inner_html",
    "inner_text",
    "fullText",
    "rawText",
    "page_text",
}


def _safe_card_summary_payload(summary: Mapping[str, object]) -> dict[str, object]:
    return _safe_card_summary_mapping(summary, skip_keys={"provider_rank", "ref"})


def _safe_card_summary_mapping(value: Mapping[str, object], *, skip_keys: set[str] | None = None) -> dict[str, object]:
    skipped = skip_keys or set()
    result: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in FORBIDDEN_CARD_SUMMARY_KEYS or key_text in skipped:
            continue
        result[key_text] = _safe_card_summary_value(item)
    return result


def _safe_card_summary_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _safe_card_summary_mapping(value)
    if isinstance(value, list):
        return [_safe_card_summary_value(item) for item in value]
    return value
```

Use it before any write:

```python
safe_summary = _safe_card_summary_payload(summary)
digest = hashlib.sha256(json.dumps(safe_summary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
...
"display_name_masked": bool(safe_summary.get("masked_name", True)),
"safe_card_summary": safe_summary,
```

Write `safe_summary` to both artifacts:

```python
safe_summary_ref = write_pi_artifact(
    "public-summary",
    f"pi-card/{safe_run_id}/{rank}.json",
    safe_summary,
)
protected_snapshot_ref = write_pi_artifact(
    "protected",
    f"pi-card/{safe_run_id}/{rank}.json",
    {"schema_version": "seektalent.opencli_card_snapshot.v1", "rank": rank, "summary": safe_summary},
)
```

- [ ] **Step 4: Add structured compatibility text helper**

In `src/seektalent/providers/liepin/mapper.py`, add:

```python
def _structured_card_search_text(summary: Mapping[str, object]) -> str:
    values: list[str] = []
    for key in (
        "display_title",
        "current_or_recent_company",
        "current_or_recent_title",
        "city",
        "expected_city",
        "education_level",
        "job_intention",
        "active_status",
    ):
        value = summary.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    for key in ("badges", "school_names", "major_names", "skill_tags"):
        value = summary.get(key)
        if isinstance(value, list | tuple):
            values.extend(str(item).strip() for item in value if str(item).strip())
    for list_key, item_keys in (
        ("experience_preview", ("company", "title", "date_range", "duration")),
        ("education_preview", ("school", "major", "degree", "recruitment_type", "date_range")),
    ):
        value = summary.get(list_key)
        if not isinstance(value, list | tuple):
            continue
        for item in value:
            if not isinstance(item, Mapping):
                continue
            values.extend(str(item.get(key) or "").strip() for key in item_keys)
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = " ".join(value.split())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return " ".join(result)[:4000]
```

Update `_mapped_normalized_text()` for card candidates:

```python
def _mapped_normalized_text(worker_candidate: LiepinWorkerCandidate, provider_payload: dict[str, object]) -> str:
    if isinstance(worker_candidate, LiepinWorkerCandidateDetail):
        return structured_liepin_detail_text(provider_payload)
    if isinstance(worker_candidate, LiepinWorkerCandidateCard) and worker_candidate.safe_card_summary is not None:
        return _structured_card_search_text(worker_candidate.safe_card_summary.model_dump(mode="json"))
    return worker_candidate.normalized_text
```

The fallback to `worker_candidate.normalized_text` remains only for non-Liepin-card compatibility tests that construct old worker objects. Add a follow-up assertion in `tests/test_liepin_provider_mapping.py` that a structured card ignores a sentinel `normalized_text`.

- [ ] **Step 5: Update card-policy usage**

In `src/seektalent/providers/liepin/card_policy.py`, ensure `_card_text_values()` from Task 1 is the only source of searchable card text. Remove every reference to `normalized_card_text`.

Run:

```bash
rg -n "normalized_card_text|visible_text" src/seektalent/providers/liepin tests/test_liepin_card_policy.py tests/test_liepin_provider_mapping.py
```

Expected: remaining hits are only test names or compatibility action names that explicitly prove absence.

- [ ] **Step 6: Update runtime lane and Liepin normalizer**

In `src/seektalent/sources/liepin/runtime_lane.py`, update `_card_summary_for_candidate()` so it builds `LiepinCardSummary` from `safe_card_summary` only. Remove `normalized_card_text=candidate.search_text`, and add structured preview fields:

```python
return LiepinCardSummary(
    candidate_resume_id=candidate.resume_id,
    provider_rank=provider_rank,
    display_title=_summary_string(summary, "display_title"),
    current_or_recent_company=_summary_string(summary, "current_or_recent_company"),
    current_or_recent_title=_summary_string(summary, "current_or_recent_title"),
    work_years=_summary_int(summary, "work_years"),
    age=_summary_int(summary, "age"),
    gender=_summary_string(summary, "gender"),
    city=_summary_string(summary, "city"),
    expected_city=_summary_string(summary, "expected_city"),
    education_level=_summary_string(summary, "education_level"),
    school_names=_summary_string_tuple(summary, "school_names"),
    major_names=_summary_string_tuple(summary, "major_names"),
    skill_tags=_summary_string_tuple(summary, "skill_tags"),
    job_intention=_summary_string(summary, "job_intention"),
    active_status=_summary_string(summary, "active_status"),
    badges=_summary_string_tuple(summary, "badges"),
    experience_preview=_summary_mapping_tuple(summary, "experience_preview"),
    education_preview=_summary_mapping_tuple(summary, "education_preview"),
    masked_name=bool(summary.get("masked_name", False)),
)
```

Add:

```python
def _summary_mapping_tuple(summary: dict[object, object], key: str) -> tuple[dict[str, object], ...]:
    value = summary.get(key)
    if not isinstance(value, list | tuple):
        return ()
    result: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            result.append({str(field): field_value for field, field_value in item.items()})
    return tuple(result)
```

In `src/seektalent/resume_normalizers/liepin.py`, replace `_safe_card_work_items()` so it prefers `experience_preview` and never reads `normalized_card_text`:

```python
def _safe_card_work_items(safe_card: Mapping[str, object]) -> list[StructuredResumeTimelineItem]:
    preview_items = _list_of_mappings(safe_card.get("experience_preview"))
    items = [
        StructuredResumeTimelineItem(
            company=_text(item.get("company")),
            title=_text(item.get("title")),
            duration=_first_text(item.get("duration"), item.get("date_range")),
            summary="",
        )
        for item in preview_items
    ]
    items = [item for item in items if any(item.model_dump(mode="json").values())]
    if items:
        return items
    title = _first_text(safe_card.get("current_or_recent_title"), safe_card.get("display_title"))
    company = _text(safe_card.get("current_or_recent_company"))
    work_years = _int_or_none(safe_card.get("work_years"))
    item = StructuredResumeTimelineItem(
        company=company,
        title=title,
        duration=f"{work_years}y" if work_years is not None else "",
        summary=_text(safe_card.get("recent_experience_text")),
    )
    return [item] if any(item.model_dump(mode="json").values()) else []
```

Add the helper if the module does not already have it:

```python
def _list_of_mappings(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, Mapping)]
```

- [ ] **Step 7: Run payload, mapping, runtime, and normalization tests**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py::test_search_liepin_cards_runs_bounded_opencli_flow_and_writes_valid_artifacts \
  tests/test_liepin_provider_mapping.py \
  tests/test_liepin_card_policy.py \
  tests/test_liepin_runtime_source_lane.py::test_liepin_card_summary_for_candidate_ignores_candidate_search_text \
  tests/test_normalization.py::test_liepin_safe_card_summary_feeds_normalized_resume \
  tests/test_normalization.py::test_liepin_normalizer_uses_card_experience_preview_not_normalized_card_text \
  tests/test_normalization.py::test_cts_normalizer_ignores_liepin_safe_card_summary -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/seektalent/providers/liepin/liepin_site_payloads.py \
  src/seektalent/providers/liepin/mapper.py \
  src/seektalent/providers/liepin/card_policy.py \
  src/seektalent/sources/liepin/runtime_lane.py \
  src/seektalent/resume_normalizers/liepin.py \
  tests/test_liepin_opencli_browser.py \
  tests/test_liepin_provider_mapping.py \
  tests/test_liepin_card_policy.py \
  tests/test_normalization.py \
  tests/test_liepin_runtime_source_lane.py
git commit -m "fix: derive Liepin card text from structured evidence"
```

---

### Task 4: Extract Liepin Detail-Backed Search Workflow

**Files:**
- Create: `src/seektalent/providers/liepin/liepin_search_workflow.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Test: `tests/test_liepin_search_workflow.py`
- Modify: `tests/test_liepin_opencli_retriever.py`
- Modify: `tests/test_liepin_provider_source_composition.py`

- [ ] **Step 1: Write workflow unit tests with a fake site**

Create `tests/test_liepin_search_workflow.py`:

```python
from __future__ import annotations

from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin.liepin_search_workflow import LiepinSearchWorkflow, LiepinSearchWorkflowRequest


class FakeLiepinSearchSite:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.cards = [
            {"provider_rank": 1, "ref": "70", "current_or_recent_title": "AI算法工程师"},
            {"provider_rank": 2, "ref": "71", "current_or_recent_title": "架构师"},
        ]
        self.captured: list[int] = []

    def append_agent_event(self, source_run_id: str, event: dict[str, object]) -> None:
        self.calls.append(("event", event["action_kind"]))

    def search_liepin_cards(self, **kwargs) -> dict[str, object]:
        self.calls.append(("search_liepin_cards", kwargs["query"]))
        return {"status": "succeeded", "cards_seen": len(self.cards)}

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        self.calls.append(("extract_structured_liepin_cards", max_cards))
        return OpenCliBrowserResult(
            ok=True,
            action="extract_structured_liepin_cards",
            observation={"cards": self.cards[:max_cards]},
            private_output="{}",
        )

    def safe_liepin_detail_url_for_ref(self, ref: str) -> str | None:
        self.calls.append(("safe_liepin_detail_url_for_ref", ref))
        return f"https://h.liepin.com/resume/showresumedetail/?res_id_encode={ref}"

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        self.calls.append(("open_liepin_detail", rank))
        return OpenCliBrowserResult(ok=True, action="open_liepin_detail")

    def open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult:
        self.calls.append(("open_liepin_detail_cached_url", rank))
        return OpenCliBrowserResult(ok=True, action="open_liepin_detail")

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        self.calls.append(("capture_liepin_detail_resume", rank))
        self.captured.append(rank)
        return OpenCliBrowserResult(ok=True, action="capture_liepin_detail_resume")

    def restore_liepin_search_page(self) -> str | None:
        self.calls.append(("restore_liepin_search_page", None))
        return "page-1"

    def finalize_liepin_resumes(self, **kwargs) -> dict[str, object]:
        self.calls.append(("finalize_liepin_resumes", kwargs["cards_seen"]))
        return {
            "schema_version": "seektalent.liepin_opencli_resumes.v1",
            "status": "succeeded",
            "cards_seen": kwargs["cards_seen"],
            "resumes": [{"provider_rank": rank} for rank in self.captured],
        }

    def blocked_resumes_envelope(self, **kwargs) -> dict[str, object]:
        self.calls.append(("blocked_resumes_envelope", kwargs["safe_reason_code"]))
        return {
            "schema_version": "seektalent.liepin_opencli_resumes.v1",
            "status": "blocked",
            "safe_reason_code": kwargs["safe_reason_code"],
            "resumes": [],
        }


def test_workflow_opens_details_until_target_count() -> None:
    site = FakeLiepinSearchSite()
    workflow = LiepinSearchWorkflow(site=site)

    envelope = workflow.search_detail_backed_resumes(
        LiepinSearchWorkflowRequest(
            source_run_id="run-1",
            query="AI Agent",
            target_resumes=2,
            max_pages=1,
            max_cards=10,
            native_filters=None,
        )
    )

    assert envelope["status"] == "succeeded"
    assert envelope["resumes"] == [{"provider_rank": 1}, {"provider_rank": 2}]
    assert ("search_liepin_cards", "AI Agent") in site.calls
    assert ("extract_structured_liepin_cards", 10) in site.calls
    assert ("finalize_liepin_resumes", 2) in site.calls


def test_workflow_blocks_when_no_detail_can_be_captured() -> None:
    site = FakeLiepinSearchSite()

    def fail_capture(*, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        return OpenCliBrowserResult(
            ok=False,
            action="capture_liepin_detail_resume",
            safe_reason_code="liepin_opencli_detail_not_opened",
        )

    site.capture_liepin_detail_resume = fail_capture  # type: ignore[method-assign]
    workflow = LiepinSearchWorkflow(site=site)

    envelope = workflow.search_detail_backed_resumes(
        LiepinSearchWorkflowRequest(
            source_run_id="run-1",
            query="AI Agent",
            target_resumes=1,
            max_pages=1,
            max_cards=10,
            native_filters=None,
        )
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
```

- [ ] **Step 2: Run workflow tests and verify they fail**

Run:

```bash
pytest tests/test_liepin_search_workflow.py -q
```

Expected: FAIL because `liepin_search_workflow.py` does not exist.

- [ ] **Step 3: Create workflow request and protocol**

Create `src/seektalent/providers/liepin/liepin_search_workflow.py` with:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin.liepin_site_parsing import _positive_int_or_none, _string_key_mapping_or_none


@dataclass(frozen=True, kw_only=True)
class LiepinSearchWorkflowRequest:
    source_run_id: str
    query: str
    target_resumes: int
    max_pages: int
    max_cards: int
    native_filters: Mapping[str, object] | None = None


class LiepinSearchWorkflowSite(Protocol):
    def append_agent_event(self, source_run_id: str, event: dict[str, object]) -> None: ...

    def search_liepin_cards(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]: ...

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult: ...

    def safe_liepin_detail_url_for_ref(self, ref: str) -> str | None: ...

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult: ...

    def open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult: ...

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult: ...

    def restore_liepin_search_page(self) -> str | None: ...

    def finalize_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        cards_seen: int | None = None,
        target_resumes: int | None = None,
    ) -> dict[str, object]: ...

    def blocked_resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str,
        cards_seen: int,
    ) -> dict[str, object]: ...
```

- [ ] **Step 4: Move detail-backed loop into workflow class**

In the same new file, add:

```python
class LiepinSearchWorkflow:
    def __init__(self, *, site: LiepinSearchWorkflowSite) -> None:
        self._site = site

    def search_detail_backed_resumes(self, request: LiepinSearchWorkflowRequest) -> dict[str, object]:
        if request.target_resumes < 1 or request.target_resumes > 10:
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code="liepin_opencli_forbidden_command",
                cards_seen=0,
            )
        self._event(request.source_run_id, {"action_kind": "search_cards_started", "route_kind": "search", "ok": True})
        if request.native_filters:
            self._event(request.source_run_id, {"action_kind": "apply_filters_started", "route_kind": "search", "ok": True})
        cards = self._site.search_liepin_cards(
            source_run_id=request.source_run_id,
            query=request.query,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            native_filters=request.native_filters,
        )
        cards_seen = _positive_int_or_none(cards.get("cards_seen")) or 0
        ok = cards.get("status") == "succeeded"
        self._event(
            request.source_run_id,
            {
                "action_kind": "search_submitted",
                "route_kind": "search",
                "ok": ok,
                "cards_seen": cards_seen,
                "safe_reason_code": (
                    str(cards.get("safe_reason_code") or cards.get("stop_reason") or "")
                    if not ok
                    else None
                ),
            },
        )
        if request.native_filters:
            self._event(
                request.source_run_id,
                {"action_kind": "apply_filters_completed", "route_kind": "search", "ok": ok},
            )
        if not ok:
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code=str(cards.get("safe_reason_code") or cards.get("stop_reason") or "failed_provider_error"),
                cards_seen=cards_seen,
            )

        visible = self._site.extract_structured_liepin_cards(
            source_run_id=request.source_run_id,
            max_cards=request.max_cards,
        )
        if not visible.ok:
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code=visible.safe_reason_code,
                cards_seen=cards_seen,
            )
        raw_cards = visible.observation.get("cards") if isinstance(visible.observation, Mapping) else None
        card_items = raw_cards if isinstance(raw_cards, list) else []
        self._event(
            request.source_run_id,
            {
                "action_kind": "visible_cards_observed",
                "route_kind": "search",
                "ok": True,
                "visible_cards": len(card_items),
                "target_resumes": request.target_resumes,
                "cards_seen": cards_seen or len(card_items),
            },
        )
        return self._open_detail_loop(
            request=request,
            card_items=card_items,
            cards_seen=max(cards_seen, len(card_items)),
        )
```

Add helper methods by moving the loop from `LiepinSiteAdapter.search_liepin_resumes()` and replacing adapter-private calls with protocol calls:

```python
    def _open_detail_loop(
        self,
        *,
        request: LiepinSearchWorkflowRequest,
        card_items: Sequence[object],
        cards_seen: int,
    ) -> dict[str, object]:
        detail_urls_by_rank = self._remember_detail_urls(card_items)
        self._event(
            request.source_run_id,
            {
                "action_kind": "detail_urls_cached",
                "route_kind": "search",
                "ok": True,
                "cached_detail_urls": len(detail_urls_by_rank),
            },
        )
        opened = 0
        attempted_ranks: set[int] = set()
        using_cached_card_items = False
        cards_seen_for_resume = cards_seen
        mutable_cards = list(card_items)

        while opened < request.target_resumes:
            selected = self._select_next_card(mutable_cards, attempted_ranks)
            if selected is None:
                break
            selected_ref, selected_rank = selected
            attempted_ranks.add(selected_rank)
            self._event(
                request.source_run_id,
                {
                    "action_kind": "detail_candidate_selected",
                    "route_kind": "search",
                    "ok": True,
                    "rank": selected_rank,
                    "ref": selected_ref,
                },
            )
            cached_detail_url = detail_urls_by_rank.get(selected_rank)
            if using_cached_card_items and cached_detail_url is not None:
                open_result = self._site.open_liepin_detail_cached_url(
                    source_run_id=request.source_run_id,
                    ref=selected_ref,
                    rank=selected_rank,
                    detail_url=cached_detail_url,
                )
            else:
                open_result = self._site.open_liepin_detail(
                    source_run_id=request.source_run_id,
                    ref=selected_ref,
                    rank=selected_rank,
                )
            if not open_result.ok:
                # The adapter records page-level open_detail failure events.
                # The workflow does not duplicate those events.
                continue
            capture_result = self._site.capture_liepin_detail_resume(
                source_run_id=request.source_run_id,
                rank=selected_rank,
            )
            if not capture_result.ok:
                # The adapter records page-level capture failure events.
                # The workflow does not duplicate those events.
                continue
            opened += 1
            self._event(
                request.source_run_id,
                {
                    "action_kind": "capture_detail_succeeded",
                    "route_kind": "detail",
                    "ok": True,
                    "rank": selected_rank,
                },
            )
            if opened >= request.target_resumes:
                continue
            restored_page_id = self._site.restore_liepin_search_page()
            self._event(
                request.source_run_id,
                {
                    "action_kind": "return_to_search_after_capture",
                    "route_kind": "search",
                    "ok": restored_page_id is not None,
                    "rank": selected_rank,
                },
            )
            if restored_page_id is None:
                using_cached_card_items = True
                continue
            refreshed = self._site.extract_structured_liepin_cards(
                source_run_id=request.source_run_id,
                max_cards=request.max_cards,
            )
            if not refreshed.ok:
                self._event(
                    request.source_run_id,
                    {
                        "action_kind": "visible_cards_refresh_failed_after_return",
                        "route_kind": "search",
                        "ok": False,
                        "safe_reason_code": refreshed.safe_reason_code,
                    },
                )
                break
            raw_refreshed_cards = refreshed.observation.get("cards") if isinstance(refreshed.observation, Mapping) else None
            refreshed_card_items = raw_refreshed_cards if isinstance(raw_refreshed_cards, list) else []
            if refreshed_card_items:
                mutable_cards = list(refreshed_card_items)
                using_cached_card_items = False
                detail_urls_by_rank.update(self._remember_detail_urls(mutable_cards))
            else:
                using_cached_card_items = True
            cards_seen_for_resume = max(cards_seen_for_resume, len(refreshed_card_items))
            self._event(
                request.source_run_id,
                {
                    "action_kind": "visible_cards_refreshed_after_return",
                    "route_kind": "search",
                    "ok": True,
                    "visible_cards": len(refreshed_card_items),
                    "cards_seen": cards_seen_for_resume,
                },
            )

        if opened == 0:
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code="liepin_opencli_detail_not_opened",
                cards_seen=cards_seen_for_resume,
            )
        if opened < request.target_resumes:
            self._event(
                request.source_run_id,
                {
                    "action_kind": "detail_target_not_met",
                    "route_kind": "detail",
                    "ok": False,
                    "target_resumes": request.target_resumes,
                    "resumes_returned": opened,
                    "visible_cards": len(mutable_cards),
                },
            )
        return self._site.finalize_liepin_resumes(
            source_run_id=request.source_run_id,
            query=request.query,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            cards_seen=cards_seen_for_resume,
            target_resumes=request.target_resumes,
        )
```

Add remaining helpers:

```python
    def _remember_detail_urls(self, cards_to_cache: Sequence[object]) -> dict[int, str]:
        detail_urls_by_rank: dict[int, str] = {}
        for card in cards_to_cache:
            card_payload = _string_key_mapping_or_none(card)
            if card_payload is None:
                continue
            ref = card_payload.get("ref")
            if not isinstance(ref, str) or not ref:
                continue
            rank = _positive_int_or_none(card_payload.get("provider_rank") or 0)
            if rank is None or rank in detail_urls_by_rank:
                continue
            detail_url = self._site.safe_liepin_detail_url_for_ref(ref)
            if detail_url is not None:
                detail_urls_by_rank[rank] = detail_url
        return detail_urls_by_rank

    @staticmethod
    def _select_next_card(card_items: Sequence[object], attempted_ranks: set[int]) -> tuple[str, int] | None:
        for card in card_items:
            card_payload = _string_key_mapping_or_none(card)
            if card_payload is None:
                continue
            ref = card_payload.get("ref")
            rank = _positive_int_or_none(card_payload.get("provider_rank") or 0)
            if rank is None or rank in attempted_ranks:
                continue
            if isinstance(ref, str) and ref:
                return ref, rank
        return None

    def _event(self, source_run_id: str, event: dict[str, object]) -> None:
        self._site.append_agent_event(source_run_id, event)
```

- [ ] **Step 5: Add a private workflow-facing wrapper**

In `src/seektalent/providers/liepin/liepin_site_adapter.py`, do not add public methods such as `append_agent_event()` or `blocked_resumes_envelope()`. The repository has an exact public-method signature test for `LiepinSiteAdapter`; keep that public surface narrow. Add a private wrapper class near `LiepinSiteAdapter` instead:

```python
class _LiepinSearchWorkflowSite:
    def __init__(self, adapter: LiepinSiteAdapter) -> None:
        self._adapter = adapter

    def append_agent_event(self, source_run_id: str, event: dict[str, object]) -> None:
        self._adapter._append_agent_event(source_run_id, event)

    def search_liepin_cards(self, **kwargs: object) -> dict[str, object]:
        return self._adapter.search_liepin_cards(**kwargs)

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        return self._adapter.extract_structured_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)

    def safe_liepin_detail_url_for_ref(self, ref: str) -> str | None:
        return self._adapter._safe_liepin_detail_url_for_ref(ref)

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        return self._adapter.open_liepin_detail(source_run_id=source_run_id, ref=ref, rank=rank)

    def open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult:
        return self._adapter._open_liepin_detail_cached_url(
            source_run_id=source_run_id,
            ref=ref,
            rank=rank,
            detail_url=detail_url,
        )

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        return self._adapter.capture_liepin_detail_resume(source_run_id=source_run_id, rank=rank)

    def restore_liepin_search_page(self) -> str | None:
        return self._adapter._select_canonical_liepin_search_page()

    def finalize_liepin_resumes(self, **kwargs: object) -> dict[str, object]:
        return self._adapter.finalize_liepin_resumes(**kwargs)

    def blocked_resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str,
        cards_seen: int,
    ) -> dict[str, object]:
        return self._adapter._blocked_resumes_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_reason_code=safe_reason_code or "failed_provider_error",
            cards_seen=cards_seen,
        )
```

Replace `search_liepin_resumes()` with:

```python
def search_liepin_resumes(
    self,
    *,
    source_run_id: str,
    query: str,
    target_resumes: int,
    max_pages: int,
    max_cards: int,
    native_filters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    from seektalent.providers.liepin.liepin_search_workflow import (
        LiepinSearchWorkflow,
        LiepinSearchWorkflowRequest,
    )

    return LiepinSearchWorkflow(site=_LiepinSearchWorkflowSite(self)).search_detail_backed_resumes(
        LiepinSearchWorkflowRequest(
            source_run_id=source_run_id,
            query=query,
            target_resumes=target_resumes,
            max_pages=max_pages,
            max_cards=max_cards,
            native_filters=native_filters,
        )
    )
```

- [ ] **Step 6: Keep the retriever protocol stable**

Do not require a new public `LiepinSiteAdapter.search_detail_backed_resumes()` method. Keep the existing retriever call to `search_liepin_resumes()` unless the implementation creates a separate non-adapter runner object. The public adapter signature test should only need to add `extract_structured_liepin_cards()`, not the private workflow wrapper methods.

If a separate runner object is introduced, the protocol can use:

```python
def search_detail_backed_resumes(
    self,
    *,
    source_run_id: str,
    query: str,
    target_resumes: int,
    max_pages: int,
    max_cards: int,
    native_filters: dict[str, object] | None = None,
) -> dict[str, object]: ...
```

In that case only, update `_search_liepin_resumes()`:

```python
return self._runner.search_detail_backed_resumes(
    source_run_id=request.source_run_id,
    query=request.keyword_query,
    target_resumes=request.target_resumes,
    max_pages=request.max_pages,
    max_cards=request.max_cards,
    native_filters=request.native_filters,
)
```

Otherwise, leave `opencli_retriever.py` and its fakes on `search_liepin_resumes()`.

- [ ] **Step 7: Add composition boundary test**

In `tests/test_liepin_provider_source_composition.py`, update the expected public method list to include only the new public extraction method:

```python
"extract_structured_liepin_cards(self, *, source_run_id: 'str', max_cards: 'int') -> 'OpenCliBrowserResult'"
```

Do not add `append_agent_event`, `safe_liepin_detail_url_for_ref`, `open_liepin_detail_cached_url`, `restore_liepin_search_page`, `blocked_resumes_envelope`, or `search_detail_backed_resumes` to the public signature expectation.

- [ ] **Step 8: Run workflow and retriever tests**

Run:

```bash
pytest tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_provider_source_composition.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/providers/liepin/liepin_search_workflow.py \
  src/seektalent/providers/liepin/liepin_site_adapter.py \
  src/seektalent/providers/liepin/opencli_retriever.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_provider_source_composition.py
git commit -m "refactor: split Liepin search workflow from site adapter"
```

---

### Task 5: Boundary Sweep And Regression Verification

**Files:**
- Modify: `tests/test_liepin_drift_smoke.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_liepin_opencli_worker_client.py`

- [ ] **Step 1: Update remaining test fixtures**

Run:

```bash
rg -n "visible_text|normalized_card_text|extract_visible_liepin_cards|search_liepin_resumes" \
  tests \
  src/seektalent/providers/liepin \
  src/seektalent/sources/liepin \
  src/seektalent/resume_normalizers/liepin.py \
  src/seektalent_runtime_control
```

For fixtures that model current card payloads, replace:

```python
{"provider_rank": 1, "ref": "70", "visible_text": "Python engineer"}
```

with:

```python
{
    "provider_rank": 1,
    "ref": "70",
    "current_or_recent_title": "Python engineer",
    "skill_tags": ["Python"],
    "experience_preview": [{"title": "Python engineer"}],
}
```

For stale compatibility checks that still call `extract_visible_liepin_cards`, keep the old action name only when the test is explicitly proving CLI compatibility.

- [ ] **Step 2: Add final source scan test**

In `tests/test_liepin_boundaries.py`, add:

```python
def test_liepin_card_evidence_does_not_emit_text_tail_fields() -> None:
    forbidden = {
        "visible_text",
        "normalized_card_text",
    }
    scan_paths = [
        ROOT / "src/seektalent/providers/liepin",
        ROOT / "src/seektalent/sources/liepin",
        ROOT / "src/seektalent/resume_normalizers/liepin.py",
        ROOT / "src/seektalent_runtime_control",
    ]
    hits: list[str] = []
    paths: list[Path] = []
    for scan_path in scan_paths:
        if scan_path.is_file():
            paths.append(scan_path)
        else:
            paths.extend(scan_path.rglob("*.py"))
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                hits.append(f"{rel}:{token}")
    assert hits == []
```

If compatibility action names require `extract_visible_liepin_cards`, do not include that action name in this scan. The hard contract is no card evidence fields named `visible_text` or `normalized_card_text`.

- [ ] **Step 3: Run targeted provider suite**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py \
  tests/test_liepin_opencli_browser_window_policy.py \
  tests/test_liepin_card_policy.py \
  tests/test_liepin_provider_mapping.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_search_workflow.py \
  tests/test_liepin_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 4: Run runtime and workbench regressions**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_workbench_api.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_normalization.py -q
```

Expected: PASS.

- [ ] **Step 5: Run final grep gates**

Run:

```bash
rg -n "visible_text|normalized_card_text|fullText|rawText" \
  src/seektalent/providers/liepin \
  src/seektalent/sources/liepin \
  src/seektalent/resume_normalizers/liepin.py \
  src/seektalent_runtime_control \
  tests
```

Expected:

- No production Liepin card evidence hits for `visible_text` or `normalized_card_text`.
- No production Liepin detail payload hits for `fullText` or `rawText`.
- Test hits are limited to negative assertions that prove absence or rejection.

- [ ] **Step 6: Commit**

```bash
git add tests/test_liepin_drift_smoke.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_workbench_api.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_liepin_boundaries.py
git commit -m "test: verify Liepin structured card boundary"
```

---

## Self-Review Notes

Spec coverage:

1. OpenCLI remains generic: Tasks 2 and 5 keep Liepin methods out of `OpenCliBrowserAutomation`.
2. Liepin adapter owns page extraction only: Tasks 2 and 4 add structured extraction and move the detail-backed loop.
3. Workflow owns orchestration: Task 4 introduces `LiepinSearchWorkflow`.
4. Structured card evidence replaces visible text: Tasks 1 through 3 remove `visible_text` and `normalized_card_text`.
5. Worker/client stability: Task 4 keeps `LiepinOpenCliResumeRetriever` response mapping unchanged.
6. Scoring parallelism unchanged: no task modifies scoring code or runtime scoring concurrency.
7. UI workflow observations preserved: Task 4 keeps safe action events and existing workflow-step projection.
8. Production tail risks from external review are covered: `search_liepin_cards()` is structured-only, `runtime_lane.py` does not rehydrate card policy from `candidate.search_text`, `resume_normalizers/liepin.py` does not use `normalized_card_text`, card artifacts are sanitized before write, TS extension gates are fully updated, public adapter signatures stay narrow, workflow event duplication is avoided, and blocked reason codes are non-null.

Completion scan:

1. No step uses unfinished-marker text or vague error-handling instructions.
2. Each code-changing task has a failing test step, an implementation step, a passing test command, and a commit step.
3. Type names are consistent: `LiepinStructuredCardEvidence`, `LiepinSafeCardSummary`, `LiepinSearchWorkflow`, and `LiepinSearchWorkflowRequest`.
