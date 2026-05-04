# LLM PRF Probe Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the active `prf_probe` candidate proposal backend for the 30% typed second lane with a DeepSeek V4 Flash LLM extractor, while keeping deterministic grounding, phrase-family support checks, the existing PRF gate, `generic_explore` fallback, 70/30 allocation, and low-quality rescue `candidate_feedback` behavior intact.

**Architecture:** Keep PRF as one producer feeding the existing `FeedbackCandidateExpression -> PRFPolicyDecision -> build_second_lane_decision` path. Add a dedicated `prf_probe_phrase_proposal` text-LLM stage and a focused `candidate_feedback.llm_prf` module. The LLM proposes candidate phrases only; runtime validates evidence, recovers raw offsets, reclassifies deterministically, computes phrase-family support/conflicts, and records typed artifacts before the existing PRF gate decides whether the second lane becomes `prf_probe` or `generic_explore`.

**Tech Stack:** Python 3.12, Pydantic models, pydantic-ai `Agent`, existing text-LLM stage resolver, existing artifact registry, pytest, existing runtime tests

---

## File Map

### New files

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/llm_prf.py`
  - Own LLM PRF input/output schemas, prompt rendering, extractor, grounding, conservative familying, conversion into `FeedbackCandidateExpression`, skip/failure result models, and artifact ref helpers.
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/llm_prf_bakeoff.py`
  - Non-CI live DeepSeek bakeoff harness and metrics writer.
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/prf_probe_phrase_proposal.md`
  - System prompt for extractive PRF phrase proposal.
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_prf.py`
  - Pure data-contract, grounding, familying, classification, and policy integration tests.
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_prf_bakeoff.py`
  - Offline metrics and blocker-condition tests for the bakeoff harness.
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/fixtures/llm_prf_bakeoff/cases.jsonl`
  - Sanitized English, Chinese, and mixed-language fixed slices used by the harness tests.

### Modified files

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py`
  - Add PRF proposal backend, dedicated PRF LLM model id, reasoning effort, timeout, and max output token settings.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/llm.py`
  - Add `prf_probe_phrase_proposal` stage resolution and force it through prompted JSON, including after the OpenAI-default branch lands.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/resources.py`
  - Register the new prompt as a required packaged prompt.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/registry.py`
  - Register typed logical artifacts for LLM PRF input, call, candidates, and grounding.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/__init__.py`
  - Export only stable LLM PRF boundary models/functions needed by runtime and tests.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
  - Extend `SecondLaneDecision` and `ReplaySnapshot` with proposal backend, LLM PRF failure, version, and artifact refs.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
  - Instantiate the LLM PRF extractor, select the active proposal backend, enforce timeout/fallback, write artifacts, and keep sidecar/legacy paths explicit.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py`
  - Carry proposal backend and LLM PRF refs/failure metadata into `SecondLaneDecision`.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
  - Include LLM PRF replay metadata and classify failures in replay snapshots.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/.env.example`
  - Expose the new PRF backend/stage settings.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/default.env`
  - Mirror `.env.example` for packaged installs.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/configuration.md`
  - Document the new PRF settings and rescue-lane separation.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
  - Document the new LLM PRF artifacts and replay fields.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py`
  - Lock PRF stage defaults, stage resolution, prompted JSON mode, and env template settings.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_store.py`
  - Lock LLM PRF artifact registry paths.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py`
  - Lock proposal-backend metadata propagation.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
  - Lock runtime LLM PRF success/fallback/timeout behavior, sidecar explicitness, and rescue isolation.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py`
  - Keep legacy regex and sidecar tests explicit and add dormant rescue-stage non-pollution checks where local.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
  - Lock public run-config serialization and artifact/report expectations.
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`
  - Lock replay snapshot parsing with new optional LLM PRF fields.

## Task 1: Add Config, Stage, Prompt, And Artifact Registry Contracts

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_store.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/llm.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/resources.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/registry.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/prompts/prf_probe_phrase_proposal.md`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/.env.example`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/default.env`

- [ ] **Step 1: Write failing config/stage tests**

Add to `tests/test_llm_provider_config.py`:

```python
def test_prf_probe_llm_backend_defaults_are_explicit() -> None:
    settings = make_settings()

    assert settings.prf_probe_proposal_backend == "llm_deepseek_v4_flash"
    assert settings.prf_probe_phrase_proposal_model_id == "deepseek-v4-flash"
    assert settings.prf_probe_phrase_proposal_reasoning_effort == "off"
    assert settings.prf_probe_phrase_proposal_timeout_seconds == 3.0
    assert settings.prf_probe_phrase_proposal_max_output_tokens == 2048


def test_prf_probe_phrase_proposal_stage_uses_prompted_json() -> None:
    settings = make_settings()

    stage = resolve_stage_model_config(settings, stage="prf_probe_phrase_proposal")

    assert stage.model_id == "deepseek-v4-flash"
    assert stage.reasoning_effort == "off"
    assert stage.thinking_mode is False
    assert resolve_structured_output_mode(stage) == "prompted_json"
```

Extend `test_checked_in_env_templates_use_new_text_llm_keys`:

```python
assert "SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND=llm_deepseek_v4_flash" in text
assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash" in text
assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT=off" in text
assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=3.0" in text
assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS=2048" in text
```

Add to `tests/test_artifact_store.py`:

```python
@pytest.mark.parametrize(
    ("logical_name", "expected_path"),
    [
        ("round.02.retrieval.llm_prf_input", "rounds/02/retrieval/llm_prf_input.json"),
        ("round.02.retrieval.llm_prf_call", "rounds/02/retrieval/llm_prf_call.json"),
        ("round.02.retrieval.llm_prf_candidates", "rounds/02/retrieval/llm_prf_candidates.json"),
        ("round.02.retrieval.llm_prf_grounding", "rounds/02/retrieval/llm_prf_grounding.json"),
    ],
)
def test_llm_prf_retrieval_artifacts_are_registered_and_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logical_name: str,
    expected_path: str,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    assert logical_name.split(".")[-1] in ROUND_CONTENT_TYPES
    entry = resolve_descriptor(logical_name)
    path = session.write_json(logical_name, {"schema_version": "llm-prf-v1"})

    assert entry.path == expected_path
    assert path == session.root / expected_path
    assert session.resolver().resolve(logical_name) == session.root / expected_path
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/test_llm_provider_config.py::test_prf_probe_llm_backend_defaults_are_explicit \
  tests/test_llm_provider_config.py::test_prf_probe_phrase_proposal_stage_uses_prompted_json \
  tests/test_llm_provider_config.py::test_checked_in_env_templates_use_new_text_llm_keys \
  tests/test_artifact_store.py::test_llm_prf_retrieval_artifacts_are_registered_and_written
```

Expected: FAIL because the settings, stage, prompt, env vars, and artifact descriptors do not exist yet.

- [ ] **Step 3: Add settings and validation**

Update `src/seektalent/config.py`:

```python
PRFProbeProposalBackend = Literal[
    "llm_deepseek_v4_flash",
    "legacy_regex",
    "sidecar_span",
]
```

Add `prf_probe_phrase_proposal_model_id` to `TEXT_LLM_MODEL_ID_FIELDS`.

Add fields to `AppSettings` near existing PRF settings:

```python
prf_probe_proposal_backend: PRFProbeProposalBackend = "llm_deepseek_v4_flash"
prf_probe_phrase_proposal_model_id: str = "deepseek-v4-flash"
prf_probe_phrase_proposal_reasoning_effort: ReasoningEffort = "off"
prf_probe_phrase_proposal_timeout_seconds: float = 3.0
prf_probe_phrase_proposal_max_output_tokens: int = 2048
```

Extend `validate_ranges()`:

```python
if self.prf_probe_phrase_proposal_timeout_seconds <= 0:
    raise ValueError("prf_probe_phrase_proposal_timeout_seconds must be > 0")
if self.prf_probe_phrase_proposal_max_output_tokens < 256:
    raise ValueError("prf_probe_phrase_proposal_max_output_tokens must be >= 256")
```

- [ ] **Step 4: Add stage resolution with prompted JSON**

Update `src/seektalent/llm.py`:

Add this entry to `STAGE_MODEL_ATTR`:

```python
"prf_probe_phrase_proposal": "prf_probe_phrase_proposal_model_id",
```

In `_resolve_stage_reasoning_policy`:

```python
if stage == "prf_probe_phrase_proposal":
    effort = settings.prf_probe_phrase_proposal_reasoning_effort
    return effort != "off", effort
```

In `resolve_structured_output_mode`, force this stage through prompted JSON before capability lookup:

```python
if config.stage in {"candidate_feedback", "tui_summary", "prf_probe_phrase_proposal"}:
    return "prompted_json"
```

This stage-level override is required even after the OpenAI-default restoration branch lands, because Bailian-hosted DeepSeek V4 must not be assumed to support native strict JSON Schema for this extractor.

- [ ] **Step 5: Register prompt and artifacts**

Add `"prf_probe_phrase_proposal"` to `REQUIRED_PROMPTS` in `src/seektalent/resources.py`.

Create `src/seektalent/prompts/prf_probe_phrase_proposal.md`:

```markdown
You extract common, explicit PRF phrases from already-scored seed resumes.

