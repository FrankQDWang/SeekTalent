# Liepin Filter Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Liepin use the same Runtime-owned filter semantics as CTS for city, work experience, age, degree, recruitment type, and school type.

**Architecture:** Runtime continues to emit provider-neutral filter and location intent. The Liepin adapter compiles that intent into a typed safe native-filter payload with exact UI section targets, and the OpenCLI runner applies those targets before reading cards. CTS behavior remains the reference for range projection and must not regress.

**Tech Stack:** Python 3.12, pytest, ruff, ty, Svelte/Vite build, SeekTalent Runtime source adapters, OpenCLI-backed real Chrome QA.

---

Linked spec: `docs/superpowers/specs/2026-05-23-liepin-filter-parity-design.md`

## File Structure

- Modify `src/seektalent/providers/liepin/filter_compiler.py`
  - Owns Liepin-specific projection from Runtime intent to typed safe native-filter targets.
  - Adds city section, projected labels, degree, recruitment type, and school type.
- Modify `src/seektalent/providers/liepin/source_compiler.py`
  - Carries the new safe payload through `SearchRequest.provider_context`.
  - Converts projection misses into source-scoped partial reasons.
- Modify `src/seektalent/providers/pi_agent/contracts.py`
  - Extends the PI boundary schema for typed Liepin native filters.
- Modify `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Applies typed filters in the correct UI section and records protected trace events.
- Modify `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
  - Validates and forwards the extended `nativeFilters` payload unchanged.
- Modify `src/seektalent/providers/liepin/pi_worker_client.py`
  - Keeps forwarding `liepin_native_filters_json` to the executor.
- Modify `src/seektalent/providers/liepin/pi_executor.py`
  - Keeps the extended payload on the `LiepinSearchCardsTask`.
- Modify `src/seektalent/runtime/orchestrator.py`
  - Treats newly supported Liepin filters as supported when computing public warning reasons.
- Test `tests/test_liepin_native_filter_compiler.py`
  - Adds projection tests for city section, degree, recruitment type, school type, and work-experience mismatch.
- Test `tests/test_liepin_source_compiler.py`
  - Adds end-to-end source compiler payload and partial-reason tests.
- Test `tests/test_pi_agent_boundaries.py`
  - Adds contract validation for extended native filters.
- Test `tests/test_pi_opencli_browser.py`
  - Adds section-aware browser action ordering tests.
- Test `tests/test_runtime_source_adapter_boundary.py`
  - Adds public-warning regression tests for newly supported Liepin filters.

## Behavior Decisions

City:
- Runtime `location_intent` maps to Liepin `期望城市`.
- Single city emits one Liepin target.
- Multi-city emits one target per Runtime city target using existing `allocate_balanced_city_targets(...)` behavior.
- If a city is not visible in `期望城市`, do not click `目前城市` as a fallback. Record a safe partial reason and continue the source query.

Work experience:
- Use the same bucket overlap rule as CTS.
- Liepin labels are `应届生`, `1-3年`, `3-5年`, `5-10年`, `10年以上`.
- `min=3,max=5` maps to `3-5年`.
- `min=2,max=4` maps to the best-overlap bucket using the same tie order as CTS.
- Spans across three or more buckets are not applied natively.
- `自定义` is not used in this patch.

Education:
- `degree_requirement=本科` maps to section `教育经历`, option `本科`.
- `school_type_requirement=统招` maps to section `统招要求`, option `统招`.
- `school_type_requirement=211/985/双一流` maps to section `院校要求`.
- If `985` and `211` are both requested and the page supports selecting both, click both. If the page behaves like a single-select control, keep the broadest safe value applied and record the other as runtime-only partial.

## Task 1: Extend Liepin Filter Compiler Projection

**Files:**
- Modify: `src/seektalent/providers/liepin/filter_compiler.py`
- Test: `tests/test_liepin_native_filter_compiler.py`

- [ ] **Step 1: Add failing compiler tests**

Append these tests to `tests/test_liepin_native_filter_compiler.py`:

```python
def test_compile_liepin_native_filters_targets_expected_city_section() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("北京",),
            preferred_locations=(),
            priority_order=("北京",),
            balanced_order=("北京",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.city == "北京"
    assert target.city_section == "expected"
    assert target.to_safe_payload()["city"] == {"section": "expected", "label": "北京"}
```

