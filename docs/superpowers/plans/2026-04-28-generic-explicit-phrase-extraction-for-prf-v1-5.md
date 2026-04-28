# Generic Explicit Phrase Extraction For PRF v1.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current regex-heavy PRF phrase proposal step with a replayable, extractive, model-assisted phrase proposal pipeline while preserving the existing typed second-lane, PRF gate, artifact taxonomy, and retrieval flywheel comparison boundaries.

**Architecture:** Keep the current `prf_probe if safe else generic_explore` runtime shape and deterministic `PRFPolicyDecision`. Add a bounded proposal submodule that emits exact-offset candidate spans, guarded phrase families, and versioned proposal artifacts before the existing gate runs. Roll out in three product-safe stages: offline bakeoff, runtime shadow artifacts, then mainline switch.

**Tech Stack:** Python 3.12, Pydantic models, existing artifact resolver/store, pytest, local Hugging Face model dependencies for GLiNER2 and multilingual embeddings, existing retrieval flywheel runtime and evaluation artifacts.

---

## File Map

### New modules

- Create: `src/seektalent/candidate_feedback/span_models.py`
  - Pydantic models for candidate spans, phrase families, proposal metadata, and bakeoff metrics.
- Create: `src/seektalent/candidate_feedback/span_extractors.py`
  - Exact-span proposal interfaces, current-regex adapter, and placeholder model-backed extractor entrypoints.
- Create: `src/seektalent/candidate_feedback/familying.py`
  - Normalization, familying guardrails, confusable checks, and surface-merge helpers.
- Create: `src/seektalent/candidate_feedback/proposal_runtime.py`
  - Proposal orchestration from seed resumes to artifact-ready span and family outputs.
- Create: `src/seektalent/candidate_feedback/bakeoff.py`
  - Offline extractor comparison helpers and rubric-scored metrics.
- Create: `tests/test_candidate_feedback_span_models.py`
- Create: `tests/test_candidate_feedback_familying.py`
- Create: `tests/test_candidate_feedback_bakeoff.py`

### Existing modules to modify

- Modify: `src/seektalent/candidate_feedback/models.py`
  - Extend PRF-side models for versioned proposal metadata and artifact refs.
- Modify: `src/seektalent/candidate_feedback/policy.py`
  - Accept proposal metadata inputs, artifact refs, and stricter reject reasons without changing the gate role.
- Modify: `src/seektalent/candidate_feedback/extraction.py`
  - Keep seed selection, retire direct regex-only proposal logic behind adapter seams, and preserve legacy fallback extractor.
- Modify: `src/seektalent/runtime/orchestrator.py`
  - Shadow proposal artifacts first, then optional mainline switch hook later.
- Modify: `src/seektalent/runtime/rescue_execution_runtime.py`
  - Keep rescue-side expression evidence aligned with new proposal models where needed.
- Modify: `src/seektalent/models.py`
  - Extend replay and second-lane records with proposal metadata refs where they belong.
- Modify: `src/seektalent/evaluation.py`
  - Read proposal artifacts and expose proposal-aware replay export fields.
- Modify: `tests/test_candidate_feedback.py`
  - Preserve legacy behavior where explicitly intended and add integration coverage.
- Modify: `tests/test_runtime_state_flow.py`
  - Verify new logical artifacts and proposal metadata in round artifacts.
- Modify: `tests/test_evaluation.py`
  - Verify replay export includes proposal versioning and refs.

### Existing docs likely to update late in the work

- Modify: `docs/outputs.md`
  - Add new logical artifacts for PRF v1.5 once runtime shape is settled.

## Task 1: Define Proposal Data Contract

**Files:**
- Create: `src/seektalent/candidate_feedback/span_models.py`
- Modify: `src/seektalent/candidate_feedback/models.py`
- Test: `tests/test_candidate_feedback_span_models.py`

- [ ] **Step 1: Write the failing span-model tests**