Return json only. Do not rewrite the search query. Do not infer skills that are not visible in the evidence.
Every candidate surface must appear in one or more referenced seed evidence texts.
Prefer phrases supported by at least two fit seed resumes.
Avoid company names, locations, schools, degrees, salary, age, title-only phrases, and generic boilerplate.
candidate_term_type and risk_flags are advisory only; runtime validation is authoritative.
```

Add these leaves to `ROUND_CONTENT_TYPES` in `src/seektalent/artifacts/registry.py`:

```python
"llm_prf_input": "application/json",
"llm_prf_call": "application/json",
"llm_prf_candidates": "application/json",
"llm_prf_grounding": "application/json",
```

- [ ] **Step 6: Sync env templates**

Add the same PRF settings to both `.env.example` and `src/seektalent/default.env` near existing PRF settings:

```dotenv
SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND=llm_deepseek_v4_flash
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT=off
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=3.0
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS=2048
```

- [ ] **Step 7: Re-run focused tests**

Run:

```bash
uv run pytest -q \
  tests/test_llm_provider_config.py::test_prf_probe_llm_backend_defaults_are_explicit \
  tests/test_llm_provider_config.py::test_prf_probe_phrase_proposal_stage_uses_prompted_json \
  tests/test_llm_provider_config.py::test_checked_in_env_templates_use_new_text_llm_keys \
  tests/test_artifact_store.py::test_llm_prf_retrieval_artifacts_are_registered_and_written
```

Expected: PASS.

- [ ] **Step 8: Commit this slice**

```bash
git add \
  src/seektalent/config.py \
  src/seektalent/llm.py \
  src/seektalent/resources.py \
  src/seektalent/artifacts/registry.py \
  src/seektalent/prompts/prf_probe_phrase_proposal.md \
  .env.example \
  src/seektalent/default.env \
  tests/test_llm_provider_config.py \
  tests/test_artifact_store.py
git commit -m "feat: add llm prf proposal config"
```

## Task 2: Build LLM PRF Contracts, Grounding, And Familying

**Files:**
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/__init__.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_prf.py`

- [ ] **Step 1: Write failing pure tests for input, grounding, and familying**

Add `tests/test_llm_prf.py` with tests covering these exact behaviors:

```python
def test_build_llm_prf_input_freezes_source_text_hashes() -> None:
    payload = build_llm_prf_input(
        round_no=2,
        requirement_sheet=_requirement_sheet(),
        retrieval_plan=_retrieval_plan(["python", "streaming"]),
        seed_resumes=[
            _scored("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored("seed-2", matched_must_haves=["Flink CDC production experience."]),
        ],
        negative_resumes=[],
        query_term_pool=[],
        sent_query_history=[],
    )

    assert payload is not None
    assert payload.seed_resume_ids == ["seed-1", "seed-2"]
    assert payload.source_texts[0].source_kind == "grounding_eligible"
    assert payload.source_texts[0].source_text_hash == text_sha256(payload.source_texts[0].source_text_raw)


def test_grounding_accepts_exact_raw_substring_with_offsets() -> None:
    payload = _input_with_text("seed-1", "evidence", 0, "Built Flink CDC pipelines.")
    extraction = _extraction(
        "Flink CDC",
        refs=[_ref("seed-1", "evidence", 0, payload.source_texts[0].source_text_hash)],
    )

    grounding = ground_llm_prf_candidates(payload, extraction)

    accepted = grounding.records[0]
    assert accepted.accepted is True
    assert accepted.raw_surface == "Flink CDC"
    assert accepted.start_char == 6
    assert accepted.end_char == 15


def test_grounding_uses_nfkc_offset_map_back_to_raw_text() -> None:
    payload = _input_with_text("seed-1", "evidence", 0, "熟悉Ｆｌｉｎｋ CDC治理")
    extraction = _extraction(
        "Flink CDC",
        refs=[_ref("seed-1", "evidence", 0, payload.source_texts[0].source_text_hash)],
    )

    grounding = ground_llm_prf_candidates(payload, extraction)

    assert grounding.records[0].accepted is True
    assert grounding.records[0].raw_surface == "Ｆｌｉｎｋ CDC"


@pytest.mark.parametrize(
    ("surface", "text"),
    [
        ("Java", "Built JavaScript services."),
        ("React", "React Native mobile application."),
        ("阿里", "阿里云大数据平台经验"),
    ],
)
def test_grounding_rejects_unsafe_substrings(surface: str, text: str) -> None:
    payload = _input_with_text("seed-1", "evidence", 0, text)
    extraction = _extraction(
        surface,
        refs=[_ref("seed-1", "evidence", 0, payload.source_texts[0].source_text_hash)],
    )

    grounding = ground_llm_prf_candidates(payload, extraction)

    assert grounding.records[0].accepted is False
    assert "unsafe_substring_match" in grounding.records[0].reject_reasons


def test_family_support_counts_separator_and_camelcase_variants() -> None:
    payload = _input_with_sources(
        [
            ("seed-1", "evidence", "Flink CDC pipelines"),
            ("seed-2", "evidence", "FlinkCDC production jobs"),
        ]
    )
    extraction = LLMPRFExtraction(
        schema_version="llm-prf-v1",
        candidates=[
            _candidate("Flink CDC", payload.source_texts[0]),
            _candidate("FlinkCDC", payload.source_texts[1]),
        ],
    )

    grounding = ground_llm_prf_candidates(payload, extraction)
    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=[],
    )

    assert [item.canonical_expression for item in expressions] == ["Flink CDC"]
    assert expressions[0].term_family_id == "feedback.flinkcdc"
    assert expressions[0].positive_seed_support_count == 2


def test_llm_candidate_type_is_advisory_and_runtime_reclassifies_company() -> None:
    payload = _input_with_sources(
        [
            ("seed-1", "evidence", "阿里云实时计算经验"),
            ("seed-2", "evidence", "阿里云数据湖项目"),
        ]
    )
    extraction = LLMPRFExtraction(
        schema_version="llm-prf-v1",
        candidates=[
            _candidate("阿里云", payload.source_texts[0], candidate_term_type="product_or_platform"),
            _candidate("阿里云", payload.source_texts[1], candidate_term_type="product_or_platform"),
        ],
    )

    grounding = ground_llm_prf_candidates(payload, extraction)
    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities={"阿里云"},
        tried_term_family_ids=[],
    )

    assert expressions[0].candidate_term_type == "company_entity"
    assert "company_entity" in expressions[0].reject_reasons


def test_llm_prf_artifact_refs_are_centralized() -> None:
    refs = build_llm_prf_artifact_refs(round_no=2)

    assert refs.input_artifact_ref == "round.02.retrieval.llm_prf_input"
    assert refs.call_artifact_ref == "round.02.retrieval.llm_prf_call"
    assert refs.candidates_artifact_ref == "round.02.retrieval.llm_prf_candidates"
    assert refs.grounding_artifact_ref == "round.02.retrieval.llm_prf_grounding"
    assert refs.policy_decision_artifact_ref == "round.02.retrieval.prf_policy_decision"


def test_advisory_platform_label_without_known_company_is_still_ambiguous() -> None:
    payload = _input_with_sources(
        [
            ("seed-1", "evidence", "腾讯云实时数仓经验"),
            ("seed-2", "evidence", "腾讯云数据平台项目"),
        ]
    )
    extraction = LLMPRFExtraction(
        schema_version="llm-prf-v1",
        candidates=[
            _candidate("腾讯云", payload.source_texts[0], candidate_term_type="product_or_platform"),
            _candidate("腾讯云", payload.source_texts[1], candidate_term_type="product_or_platform"),
        ],
    )

    grounding = ground_llm_prf_candidates(payload, extraction)
    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=[],
    )

    assert expressions[0].candidate_term_type == "ambiguous_company_or_product_entity"
    assert "ambiguous_company_or_product_entity" in expressions[0].reject_reasons
```

Also include focused tests for:

- hash mismatch rejects with `source_hash_mismatch`;
- unknown source reference rejects with `source_reference_not_found`;
- strengths-only support produces `field_hits == {"strengths": 2}` but `positive_seed_support_count == 0`;
- positive support requires at least one `source_kind="grounding_eligible"` hit per supporting seed resume;
- negative support is computed by deterministic scan over `negative_source_texts`, not by LLM judgment;
- tried-family conflicts use `build_conservative_prf_family_id`.

- [ ] **Step 2: Run the pure tests and verify failure**

Run:

```bash
uv run pytest -q tests/test_llm_prf.py
```

Expected: FAIL because `candidate_feedback.llm_prf` does not exist.

- [ ] **Step 3: Add Pydantic contracts and constants**

Create `src/seektalent/candidate_feedback/llm_prf.py` with these constants:

```python
LLM_PRF_SCHEMA_VERSION = "llm-prf-v1"
LLM_PRF_EXTRACTOR_VERSION = "llm-prf-deepseek-v4-flash-v1"
GROUNDING_VALIDATOR_VERSION = "llm-prf-grounding-v1"
LLM_PRF_FAMILYING_VERSION = "llm-prf-conservative-surface-family-v1"
LLM_PRF_OUTPUT_RETRIES = 2
LLM_PRF_TOP_N_CANDIDATE_CAP = 16
```

Add small explicit models close to use:

```python
class LLMPRFSourceText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_field: SourceField
    source_kind: Literal["grounding_eligible", "hint_only"]
    source_text_index: int = Field(ge=0)
    source_text_raw: str
    source_text_hash: str


class LLMPRFInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = LLM_PRF_SCHEMA_VERSION
    round_no: int
    role_title: str
    role_summary: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    retrieval_query_terms: list[str] = Field(default_factory=list)
    existing_query_terms: list[str] = Field(default_factory=list)
    sent_query_terms: list[str] = Field(default_factory=list)
    tried_term_family_ids: list[str] = Field(default_factory=list)
    seed_resume_ids: list[str] = Field(default_factory=list)
    negative_resume_ids: list[str] = Field(default_factory=list)
    source_texts: list[LLMPRFSourceText] = Field(default_factory=list)
    negative_source_texts: list[LLMPRFSourceText] = Field(default_factory=list)


class LLMPRFSourceEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_field: SourceField
    source_text_index: int = Field(ge=0)
    source_text_hash: str


class LLMPRFCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str = Field(min_length=1)
    normalized_surface: str = Field(min_length=1)
    candidate_term_type: CandidateTermType = "unknown"
    source_evidence_refs: list[LLMPRFSourceEvidenceRef] = Field(default_factory=list)
    source_resume_ids: list[str] = Field(default_factory=list)
    linked_requirements: list[str] = Field(default_factory=list)
    rationale: str = ""
    risk_flags: list[str] = Field(default_factory=list)


class LLMPRFExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = LLM_PRF_SCHEMA_VERSION
    candidates: list[LLMPRFCandidate] = Field(default_factory=list, max_length=LLM_PRF_TOP_N_CANDIDATE_CAP)
```