Add a second test for the full historical JD filters:

```python
def test_compile_liepin_native_filters_projects_degree_recruitment_and_school_type() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(field="degree_requirement", value="本科", required=False, origin="controller"),
            RuntimeFilterIntent(field="school_type_requirement", value=["统招", "985", "211"], required=False, origin="controller"),
            RuntimeFilterIntent(field="experience_requirement", value=["min=2", "max=4"], required=False, origin="controller"),
        ),
        location_intent=None,
        age_intent=None,
    )

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.degree_label == "本科"
    assert target.recruitment_type_label == "统招"
    assert target.school_type_labels == ("211", "985")
    assert target.experience_label in {"1-3年", "3-5年"}
    assert target.to_safe_payload()["degree"] == {"section": "education", "label": "本科"}
    assert target.to_safe_payload()["recruitmentType"] == {"section": "recruitment_type", "label": "统招"}
    assert target.to_safe_payload()["schoolTypes"] == [
        {"section": "school_type", "label": "211"},
        {"section": "school_type", "label": "985"},
    ]
```

Add a third test for unsafe experience spans:

```python
def test_compile_liepin_native_filters_skips_experience_spanning_three_buckets() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(field="experience_requirement", value=["min=1", "max=10"], required=False, origin="controller"),
        ),
        location_intent=None,
        age_intent=None,
    )

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.experience_label is None
    assert "experience" not in target.to_safe_payload()
    assert any(reason.field == "experience_requirement" for reason in target.partial_reasons)
```

- [ ] **Step 2: Run compiler tests and verify failure**

Run:

```bash
uv run pytest tests/test_liepin_native_filter_compiler.py -q
```

Expected: FAIL because `city_section`, `degree_label`, `recruitment_type_label`, `school_type_labels`, `experience_label`, and `partial_reasons` do not exist yet.

- [ ] **Step 3: Implement typed projection fields**

Modify `LiepinNativeFilterTarget` in `src/seektalent/providers/liepin/filter_compiler.py` to include:

```python
@dataclass(frozen=True)
class LiepinNativeFilterPartial:
    field: str
    safe_reason_code: str
    detail: str


@dataclass(frozen=True)
class LiepinNativeFilterTarget:
    phase: str
    batch_no: int
    requested_count: int
    city: str | None = None
    city_section: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    experience_label: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    age_label: str | None = None
    degree_label: str | None = None
    recruitment_type_label: str | None = None
    school_type_labels: tuple[str, ...] = ()
    partial_reasons: tuple[LiepinNativeFilterPartial, ...] = ()
```

Update `to_safe_payload()` so it emits typed browser targets:

```python
def to_safe_payload(self) -> dict[str, object]:
    payload: dict[str, object] = {}
    if self.city and self.city_section:
        payload["city"] = {"section": self.city_section, "label": self.city}
    if self.experience_label:
        payload["experience"] = {"section": "experience", "label": self.experience_label}
    if self.age_label:
        payload["age"] = {"section": "age", "label": self.age_label}
    if self.degree_label:
        payload["degree"] = {"section": "education", "label": self.degree_label}
    if self.recruitment_type_label:
        payload["recruitmentType"] = {"section": "recruitment_type", "label": self.recruitment_type_label}
    if self.school_type_labels:
        payload["schoolTypes"] = [
            {"section": "school_type", "label": label}
            for label in self.school_type_labels
        ]
    if self.partial_reasons:
        payload["partialReasonCodes"] = [reason.safe_reason_code for reason in self.partial_reasons]
    payload["sourceTarget"] = {
        "phase": self.phase,
        "batchNo": self.batch_no,
        "requestedCount": self.requested_count,
    }
    return payload
```

- [ ] **Step 4: Add Liepin projection helpers**

In `src/seektalent/providers/liepin/filter_compiler.py`, add constants and helpers:

```python
LIEPIN_EXPERIENCE_BUCKETS = (
    ("应届生", 0, 0, 1),
    ("1-3年", 1, 1, 3),
    ("3-5年", 2, 3, 5),
    ("5-10年", 3, 5, 10),
    ("10年以上", 4, 10, None),
)
LIEPIN_EXPERIENCE_TIE_ORDER = {
    "3-5年": 0,
    "5-10年": 1,
    "1-3年": 2,
    "10年以上": 3,
    "应届生": 4,
}
LIEPIN_DEGREE_LABELS = {"大专", "本科", "硕士", "博士/博士后", "中专/中技", "高中及以下"}
LIEPIN_SCHOOL_TYPE_LABELS = {"211", "985", "双一流", "海外留学"}


def _project_liepin_range_label(
    *,
    field: str,
    value: object,
    buckets: tuple[tuple[str, int, int, int | None], ...],
    tie_order: dict[str, int],
) -> tuple[str | None, LiepinNativeFilterPartial | None]:
    bounds = _parse_min_max(value)
    if not bounds:
        return None, LiepinNativeFilterPartial(
            field=field,
            safe_reason_code="source_filter_partial",
            detail=f"{field} stayed runtime-only because range normalization is invalid.",
        )
    lower = bounds.get("min")
    upper = bounds.get("max")
    overlaps: list[tuple[str, float]] = []
    for label, _code, bucket_min, bucket_max in buckets:
        overlap = _range_overlap(lower, upper, bucket_min, bucket_max)
        if overlap > 0:
            overlaps.append((label, overlap))
    if not overlaps:
        return None, LiepinNativeFilterPartial(
            field=field,
            safe_reason_code="source_filter_partial",
            detail=f"{field} does not match any supported Liepin range.",
        )
    if len(overlaps) >= 3:
        return None, LiepinNativeFilterPartial(
            field=field,
            safe_reason_code="source_filter_partial",
            detail=f"{field} spans 3 or more Liepin ranges.",
        )
    overlaps.sort(key=lambda item: (-item[1], tie_order[item[0]]))
    first = overlaps[0]
    if len(overlaps) == 2 and first[1] == overlaps[1][1]:
        overlaps.sort(key=lambda item: tie_order[item[0]])
        first = overlaps[0]
    return first[0], None
```

Reuse the existing `_parse_min_max(...)` function and add `_range_overlap(...)` equivalent to the CTS helper if it is not already present in this module.

- [ ] **Step 5: Wire projection into `compile_liepin_native_filters`**

Inside the filter-intent loop:

```python
degree_label: str | None = None
recruitment_type_label: str | None = None
school_type_labels: list[str] = []
experience_label: str | None = None
partial_reasons: list[LiepinNativeFilterPartial] = []

for filter_intent in intent.filter_intents:
    if filter_intent.field == "degree_requirement":
        label = str(filter_intent.value).strip()
        if label in LIEPIN_DEGREE_LABELS:
            degree_label = label
        else:
            partial_reasons.append(LiepinNativeFilterPartial("degree_requirement", "source_filter_partial", "degree_requirement stayed runtime-only because Liepin has no stable label."))
    elif filter_intent.field == "school_type_requirement":
        values = filter_intent.value if isinstance(filter_intent.value, list) else [filter_intent.value]
        for raw in values:
            label = str(raw).strip()
            if label == "统招":
                recruitment_type_label = "统招"
            elif label in LIEPIN_SCHOOL_TYPE_LABELS and label not in school_type_labels:
                school_type_labels.append(label)
            elif label != "不限":
                partial_reasons.append(LiepinNativeFilterPartial("school_type_requirement", "source_filter_partial", "school_type_requirement stayed runtime-only because Liepin has no stable label."))
    elif filter_intent.field == "experience_requirement":
        experience_label, partial = _project_liepin_range_label(
            field="experience_requirement",
            value=filter_intent.value,
            buckets=LIEPIN_EXPERIENCE_BUCKETS,
            tie_order=LIEPIN_EXPERIENCE_TIE_ORDER,
        )
        if partial is not None:
            partial_reasons.append(partial)
```

When constructing each `LiepinNativeFilterTarget`, set:

```python
city=city,
city_section="expected" if city else None,
experience_label=experience_label,
degree_label=degree_label,
recruitment_type_label=recruitment_type_label,
school_type_labels=tuple(label for label in ("双一流", "211", "985", "海外留学") if label in school_type_labels),
partial_reasons=tuple(partial_reasons),
```

- [ ] **Step 6: Run compiler tests**

Run:

```bash
uv run pytest tests/test_liepin_native_filter_compiler.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/seektalent/providers/liepin/filter_compiler.py tests/test_liepin_native_filter_compiler.py
git commit -m "feat: project liepin runtime filters"
```

## Task 2: Update Source Compiler Partial Semantics

**Files:**
- Modify: `src/seektalent/providers/liepin/source_compiler.py`
- Test: `tests/test_liepin_source_compiler.py`
- Test: `tests/test_runtime_source_adapter_boundary.py`

- [ ] **Step 1: Add failing source compiler tests**

Append to `tests/test_liepin_source_compiler.py`:

```python
def test_liepin_source_compiler_payload_contains_expected_city_and_education_filters() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(field="degree_requirement", value="本科", required=False, origin="controller"),
            RuntimeFilterIntent(field="school_type_requirement", value=["统招", "985", "211"], required=False, origin="controller"),
        ),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("北京",),
            preferred_locations=(),
            priority_order=("北京",),
            balanced_order=("北京",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))
    payload = json.loads(str(compiled.queries[0].search_request.provider_context["liepin_native_filters_json"]))

    assert compiled.unsupported_filters == ()
    assert payload["city"] == {"section": "expected", "label": "北京"}
    assert payload["degree"] == {"section": "education", "label": "本科"}
    assert payload["recruitmentType"] == {"section": "recruitment_type", "label": "统招"}
    assert payload["schoolTypes"] == [
        {"section": "school_type", "label": "211"},
        {"section": "school_type", "label": "985"},
    ]
```

Append a partial regression test:

```python
def test_liepin_source_compiler_records_partial_for_unprojected_filter() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(field="experience_requirement", value=["min=1", "max=10"], required=False, origin="controller"),
        ),
        location_intent=None,
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))

    assert [item.safe_reason_code for item in compiled.unsupported_filters] == ["source_filter_partial"]
    assert "runtime-only" in compiled.queries[0].search_request.adapter_notes[0]
```

- [ ] **Step 2: Run source compiler tests and verify failure**

Run:

```bash
uv run pytest tests/test_liepin_source_compiler.py -q
```

Expected: FAIL because the source compiler still reports supported education filters as unsupported and does not convert target partials.

- [ ] **Step 3: Convert target partials to `UnsupportedSourceFilter`**

Modify `src/seektalent/providers/liepin/source_compiler.py` so `_unsupported_filters(...)` starts with target partials:

```python
unsupported = [
    UnsupportedSourceFilter(
        source_kind="liepin",
        field=partial.field,
        query_instance_id=intent.query_instance_id,
        safe_reason_code=partial.safe_reason_code,
        detail=partial.detail,
    )
    for partial in native_filter_target.partial_reasons
]
```

Then treat these Liepin fields as supported when they are present in the target:

```python
supported_fields = set()
if native_filter_target.city is not None:
    supported_fields.add("location")
if native_filter_target.experience_label is not None:
    supported_fields.add("experience_requirement")
if native_filter_target.age_label is not None or native_filter_target.age_min is not None or native_filter_target.age_max is not None:
    supported_fields.add("age_requirement")
if native_filter_target.degree_label is not None:
    supported_fields.add("degree_requirement")
if native_filter_target.recruitment_type_label is not None or native_filter_target.school_type_labels:
    supported_fields.add("school_type_requirement")
```

Only emit a new `source_filter_unsupported` when `filter_intent.field` is absent from `supported_fields` and absent from the target partial fields.

- [ ] **Step 4: Update runtime public warning regression**

Append to `tests/test_runtime_source_adapter_boundary.py`:

```python
def test_liepin_supported_education_filters_do_not_emit_unsupported_warning() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(field="degree_requirement", value="本科", required=False, origin="controller"),
            RuntimeFilterIntent(field="school_type_requirement", value=["统招", "985", "211"], required=False, origin="controller"),
        ),
        location_intent=None,
        age_intent=None,
    )

    assert _liepin_filter_warning_reason((intent,)) is None
```

- [ ] **Step 5: Update `_liepin_filter_warning_reason`**

Modify `src/seektalent/runtime/orchestrator.py` so its supported Liepin filter set includes:

```python
supported_filter_fields = {
    "degree_requirement",
    "school_type_requirement",
    "experience_requirement",
    "age_requirement",
}
```