```python
from seektalent.candidate_feedback.span_models import (
    CandidateSpan,
    PhraseFamily,
    ProposalMetadata,
)


def test_candidate_span_requires_exact_source_coordinates():
    span = CandidateSpan(
        source_resume_id="resume-1",
        source_field="evidence",
        start_char=4,
        end_char=12,
        raw_surface="Flink CDC",
        normalized_surface="Flink CDC",
        model_label="technical_phrase",
        model_score=0.91,
        extractor_schema_version="gliner2-schema-v1",
    )
    assert span.raw_surface == "Flink CDC"
    assert span.start_char == 4
    assert span.end_char == 12


def test_phrase_family_carries_support_and_guard_metadata():
    family = PhraseFamily(
        family_id="feedback.flink-cdc",
        canonical_surface="Flink CDC",
        candidate_term_type="technical_phrase",
        surfaces=["Flink CDC", "flink-cdc"],
        source_span_ids=["span-1", "span-2"],
        positive_seed_support_count=2,
        negative_support_count=0,
        familying_rule="separator_variant",
        familying_score=1.0,
        reject_reasons=[],
    )
    assert family.family_id == "feedback.flink-cdc"
    assert family.familying_rule == "separator_variant"


def test_proposal_metadata_records_model_and_familying_versions():
    metadata = ProposalMetadata(
        extractor_version="prf-span-v1",
        span_model_name="fastino/gliner2-multi-v1",
        span_model_revision="rev-span",
        tokenizer_revision="rev-tokenizer",
        schema_version="gliner2-schema-v1",
        schema_payload={"labels": ["skill", "technical_phrase"]},
        thresholds_version="thresholds-v1",
        embedding_model_name="Alibaba-NLP/gte-multilingual-base",
        embedding_model_revision="rev-embed",
        familying_version="familying-v1",
        familying_thresholds={"embedding_similarity": 0.92},
        runtime_mode="offline",
        top_n_candidate_cap=32,
    )
    assert metadata.span_model_name == "fastino/gliner2-multi-v1"
    assert metadata.familying_thresholds["embedding_similarity"] == 0.92
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_candidate_feedback_span_models.py`

Expected: FAIL with `ModuleNotFoundError` or missing model definitions.

- [ ] **Step 3: Implement minimal span models**

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CandidateTermType = Literal[
    "skill",
    "tool_or_framework",
    "product_or_platform",
    "technical_phrase",
    "responsibility_phrase",
    "company_entity",
    "unknown",
]


class CandidateSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_id: str | None = None
    source_resume_id: str
    source_field: str
    start_char: int
    end_char: int
    raw_surface: str
    normalized_surface: str
    model_label: str
    model_score: float
    extractor_schema_version: str
    reject_reasons: list[str] = Field(default_factory=list)


class PhraseFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: str
    canonical_surface: str
    candidate_term_type: CandidateTermType
    surfaces: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    positive_seed_support_count: int = 0
    negative_support_count: int = 0
    familying_rule: str
    familying_score: float
    reject_reasons: list[str] = Field(default_factory=list)


class ProposalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extractor_version: str
    span_model_name: str
    span_model_revision: str
    tokenizer_revision: str
    schema_version: str
    schema_payload: dict[str, object]
    thresholds_version: str
    embedding_model_name: str
    embedding_model_revision: str
    familying_version: str
    familying_thresholds: dict[str, object]
    runtime_mode: str
    top_n_candidate_cap: int
```

- [ ] **Step 4: Extend PRF models with proposal artifact refs and version vectors**

```python
class PRFProposalArtifactRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_span_artifact_ref: str
    expression_family_artifact_ref: str
    policy_decision_artifact_ref: str


class PRFProposalVersionVector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_extractor_version: str
    span_model_name: str
    span_model_revision: str
    span_tokenizer_revision: str
    span_schema_version: str
    span_thresholds_version: str
    embedding_model_name: str
    embedding_model_revision: str
    familying_version: str
    familying_thresholds: dict[str, object] = Field(default_factory=dict)
    runtime_mode: str
    top_n_candidate_cap: int
```

Add these to the existing PRF-side models in `src/seektalent/candidate_feedback/models.py` rather than creating parallel ad hoc dicts later.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_candidate_feedback_span_models.py`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/candidate_feedback/span_models.py \
  src/seektalent/candidate_feedback/models.py \
  tests/test_candidate_feedback_span_models.py