Add grounding result models with candidate-level reject reasons:

```python
class LLMPRFGroundingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str
    normalized_surface: str
    advisory_candidate_term_type: CandidateTermType
    accepted: bool
    reject_reasons: list[str] = Field(default_factory=list)
    resume_id: str | None = None
    source_field: SourceField | None = None
    source_text_index: int | None = None
    source_text_hash: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    raw_surface: str | None = None


class LLMPRFGroundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = LLM_PRF_SCHEMA_VERSION
    grounding_validator_version: str = GROUNDING_VALIDATOR_VERSION
    familying_version: str = LLM_PRF_FAMILYING_VERSION
    records: list[LLMPRFGroundingRecord] = Field(default_factory=list)


class LLMPRFArtifactRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_artifact_ref: str
    call_artifact_ref: str
    candidates_artifact_ref: str
    grounding_artifact_ref: str
    policy_decision_artifact_ref: str


def build_llm_prf_artifact_refs(round_no: int) -> LLMPRFArtifactRefs:
    prefix = f"round.{round_no:02d}.retrieval"
    return LLMPRFArtifactRefs(
        input_artifact_ref=f"{prefix}.llm_prf_input",
        call_artifact_ref=f"{prefix}.llm_prf_call",
        candidates_artifact_ref=f"{prefix}.llm_prf_candidates",
        grounding_artifact_ref=f"{prefix}.llm_prf_grounding",
        policy_decision_artifact_ref=f"{prefix}.prf_policy_decision",
    )
```

- [ ] **Step 4: Build deterministic input and negative selection**

Implement:

```python
def select_llm_prf_negative_resumes(candidates: Iterable[ScoredCandidate], *, limit: int = 5) -> list[ScoredCandidate]:
    selected = [item for item in candidates if item.fit_bucket != "fit" or item.risk_score > 60]
    selected.sort(key=lambda item: (-item.risk_score, item.overall_score, item.resume_id))
    return selected[:limit]
```

Implement `build_llm_prf_input` using source fields in this fixed order:

```python
_SOURCE_FIELD_GETTERS = (
    ("evidence", lambda resume: list(resume.evidence)),
    ("matched_must_haves", lambda resume: list(resume.matched_must_haves)),
    ("matched_preferences", lambda resume: list(resume.matched_preferences)),
    ("strengths", lambda resume: list(resume.strengths)),
)
```

For each `LLMPRFSourceText`, set:

```python
source_kind = "hint_only" if source_field == "strengths" else "grounding_eligible"
```

Keep `strengths` in the prompt as proposal hints, but do not let `strengths` contribute to accepted positive seed support.

Return `None` when `len(seed_resumes) < 2`; the runtime task will convert that skip into `insufficient_prf_seed_support`.

- [ ] **Step 5: Implement grounding with raw offsets and NFKC map**

Implement exact matching first, then NFKC matching with a raw-offset map. Keep helper functions local and literal:

```python
def _normalized_with_offset_map(text: str) -> tuple[str, list[int]]:
    pieces: list[str] = []
    offsets: list[int] = []
    for raw_index, char in enumerate(text):
        normalized = unicodedata.normalize("NFKC", char)
        for normalized_char in normalized:
            pieces.append(normalized_char)
            offsets.append(raw_index)
    return "".join(pieces), offsets
```

Use deterministic tie-break by source field, `source_text_index`, and `start_char`. Reject unsafe substrings with a conservative boundary guard:

```python
def _unsafe_substring_match(raw_text: str, start: int, end: int, surface: str) -> bool:
    before = raw_text[start - 1] if start > 0 else ""
    after = raw_text[end] if end < len(raw_text) else ""
    if _ascii_word(surface) and (before.isascii() and before.isalnum() or after.isascii() and after.isalnum()):
        return True
    if surface in {"Java", "React", "阿里", "算法"} and (before or after):
        return True
    return False
```

Keep the guard conservative. It does not need a domain dictionary; it only prevents known substring false positives and alphanumeric token slicing.

- [ ] **Step 6: Implement conservative familying and expression conversion**

Implement LLM-only family ids without changing legacy `build_term_family_id`:

```python
def build_conservative_prf_family_id(surface: str) -> str:
    normalized = normalize_expression(surface)
    collapsed = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", normalized.casefold())
    if not collapsed:
        collapsed = "unknown"
    return f"feedback.{collapsed}"
```

Use this family id for:

- grouping grounded LLM candidates;
- positive support;
- negative support;
- current retrieval query conflicts;
- sent query conflicts;
- tried family conflicts.

Compute positive support at phrase-family level, not raw surface level. A seed resume counts only if that family has at least one grounded record from `source_kind="grounding_eligible"` for that resume. `strengths` hits may remain in `field_hits` for audit, but they do not make a candidate safe.

Compute negative support by deterministically scanning `payload.negative_source_texts` with the same raw/NFKC/family matching rules. Do not ask the LLM to determine negative support, and do not read negative support from advisory candidate metadata.

Build `FeedbackCandidateExpression` values with deterministic classifier output:

```python
classified = classify_feedback_expressions(
    [canonical_surface],
    known_company_entities=known_company_entities,
    known_product_platforms=set(),
)[0]
```

Do not trust `LLMPRFCandidate.candidate_term_type` as a safe classification. Preserve it only in `LLMPRFGroundingRecord.advisory_candidate_term_type`.

Add one conservative ambiguous company/product guard before PRF gate input construction. It is not a maintained vendor dictionary; it only rejects company-like product/platform phrases when the existing classifier cannot prove the phrase is a safe technical term. For example, `腾讯云` labeled by the LLM as `product_or_platform` must still become `ambiguous_company_or_product_entity` unless deterministic classification can safely override it.

Sort expressions by:

```python
(-score, -positive_seed_support_count, canonical_expression.casefold())
```

Use a simple score:

```python
score = float(positive_seed_support_count * 4 - negative_support_count * 4)
```

Avoid adding embedding familying to this path.

- [ ] **Step 7: Export stable names**

Update `src/seektalent/candidate_feedback/__init__.py` to export:

```python
LLMPRFExtraction
LLMPRFInput
LLMPRFSourceText
build_conservative_prf_family_id
build_llm_prf_artifact_refs
build_llm_prf_input
feedback_expressions_from_llm_grounding
ground_llm_prf_candidates
select_llm_prf_negative_resumes
```

Do not export internal offset-map helpers.

- [ ] **Step 8: Re-run pure tests**

Run:

```bash
uv run pytest -q tests/test_llm_prf.py
```

Expected: PASS.

- [ ] **Step 9: Commit this slice**

```bash
git add \
  src/seektalent/candidate_feedback/llm_prf.py \
  src/seektalent/candidate_feedback/__init__.py \
  tests/test_llm_prf.py
git commit -m "feat: add llm prf grounding contracts"
```

## Task 3: Add The LLM Extractor With Two Structured-Output Retries

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/tracing.py` only if needed by snapshot fields
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_prf.py`

- [ ] **Step 1: Write failing extractor tests**

Add tests to `tests/test_llm_prf.py`:

```python
def test_llm_prf_extractor_builds_agent_with_two_output_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def run(self, prompt: str):
            captured["prompt"] = prompt
            return SimpleNamespace(
                output=LLMPRFExtraction(candidates=[]),
                usage=lambda: None,
            )

    monkeypatch.setattr("seektalent.candidate_feedback.llm_prf.Agent", lambda **kwargs: FakeAgent(**kwargs))
    extractor = LLMPRFExtractor(make_settings(text_llm_api_key="test-key"), _prompt())

    asyncio.run(extractor.propose(_minimal_input()))

    assert captured["retries"] == 0
    assert captured["output_retries"] == 2
    assert captured["model_settings"]["temperature"] == 0
    assert captured["model_settings"]["max_tokens"] == 2048
    assert "json" in str(captured["prompt"]).casefold()


def test_llm_prf_output_spec_forbids_tool_and_native_output() -> None:
    settings = make_settings()
    config = resolve_stage_model_config(settings, stage="prf_probe_phrase_proposal")
    model = _fake_structured_model(supports_json_schema_output=True)

    output_spec = build_output_spec(config, model, LLMPRFExtraction)

    assert isinstance(output_spec, PromptedOutput)
    assert not isinstance(output_spec, NativeOutput)
    assert not isinstance(output_spec, ToolOutput)


def test_llm_prf_provider_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FakeAgent:
        async def run(self, prompt: str):
            nonlocal calls
            calls += 1
            raise RuntimeError("provider failed")

    monkeypatch.setattr("seektalent.candidate_feedback.llm_prf.Agent", lambda **kwargs: FakeAgent())
    extractor = LLMPRFExtractor(make_settings(text_llm_api_key="test-key"), _prompt())

    with pytest.raises(RuntimeError):
        asyncio.run(extractor.propose(_minimal_input()))

    assert calls == 1


def test_llm_prf_schema_retry_budget_is_two(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def run(self, prompt: str):
            return SimpleNamespace(output=LLMPRFExtraction(candidates=[]), usage=lambda: None)

    monkeypatch.setattr("seektalent.candidate_feedback.llm_prf.Agent", lambda **kwargs: FakeAgent(**kwargs))
    extractor = LLMPRFExtractor(make_settings(text_llm_api_key="test-key"), _prompt())

    asyncio.run(extractor.propose(_minimal_input()))

    assert captured["retries"] == 0
    assert captured["output_retries"] == 2


def test_llm_prf_schema_failures_retry_twice_then_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    model = SequenceTextModel(
        [
            "not json",
            '{"schema_version":"llm-prf-v1","candidates":"bad-shape"}',
            '{"schema_version":"llm-prf-v1","candidates":[]}',
        ]
    )
    monkeypatch.setattr("seektalent.candidate_feedback.llm_prf.build_model", lambda config: model)
    extractor = LLMPRFExtractor(make_settings(text_llm_api_key="test-key"), _prompt())

    extraction = asyncio.run(extractor.propose(_minimal_input()))

    assert extraction.candidates == []
    assert model.call_count == 3
    assert extractor.last_call_artifact["output_retries"] == 2


def test_llm_prf_extractor_records_provider_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    # Use a fake result with usage() and assert extractor.last_provider_usage is populated.
```