Keep location handled through `location_intent`, and continue returning a safe warning only for truly unsupported fields.

- [ ] **Step 6: Run source compiler and boundary tests**

Run:

```bash
uv run pytest tests/test_liepin_source_compiler.py tests/test_runtime_source_adapter_boundary.py::test_liepin_supported_education_filters_do_not_emit_unsupported_warning -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/seektalent/providers/liepin/source_compiler.py src/seektalent/runtime/orchestrator.py tests/test_liepin_source_compiler.py tests/test_runtime_source_adapter_boundary.py
git commit -m "fix: mark projected liepin filters supported"
```

## Task 3: Extend PI Boundary Schema

**Files:**
- Modify: `src/seektalent/providers/pi_agent/contracts.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
- Test: `tests/test_pi_agent_boundaries.py`

- [ ] **Step 1: Add failing boundary test**

Append to `tests/test_pi_agent_boundaries.py`:

```python
def test_liepin_search_cards_task_accepts_extended_safe_native_filters() -> None:
    task = LiepinSearchCardsTask.model_validate(
        {
            "taskType": "liepin_search_cards",
            "sourceRunId": "source-1",
            "sessionId": "session-1",
            "queryTerms": ["数据开发"],
            "keywordQuery": "数据开发 ETL",
            "maxPages": 1,
            "maxCards": 10,
            "stopConditions": ["page_exhausted"],
            "nativeFilters": {
                "city": {"section": "expected", "label": "北京"},
                "experience": {"section": "experience", "label": "3-5年"},
                "degree": {"section": "education", "label": "本科"},
                "recruitmentType": {"section": "recruitment_type", "label": "统招"},
                "schoolTypes": [
                    {"section": "school_type", "label": "211"},
                    {"section": "school_type", "label": "985"},
                ],
                "partialReasonCodes": ["source_filter_partial"],
            },
        }
    )

    assert task.native_filters is not None
    assert task.native_filters.city is not None
    assert task.native_filters.city.section == "expected"
    assert task.native_filters.degree is not None
    assert task.native_filters.school_types[0].label == "211"
```

- [ ] **Step 2: Run boundary test and verify failure**

Run:

```bash
uv run pytest tests/test_pi_agent_boundaries.py::test_liepin_search_cards_task_accepts_extended_safe_native_filters -q
```

Expected: FAIL because the current `LiepinNativeFilters` schema only accepts flat `city`, range objects, and partial reason codes.

- [ ] **Step 3: Add typed filter target schema**

Modify `src/seektalent/providers/pi_agent/contracts.py`:

```python
class LiepinNativeFilterOption(PiBoundaryModel):
    section: Literal[
        "expected",
        "current",
        "experience",
        "age",
        "education",
        "recruitment_type",
        "school_type",
    ]
    label: NonEmptyStr


class LiepinNativeFilters(PiBoundaryModel):
    city: LiepinNativeFilterOption | None = None
    experience: LiepinNativeFilterOption | None = None
    age: LiepinNativeFilterOption | None = None
    degree: LiepinNativeFilterOption | None = None
    recruitment_type: LiepinNativeFilterOption | None = Field(default=None, alias="recruitmentType")
    school_types: list[LiepinNativeFilterOption] = Field(default_factory=list, alias="schoolTypes")
    partial_reason_codes: list[NonEmptyStr] = Field(default_factory=list, alias="partialReasonCodes")
    source_target: dict[str, object] | None = Field(default=None, alias="sourceTarget")