git commit -m "feat: add prf span proposal contract models"
```

## Task 2: Build Exact-Offset Validation And Legacy Extractor Adapter

**Files:**
- Create: `src/seektalent/candidate_feedback/span_extractors.py`
- Modify: `src/seektalent/candidate_feedback/extraction.py`
- Test: `tests/test_candidate_feedback_span_models.py`
- Test: `tests/test_candidate_feedback.py`

- [ ] **Step 1: Write the failing extractive-validation tests**

```python
from seektalent.candidate_feedback.span_extractors import (
    normalize_source_text,
    validate_candidate_span,
)
from seektalent.candidate_feedback.span_models import CandidateSpan


def test_validate_candidate_span_accepts_exact_normalized_substring():
    text = "精通Python及主流Web框架（FastAPI/Flask/Django）"
    raw_surface = "FastAPI/Flask/Django"
    start = text.index(raw_surface)
    end = start + len(raw_surface)
    span = CandidateSpan(
        source_resume_id="resume-1",
        source_field="matched_must_haves",
        start_char=start,
        end_char=end,
        raw_surface=raw_surface,
        normalized_surface=raw_surface,
        model_label="tool_or_framework",
        model_score=0.88,
        extractor_schema_version="schema-v1",
    )
    assert validate_candidate_span(text, span) is None


def test_validate_candidate_span_rejects_non_extractively_generated_surface():
    text = "掌握至少一种OLAP引擎（如Doris/ClickHouse）"
    span = CandidateSpan(
        source_resume_id="resume-1",
        source_field="matched_must_haves",
        start_char=0,
        end_char=4,
        raw_surface="Doris OLAP",
        normalized_surface="Doris OLAP",
        model_label="technical_phrase",
        model_score=0.71,
        extractor_schema_version="schema-v1",
    )
    assert validate_candidate_span(text, span) == "non_extractively_generated_span"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_candidate_feedback_span_models.py tests/test_candidate_feedback.py -k span`

Expected: FAIL with missing extractor helpers.

- [ ] **Step 3: Implement exact-offset validation and source normalization**

```python
from __future__ import annotations

import unicodedata

from seektalent.candidate_feedback.span_models import CandidateSpan


def normalize_source_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def validate_candidate_span(source_text: str, span: CandidateSpan) -> str | None:
    if span.start_char < 0 or span.end_char < span.start_char or span.end_char > len(source_text):
        return "non_extractively_generated_span"
    raw = source_text[span.start_char : span.end_char]
    if normalize_source_text(raw) != normalize_source_text(span.raw_surface):
        return "non_extractively_generated_span"
    return None
```

- [ ] **Step 4: Add a bounded legacy regex adapter instead of deleting current extractor**

```python
class LegacyRegexSpanExtractor:
    """Adapter that converts current regex-only phrase proposal into CandidateSpan rows."""

    def extract(self, *, resume_id: str, source_field: str, texts: list[str]) -> list[CandidateSpan]:
        ...
```

The adapter should reuse the current regex surface extraction from `extraction.py` so the bakeoff can compare:

- current regex proposal
- model-backed proposal

without duplicating heuristics in test fixtures.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_candidate_feedback_span_models.py tests/test_candidate_feedback.py -k span`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/candidate_feedback/span_extractors.py \
  src/seektalent/candidate_feedback/extraction.py \
  tests/test_candidate_feedback_span_models.py \
  tests/test_candidate_feedback.py
git commit -m "feat: add extractive span validation and legacy adapter"
```

## Task 3: Implement Guarded Familying Without Lexicons

**Files:**
- Create: `src/seektalent/candidate_feedback/familying.py`
- Test: `tests/test_candidate_feedback_familying.py`

- [ ] **Step 1: Write the failing familying tests**

```python
from seektalent.candidate_feedback.familying import (
    canonicalize_surface,
    should_merge_spans,
)
from seektalent.candidate_feedback.span_models import CandidateSpan


def test_canonicalize_surface_collapses_separator_variants():
    assert canonicalize_surface("Flink CDC") == canonicalize_surface("flink-cdc")
    assert canonicalize_surface("FlinkCDC") != ""