Import `PromptedOutput`, `NativeOutput`, and `ToolOutput` from `pydantic_ai.output` in the tests. The output spec test is mandatory: resolving `structured_output_mode == "prompted_json"` is not enough if the constructed `Agent` still receives a tool/native output spec.

`SequenceTextModel` is a local test double that returns plain text outputs in order and increments `call_count`. Use it only for the retry-boundary test so the production extractor stays direct.

Add a timeout/failure classification unit test for call-artifact shape:

```python
def test_llm_prf_failure_call_artifact_redacts_secrets() -> None:
    artifact = build_llm_prf_failure_call_artifact(
        settings=make_settings(text_llm_api_key="secret-key"),
        prompt=_prompt(),
        input_payload=_minimal_input(),
        failure_kind="timeout",
        error_message="timed out",
        latency_ms=1500,
    )

    dumped = json.dumps(artifact, ensure_ascii=False)
    assert "secret-key" not in dumped
    assert artifact["stage"] == "prf_probe_phrase_proposal"
    assert artifact["output_retries"] == 2
    assert artifact["validator_retry_count"] == 0
    assert artifact["failure_kind"] == "timeout"
```

- [ ] **Step 2: Run extractor tests and verify failure**

Run:

```bash
uv run pytest -q tests/test_llm_prf.py -k "extractor or output_spec or call_artifact"
```

Expected: FAIL because the extractor and call-artifact builder do not exist.

- [ ] **Step 3: Implement `LLMPRFExtractor`**

In `candidate_feedback/llm_prf.py`, add:

```python
class LLMPRFExtractor:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.last_provider_usage: ProviderUsageSnapshot | None = None
        self.last_call_artifact: dict[str, object] | None = None

    async def propose(self, payload: LLMPRFInput) -> LLMPRFExtraction:
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        started = perf_counter()
        config = resolve_stage_model_config(self.settings, stage="prf_probe_phrase_proposal")
        model = build_model(config)
        model_settings = dict(build_model_settings(config))
        model_settings["temperature"] = 0
        model_settings["max_tokens"] = self.settings.prf_probe_phrase_proposal_max_output_tokens
        result = await self._agent(config, model, model_settings).run(render_llm_prf_prompt(payload))
        extraction = result.output
        self.last_provider_usage = provider_usage_from_result(result)
        self.last_call_artifact = build_llm_prf_success_call_artifact(
            settings=self.settings,
            prompt=self.prompt,
            input_payload=payload,
            extraction=extraction,
            started_at=started_at,
            latency_ms=max(1, int((perf_counter() - started) * 1000)),
            provider_usage=self.last_provider_usage,
        )
        return extraction

    def _agent(self, config: ResolvedTextModelConfig, model: Model, model_settings: dict[str, object]) -> Agent[None, LLMPRFExtraction]:
        output_spec = build_output_spec(config, model, LLMPRFExtraction)
        if not isinstance(output_spec, PromptedOutput):
            raise ValueError("prf_probe_phrase_proposal must use PromptedOutput")
        return cast(
            Agent[None, LLMPRFExtraction],
            Agent(
                model=model,
                output_type=output_spec,
                system_prompt=self.prompt.content,
                model_settings=cast(ModelSettings, model_settings),
                retries=0,
                output_retries=LLM_PRF_OUTPUT_RETRIES,
            ),
        )
```

Keep network/provider errors uncaught in `propose`; the runtime task will catch them and fall back. This avoids hidden retry chains.

- [ ] **Step 4: Render compact JSON prompt**

Implement:

```python
def render_llm_prf_prompt(payload: LLMPRFInput) -> str:
    body = payload.model_dump(mode="json")
    return (
        "Return json only with schema_version and candidates. "
        "Each candidate must copy surface text from source_texts and cite source_evidence_refs. "
        "Do not invent source references. "
        f"{json.dumps(body, ensure_ascii=False, separators=(',', ':'))}"
    )
```

This supplements the system prompt and ensures the word `json` is present for JSON-output mode.

- [ ] **Step 5: Add success and failure call artifacts**

Implement small builders returning dicts consumed by `WorkflowRuntime._write_aux_llm_call_artifact`:

```python
def build_llm_prf_success_call_artifact(
    *,
    settings: AppSettings,
    prompt: LoadedPrompt,
    input_payload: LLMPRFInput,
    extraction: LLMPRFExtraction,
    started_at: str,
    latency_ms: int,
    provider_usage: ProviderUsageSnapshot | None,
) -> dict[str, object]:
    config = resolve_stage_model_config(settings, stage="prf_probe_phrase_proposal")
    user_prompt_text = render_llm_prf_prompt(input_payload)
    return {
        "stage": "prf_probe_phrase_proposal",
        "call_id": f"llm-prf-{input_payload.round_no:02d}",
        "model_id": config.model_id,
        "prompt_name": "prf_probe_phrase_proposal",
        "user_payload": input_payload.model_dump(mode="json"),
        "user_prompt_text": user_prompt_text,
        "started_at": started_at,
        "latency_ms": latency_ms,
        "status": "succeeded",
        "retries": 0,
        "output_retries": LLM_PRF_OUTPUT_RETRIES,
        "validator_retry_count": 0,
        "structured_output": extraction.model_dump(mode="json"),
        "provider_usage": provider_usage.model_dump(mode="json") if provider_usage is not None else None,
    }
```

For failure:

```python
"status": "failed",
"structured_output": None,
"failure_kind": failure_kind,
"validator_retry_count": 0,
"error_message": error_message,
```

Do not include headers, API keys, or raw provider request objects.

- [ ] **Step 6: Extend LLM call snapshot builder to preserve failure_kind**

Update `WorkflowRuntime._write_aux_llm_call_artifact` and `_build_llm_call_snapshot` so optional keys from the call artifact are copied into `LLMCallSnapshot`:

```python
failure_kind=call_artifact.get("failure_kind"),
provider_failure_kind=call_artifact.get("provider_failure_kind"),
provider_status_code=call_artifact.get("provider_status_code"),
provider_error_type=call_artifact.get("provider_error_type"),
provider_error_code=call_artifact.get("provider_error_code"),
provider_request_id=call_artifact.get("provider_request_id"),
validator_retry_count=int(call_artifact.get("validator_retry_count", 0)),
validator_retry_reasons=list(call_artifact.get("validator_retry_reasons", [])),
```

Add parameters with defaults to `_build_llm_call_snapshot` and pass them into `LLMCallSnapshot`. Existing callers continue to omit these values.

- [ ] **Step 7: Re-run extractor tests**

Run:

```bash
uv run pytest -q tests/test_llm_prf.py -k "extractor or output_spec or call_artifact"
```

Expected: PASS.

- [ ] **Step 8: Commit this slice**

```bash
git add \
  src/seektalent/candidate_feedback/llm_prf.py \
  src/seektalent/runtime/orchestrator.py \
  tests/test_llm_prf.py
git commit -m "feat: add llm prf extractor"
```

## Task 4: Extend Second-Lane And Replay Models With Proposal Metadata

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`

- [ ] **Step 1: Write failing metadata propagation tests**

Add to `tests/test_second_lane_runtime.py`:

```python
def test_second_lane_decision_carries_llm_prf_metadata_on_fallback() -> None:
    decision, lane = build_second_lane_decision(
        round_no=2,
        retrieval_plan=_retrieval_plan(query_terms=["python", "ranking"]),
        query_term_pool=[],
        sent_query_history=[],
        prf_decision=None,
        run_id="run-a",
        job_intent_fingerprint="job-1",
        source_plan_version="2",
        prf_probe_proposal_backend="llm_deepseek_v4_flash",
        llm_prf_failure_kind="llm_prf_timeout",
        llm_prf_input_artifact_ref="round.02.retrieval.llm_prf_input",
        llm_prf_call_artifact_ref="round.02.retrieval.llm_prf_call",
        llm_prf_candidates_artifact_ref="round.02.retrieval.llm_prf_candidates",
        llm_prf_grounding_artifact_ref="round.02.retrieval.llm_prf_grounding",
    )

    assert lane is not None
    assert lane.lane_type == "generic_explore"
    assert decision.prf_probe_proposal_backend == "llm_deepseek_v4_flash"
    assert decision.llm_prf_failure_kind == "llm_prf_timeout"
    assert decision.llm_prf_call_artifact_ref == "round.02.retrieval.llm_prf_call"