```

Keep compatibility only if needed by tests by allowing old flat city payload in `opencli_browser.py`, not at the PI contract layer.

- [ ] **Step 4: Ensure CLI forwards dict payload unchanged**

In `src/seektalent/providers/pi_agent/opencli_browser_cli.py`, verify the `native_filters` extraction still accepts a dict:

```python
native_filters = payload.get("nativeFilters") or payload.get("native_filters")
...
native_filters=cast(Mapping[str, object], native_filters) if isinstance(native_filters, dict) else None,
```

Do not parse selectors or page state in the CLI.

- [ ] **Step 5: Run boundary tests**

Run:

```bash
uv run pytest tests/test_pi_agent_boundaries.py::test_liepin_search_cards_task_accepts_extended_safe_native_filters tests/test_liepin_pi_worker_client.py::test_pi_worker_forwards_native_filters_to_executor -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/seektalent/providers/pi_agent/contracts.py src/seektalent/providers/pi_agent/opencli_browser_cli.py tests/test_pi_agent_boundaries.py
git commit -m "feat: extend liepin native filter contract"
```

## Task 4: Apply Liepin Filters By UI Section

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Test: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Add failing OpenCLI runner test**

Append to `tests/test_pi_opencli_browser.py`:

```python
def test_search_liepin_cards_clicks_filters_in_named_sections(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[10]<label>目前城市：</label>
[11]<label>北京</label>
[20]<label>期望城市：</label>
[21]<label>北京</label>
[30]<label>教育经历：</label>
[31]<label>本科</label>
[40]<label>统招要求：</label>
[41]<button>统招/非统招（不限）</button>
[50]<label>院校要求：</label>
[51]<label>211</label>
[52]<label>985</label>
"""
    state_after_filters = (
        "已选 期望城市北京 本科 统招 211 985\n"
        "王** 男 34岁 工作5年 本科 北京\n"
        "求职期望：北京 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
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
                state_after_search,
                state_after_search,
                state_after_search,
                state_after_search,
                state_after_filters,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发 ETL"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "31"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "51"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "52"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="source-1",
        query="数据开发 ETL",
        max_pages=1,
        max_cards=10,
        native_filters={
            "city": {"section": "expected", "label": "北京"},
            "degree": {"section": "education", "label": "本科"},
            "recruitmentType": {"section": "recruitment_type", "label": "统招"},
            "schoolTypes": [
                {"section": "school_type", "label": "211"},
                {"section": "school_type", "label": "985"},
            ],
        },
    )

    assert result["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "21") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "31") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "51") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "52") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "11") not in commands.calls
```

- [ ] **Step 2: Run the OpenCLI test and verify failure**

Run:

```bash
uv run pytest tests/test_pi_opencli_browser.py::test_search_liepin_cards_clicks_filters_in_named_sections -q
```

Expected: FAIL because the runner currently resolves options by global label, not section.

- [ ] **Step 3: Add section label mapping**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add:

```python
LIEPIN_FILTER_SECTION_LABELS = {
    "current": "目前城市",
    "expected": "期望城市",
    "experience": "工作年限",
    "age": "年龄",
    "education": "教育经历",
    "recruitment_type": "统招要求",
    "school_type": "院校要求",
}
```

Add a parser:

```python
def _native_filter_option_ref_in_section(state_text: str, *, section: str, label: str) -> str | None:
    section_label = LIEPIN_FILTER_SECTION_LABELS.get(section)
    if section_label is None:
        return None
    in_section = False
    for line in state_text.splitlines():
        if section_label in line:
            in_section = True
            continue
        if in_section and _line_starts_known_filter_section(line):
            return None
        if in_section:
            match = re.search(rf"\[([A-Za-z0-9_-]{{1,64}})\]<label[^>]*>\s*{re.escape(label)}\s*</label>", line)
            if match is not None:
                return match.group(1)
    return None
```

Add:

```python
def _line_starts_known_filter_section(line: str) -> bool:
    return any(label in line for label in LIEPIN_FILTER_SECTION_LABELS.values())
```

If the real OpenCLI output puts section and options on the same line, keep the existing line in the scan after setting `in_section = True`.

- [ ] **Step 4: Convert native filter payload to ordered actions**

Replace `_liepin_filter_labels(...)` with an ordered action builder:

```python
def _liepin_filter_actions(native_filters: Mapping[str, object]) -> tuple[tuple[str, str, str], ...]:
    actions: list[tuple[str, str, str]] = []
    for key in ("city", "experience", "age", "degree", "recruitmentType"):
        item = native_filters.get(key)
        if isinstance(item, Mapping):
            section = str(item.get("section") or "").strip()
            label = str(item.get("label") or "").strip()
            if section and label:
                actions.append((key, section, label))
    school_types = native_filters.get("schoolTypes")
    if isinstance(school_types, list):
        for item in school_types:
            if isinstance(item, Mapping):
                section = str(item.get("section") or "").strip()
                label = str(item.get("label") or "").strip()
                if section and label:
                    actions.append(("schoolTypes", section, label))
    return tuple(actions)
```

- [ ] **Step 5: Click by section-aware refs**

Update `_select_liepin_native_filter(...)` to accept `section`:

```python
def _select_liepin_native_filter(
    self,
    *,
    filter_name: str,
    section: str,
    label: str,
    current_state: OpenCliBrowserResult,
    events: list[dict[str, object]],
) -> OpenCliBrowserResult:
    ...
    ref = _native_filter_option_ref_in_section(state_text, section=section, label=label)
    if ref is None:
        self._click_native_filter_menu(filter_name)
        ...
        ref = _native_filter_option_ref_in_section(state_text, section=section, label=label)
    if ref is None:
        raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
    self._click_native_filter_ref(ref)
```

Add:

```python
def _click_native_filter_ref(self, ref: str) -> None:
    argv = tuple(self._config.command) + ("browser", self._config.session, "click", ref)
    self._run(argv)
    self._touch_lease()
```

Keep `_validate_native_filter_label(...)` before clicking and validate `section` is in `LIEPIN_FILTER_SECTION_LABELS`.

- [ ] **Step 6: Run OpenCLI tests**

Run:

```bash
uv run pytest tests/test_pi_opencli_browser.py::test_search_liepin_cards_clicks_filters_in_named_sections tests/test_pi_opencli_browser.py::test_search_liepin_cards_applies_native_filters_before_reading_cards tests/test_pi_opencli_browser.py::test_search_liepin_cards_records_filter_failure_without_blocking_cards -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py tests/test_pi_opencli_browser.py
git commit -m "fix: apply liepin filters by section"
```

## Task 5: Focused Verification And Real Chrome QA

**Files:**
- No source files unless tests reveal a defect.
- Artifact screenshots under `/tmp/seektalent-liepin-filter-parity-qa/`.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
uv run pytest tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_pi_agent_boundaries.py tests/test_pi_opencli_browser.py tests/test_runtime_source_adapter_boundary.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint and type checks**

Run:

```bash
uv run ruff check src/seektalent/providers/liepin/filter_compiler.py src/seektalent/providers/liepin/source_compiler.py src/seektalent/providers/pi_agent/contracts.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/runtime/orchestrator.py tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_pi_agent_boundaries.py tests/test_pi_opencli_browser.py tests/test_runtime_source_adapter_boundary.py
uv run ty check src/seektalent/providers/liepin/filter_compiler.py src/seektalent/providers/liepin/source_compiler.py src/seektalent/providers/pi_agent/contracts.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/runtime/orchestrator.py tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_pi_agent_boundaries.py tests/test_pi_opencli_browser.py tests/test_runtime_source_adapter_boundary.py
```

Expected: both commands PASS.

- [ ] **Step 3: Build backend and frontend**

Run:

```bash
uv build
cd apps/web-svelte && bun run build
```

Expected: both commands PASS. Existing Vite chunk-size warnings are acceptable if no new build error appears.

- [ ] **Step 4: Start Workbench locally**

Run the repository's existing Workbench dev command used in prior QA. If a previous dev server is still running, stop it first and use a fresh one.

Expected: local Workbench API and Svelte UI are reachable in Chrome.

- [ ] **Step 5: Reuse the complete historical input**

Read the source session from `.seektalent/workbench.sqlite3`:

```bash
uv run python - <<'PY'
import sqlite3
from pathlib import Path

db = Path(".seektalent/workbench.sqlite3")
conn = sqlite3.connect(db)
row = conn.execute(
    "SELECT job_title, jd_text, notes FROM workbench_sessions WHERE session_id = ?",
    ("session_814bd4d124df48ce",),
).fetchone()
assert row is not None
print(row[0])
print(row[1])
print(row[2] or "")
PY
```

Expected: job title is `数据开发专家`, and the JD includes `工作城市: 北京`, `学历要求: 本科·统招·985/211`, and `工作年限: 不限`.

- [ ] **Step 6: Run real Chrome Workbench QA**

In real Chrome, create a new Workbench session using the exact values from Step 5. Capture screenshots at:

- `/tmp/seektalent-liepin-filter-parity-qa/01-filled.png`
- `/tmp/seektalent-liepin-filter-parity-qa/02-created.png`
- `/tmp/seektalent-liepin-filter-parity-qa/03-triage-ready.png`
- `/tmp/seektalent-liepin-filter-parity-qa/04-runtime-started.png`
- `/tmp/seektalent-liepin-filter-parity-qa/05-liepin-filter-page.png`
- `/tmp/seektalent-liepin-filter-parity-qa/06-results-visible.png`

Expected in protected Liepin trace:

```json
{"action_kind":"apply_native_filter","filter":"city","section":"expected","value":"北京","ok":true}
{"action_kind":"apply_native_filter","filter":"degree","section":"education","value":"本科","ok":true}
{"action_kind":"apply_native_filter","filter":"recruitmentType","section":"recruitment_type","value":"统招","ok":true}
```

For school type, either both are applied:

```json
{"action_kind":"apply_native_filter","filter":"schoolTypes","section":"school_type","value":"211","ok":true}
{"action_kind":"apply_native_filter","filter":"schoolTypes","section":"school_type","value":"985","ok":true}
```

or one is applied and the other is recorded as a safe source partial:

```json
{"action_kind":"apply_native_filter","filter":"schoolTypes","section":"school_type","value":"985","ok":false,"safe_reason_code":"source_filter_partial"}
```

- [ ] **Step 7: Verify Workbench public payloads**

Query:

```bash
test -n "$SEEKTALENT_QA_SESSION_ID"
SEEKTALENT_QA_BASE_URL="${SEEKTALENT_QA_BASE_URL:-http://127.0.0.1:5173}"
curl -s "$SEEKTALENT_QA_BASE_URL/api/workbench/sessions/$SEEKTALENT_QA_SESSION_ID" > /tmp/seektalent-liepin-filter-parity-qa/session.json
curl -s "$SEEKTALENT_QA_BASE_URL/api/workbench/sessions/$SEEKTALENT_QA_SESSION_ID/events" > /tmp/seektalent-liepin-filter-parity-qa/events.json
curl -s "$SEEKTALENT_QA_BASE_URL/api/workbench/sessions/$SEEKTALENT_QA_SESSION_ID/final-top10" > /tmp/seektalent-liepin-filter-parity-qa/final-top10.json
```

Expected:

```bash
! rg -n "OpenCLI|DokoBot|mcp|pi_agent|cookie|authorization|raw_provider_payload|raw_resume|/Users/" /tmp/seektalent-liepin-filter-parity-qa/session.json /tmp/seektalent-liepin-filter-parity-qa/events.json /tmp/seektalent-liepin-filter-parity-qa/final-top10.json
```

The command should return no matches.

- [ ] **Step 8: Cleanup browser state**

Run the existing OpenCLI orphan cleanup command:

```bash
env NODE_PATH="$PWD/apps/web-svelte/node_modules" PYTHONPATH="$PWD/src" SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$PWD/apps/web-svelte/node_modules/.bin/opencli" SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR="$PWD/.seektalent/opencli_leases" uv run python -m seektalent.providers.pi_agent.opencli_browser_cli cleanup_orphaned_tabs <<<'{"force":true}'
```

Expected: command exits 0 and reports an ok cleanup envelope.

Close or finalize any agent-created Chrome tabs used for QA.

- [ ] **Step 9: Commit verification follow-up if needed**

If QA reveals a code defect, fix it with a focused test and commit:

```bash
git add src/seektalent/providers/liepin/filter_compiler.py src/seektalent/providers/liepin/source_compiler.py src/seektalent/providers/pi_agent/contracts.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/runtime/orchestrator.py tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_pi_agent_boundaries.py tests/test_pi_opencli_browser.py tests/test_runtime_source_adapter_boundary.py
git commit -m "fix: complete liepin filter parity qa"
```

If QA passes without code changes, do not create an empty commit.

## Self-Review

- Spec coverage: The plan covers city section disambiguation, multi-city allocation, work-experience mismatch handling, degree, recruitment type, school type, source-scoped partials, safe public payloads, and real Chrome verification.
- Placeholder scan: No task uses placeholder markers, open-ended future-work markers, or unspecified tests. The only conditional behavior is the real page's school-type multi-select behavior, and both acceptable outcomes are explicitly defined and verified.
- Type consistency: The plan consistently uses `city.section`, `degree`, `recruitmentType`, `schoolTypes`, `partialReasonCodes`, and `sourceTarget` as the safe native-filter payload contract.