def test_should_merge_spans_accepts_separator_or_case_variants():
    left = CandidateSpan(
        source_resume_id="resume-1",
        source_field="evidence",
        start_char=0,
        end_char=9,
        raw_surface="Flink CDC",
        normalized_surface="Flink CDC",
        model_label="technical_phrase",
        model_score=0.9,
        extractor_schema_version="schema-v1",
    )
    right = CandidateSpan(
        source_resume_id="resume-2",
        source_field="evidence",
        start_char=0,
        end_char=9,
        raw_surface="flink-cdc",
        normalized_surface="flink-cdc",
        model_label="technical_phrase",
        model_score=0.87,
        extractor_schema_version="schema-v1",
    )
    assert should_merge_spans(left, right, embedding_similarity=0.75) == (True, "surface_variant")


def test_should_merge_spans_rejects_confusable_neighbors():
    left = CandidateSpan(
        source_resume_id="resume-1",
        source_field="evidence",
        start_char=0,
        end_char=4,
        raw_surface="Java",
        normalized_surface="Java",
        model_label="skill",
        model_score=0.9,
        extractor_schema_version="schema-v1",
    )
    right = CandidateSpan(
        source_resume_id="resume-2",
        source_field="evidence",
        start_char=0,
        end_char=10,
        raw_surface="JavaScript",
        normalized_surface="JavaScript",
        model_label="skill",
        model_score=0.89,
        extractor_schema_version="schema-v1",
    )
    assert should_merge_spans(left, right, embedding_similarity=0.96) == (False, "confusable_neighbor")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_candidate_feedback_familying.py`

Expected: FAIL with missing module or functions.

- [ ] **Step 3: Implement canonicalization and merge guards**

```python
from __future__ import annotations

import re

from seektalent.candidate_feedback.span_extractors import normalize_source_text
from seektalent.candidate_feedback.span_models import CandidateSpan


def canonicalize_surface(value: str) -> str:
    normalized = normalize_source_text(value).casefold()
    normalized = re.sub(r"[-_/]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def should_merge_spans(
    left: CandidateSpan,
    right: CandidateSpan,
    *,
    embedding_similarity: float,
    similarity_threshold: float = 0.92,
) -> tuple[bool, str]:
    left_norm = canonicalize_surface(left.normalized_surface)
    right_norm = canonicalize_surface(right.normalized_surface)
    if left_norm == right_norm:
        return True, "surface_variant"
    if _is_confusable_neighbor(left_norm, right_norm):
        return False, "confusable_neighbor"
    if embedding_similarity >= similarity_threshold and _has_shared_anchor(left_norm, right_norm):
        return True, "embedding_anchor_match"
    return False, "no_merge_rule"
```

Implement `_is_confusable_neighbor()` conservatively. Start with explicit guards for:

- prefix-only containment like `java` vs `javascript`
- short-platform overlaps
- Chinese short phrases with low character-overlap ratio

Do not invent a huge rule system. Keep the guards narrow and obvious.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_candidate_feedback_familying.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/candidate_feedback/familying.py \
  tests/test_candidate_feedback_familying.py
git commit -m "feat: add guarded phrase familying"
```

## Task 4: Add Proposal Runtime And Typed Artifacts

**Files:**
- Create: `src/seektalent/candidate_feedback/proposal_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/models.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_candidate_feedback.py`

- [ ] **Step 1: Write the failing runtime-artifact tests**

```python
def test_prf_shadow_runtime_writes_span_and_family_artifacts(tmp_path):
    run = build_test_run(tmp_path)
    runtime = build_runtime_for_test(run)

    runtime._run_rounds(...)

    round_dir = run.round_dir(2)
    assert resolver.resolve("round.02.retrieval.prf_span_candidates").exists()
    assert resolver.resolve("round.02.retrieval.prf_expression_families").exists()
    assert resolver.resolve("round.02.retrieval.prf_policy_decision").exists()


def test_replay_snapshot_includes_prf_version_vector_and_artifact_refs(tmp_path):
    run = build_test_run(tmp_path)
    runtime = build_runtime_for_test(run)

    runtime._run_rounds(...)

    snapshot = json.loads(resolver.resolve("round.02.retrieval.replay_snapshot").read_text())
    assert snapshot["prf_span_model_name"]
    assert snapshot["prf_span_model_revision"]
    assert snapshot["prf_candidate_span_artifact_ref"]
    assert snapshot["prf_expression_family_artifact_ref"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_runtime_state_flow.py tests/test_candidate_feedback.py -k prf`