```

Add a replay snapshot test in `tests/test_evaluation.py` or the existing replay section:

```python
def test_replay_snapshot_accepts_llm_prf_optional_fields() -> None:
    snapshot = ReplaySnapshot.model_validate(
        {
            "run_id": "run-a",
            "round_no": 2,
            "retrieval_snapshot_id": "run-a:round:2",
            "provider_request": {},
            "provider_response_resume_ids": [],
            "provider_response_raw_rank": [],
            "dedupe_version": "v1",
            "scoring_model_version": "stub",
            "query_plan_version": "2",
            "prf_gate_version": "prf-policy-v1",
            "prf_probe_proposal_backend": "llm_deepseek_v4_flash",
            "llm_prf_model_id": "deepseek-v4-flash",
            "llm_prf_failure_kind": "llm_prf_timeout",
        }
    )

    assert snapshot.prf_probe_proposal_backend == "llm_deepseek_v4_flash"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/test_second_lane_runtime.py::test_second_lane_decision_carries_llm_prf_metadata_on_fallback \
  tests/test_evaluation.py::test_replay_snapshot_accepts_llm_prf_optional_fields
```

Expected: FAIL because the model fields and function parameters do not exist.

- [ ] **Step 3: Extend `SecondLaneDecision`**

Add optional fields to `SecondLaneDecision` in `src/seektalent/models.py`:

```python
prf_probe_proposal_backend: Literal["llm_deepseek_v4_flash", "legacy_regex", "sidecar_span"] | None = None
llm_prf_failure_kind: str | None = None
llm_prf_input_artifact_ref: str | None = None
llm_prf_call_artifact_ref: str | None = None
llm_prf_candidates_artifact_ref: str | None = None
llm_prf_grounding_artifact_ref: str | None = None
```

Keep existing `prf_v1_5_mode` and `shadow_prf_v1_5_artifact_ref` fields for explicit sidecar operation.

- [ ] **Step 4: Extend `ReplaySnapshot`**

Add optional replay fields:

```python
prf_probe_proposal_backend: str | None = None
llm_prf_extractor_version: str | None = None
llm_prf_grounding_validator_version: str | None = None
llm_prf_familying_version: str | None = None
llm_prf_model_id: str | None = None
llm_prf_protocol_family: str | None = None
llm_prf_endpoint_kind: str | None = None
llm_prf_endpoint_region: str | None = None
llm_prf_structured_output_mode: str | None = None
llm_prf_prompt_hash: str | None = None
llm_prf_output_retry_count: int | None = None
llm_prf_failure_kind: str | None = None
llm_prf_input_artifact_ref: str | None = None
llm_prf_call_artifact_ref: str | None = None
llm_prf_candidates_artifact_ref: str | None = None
llm_prf_grounding_artifact_ref: str | None = None
```

- [ ] **Step 5: Propagate fields through second-lane runtime**

Extend `build_second_lane_decision` parameters with defaults:

```python
prf_probe_proposal_backend: str | None = None
llm_prf_failure_kind: str | None = None
llm_prf_input_artifact_ref: str | None = None
llm_prf_call_artifact_ref: str | None = None
llm_prf_candidates_artifact_ref: str | None = None
llm_prf_grounding_artifact_ref: str | None = None
```

Pass the same fields into every `SecondLaneDecision` construction branch, including round-one no-fetch and `prf_probe` success.

- [ ] **Step 6: Propagate fields into replay snapshots**

In `build_replay_snapshot`, copy fields from `second_lane_decision` into the base snapshot:

```python
prf_probe_proposal_backend=second_lane_decision.prf_probe_proposal_backend,
llm_prf_failure_kind=second_lane_decision.llm_prf_failure_kind,
llm_prf_input_artifact_ref=second_lane_decision.llm_prf_input_artifact_ref,
llm_prf_call_artifact_ref=second_lane_decision.llm_prf_call_artifact_ref,
llm_prf_candidates_artifact_ref=second_lane_decision.llm_prf_candidates_artifact_ref,
llm_prf_grounding_artifact_ref=second_lane_decision.llm_prf_grounding_artifact_ref,
```

The runtime wiring task will populate model/protocol/version fields when LLM PRF actually runs.

- [ ] **Step 7: Re-run focused tests**

Run:

```bash
uv run pytest -q tests/test_second_lane_runtime.py tests/test_evaluation.py -k "llm_prf or second_lane_decision"
```

Expected: PASS.

- [ ] **Step 8: Commit this slice**

```bash
git add \
  src/seektalent/models.py \
  src/seektalent/runtime/second_lane_runtime.py \
  src/seektalent/runtime/runtime_diagnostics.py \
  tests/test_second_lane_runtime.py \
  tests/test_evaluation.py
git commit -m "feat: carry llm prf replay metadata"
```

## Task 5: Wire Active Backend Selection Into Runtime

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py`

- [ ] **Step 1: Write failing runtime tests for default LLM backend success**

In `tests/test_runtime_state_flow.py`, add a fake extractor:

```python
class FakeLLMPRFExtractor:
    def __init__(self, extraction: LLMPRFExtraction) -> None:
        self.extraction = extraction
        self.calls: list[LLMPRFInput] = []
        self.last_call_artifact: dict[str, object] | None = None

    async def propose(self, payload: LLMPRFInput) -> LLMPRFExtraction:
        self.calls.append(payload)
        self.last_call_artifact = {
            "stage": "prf_probe_phrase_proposal",
            "call_id": "llm-prf-02",
            "model_id": "deepseek-v4-flash",
            "prompt_name": "prf_probe_phrase_proposal",
            "user_payload": payload.model_dump(mode="json"),
            "user_prompt_text": "json " + payload.model_dump_json(),
            "started_at": "2026-05-04T00:00:00+08:00",
            "latency_ms": 1,
            "status": "succeeded",
            "retries": 0,
            "output_retries": 2,
            "structured_output": self.extraction.model_dump(mode="json"),
        }
        return self.extraction
```

Add:

```python
def test_default_llm_prf_backend_can_drive_prf_probe(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=2,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    runtime.llm_prf_extractor = FakeLLMPRFExtractor(
        LLMPRFExtraction(
            candidates=[
                LLMPRFCandidate(
                    surface="LangGraph",
                    normalized_surface="LangGraph",
                    candidate_term_type="technical_phrase",
                    source_evidence_refs=[
                        LLMPRFSourceEvidenceRef(
                            resume_id="seed-1",
                            source_field="evidence",
                            source_text_index=0,
                            source_text_hash=text_sha256("LangGraph"),
                        ),
                        LLMPRFSourceEvidenceRef(
                            resume_id="seed-2",
                            source_field="evidence",
                            source_text_index=0,
                            source_text_hash=text_sha256("LangGraph"),
                        ),
                    ],
                    source_resume_ids=["seed-1", "seed-2"],
                )
            ]
        )
    )
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    queries = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "cts_queries").read_text())
    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())

    assert [item["lane_type"] for item in queries] == ["exploit", "prf_probe"]
    assert "LangGraph" in queries[1]["query_terms"]
    assert queries[1]["promoted_prf_expression"] == "LangGraph"
    assert decision["prf_probe_proposal_backend"] == "llm_deepseek_v4_flash"
    assert decision["selected_lane_type"] == "prf_probe"
    assert decision["accepted_prf_expression"] == "LangGraph"
    assert decision["llm_prf_call_artifact_ref"] == "round.02.retrieval.llm_prf_call"
```

Patch `PRFProbeScorer` evidence in tests so seed evidence raw text is exactly `"LangGraph"` for both seeds. This keeps source hashes deterministic.

- [ ] **Step 2: Write failing runtime fallback tests**

Add five tests with these exact names:

- `test_llm_prf_backend_skips_model_when_seed_support_is_insufficient`
- `test_llm_prf_backend_falls_back_to_generic_on_timeout`
- `test_llm_prf_backend_falls_back_to_generic_on_provider_failure_without_legacy_retry`
- `test_llm_prf_backend_falls_back_to_generic_when_all_candidates_rejected`
- `test_llm_prf_backend_writes_input_candidates_grounding_and_policy_artifacts`

Assertions:

```python
assert decision["selected_lane_type"] == "generic_explore"
assert decision["llm_prf_failure_kind"] in {
    "insufficient_prf_seed_support",
    "llm_prf_timeout",
    "llm_prf_provider_failure",
    "no_safe_llm_prf_expression",
}
assert _round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_input").exists()
assert _round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_candidates").exists()
assert _round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_grounding").exists()
assert _round_artifact(tracer.run_dir, 2, "retrieval", "prf_policy_decision").exists()
```

For timeout, use an extractor:

```python
class SlowLLMPRFExtractor:
    async def propose(self, payload: LLMPRFInput) -> LLMPRFExtraction:
        await asyncio.sleep(10)
        return LLMPRFExtraction()
```

Run with `prf_probe_phrase_proposal_timeout_seconds=0.01`.

For provider failure, use an extractor that increments `calls` and raises a provider-like exception. Assert `calls == 1`, `selected_lane_type == "generic_explore"`, no sidecar shadow artifact was written, and no legacy regex proposal artifact or legacy accepted expression was used. LLM failure fallback is `generic_explore`, not "try another proposal backend".

- [ ] **Step 3: Make legacy and sidecar tests explicit**

Update existing tests that are specifically about regex or PRF v1.5 sidecar:

```python
settings = make_settings(
    runs_dir=str(tmp_path / "runs"),
    mock_cts=True,
    prf_probe_proposal_backend="legacy_regex",
)
```

For sidecar tests:

```python
settings = make_settings(
    runs_dir=str(tmp_path / "runs"),
    mock_cts=True,
    prf_probe_proposal_backend="sidecar_span",
    prf_v1_5_mode="shadow",
)
```

This preserves existing coverage while making the new default LLM backend unambiguous.

- [ ] **Step 4: Run focused runtime tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/test_runtime_state_flow.py -k "llm_prf or prf_probe or prf_shadow or prf_sidecar" \
  tests/test_candidate_feedback.py -k "proposal_runtime or sidecar or extract_feedback_candidate"
```

Expected: FAIL because runtime still uses legacy/sidecar selection directly.

- [ ] **Step 5: Instantiate the LLM PRF extractor**

In `WorkflowRuntime.__init__`, add `"prf_probe_phrase_proposal"` to the existing prompt-name list passed into `self.prompts.load_many`.

Then:

```python
self.llm_prf_extractor = LLMPRFExtractor(settings, prompt_map["prf_probe_phrase_proposal"])
```

- [ ] **Step 6: Add active backend helper**

Replace the current in-line PRF block in `_run_rounds` with an async helper:

```python
prf_result = await self._build_active_prf_policy_decision(
    run_state=run_state,
    retrieval_plan=retrieval_plan,
    tracer=tracer,
)
```

Use a small dataclass near runtime internals:

```python
@dataclass(frozen=True)
class ActivePRFDecisionResult:
    decision: PRFPolicyDecision
    prf_proposal: PRFProposalOutput | None = None
    proposal_backend: str = "legacy_regex"
    shadow_prf_v1_5_artifact_ref: str | None = None
    llm_prf_failure_kind: str | None = None
    llm_prf_input_artifact_ref: str | None = None
    llm_prf_call_artifact_ref: str | None = None
    llm_prf_candidates_artifact_ref: str | None = None
    llm_prf_grounding_artifact_ref: str | None = None
```

Backend semantics:

```python
if self.settings.prf_probe_proposal_backend == "legacy_regex":
    # Existing _build_prf_policy_decision and existing prf_policy_decision artifact.
elif self.settings.prf_probe_proposal_backend == "sidecar_span":
    # Existing _build_prf_v1_5_proposal_and_decision.
else:
    # New LLM path.
```

Do not run PRF v1.5 sidecar shadow artifacts when `prf_probe_proposal_backend == "llm_deepseek_v4_flash"`, even if the legacy `prf_v1_5_mode` setting is still `"shadow"`.

Do not run legacy regex fallback after an LLM backend failure. `legacy_regex` and `sidecar_span` are active only when explicitly selected by `prf_probe_proposal_backend`; otherwise every LLM skip/failure/rejection falls back to typed `generic_explore`.

- [ ] **Step 7: Implement LLM backend helper**

Add `_build_llm_prf_policy_decision`:

```python
async def _build_llm_prf_policy_decision(
    self,
    *,
    run_state: RunState,
    retrieval_plan: RoundRetrievalPlan,
    tracer: RunTracer,
) -> ActivePRFDecisionResult:
    seeds, _ = self._feedback_seed_sets(run_state=run_state)
    negatives = select_llm_prf_negative_resumes(run_state.scorecards_by_resume_id.values())
    payload = build_llm_prf_input(
        round_no=retrieval_plan.round_no,
        requirement_sheet=run_state.requirement_sheet,
        retrieval_plan=retrieval_plan,
        seed_resumes=seeds,
        negative_resumes=negatives,
        query_term_pool=run_state.retrieval_state.query_term_pool,
        sent_query_history=run_state.retrieval_state.sent_query_history,
    )
    refs = build_llm_prf_artifact_refs(round_no=retrieval_plan.round_no)
    if payload is None:
        decision = self._build_prf_failure_decision(
            run_state=run_state,
            retrieval_plan=retrieval_plan,
            seed_resume_ids=[],
            negative_resume_ids=[],
            reason="insufficient_prf_seed_support",
        )
        self._write_empty_llm_prf_artifacts(
            tracer=tracer,
            round_no=retrieval_plan.round_no,
            refs=refs,
            input_payload=None,
            failure_kind="insufficient_prf_seed_support",
            decision=decision,
        )
        return ActivePRFDecisionResult(
            decision=decision,
            proposal_backend="llm_deepseek_v4_flash",
            llm_prf_failure_kind="insufficient_prf_seed_support",
            llm_prf_input_artifact_ref=refs.input_artifact_ref,
            llm_prf_call_artifact_ref=refs.call_artifact_ref,
            llm_prf_candidates_artifact_ref=refs.candidates_artifact_ref,
            llm_prf_grounding_artifact_ref=refs.grounding_artifact_ref,
        )
```

For live calls:

```python
try:
    extraction = await asyncio.wait_for(
        self.llm_prf_extractor.propose(payload),
        timeout=self.settings.prf_probe_phrase_proposal_timeout_seconds,
    )
except TimeoutError:
    failure_kind = "llm_prf_timeout"
except Exception as exc:
    failure_info = classify_text_llm_failure(exc)
    failure_kind = _llm_prf_failure_kind(failure_info.failure_kind)
```

Use `classify_text_llm_failure` from `runtime_diagnostics.py` for provider failure details, but do not retry network/provider/timeouts.

After success:

```python
grounding = ground_llm_prf_candidates(payload, extraction)
expressions = feedback_expressions_from_llm_grounding(
    payload,
    grounding,
    known_company_entities=self._known_company_entities(run_state=run_state),
    tried_term_family_ids=tried_family_ids,
)
decision = build_prf_policy_decision(
    PRFGateInput(
        round_no=retrieval_plan.round_no,
        seed_resume_ids=payload.seed_resume_ids,
        seed_count=len(payload.seed_resume_ids),
        negative_resume_ids=payload.negative_resume_ids,
        candidate_expressions=expressions,
        candidate_expression_count=len(expressions),
        tried_term_family_ids=payload.tried_term_family_ids,
        tried_query_fingerprints=[
            record.query_fingerprint
            for record in run_state.retrieval_state.sent_query_history
            if record.query_fingerprint is not None
        ],
        min_seed_count=MIN_PRF_SEED_COUNT,
        max_negative_support_rate=MAX_NEGATIVE_SUPPORT_RATE,
        policy_version=PRF_POLICY_VERSION,
    )
)
if not decision.gate_passed:
    failure_kind = "no_safe_llm_prf_expression"
```

Grounding failures do not retry the model.

- [ ] **Step 8: Write LLM PRF artifacts through registry**

Write these logical artifacts in every LLM path, including skip/failure:

```python
tracer.write_json(refs.input_artifact_ref, input_payload)
tracer.write_json(refs.candidates_artifact_ref, candidates_payload)
tracer.write_json(refs.grounding_artifact_ref, grounding_payload)
tracer.write_json(refs.policy_decision_artifact_ref, decision.model_dump(mode="json"))
```

Write call artifact:

```python
self._write_aux_llm_call_artifact(
    tracer=tracer,
    path=refs.call_artifact_ref,
    call_artifact=call_artifact,
    input_artifact_refs=[refs.input_artifact_ref],
    output_artifact_refs=[
        refs.candidates_artifact_ref,
        refs.grounding_artifact_ref,
        refs.policy_decision_artifact_ref,
    ],
    round_no=round_no,
)
```

The `llm_prf_candidates` artifact preserves raw LLM candidates. The `llm_prf_grounding` artifact preserves accepted/rejected grounding records and advisory labels.

- [ ] **Step 9: Pass metadata into second-lane bundle**

Extend `_build_round_query_bundle` parameters and calls to include:

```python
prf_probe_proposal_backend=prf_result.proposal_backend,
llm_prf_failure_kind=prf_result.llm_prf_failure_kind,
llm_prf_input_artifact_ref=prf_result.llm_prf_input_artifact_ref,
llm_prf_call_artifact_ref=prf_result.llm_prf_call_artifact_ref,
llm_prf_candidates_artifact_ref=prf_result.llm_prf_candidates_artifact_ref,
llm_prf_grounding_artifact_ref=prf_result.llm_prf_grounding_artifact_ref,
```

Keep `prf_v1_5_mode` and `shadow_prf_v1_5_artifact_ref` only for explicit `sidecar_span`.

- [ ] **Step 10: Preserve exploit query construction before LLM wait**

Split exploit query construction from second-lane construction so the exploit logical query is built before waiting for LLM PRF:

```python
exploit_query_state = self._build_exploit_query_state(
    round_no=round_no,
    retrieval_plan=retrieval_plan,
    run_id=tracer.run_id,
    job_intent_fingerprint=job_intent_fingerprint,
    source_plan_version=str(retrieval_plan.plan_version),
)
prf_result = await self._build_active_prf_policy_decision(
    run_state=run_state,
    retrieval_plan=retrieval_plan,
    tracer=tracer,
)
second_lane_decision, second_lane_query_state = self._build_second_lane_query_state(
    round_no=round_no,
    retrieval_plan=retrieval_plan,
    query_term_pool=run_state.retrieval_state.query_term_pool,
    sent_query_history=run_state.retrieval_state.sent_query_history,
    prf_result=prf_result,
    run_id=tracer.run_id,
    job_intent_fingerprint=job_intent_fingerprint,
    source_plan_version=str(retrieval_plan.plan_version),
)
query_states = [exploit_query_state, *([second_lane_query_state] if second_lane_query_state else [])]
```

Do not start CTS execution before second-lane selection in this rollout. The timeout caps the only LLM delay.

- [ ] **Step 11: Re-run focused runtime tests**

Run:

```bash
uv run pytest -q \
  tests/test_runtime_state_flow.py -k "llm_prf or prf_probe or prf_shadow or prf_sidecar" \
  tests/test_second_lane_runtime.py \
  tests/test_candidate_feedback.py -k "proposal_runtime or sidecar or extract_feedback_candidate"
```

Expected: PASS.

- [ ] **Step 12: Commit this slice**

```bash
git add \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/runtime/runtime_diagnostics.py \
  tests/test_runtime_state_flow.py \
  tests/test_candidate_feedback.py