Expected: FAIL with missing artifacts or snapshot fields.

- [ ] **Step 3: Implement proposal runtime output contract**

```python
def build_prf_proposal_artifacts(...):
    return {
        "candidate_spans": [...],
        "phrase_families": [...],
        "proposal_metadata": metadata,
        "artifact_refs": refs,
    }
```

The runtime must:

- run proposal after seed selection
- validate candidate spans before familying
- persist candidate spans under `round.XX.retrieval.prf_span_candidates`
- persist phrase families under `round.XX.retrieval.prf_expression_families`
- persist the final PRF policy decision under `round.XX.retrieval.prf_policy_decision`

- [ ] **Step 4: Extend replay snapshot and second-lane metadata**

```python
snapshot = snapshot.model_copy(
    update={
        "prf_span_model_name": metadata.span_model_name,
        "prf_span_model_revision": metadata.span_model_revision,
        "prf_span_schema_version": metadata.schema_version,
        "prf_embedding_model_name": metadata.embedding_model_name,
        "prf_embedding_model_revision": metadata.embedding_model_revision,
        "prf_familying_version": metadata.familying_version,
        "prf_candidate_span_artifact_ref": refs.candidate_span_artifact_ref,
        "prf_expression_family_artifact_ref": refs.expression_family_artifact_ref,
        "prf_policy_decision_artifact_ref": refs.policy_decision_artifact_ref,
    }
)
```

Do this through typed model fields, not free-form dict stuffing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_runtime_state_flow.py tests/test_candidate_feedback.py -k prf`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/candidate_feedback/proposal_runtime.py \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/models.py \
  tests/test_runtime_state_flow.py \
  tests/test_candidate_feedback.py
git commit -m "feat: add prf proposal runtime artifacts"
```

## Task 5: Enforce Responsibility Shadow Mode And Entity Conservatism

**Files:**
- Modify: `src/seektalent/candidate_feedback/proposal_runtime.py`
- Modify: `src/seektalent/candidate_feedback/policy.py`
- Modify: `tests/test_candidate_feedback.py`

- [ ] **Step 1: Write the failing policy tests**

```python
def test_responsibility_phrase_is_shadow_only_in_phase_1_5():
    family = make_phrase_family(
        canonical_surface="负责系统设计",
        candidate_term_type="responsibility_phrase",
        positive_seed_support_count=3,
        negative_support_count=0,
    )
    decision = build_prf_policy_decision(make_policy_input([family]))
    assert decision.gate_passed is False
    assert decision.reject_reasons == ["no_safe_prf_expression"]
    assert "shadow_only_responsibility_phrase" in decision.candidate_expressions[0].reject_reasons


def test_ambiguous_company_or_product_entity_is_rejected_by_default():
    family = make_phrase_family(
        canonical_surface="Databricks",
        candidate_term_type="product_or_platform",
        positive_seed_support_count=3,
        negative_support_count=0,
        reject_reasons=["ambiguous_company_or_product_entity"],
    )
    decision = build_prf_policy_decision(make_policy_input([family]))
    assert decision.gate_passed is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_candidate_feedback.py -k 'responsibility or ambiguous'`

Expected: FAIL because these reject reasons are not enforced yet.

- [ ] **Step 3: Add the minimal policy enforcement**

```python
if expression.candidate_term_type == "responsibility_phrase":
    reject_reasons.append("shadow_only_responsibility_phrase")

if "ambiguous_company_or_product_entity" in reject_reasons:
    ...
```

Keep the logic simple:

- `responsibility_phrase` always rejects in Phase 1.5
- ambiguity reject reason blocks promotion
- no attempt to rescue ambiguity with extra model calls

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_candidate_feedback.py -k 'responsibility or ambiguous'`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/candidate_feedback/proposal_runtime.py \
  src/seektalent/candidate_feedback/policy.py \
  tests/test_candidate_feedback.py
git commit -m "feat: enforce prf responsibility and ambiguity guards"
```