git commit -m "feat: route prf probe through llm proposal"
```

## Task 6: Protect Low-Quality Rescue Candidate Feedback

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_execution_runtime.py` only if a test reveals accidental coupling

- [ ] **Step 1: Write a rescue isolation test**

Add to `tests/test_runtime_state_flow.py` near existing rescue tests:

```python
def test_low_quality_rescue_candidate_feedback_does_not_call_llm_prf(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=2,
        max_rounds=2,
        candidate_feedback_enabled=True,
        prf_probe_proposal_backend="legacy_regex",
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopAfterSecondRoundController(), resume_scorer=LowQualityScorer())

    class ExplodingLLMPRFExtractor:
        async def propose(self, payload: LLMPRFInput) -> LLMPRFExtraction:
            raise AssertionError("low-quality rescue must not call llm_prf")

    runtime.llm_prf_extractor = ExplodingLLMPRFExtractor()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )
    assert rescue_decision["selected_lane"] in {"candidate_feedback", "anchor_only", "allow_stop"}
```

The test sets the typed-second-lane backend to `legacy_regex` so it only exercises low-quality rescue.

- [ ] **Step 2: Run rescue-focused tests and verify behavior**

Run:

```bash
uv run pytest -q tests/test_runtime_state_flow.py -k "candidate_feedback or rescue"
```

Expected: PASS after Task 5 if rescue stayed isolated. If it fails because rescue imports or calls `llm_prf.py`, remove that coupling and keep rescue on its existing deterministic path.

- [ ] **Step 3: Keep dormant `CandidateFeedbackModelSteps` separate**

If any tests around `CandidateFeedbackModelSteps` need updates, keep them stage-specific:

```python
resolve_stage_model_config(settings, stage="candidate_feedback")
```

Do not point dormant model ranking to `stage="prf_probe_phrase_proposal"`, and do not reuse `CandidateFeedbackModelRanking` for LLM PRF proposal.

- [ ] **Step 4: Commit this slice if code changed**

If this task only added tests:

```bash
git add tests/test_runtime_state_flow.py tests/test_candidate_feedback.py
git commit -m "test: guard candidate feedback rescue isolation"
```

If code needed a rescue decoupling fix, include that file:

```bash
git add \
  src/seektalent/runtime/rescue_execution_runtime.py \
  tests/test_runtime_state_flow.py \
  tests/test_candidate_feedback.py
git commit -m "fix: keep rescue candidate feedback separate from llm prf"
```

## Task 7: Add Run Config, Docs, And Replay Output Coverage

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/configuration.md`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`

- [ ] **Step 1: Write failing audit/docs-adjacent tests**

Update the run-config assertion in `tests/test_runtime_audit.py`:

```python
assert run_config["settings"]["prf_probe_proposal_backend"] == "llm_deepseek_v4_flash"
assert run_config["settings"]["prf_probe_phrase_proposal_model_id"] == "deepseek-v4-flash"
assert run_config["settings"]["prf_probe_phrase_proposal_reasoning_effort"] == "off"
assert run_config["settings"]["prf_probe_phrase_proposal_timeout_seconds"] == 3.0
assert run_config["settings"]["prf_probe_phrase_proposal_max_output_tokens"] == 2048
```

Add a replay metadata assertion to a runtime replay test:

```python
snapshot = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "replay_snapshot").read_text())
assert snapshot["prf_probe_proposal_backend"] == "llm_deepseek_v4_flash"
assert snapshot["llm_prf_input_artifact_ref"] == "round.02.retrieval.llm_prf_input"
assert snapshot["llm_prf_grounding_validator_version"] == "llm-prf-grounding-v1"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest -q tests/test_runtime_audit.py tests/test_runtime_state_flow.py -k "run_config or replay_snapshot or llm_prf"
```

Expected: FAIL until public run config and replay metadata are filled.

- [ ] **Step 3: Serialize settings in public run config**

Add to `_build_public_run_config()`:

```python
"prf_probe_proposal_backend": self.settings.prf_probe_proposal_backend,
"prf_probe_phrase_proposal_model_id": self.settings.prf_probe_phrase_proposal_model_id,
"prf_probe_phrase_proposal_reasoning_effort": self.settings.prf_probe_phrase_proposal_reasoning_effort,
"prf_probe_phrase_proposal_timeout_seconds": self.settings.prf_probe_phrase_proposal_timeout_seconds,
"prf_probe_phrase_proposal_max_output_tokens": self.settings.prf_probe_phrase_proposal_max_output_tokens,
```

- [ ] **Step 4: Fill replay version fields**

When writing replay snapshots for an LLM PRF round, include:

```python
"llm_prf_extractor_version": LLM_PRF_EXTRACTOR_VERSION,
"llm_prf_grounding_validator_version": GROUNDING_VALIDATOR_VERSION,
"llm_prf_familying_version": LLM_PRF_FAMILYING_VERSION,
"llm_prf_model_id": stage_config.model_id,
"llm_prf_protocol_family": stage_config.protocol_family,
"llm_prf_endpoint_kind": stage_config.endpoint_kind,
"llm_prf_endpoint_region": stage_config.endpoint_region,
"llm_prf_structured_output_mode": stage_config.structured_output_mode,
"llm_prf_prompt_hash": self.prompts.load("prf_probe_phrase_proposal").sha256,
"llm_prf_output_retry_count": 2,
```

Keep fields optional for legacy and sidecar backends.

- [ ] **Step 5: Update docs**

In `docs/configuration.md`, add PRF probe settings to the PRF/rescue section:

```markdown
| `SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND` | `llm_deepseek_v4_flash` | Active proposal backend for the typed second-lane `prf_probe`. Supported values: `llm_deepseek_v4_flash`, `legacy_regex`, `sidecar_span`. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID` | `deepseek-v4-flash` | Dedicated text-LLM stage for PRF phrase proposal. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT` | `off` | Reasoning effort for PRF phrase proposal. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS` | `3.0` | Hard timeout before falling back to `generic_explore`. Local smoke tests may override this lower. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS` | `2048` | Output cap for prompted JSON extraction; prevents common truncation false failures. |
```

State explicitly:

```markdown
This is separate from the low-quality rescue `candidate_feedback` lane. The rescue lane does not call the LLM PRF extractor.
```

In `docs/outputs.md`, add the four LLM PRF artifact logical names and the meaning of `prf_policy_decision` as the final acceptance artifact.

- [ ] **Step 6: Re-run docs/audit tests**

Run:

```bash
uv run pytest -q tests/test_runtime_audit.py tests/test_evaluation.py tests/test_runtime_state_flow.py -k "run_config or replay_snapshot or llm_prf"
```

Expected: PASS.

- [ ] **Step 7: Commit this slice**

```bash
git add \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/runtime/runtime_diagnostics.py \
  docs/configuration.md \
  docs/outputs.md \
  tests/test_runtime_audit.py \
  tests/test_evaluation.py \
  tests/test_runtime_state_flow.py
git commit -m "docs: document llm prf runtime artifacts"
```

## Task 8: Add Non-CI Live Bakeoff Harness

**Files:**
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/llm_prf_bakeoff.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_prf_bakeoff.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/fixtures/llm_prf_bakeoff/cases.jsonl`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/configuration.md`

- [ ] **Step 1: Add smoke bakeoff fixture cases**

Create `tests/fixtures/llm_prf_bakeoff/cases.jsonl` with three sanitized smoke cases:

```jsonl
{"case_id":"english_streaming","language_bucket":"english","role_title":"Streaming Data Engineer","must_have_capabilities":["streaming data pipelines"],"seed_texts":["Built Flink CDC jobs for realtime ingestion.","Maintained Flink CDC pipelines in production."],"expected_query_material":["Flink CDC"],"blocked_terms":["Example Co"]}
{"case_id":"chinese_algorithm","language_bucket":"chinese","role_title":"推荐算法工程师","must_have_capabilities":["推荐系统"],"seed_texts":["负责召回排序链路中的向量检索优化。","有向量检索和排序模型上线经验。"],"expected_query_material":["向量检索"],"blocked_terms":["推荐算法工程师"]}
{"case_id":"mixed_llm_ops","language_bucket":"mixed","role_title":"AI Platform Engineer","must_have_capabilities":["LLM application platform"],"seed_texts":["Built LangGraph workflow orchestration for agents.","维护 LangGraph agent runtime and evaluation."],"expected_query_material":["LangGraph"],"blocked_terms":["agent runtime"]}
```

- [ ] **Step 2: Write failing bakeoff tests**

Add `tests/test_llm_prf_bakeoff.py`:

```python
def test_bakeoff_metrics_mark_blocker_for_accepted_non_extractive_phrase() -> None:
    result = LLMPRFBakeoffResult(
        case_id="case-1",
        language_bucket="english",
        accepted_expression="Invented Phrase",
        accepted_grounded=False,
        accepted_reject_reasons=[],
        fallback_reason=None,
        structured_output_failed=False,
        latency_ms=1200,
    )

    metrics = score_llm_prf_bakeoff_results([result])

    assert metrics["blocker_count"] == 1
    assert metrics["non_extractive_accepted_count"] == 1


def test_bakeoff_metrics_count_no_safe_expression_as_fallback_not_blocker() -> None:
    result = LLMPRFBakeoffResult(
        case_id="case-1",
        language_bucket="mixed",
        accepted_expression=None,
        accepted_grounded=False,
        accepted_reject_reasons=[],
        fallback_reason="no_safe_llm_prf_expression",
        structured_output_failed=False,
        latency_ms=900,
    )

    metrics = score_llm_prf_bakeoff_results([result])

    assert metrics["generic_fallback_rate"] == 1.0
    assert metrics["blocker_count"] == 0
```

- [ ] **Step 3: Implement bakeoff models and metrics**

In `llm_prf_bakeoff.py`, add small Pydantic models:

```python
class LLMPRFBakeoffCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    language_bucket: Literal["english", "chinese", "mixed"]
    role_title: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    seed_texts: list[str] = Field(default_factory=list)
    expected_query_material: list[str] = Field(default_factory=list)
    blocked_terms: list[str] = Field(default_factory=list)


class LLMPRFBakeoffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    language_bucket: Literal["english", "chinese", "mixed"]
    accepted_expression: str | None = None
    accepted_grounded: bool = False
    accepted_reject_reasons: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    structured_output_failed: bool = False
    latency_ms: int | None = None
```

Implement metrics with blocker conditions:

```python
def score_llm_prf_bakeoff_results(results: list[LLMPRFBakeoffResult]) -> dict[str, object]:
    accepted = [item for item in results if item.accepted_expression is not None]
    blockers = [
        item
        for item in accepted
        if not item.accepted_grounded
        or any(reason in item.accepted_reject_reasons for reason in {
            "company_entity",
            "company_entity_rejected",
            "generic_or_filter_like",
            "derived_summary_only_grounding",
            "insufficient_seed_support",
        })
    ]
    return {
        "case_count": len(results),
        "accepted_count": len(accepted),
        "blocker_count": len(blockers),
        "non_extractive_accepted_count": sum(1 for item in accepted if not item.accepted_grounded),
        "structured_output_failure_rate": _rate(sum(1 for item in results if item.structured_output_failed), len(results)),
        "generic_fallback_rate": _rate(sum(1 for item in results if item.fallback_reason), len(results)),
        "latency_ms_p50": _percentile([item.latency_ms for item in results if item.latency_ms is not None], 0.50),
        "latency_ms_p95": _percentile([item.latency_ms for item in results if item.latency_ms is not None], 0.95),
        "language_bucket_counts": dict(Counter(item.language_bucket for item in results)),
    }
```

Implement `_percentile(values, percentile)` as a small deterministic helper that returns `None` for an empty input.

- [ ] **Step 4: Implement explicit live runner**

Add a `main()` that is not wired into normal CI:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if not args.live:
        raise SystemExit("--live is required so real DeepSeek calls are never accidental")
    settings = AppSettings(_env_file=args.env_file)
    settings = settings.with_overrides(prf_probe_proposal_backend="llm_deepseek_v4_flash")
    cases = load_bakeoff_cases(args.cases)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = run_live_bakeoff(settings=settings, cases=cases, output_dir=args.output_dir)
    metrics = score_llm_prf_bakeoff_results(results)
    (args.output_dir / "llm_prf_bakeoff_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0
```

It should write:

- `llm_prf_bakeoff_results.jsonl`
- `llm_prf_bakeoff_metrics.json`
- per-case raw proposal/grounding/policy payloads under the output directory

The command the operator runs manually:

```bash
uv run python -m seektalent.candidate_feedback.llm_prf_bakeoff \
  --live \
  --cases tests/fixtures/llm_prf_bakeoff/cases.jsonl \
  --output-dir artifacts/manual/llm-prf-bakeoff
```

- [ ] **Step 5: Run offline bakeoff tests**

Run:

```bash
uv run pytest -q tests/test_llm_prf_bakeoff.py
```

Expected: PASS. Do not run live model calls in CI.

- [ ] **Step 6: Document manual gate**

In `docs/configuration.md`, add:

```markdown
Before using `llm_deepseek_v4_flash` as production-ready benchmark behavior, run the live LLM PRF bakeoff manually and require `blocker_count == 0`.

The checked-in three-case fixture is only a harness smoke test. Production promotion requires an external private sanitized slice, preferably at least 30 cases across English, Chinese, and mixed-language roles, and should inspect `generic_fallback_rate`, `structured_output_failure_rate`, and p50/p95 latency in addition to `blocker_count`.
```

- [ ] **Step 7: Commit this slice**

```bash
git add \
  src/seektalent/candidate_feedback/llm_prf_bakeoff.py \
  tests/test_llm_prf_bakeoff.py \
  tests/fixtures/llm_prf_bakeoff/cases.jsonl \
  docs/configuration.md
git commit -m "feat: add llm prf bakeoff harness"
```

## Task 9: Full Regression Slice And Cleanup

**Files:**
- Modify only files needed to fix failures from the commands below.

- [ ] **Step 1: Run the focused PRF/runtime regression suite**

Run:

```bash
uv run pytest -q \
  tests/test_llm_prf.py \
  tests/test_llm_prf_bakeoff.py \
  tests/test_llm_provider_config.py \
  tests/test_artifact_store.py \
  tests/test_second_lane_runtime.py \
  tests/test_candidate_feedback.py \
  tests/test_candidate_feedback_familying.py \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_audit.py \
  tests/test_evaluation.py
```

Expected: PASS.

- [ ] **Step 2: Run the repository's common fast test slice**

Run:

```bash
uv run pytest -q
```

Expected: PASS. If this is too slow in the local environment, run the largest available relevant slice and record the skipped reason in the final implementation handoff.

- [ ] **Step 3: Check for rescue or sidecar boundary regressions**

Run:

```bash
uv run pytest -q \
  tests/test_runtime_state_flow.py -k "rescue or candidate_feedback or prf_sidecar or prf_shadow" \
  tests/test_artifact_path_contract.py
```

Expected: PASS. This specifically guards against:

- LLM PRF leaking into low-quality rescue;
- sidecar shadow artifacts being written in default LLM backend mode;
- artifact resolver paths falling back to unregistered legacy names.

- [ ] **Step 4: Inspect the diff for accidental broad rewrites**

Run:

```bash
git diff --stat
git diff -- src/seektalent/runtime/orchestrator.py src/seektalent/candidate_feedback/llm_prf.py
```

Expected:

- `llm_prf.py` contains the new proposal boundary.
- `orchestrator.py` has routing changes only around PRF proposal selection, artifact writing, preflight/run-config metadata, and call snapshots.
- `rescue_execution_runtime.py` is unchanged unless Task 6 found a real coupling bug.

- [ ] **Step 5: Final commit**

If any final cleanup changes were made, stage the known PRF implementation surface:

```bash
git add \
  src/seektalent/candidate_feedback/llm_prf.py \
  src/seektalent/candidate_feedback/llm_prf_bakeoff.py \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/runtime/second_lane_runtime.py \
  src/seektalent/runtime/runtime_diagnostics.py \
  src/seektalent/models.py \
  tests/test_llm_prf.py \
  tests/test_llm_prf_bakeoff.py \
  tests/test_runtime_state_flow.py \
  tests/test_second_lane_runtime.py \
  tests/test_runtime_audit.py \
  tests/test_evaluation.py
git commit -m "test: cover llm prf runtime boundaries"
```

## Implementation Notes

- The current `candidate_feedback_model_id` and `CandidateFeedbackModelSteps` path are not the LLM PRF path. The LLM PRF stage is `prf_probe_phrase_proposal`.
- `prf_v1_5_mode` remains meaningful only for explicit `sidecar_span` operation. The default LLM backend must not run sidecar shadow artifacts.
- The LLM stage must use real Prompted JSON: `PromptedOutput(LLMPRFExtraction)` or an equivalent explicit plain-JSON parse path. `ToolOutput` and `NativeOutput` are forbidden for `prf_probe_phrase_proposal`.
- The LLM stage must use `output_retries=2` only for structured-output parse/schema failures. Do not add network/provider retry chains.
- Set `temperature=0` and `max_tokens=settings.prf_probe_phrase_proposal_max_output_tokens` on the proposal call.
- For missing API key, provider errors, timeouts, and unsupported capability errors, record failure metadata and fall back to `generic_explore` without trying legacy regex or sidecar as a backup backend.
- Grounding failures are candidate failures, not model-output retries.
- Strengths are hint-only source texts; accepted support requires grounded non-strengths evidence from at least two distinct seed resumes.
- Negative support is a deterministic scan over negative source texts, not an LLM judgment.
- Keep the deterministic PRF gate authoritative. If a model says `candidate_term_type="product_or_platform"` for a company-like term, deterministic classification still decides.
- Avoid domain dictionaries. The only deterministic rules added here are generic structural guards: source grounding, raw offsets, normalized offset maps, conservative substring rejection, filter-like classifier reuse, and phrase-family conflict checks.

## Success Criteria

This implementation is complete when:

1. Default `prf_probe_proposal_backend` is `llm_deepseek_v4_flash`.
2. `prf_probe_phrase_proposal` resolves to `deepseek-v4-flash`, reasoning off, prompted JSON, `max_tokens=2048`, and `output_retries=2`.
3. Round 2+ with enough high-quality seeds uses LLM proposal before deterministic grounding and PRF gate.
4. Accepted PRF terms are grounded to raw seed evidence offsets and supported by at least two seed resumes with non-strengths evidence.
5. Empty, invalid, truncated, schema-invalid, timeout, provider-failed, ungrounded, and unsafe outputs fall back to `generic_explore` with deterministic reasons and no provider/network retry.
6. LLM advisory labels never bypass deterministic classification or PRF gate rejection.
7. Conservative family ids drive LLM PRF support, negative support, and conflict checks.
8. Existing 70/30 fetch allocation is unchanged.
9. Low-quality rescue `candidate_feedback` does not call `llm_prf.py`.
10. Sidecar PRF remains available only through explicit `prf_probe_proposal_backend="sidecar_span"`.
11. LLM PRF artifacts are resolver-addressed and include version/model/protocol/prompt/retry/failure metadata.
12. CI fake harness covers English, Chinese, and mixed-language fixture behavior.
13. Live bakeoff harness exists, checked-in cases are smoke only, and production promotion uses a larger private sanitized slice with `blocker_count == 0` plus fallback and latency review.