## Task 6: Build Offline Bakeoff Harness And Promotion Rubric

**Files:**
- Create: `src/seektalent/candidate_feedback/bakeoff.py`
- Create: `tests/test_candidate_feedback_bakeoff.py`
- Modify: `src/seektalent/evaluation.py`
- Modify: `tests/test_evaluation.py`

- [ ] **Step 1: Write the failing bakeoff tests**

```python
from seektalent.candidate_feedback.bakeoff import (
    PhraseQualityLabel,
    score_phrase_quality_rows,
)


def test_phrase_quality_rows_track_denominators_and_blockers():
    rows = [
        PhraseQualityLabel(extractor="regex", label="template_fragment", accepted=False),
        PhraseQualityLabel(extractor="model", label="query_material", accepted=True),
        PhraseQualityLabel(extractor="model", label="company_leakage", accepted=True),
    ]
    metrics = score_phrase_quality_rows(rows)
    assert metrics["model"]["accepted_count"] == 2
    assert metrics["model"]["blocker_count"] == 1


def test_replay_export_includes_prf_proposal_refs(tmp_path):
    exported = export_replay_rows(...)
    row = exported[0]
    assert row["prf_span_model_name"]
    assert row["prf_candidate_span_artifact_ref"]
    assert row["prf_expression_family_artifact_ref"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_candidate_feedback_bakeoff.py tests/test_evaluation.py -k prf`

Expected: FAIL with missing bakeoff helpers and replay fields.

- [ ] **Step 3: Implement explicit rubric-bearing bakeoff helpers**

```python
class PhraseQualityLabel(BaseModel):
    extractor: str
    label: Literal[
        "query_material",
        "template_fragment",
        "generic_boilerplate",
        "company_leakage",
        "non_extractive",
    ]
    accepted: bool


def score_phrase_quality_rows(rows: list[PhraseQualityLabel]) -> dict[str, dict[str, int]]:
    ...
```

Bakeoff output must make denominators explicit:

- total candidate spans
- total families
- accepted families
- blocker counts

- [ ] **Step 4: Extend replay export with proposal refs**

```python
row.update(
    {
        "prf_span_model_name": snapshot.prf_span_model_name,
        "prf_span_model_revision": snapshot.prf_span_model_revision,
        "prf_embedding_model_name": snapshot.prf_embedding_model_name,
        "prf_candidate_span_artifact_ref": snapshot.prf_candidate_span_artifact_ref,
        "prf_expression_family_artifact_ref": snapshot.prf_expression_family_artifact_ref,
        "prf_policy_decision_artifact_ref": snapshot.prf_policy_decision_artifact_ref,
    }
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_candidate_feedback_bakeoff.py tests/test_evaluation.py -k prf`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/candidate_feedback/bakeoff.py \
  src/seektalent/evaluation.py \
  tests/test_candidate_feedback_bakeoff.py \
  tests/test_evaluation.py
git commit -m "feat: add prf extractor bakeoff rubric and replay export"
```

## Task 7: Add Model Dependency Gate And Config Surface

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `tests/test_llm_provider_config.py`
- Modify: `tests/test_candidate_feedback.py`

- [ ] **Step 1: Write the failing config tests**

```python
def test_prf_model_dependency_settings_are_explicit():
    settings = AppSettings()
    assert settings.prf_span_model_name
    assert settings.prf_span_model_revision
    assert settings.prf_embedding_model_name
    assert settings.prf_embedding_model_revision
    assert settings.prf_allow_remote_code is False


def test_model_unavailable_falls_back_to_legacy_regex_extractor():
    runtime = build_runtime_for_test(prf_span_model_available=False)
    proposal = runtime._build_prf_proposal(...)
    assert proposal.metadata.extractor_version == "legacy-regex-v1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_llm_provider_config.py tests/test_candidate_feedback.py -k 'prf_span or legacy-regex'`

Expected: FAIL with missing settings or fallback behavior.

- [ ] **Step 3: Add explicit PRF model settings**

```python
prf_span_model_name: str = "fastino/gliner2-multi-v1"
prf_span_model_revision: str = ""
prf_span_tokenizer_revision: str = ""
prf_embedding_model_name: str = "Alibaba-NLP/gte-multilingual-base"
prf_embedding_model_revision: str = ""
prf_allow_remote_code: bool = False
prf_familying_embedding_threshold: float = 0.92
```

Keep the settings narrow. Do not add speculative knobs beyond what the spec requires.

- [ ] **Step 4: Wire fallback to legacy extractor when model dependency gate fails**

```python
if not proposal_backend_available(settings):
    return build_legacy_regex_proposal(...)
```

Do not add broad retry logic. A single explicit fallback is enough.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_llm_provider_config.py tests/test_candidate_feedback.py -k 'prf_span or legacy-regex'`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/config.py \
  tests/test_llm_provider_config.py \
  tests/test_candidate_feedback.py
git commit -m "feat: add prf model dependency gate settings"
```

## Task 8: Document Logical Artifacts And Shadow Rollout

**Files:**
- Modify: `docs/outputs.md`
- Modify: `tests/test_runtime_audit.py`

- [ ] **Step 1: Write the failing docs-contract test**

```python
def test_outputs_doc_mentions_prf_v1_5_artifacts():
    text = Path("docs/outputs.md").read_text(encoding="utf-8")
    assert "round.XX.retrieval.prf_span_candidates" in text
    assert "round.XX.retrieval.prf_expression_families" in text
    assert "round.XX.retrieval.prf_policy_decision" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_runtime_audit.py -k prf_v1_5`

Expected: FAIL because docs and audit expectations do not mention the new artifacts.

- [ ] **Step 3: Update outputs documentation**

Add a compact section to `docs/outputs.md` describing:

- `round.XX.retrieval.prf_span_candidates`
- `round.XX.retrieval.prf_expression_families`
- `round.XX.retrieval.prf_policy_decision`
- replay snapshot proposal metadata fields
- the shadow-to-mainline rollout meaning

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_runtime_audit.py -k prf_v1_5`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/outputs.md tests/test_runtime_audit.py
git commit -m "docs: describe prf v1.5 proposal artifacts"
```

## Final Verification

- [ ] **Step 1: Run focused PRF test suite**

Run:

```bash
uv run pytest -q \
  tests/test_candidate_feedback_span_models.py \
  tests/test_candidate_feedback_familying.py \
  tests/test_candidate_feedback_bakeoff.py \
  tests/test_candidate_feedback.py \
  tests/test_runtime_state_flow.py \
  tests/test_evaluation.py \
  tests/test_llm_provider_config.py \
  tests/test_runtime_audit.py
```

Expected: PASS

- [ ] **Step 2: Run broader regression suite touching retrieval and artifacts**

Run:

```bash
uv run pytest -q \
  tests/test_artifact_store.py \
  tests/test_artifact_archive.py \
  tests/test_artifact_path_contract.py \
  tests/test_runtime_lifecycle.py \
  tests/test_api.py \
  tests/test_cli.py
```

Expected: PASS

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "test: verify prf v1.5 extractor integration"
```

## Spec Coverage Check

- Proposal versioning, schema payload, artifact refs, and replay contract are implemented in Tasks 1, 4, and 6.
- Exact extractive enforcement and source grounding are implemented in Task 2.
- Ambiguous company/product conservatism and responsibility shadow-only behavior are implemented in Task 5.
- Familying guardrails are implemented in Task 3.
- Model dependency gate and fallback behavior are implemented in Task 7.
- Offline bakeoff rubric and promotion criteria hooks are implemented in Task 6.
- Typed logical artifact integration is implemented in Tasks 4 and 8.

## Placeholder Scan

- No `TODO`, `TBD`, or "implement later" placeholders remain.
- Every task includes explicit files, tests, commands, and expected outcomes.

## Type Consistency Check

- `CandidateSpan`, `PhraseFamily`, `ProposalMetadata`, `PRFProposalArtifactRefs`, and `PRFProposalVersionVector` are introduced before later tasks reference them.
- Logical artifact names stay consistent across Tasks 4, 6, and 8.
- The fallback extractor remains explicitly named `legacy-regex-v1` across Tasks 2 and 7.
