# LLM PRF Mainline Cleanup And Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LLM PRF the only active typed second-lane PRF proposal path, remove old PRF v1.5/sidecar/legacy runtime surfaces, add source provenance/support eligibility, and add focused validation before full benchmark/eval.

**Architecture:** LLM PRF remains a bounded proposal stage under `candidate_feedback`; deterministic grounding, conservative familying, and PRF policy stay authoritative. Old sidecar/span/legacy runtime code is deleted from active product paths, while historical PRF metadata is parsed through read-only compatibility helpers. Live validation reuses the same extractor/grounding/policy chain and is manually invoked.

**Tech Stack:** Python 3.12, Pydantic v2, Pydantic AI, pytest, ruff, existing SeekTalent artifact/runtime/evaluation modules.

---

## Execution Notes

This worktree currently has uncommitted implementation changes in LLM PRF files. Treat those as existing work to review and integrate; do not revert them. Before executing any task, run:

```bash
git status --short
```

Expected: the existing dirty files are visible. When committing a task, stage only the files named by that task.

The active product behavior must remain:

- round 2+ builds exploit lane from the controller plan;
- LLM PRF may promote one safe expression to the typed second lane;
- every LLM PRF failure, empty safe result, timeout, or policy rejection falls back to `generic_explore`;
- low-quality rescue `candidate_feedback` remains unchanged.

## File Structure

Create:

- `src/seektalent/legacy_artifacts.py` - read-only parsing helpers for historical PRF v1.5/sidecar replay metadata.
- `tests/test_prf_cleanup_import_graph.py` - active import graph guard for removed PRF backends.
- `tests/fixtures/llm_prf_live_validation/cases.jsonl` - sanitized LLM PRF input fixtures for manual validation.

Modify:

- `src/seektalent/config.py` - remove old PRF config fields, add stale config scanner, runtime timeout `3.0`, live harness timeout `30.0`.
- `.env.example` - remove sidecar/span/legacy PRF keys, add Chinese comments for active LLM PRF keys.
- `src/seektalent/default.env` - mirror active PRF env defaults.
- `src/seektalent/candidate_feedback/models.py` - move active `CandidateTermType` here and remove PRF proposal version models tied to sidecar/span.
- `src/seektalent/candidate_feedback/extraction.py` - keep active seed selection and deterministic classification; remove old regex proposal entry points after LLM PRF no longer imports them.
- `src/seektalent/candidate_feedback/llm_prf.py` - source provenance, sanitizer, source ids, support eligibility, conservative familying, grounding changes.
- `src/seektalent/candidate_feedback/llm_prf_bakeoff.py` - support checked-in `LLMPRFInput` fixtures, separate live timeout, provider failure metrics.
- `src/seektalent/candidate_feedback/__init__.py` - export active LLM PRF symbols only.
- `src/seektalent/runtime/orchestrator.py` - remove backend selector and old PRF v1.5 builders; always use LLM PRF on eligible second-lane PRF attempts.
- `src/seektalent/runtime/second_lane_runtime.py` - remove `prf_v1_5_mode` and `shadow_prf_v1_5_artifact_ref` parameters/fields.
- `src/seektalent/runtime/runtime_diagnostics.py` - remove active sidecar proposal metadata; keep LLM PRF metadata and use legacy parser only for historical reads.
- `src/seektalent/models.py` - remove active `prf_v1_5_mode`/shadow fields from `SecondLaneDecision`; move old replay fields behind read-only compatibility.
- `src/seektalent/evaluation.py` - tolerate historical PRF metadata through `legacy_artifacts.py`, without active sidecar runtime imports.
- `src/seektalent/cli.py` - remove sidecar prefetch command; add `llm-prf-live-validate` command for manual live validation.
- `pyproject.toml` - remove `prf-sidecar` optional dependency group and `seektalent-prf-sidecar` script.
- `docs/outputs.md` - remove active sidecar output docs; keep a historical compatibility note.
- `tests/test_llm_prf.py` - update LLM PRF source/grounding/familying tests.
- `tests/test_candidate_feedback.py` - keep policy/classification/rescue coverage; remove sidecar/span tests.
- `tests/test_runtime_state_flow.py` - update runtime flow tests to LLM-only PRF proposal.
- `tests/test_second_lane_runtime.py` - remove PRF v1.5 fields from decision tests.
- `tests/test_llm_provider_config.py` - stale PRF config scanner and active defaults.
- `tests/test_llm_prf_bakeoff.py` - deterministic/live harness summary logic.
- `tests/test_evaluation.py` - historical replay parsing coverage.
- `tests/test_artifact_store.py` and `tests/test_artifact_path_contract.py` - remove active sidecar manifest registration checks.

Delete:

- `src/seektalent/prf_sidecar/`
- `src/seektalent/candidate_feedback/proposal_runtime.py`
- `src/seektalent/candidate_feedback/span_extractors.py`
- `src/seektalent/candidate_feedback/span_models.py`
- `src/seektalent/candidate_feedback/familying.py`
- `docker/prf-model-sidecar/`
- sidecar/span-only tests:
  - `tests/test_prf_sidecar_app.py`
  - `tests/test_prf_sidecar_boundary.py`
  - `tests/test_prf_sidecar_models.py`
  - `tests/test_prf_sidecar_service.py`
  - `tests/test_candidate_feedback_span_models.py`
  - `tests/test_candidate_feedback_familying.py`

---

### Task 1: Config And Env Cleanup

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `.env.example`
- Modify: `src/seektalent/default.env`
- Test: `tests/test_llm_provider_config.py`

- [ ] **Step 1: Write failing tests for active PRF defaults and stale config rejection**

Add these tests to `tests/test_llm_provider_config.py`:

```python
import pytest

from seektalent.config import AppSettings, PRFConfigMigrationError


def test_llm_prf_runtime_and_live_harness_timeouts_are_separate() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.prf_probe_phrase_proposal_model_id == "deepseek-v4-flash"
    assert settings.prf_probe_phrase_proposal_reasoning_effort == "off"
    assert settings.prf_probe_phrase_proposal_timeout_seconds == 3.0
    assert settings.prf_probe_phrase_proposal_live_harness_timeout_seconds == 30.0
    assert settings.prf_probe_phrase_proposal_max_output_tokens == 2048


@pytest.mark.parametrize(
    "key,value",
    [
        ("SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND", "sidecar_span"),
        ("SEEKTALENT_PRF_V1_5_MODE", "shadow"),
        ("SEEKTALENT_PRF_MODEL_BACKEND", "http_sidecar"),
        ("SEEKTALENT_PRF_SIDECAR_ENDPOINT", "http://127.0.0.1:8741"),
        ("SEEKTALENT_PRF_SPAN_MODEL_NAME", "fastino/gliner2-multi-v1"),
        ("SEEKTALENT_PRF_EMBEDDING_MODEL_NAME", "Alibaba-NLP/gte-multilingual-base"),
    ],
)
def test_removed_prf_config_keys_fail_settings_validation(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)

    with pytest.raises(PRFConfigMigrationError, match=key):
        AppSettings(_env_file=None)


def test_removed_prf_config_keys_in_env_file_fail_settings_validation(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_PRF_MODEL_BACKEND=http_sidecar\n", encoding="utf-8")

    with pytest.raises(PRFConfigMigrationError, match="SEEKTALENT_PRF_MODEL_BACKEND"):
        AppSettings(_env_file=env_file)
```

If `Path` is not already imported in that test file, add:

```python
from pathlib import Path
```

- [ ] **Step 2: Run config tests and verify failure**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py -k "llm_prf_runtime_and_live_harness_timeouts_are_separate or removed_prf_config" -q
```

Expected: tests fail because old PRF fields still exist, runtime timeout is `30.0`, and `PRFConfigMigrationError` does not exist.

- [ ] **Step 3: Implement stale PRF config scanner and active defaults**

In `src/seektalent/config.py`, remove:

```python
PRFProbeProposalBackend = Literal["llm_deepseek_v4_flash", "legacy_regex", "sidecar_span"]
```

Add this near `TextLLMConfigMigrationError`:

```python
REMOVED_PRF_ENV_KEYS = {
    "SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND",
    "SEEKTALENT_PRF_V1_5_MODE",
    "SEEKTALENT_PRF_MODEL_BACKEND",
    "SEEKTALENT_PRF_SPAN_MODEL_NAME",
    "SEEKTALENT_PRF_SPAN_MODEL_REVISION",
    "SEEKTALENT_PRF_SPAN_TOKENIZER_REVISION",
    "SEEKTALENT_PRF_SPAN_SCHEMA_VERSION",
    "SEEKTALENT_PRF_EMBEDDING_MODEL_NAME",
    "SEEKTALENT_PRF_EMBEDDING_MODEL_REVISION",
    "SEEKTALENT_PRF_ALLOW_REMOTE_CODE",
    "SEEKTALENT_PRF_REQUIRE_PINNED_MODELS_FOR_MAINLINE",
    "SEEKTALENT_PRF_REMOTE_CODE_AUDIT_REVISION",
    "SEEKTALENT_PRF_FAMILYING_EMBEDDING_THRESHOLD",
    "SEEKTALENT_PRF_SIDECAR_PROFILE",
    "SEEKTALENT_PRF_SIDECAR_BIND_HOST",
    "SEEKTALENT_PRF_SIDECAR_ENDPOINT",
    "SEEKTALENT_PRF_SIDECAR_ENDPOINT_CONTRACT_VERSION",
    "SEEKTALENT_PRF_SIDECAR_SERVE_MODE",
    "SEEKTALENT_PRF_SIDECAR_TIMEOUT_SECONDS_SHADOW",
    "SEEKTALENT_PRF_SIDECAR_TIMEOUT_SECONDS_MAINLINE",
    "SEEKTALENT_PRF_SIDECAR_MAX_BATCH_SIZE",
    "SEEKTALENT_PRF_SIDECAR_MAX_PAYLOAD_BYTES",
    "SEEKTALENT_PRF_SIDECAR_BAKEOFF_PROMOTED",
}


class PRFConfigMigrationError(ValueError):
    """Raised when removed PRF sidecar/span config surfaces are still present."""


def _scan_removed_prf_inputs(
    *,
    env_file: str | Path | None,
    init_data: Mapping[str, object],
    include_default_env_file: bool,
) -> None:
    sources: list[Mapping[str, str]] = [dict(os.environ)]
    if include_default_env_file:
        sources.append(_read_env_kv_pairs(".env"))
    if env_file is not None:
        sources.append(_read_env_kv_pairs(env_file))
    sources.append(
        {
            f"SEEKTALENT_{str(key).upper()}": str(value)
            for key, value in init_data.items()
            if value is not None and not str(key).startswith("_")
        }
    )
    stale_keys = sorted({key for source in sources for key in REMOVED_PRF_ENV_KEYS if key in source})
    if stale_keys:
        joined = ", ".join(stale_keys)
        raise PRFConfigMigrationError(
            "removed PRF sidecar/span config detected: "
            f"{joined}. Remove these keys; active prf_probe proposal now uses LLM PRF only."
        )
```

In `AppSettings.__init__`, call it after the existing `_scan_legacy_text_llm_inputs` migration check:

```python
        _scan_removed_prf_inputs(
            env_file=env_file,
            init_data=data,
            include_default_env_file=not explicit_env_file,
        )
```

Remove these fields from `AppSettings`:

```python
    prf_v1_5_mode: Literal["disabled", "shadow", "mainline"] = "shadow"
    prf_span_model_name: str = "fastino/gliner2-multi-v1"
    prf_span_model_revision: str = ""
    prf_span_tokenizer_revision: str = ""
    prf_span_schema_version: str = "gliner2-schema-v1"
    prf_embedding_model_name: str = "Alibaba-NLP/gte-multilingual-base"
    prf_embedding_model_revision: str = ""
    prf_allow_remote_code: bool = False
    prf_require_pinned_models_for_mainline: bool = True
    prf_remote_code_audit_revision: str | None = None
    prf_familying_embedding_threshold: float = 0.92
    prf_probe_proposal_backend: PRFProbeProposalBackend = "llm_deepseek_v4_flash"
    prf_model_backend: Literal["legacy", "http_sidecar"] = "legacy"
    prf_sidecar_profile: Literal["host-local", "docker-internal", "linux-host-network"] = "host-local"
    prf_sidecar_bind_host: str = "127.0.0.1"
    prf_sidecar_endpoint: str = "http://127.0.0.1:8741"
    prf_sidecar_endpoint_contract_version: str = "prf-sidecar-http-v1"
    prf_sidecar_serve_mode: Literal["dev-bootstrap", "prod-serve"] = "dev-bootstrap"
    prf_sidecar_timeout_seconds_shadow: float = 0.35
    prf_sidecar_timeout_seconds_mainline: float = 1.5
    prf_sidecar_max_batch_size: int = 32
    prf_sidecar_max_payload_bytes: int = 262_144
    prf_sidecar_bakeoff_promoted: bool = False
```

Keep only:

```python
    prf_probe_phrase_proposal_model_id: str = "deepseek-v4-flash"
    prf_probe_phrase_proposal_reasoning_effort: ReasoningEffort = "off"
    prf_probe_phrase_proposal_timeout_seconds: float = 3.0
    prf_probe_phrase_proposal_live_harness_timeout_seconds: float = 30.0
    prf_probe_phrase_proposal_max_output_tokens: int = 2048
```

Remove old range checks and add the live harness timeout check:

```python
        if self.prf_probe_phrase_proposal_timeout_seconds <= 0:
            raise ValueError("prf_probe_phrase_proposal_timeout_seconds must be > 0")
        if self.prf_probe_phrase_proposal_live_harness_timeout_seconds <= 0:
            raise ValueError("prf_probe_phrase_proposal_live_harness_timeout_seconds must be > 0")
        if self.prf_probe_phrase_proposal_max_output_tokens < 256:
            raise ValueError("prf_probe_phrase_proposal_max_output_tokens must be >= 256")
```

- [ ] **Step 4: Update env templates with Chinese comments**

In `.env.example`, remove the full `# PRF v1.5 / sidecar` block and `SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND`.

Replace the active LLM PRF block with:

```dotenv
# LLM PRF probe：7:3 旁路 second lane 的短语 proposal；主运行路径超时短，失败/空结果/校验不过时回退 generic_explore。
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT=off
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=3.0
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_LIVE_HARNESS_TIMEOUT_SECONDS=30.0
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS=2048
```

In `src/seektalent/default.env`, remove old PRF sidecar/span keys and keep the same five active LLM PRF keys.

If a local `.env` exists in the execution worktree, update it manually with the same five active keys and remove stale PRF sidecar/span keys. Do not commit `.env`.

- [ ] **Step 5: Verify config tests pass**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py -q
```

Expected: all tests in `tests/test_llm_provider_config.py` pass.

- [ ] **Step 6: Commit config cleanup**

```bash
git add src/seektalent/config.py .env.example src/seektalent/default.env tests/test_llm_provider_config.py
git commit -m "refactor: remove legacy prf config surface"
```

---

### Task 2: Active Candidate Feedback Models And LLM PRF Source Schema

**Files:**
- Modify: `src/seektalent/candidate_feedback/models.py`
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `src/seektalent/candidate_feedback/__init__.py`
- Test: `tests/test_llm_prf.py`

- [ ] **Step 1: Write failing source schema tests**

Add these tests to `tests/test_llm_prf.py`:

```python
from seektalent.candidate_feedback.llm_prf import (
    LLMPRFSourceEvidenceRef,
    LLMPRFSourceText,
    build_llm_prf_source_text_id,
    text_sha256,
)


def test_llm_prf_source_text_uses_source_section_and_stable_id() -> None:
    raw = "Built LangGraph workflows for multi-agent retrieval."
    source_id = build_llm_prf_source_text_id(
        resume_id="seed-1",
        source_section="recent_experience_summary",
        original_field_path="recent_experiences[0].summary",
        normalized_text=raw,
        preparation_version="llm-prf-source-prep-v1",
    )

    source = LLMPRFSourceText(
        resume_id="seed-1",
        source_section="recent_experience_summary",
        source_text_id=source_id,
        source_text_index=0,
        source_text_raw=raw,
        source_text_hash=text_sha256(raw),
        original_field_path="recent_experiences[0].summary",
        source_kind="grounding_eligible",
        support_eligible=True,
        hint_only=False,
        preparation_version="llm-prf-source-prep-v1",
        dedupe_key="langgraph workflows for multi-agent retrieval",
        rank_reason="matched:LangGraph,multi-agent",
    )

    assert source.source_id == source_id
    assert source.source_section == "recent_experience_summary"
    assert source.support_eligible is True
    assert source.hint_only is False


def test_llm_prf_source_ref_resolves_by_source_text_id() -> None:
    ref = LLMPRFSourceEvidenceRef(
        resume_id="seed-1",
        source_section="skill",
        source_text_id="source-hash",
        source_text_index=3,
        source_text_hash="text-hash",
    )

    assert ref.source_text_id == "source-hash"
    assert ref.source_section == "skill"
```

- [ ] **Step 2: Run source schema tests and verify failure**

Run:

```bash
uv run pytest tests/test_llm_prf.py -k "source_text_uses_source_section or source_ref_resolves" -q
```

Expected: tests fail because `source_section`, `source_text_id`, and `build_llm_prf_source_text_id` are not implemented.

- [ ] **Step 3: Move active candidate term type out of `span_models`**

In `src/seektalent/candidate_feedback/models.py`, add this near the top:

```python
from typing import Literal


CandidateTermType = Literal[
    "skill",
    "tool_or_framework",
    "product_or_platform",
    "technical_phrase",
    "responsibility_phrase",
    "company_entity",
    "location",
    "degree",
    "compensation",
    "administrative",
    "generic",
    "unknown_high_risk",
    "unknown",
]
```

Remove:

```python
from seektalent.candidate_feedback.span_models import CandidateTermType
```

In `src/seektalent/candidate_feedback/llm_prf.py`, replace:

```python
from seektalent.candidate_feedback.models import FeedbackCandidateExpression
from seektalent.candidate_feedback.span_models import CandidateTermType, SourceField
```

with:

```python
from seektalent.candidate_feedback.models import CandidateTermType, FeedbackCandidateExpression
```

- [ ] **Step 4: Implement source section fields and source id helper**

In `src/seektalent/candidate_feedback/llm_prf.py`, add:

```python
LLM_PRF_SOURCE_PREPARATION_VERSION = "llm-prf-source-prep-v1"

LLMPRFSourceSection = Literal[
    "skill",
    "recent_experience_summary",
    "key_achievement",
    "raw_text_excerpt",
    "scorecard_evidence",
    "scorecard_matched_must_have",
    "scorecard_matched_preference",
    "scorecard_strength",
]


def text_sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def build_llm_prf_source_text_id(
    *,
    resume_id: str,
    source_section: LLMPRFSourceSection,
    original_field_path: str,
    normalized_text: str,
    preparation_version: str,
) -> str:
    payload = {
        "resume_id": resume_id,
        "source_section": source_section,
        "original_field_path": original_field_path,
        "normalized_text": normalized_text,
        "preparation_version": preparation_version,
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
```

Replace `LLMPRFSourceText` with:

```python
class LLMPRFSourceText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_section: LLMPRFSourceSection
    source_text_id: str
    source_text_index: int = Field(ge=0)
    source_text_raw: str = Field(min_length=1)
    source_text_hash: str
    original_field_path: str
    source_kind: LLMPRFSourceKind
    support_eligible: bool
    hint_only: bool
    preparation_version: str = LLM_PRF_SOURCE_PREPARATION_VERSION
    dedupe_key: str
    rank_reason: str = ""

    @property
    def source_id(self) -> str:
        return self.source_text_id
```

Replace `LLMPRFSourceEvidenceRef` with:

```python
class LLMPRFSourceEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_section: LLMPRFSourceSection
    source_text_id: str
    source_text_index: int = Field(ge=0)
    source_text_hash: str
```

Replace `LLMPRFGroundingRecord.source_field` with:

```python
    source_section: LLMPRFSourceSection
    source_text_id: str
    support_eligible: bool = False
    hint_only: bool = False
```

Remove `_SOURCE_FIELD_ORDER` and replace field ranking with source-section ranking:

```python
_SOURCE_SECTION_ORDER: tuple[LLMPRFSourceSection, ...] = (
    "skill",
    "recent_experience_summary",
    "key_achievement",
    "raw_text_excerpt",
    "scorecard_evidence",
    "scorecard_matched_must_have",
    "scorecard_matched_preference",
    "scorecard_strength",
)


def _source_section_rank(source_section: LLMPRFSourceSection | None) -> int:
    if source_section is None:
        return len(_SOURCE_SECTION_ORDER)
    return _SOURCE_SECTION_ORDER.index(source_section)
```

- [ ] **Step 5: Update `__init__.py` exports**

In `src/seektalent/candidate_feedback/__init__.py`, export:

```python
LLMPRFSourceSection
build_llm_prf_source_text_id
text_sha256
```

Remove exports that only come from old span/proposal modules.

- [ ] **Step 6: Verify source schema tests pass**

Run:

```bash
uv run pytest tests/test_llm_prf.py -k "source_text_uses_source_section or source_ref_resolves" -q
```

Expected: both tests pass.

- [ ] **Step 7: Commit source schema changes**

```bash
git add src/seektalent/candidate_feedback/models.py src/seektalent/candidate_feedback/llm_prf.py src/seektalent/candidate_feedback/__init__.py tests/test_llm_prf.py
git commit -m "refactor: add llm prf source provenance"
```

---

### Task 3: Source Preparation, Sanitizer, And Support Eligibility

**Files:**
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `src/seektalent/prompts/prf_probe_phrase_proposal.md`
- Test: `tests/test_llm_prf.py`

- [ ] **Step 1: Write failing tests for normalized resume sections and sanitizer**

Add tests to `tests/test_llm_prf.py`:

```python
from seektalent.models import NormalizedExperience, NormalizedResume


def test_llm_prf_input_uses_normalized_resume_source_sections() -> None:
    seed = _scored_candidate("seed-1", overall_score=92, must_have_match_score=90)
    normalized = NormalizedResume(
        resume_id="seed-1",
        dedup_key="seed-1",
        completeness_score=90,
        skills=["LangGraph", "Agent Skills"],
        recent_experiences=[
            NormalizedExperience(
                company="Example",
                title="Agent Engineer",
                summary="Built LangGraph workflows for multi-agent retrieval.",
            )
        ],
        key_achievements=["Delivered Agent Skills modules for resume matching."],
        raw_text_excerpt="Agent Skills and LangGraph were used in production retrieval.",
    )

    payload = build_llm_prf_input(
        seed_resumes=[seed],
        negative_resumes=[],
        round_no=2,
        role_title="AI Agent Engineer",
        must_have_capabilities=["LangGraph"],
        normalized_resumes_by_id={"seed-1": normalized},
    )

    assert payload is not None
    sections = {item.source_section for item in payload.source_texts}
    assert {"skill", "recent_experience_summary", "key_achievement", "raw_text_excerpt"} <= sections
    assert all(item.support_eligible for item in payload.source_texts if item.source_section != "scorecard_strength")


def test_llm_prf_source_sanitizer_rejects_metadata_dominated_snippets() -> None:
    seed = _scored_candidate("seed-1", overall_score=92, must_have_match_score=90)
    normalized = NormalizedResume(
        resume_id="seed-1",
        dedup_key="seed-1",
        completeness_score=85,
        skills=["LangGraph"],
        recent_experiences=[
            NormalizedExperience(company="阿里云", title="高级工程师", summary="阿里云 上海团队 高级工程师"),
            NormalizedExperience(company="Example", title="Engineer", summary="使用 LangGraph 构建 Agent 工作流"),
        ],
    )

    payload = build_llm_prf_input(
        seed_resumes=[seed],
        negative_resumes=[],
        round_no=2,
        role_title="AI Agent Engineer",
        must_have_capabilities=["LangGraph"],
        normalized_resumes_by_id={"seed-1": normalized},
    )

    assert payload is not None
    raw_texts = [item.source_text_raw for item in payload.source_texts]
    assert "阿里云 上海团队 高级工程师" not in raw_texts
    assert any("LangGraph" in text for text in raw_texts)
    assert payload.source_preparation["dropped_reason_counts"]["metadata_dominated"] == 1


def test_scorecard_strength_is_hint_only_and_support_ineligible() -> None:
    seed = _scored_candidate(
        "seed-1",
        overall_score=92,
        must_have_match_score=90,
        strengths=["LangGraph workflows"],
    )

    payload = build_llm_prf_input(seed_resumes=[seed], negative_resumes=[], round_no=2, role_title="Agent Engineer")

    assert payload is not None
    strength_sources = [item for item in payload.source_texts if item.source_section == "scorecard_strength"]
    assert strength_sources
    assert all(item.hint_only for item in strength_sources)
    assert all(not item.support_eligible for item in strength_sources)
```

Use existing test helpers for `_scored_candidate`. If the helper does not accept `strengths`, extend the helper with a keyword argument that maps to the `ScoredCandidate.strengths` field.

- [ ] **Step 2: Run sanitizer tests and verify failure**

Run:

```bash
uv run pytest tests/test_llm_prf.py -k "normalized_resume_source_sections or source_sanitizer or scorecard_strength" -q
```

Expected: tests fail because preparation metadata and sanitizer are missing.

- [ ] **Step 3: Extend `LLMPRFInput` with preparation metadata**

In `LLMPRFInput`, add:

```python
    source_preparation: dict[str, object] = Field(default_factory=dict)
```

When building input, populate it with:

```python
source_preparation = {
    "preparation_version": LLM_PRF_SOURCE_PREPARATION_VERSION,
    "sanitizer_version": "llm-prf-source-sanitizer-v1",
    "dropped_reason_counts": dict(dropped_reason_counts),
}
```

- [ ] **Step 4: Implement deterministic sanitizer**

Add helpers to `llm_prf.py`:

```python
_METADATA_ONLY_RE = re.compile(
    r"^(?:[\u4e00-\u9fffA-Za-z0-9&.\- ]{1,24})?(?:北京|上海|广州|深圳|杭州|成都|南京|苏州|武汉|西安|团队|部门|高级工程师|工程师|经理|总监|本科|硕士|博士|大学|学院)[\u4e00-\u9fffA-Za-z0-9&.\- ]{0,24}$",
    re.IGNORECASE,
)
_CAPABILITY_CONTEXT_RE = re.compile(
    r"(?:使用|基于|构建|开发|落地|负责|built|used|using|developed|implemented|deployed|workflow|pipeline|agent|retrieval|系统|平台)",
    re.IGNORECASE,
)


def _sanitize_llm_prf_source_text(text: str) -> tuple[str | None, str | None]:
    normalized = _normalize_source_snippet(text)
    if not normalized:
        return None, "empty"
    if len(normalized) < 2:
        return None, "too_short"
    if _METADATA_ONLY_RE.search(normalized) and not _CAPABILITY_CONTEXT_RE.search(normalized):
        return None, "metadata_dominated"
    return normalized, None
```

Keep this sanitizer conservative. Do not add domain dictionaries or a large company list.

- [ ] **Step 5: Implement source construction with support flags**

Create a single helper:

```python
def _make_llm_prf_source_text(
    *,
    resume_id: str,
    source_section: LLMPRFSourceSection,
    source_text_index: int,
    text: str,
    original_field_path: str,
    support_eligible: bool,
    hint_only: bool,
    rank_reason: str,
) -> LLMPRFSourceText:
    sanitized, dropped_reason = _sanitize_llm_prf_source_text(text)
    if dropped_reason is not None or sanitized is None:
        raise ValueError(dropped_reason or "dropped")
    dedupe_key = _source_dedupe_key(sanitized)
    source_text_id = build_llm_prf_source_text_id(
        resume_id=resume_id,
        source_section=source_section,
        original_field_path=original_field_path,
        normalized_text=sanitized,
        preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
    )
    return LLMPRFSourceText(
        resume_id=resume_id,
        source_section=source_section,
        source_text_id=source_text_id,
        source_text_index=source_text_index,
        source_text_raw=sanitized,
        source_text_hash=text_sha256(sanitized),
        original_field_path=original_field_path,
        source_kind="hint_only" if hint_only else "grounding_eligible",
        support_eligible=support_eligible,
        hint_only=hint_only,
        preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
        dedupe_key=dedupe_key,
        rank_reason=rank_reason,
    )
```

For scorecard fallback, map:

```python
scorecard_section_map = {
    "evidence": ("scorecard_evidence", True, False),
    "matched_must_haves": ("scorecard_matched_must_have", True, False),
    "matched_preferences": ("scorecard_matched_preference", True, False),
    "strengths": ("scorecard_strength", False, True),
}
```

- [ ] **Step 6: Update prompt to include support eligibility**

In `src/seektalent/prompts/prf_probe_phrase_proposal.md`, include:

```markdown
Use only source_texts where support_eligible=true to support an accepted candidate.
hint_only=true source_texts may suggest wording but cannot be the only evidence for a candidate.
Every source_evidence_ref must include source_text_id, source_section, source_text_index, and source_text_hash copied from the payload.
```

- [ ] **Step 7: Verify sanitizer tests pass**

Run:

```bash
uv run pytest tests/test_llm_prf.py -k "normalized_resume_source_sections or source_sanitizer or scorecard_strength" -q
```

Expected: tests pass.

- [ ] **Step 8: Commit source preparation changes**

```bash
git add src/seektalent/candidate_feedback/llm_prf.py src/seektalent/prompts/prf_probe_phrase_proposal.md tests/test_llm_prf.py
git commit -m "feat: prepare llm prf sources from normalized resumes"
```

---

### Task 4: Grounding, Conservative Familying, And Empty Candidate Semantics

**Files:**
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `src/seektalent/candidate_feedback/policy.py`
- Test: `tests/test_llm_prf.py`
- Test: `tests/test_candidate_feedback.py`

- [ ] **Step 1: Write failing tests for source id grounding and support eligibility**

Add to `tests/test_llm_prf.py`:

```python
def test_grounding_resolves_source_refs_by_source_text_id_and_hash() -> None:
    source = _llm_source(
        resume_id="seed-1",
        source_section="recent_experience_summary",
        text="Built LangGraph workflows.",
        support_eligible=True,
        hint_only=False,
    )
    payload = LLMPRFInput(round_no=2, seed_resume_ids=["seed-1"], source_texts=[source])
    extraction = LLMPRFExtraction(
        candidates=[
            LLMPRFCandidate(
                surface="LangGraph",
                normalized_surface="LangGraph",
                candidate_term_type="tool_or_framework",
                source_evidence_refs=[
                    LLMPRFSourceEvidenceRef(
                        resume_id="seed-1",
                        source_section="recent_experience_summary",
                        source_text_id=source.source_text_id,
                        source_text_index=99,
                        source_text_hash=source.source_text_hash,
                    )
                ],
                source_resume_ids=["seed-1"],
            )
        ]
    )

    grounding = ground_llm_prf_candidates(payload, extraction)

    assert grounding.records[0].accepted is True
    assert grounding.records[0].source_text_id == source.source_text_id
    assert grounding.records[0].support_eligible is True


def test_support_count_ignores_hint_only_records() -> None:
    source_1 = _llm_source("seed-1", "scorecard_strength", "LangGraph", support_eligible=False, hint_only=True)
    source_2 = _llm_source("seed-2", "recent_experience_summary", "LangGraph", support_eligible=True, hint_only=False)
    payload = LLMPRFInput(round_no=2, seed_resume_ids=["seed-1", "seed-2"], source_texts=[source_1, source_2])
    extraction = _llm_extraction_for_sources("LangGraph", [source_1, source_2])
    grounding = ground_llm_prf_candidates(payload, extraction)

    expressions = feedback_expressions_from_llm_grounding(payload, grounding, known_company_entities=set(), tried_term_family_ids=set())

    assert expressions[0].positive_seed_support_count == 1
    assert "insufficient_seed_support" in expressions[0].reject_reasons
```

Define test helpers in `tests/test_llm_prf.py`:

```python
def _llm_source(
    resume_id: str,
    source_section: LLMPRFSourceSection,
    text: str,
    *,
    support_eligible: bool,
    hint_only: bool,
) -> LLMPRFSourceText:
    source_text_id = build_llm_prf_source_text_id(
        resume_id=resume_id,
        source_section=source_section,
        original_field_path=f"{source_section}[0]",
        normalized_text=text,
        preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
    )
    return LLMPRFSourceText(
        resume_id=resume_id,
        source_section=source_section,
        source_text_id=source_text_id,
        source_text_index=0,
        source_text_raw=text,
        source_text_hash=text_sha256(text),
        original_field_path=f"{source_section}[0]",
        source_kind="hint_only" if hint_only else "grounding_eligible",
        support_eligible=support_eligible,
        hint_only=hint_only,
        preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
        dedupe_key=text.casefold(),
        rank_reason="test",
    )
```

- [ ] **Step 2: Write failing tests for conservative familying and empty candidate semantics**

Add:

```python
def test_conservative_familying_merges_separator_and_camelcase_variants() -> None:
    family_ids = {
        build_conservative_prf_family_id("Flink CDC"),
        build_conservative_prf_family_id("flink-cdc"),
        build_conservative_prf_family_id("FlinkCDC"),
    }

    assert family_ids == {"feedback.flinkcdc"}


def test_schema_valid_empty_candidate_list_is_successful_no_proposal() -> None:
    extraction = LLMPRFExtraction(candidates=[])

    assert extraction.candidates == []
```

Add to `tests/test_candidate_feedback.py`:

```python
def test_prf_policy_rejects_expression_with_insufficient_positive_seed_support() -> None:
    expression = FeedbackCandidateExpression(
        term_family_id="feedback.langgraph",
        canonical_expression="LangGraph",
        surface_forms=["LangGraph"],
        candidate_term_type="tool_or_framework",
        source_seed_resume_ids=["seed-1"],
        positive_seed_support_count=1,
        negative_support_count=0,
        score=10.0,
    )
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=[],
            candidate_expressions=[expression],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            min_seed_count=2,
            max_negative_support_rate=0.2,
            policy_version=PRF_POLICY_VERSION,
        )
    )

    assert decision.gate_passed is False
    assert "insufficient_seed_support" in decision.reject_reasons
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_llm_prf.py tests/test_candidate_feedback.py -k "source_text_id or hint_only or conservative_familying or empty_candidate_list or insufficient_positive_seed_support" -q
```

Expected: at least grounding/source id/familying tests fail.

- [ ] **Step 4: Implement source id grounding**

In `ground_llm_prf_candidates`, build sources by id and hash:

```python
sources_by_ref = {(source.source_text_id, source.source_text_hash): source for source in payload.source_texts}
```

Resolve each ref with:

```python
source = sources_by_ref.get((evidence_ref.source_text_id, evidence_ref.source_text_hash))
```

When creating `LLMPRFGroundingRecord`, copy:

```python
source_section=source.source_section,
source_text_id=source.source_text_id,
support_eligible=source.support_eligible,
hint_only=source.hint_only,
```

Tie-break records with `_source_section_rank(record.source_section)` instead of `_source_field_rank`.

- [ ] **Step 5: Implement support-eligible expression construction**

In `feedback_expressions_from_llm_grounding`, compute positive support only from accepted records where `record.support_eligible is True`:

```python
if record.accepted and record.support_eligible:
    support_resume_ids[family_id].add(record.resume_id)
elif record.accepted and record.hint_only:
    reject_reasons_by_family[family_id].add("hint_only_support")
```

When building `FeedbackCandidateExpression`, add:

```python
if len(seed_ids) < 2:
    reject_reasons.append("insufficient_seed_support")
```

- [ ] **Step 6: Implement conservative familying helper**

In `llm_prf.py`, implement:

```python
def build_conservative_prf_family_id(surface: str) -> str:
    normalized = unicodedata.normalize("NFKC", surface).casefold()
    normalized = re.sub(r"[\s./+_\-]+", "", normalized)
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
    return f"feedback.{normalized or 'unknown'}"
```

Use this helper for LLM PRF family ids and conflict checks. Keep `candidate_feedback.extraction.build_term_family_id` for non-LLM rescue/policy call sites unless a test proves it can be safely replaced.

- [ ] **Step 7: Ensure empty extraction is not a retry/failure**

Do not add validators that reject `LLMPRFExtraction(candidates=[])`. In runtime, keep successful call artifact status when extraction is schema-valid and candidates are empty. The second-lane failure kind for this path is:

```python
failure_kind = "no_safe_llm_prf_expression"
```

- [ ] **Step 8: Verify grounding/familying tests pass**

Run:

```bash
uv run pytest tests/test_llm_prf.py tests/test_candidate_feedback.py -k "source_text_id or hint_only or conservative_familying or empty_candidate_list or insufficient_positive_seed_support" -q
```

Expected: selected tests pass.

- [ ] **Step 9: Commit grounding and familying changes**

```bash
git add src/seektalent/candidate_feedback/llm_prf.py src/seektalent/candidate_feedback/policy.py tests/test_llm_prf.py tests/test_candidate_feedback.py
git commit -m "fix: enforce llm prf grounding support rules"
```

---

### Task 5: Runtime LLM-Only PRF Path

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/second_lane_runtime.py`
- Modify: `src/seektalent/models.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_second_lane_runtime.py`

- [ ] **Step 1: Write failing runtime tests that old backends are not selectable**

In `tests/test_runtime_state_flow.py`, add:

```python
def test_prf_selection_uses_llm_prf_without_backend_setting(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))

    assert not hasattr(runtime.settings, "prf_probe_proposal_backend")
    assert not hasattr(runtime.settings, "prf_v1_5_mode")
```

In `tests/test_second_lane_runtime.py`, remove expectations for `prf_v1_5_mode` and `shadow_prf_v1_5_artifact_ref`. Add:

```python
def test_second_lane_decision_carries_llm_prf_metadata_without_prf_v1_5_fields() -> None:
    decision, _query_state = build_second_lane_decision(
        round_no=2,
        retrieval_plan=_retrieval_plan(query_terms=["python", "agent"]),
        query_term_pool=[],
        sent_query_history=[],
        prf_decision=None,
        run_id="run",
        job_intent_fingerprint="job",
        source_plan_version="1",
        llm_prf_failure_kind="no_safe_llm_prf_expression",
        llm_prf_input_artifact_ref="round.02.retrieval.llm_prf_input",
    )

    payload = decision.model_dump(mode="json", exclude_none=True)
    assert "prf_v1_5_mode" not in payload
    assert "shadow_prf_v1_5_artifact_ref" not in payload
    assert payload["llm_prf_failure_kind"] == "no_safe_llm_prf_expression"
```

- [ ] **Step 2: Run runtime tests and verify failure**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py -k "prf_selection_uses_llm_prf or second_lane_decision_carries_llm_prf" -q
```

Expected: tests fail because old fields and backend settings still exist.

- [ ] **Step 3: Remove old fields from `SecondLaneDecision`**

In `src/seektalent/models.py`, remove:

```python
    prf_v1_5_mode: Literal["disabled", "shadow", "mainline"] | None = None
    shadow_prf_v1_5_artifact_ref: str | None = None
```

Keep `prf_probe_proposal_backend: str | None = None` only if existing reports need the constant label `llm_deepseek_v4_flash`. It must not be configurable.

- [ ] **Step 4: Simplify `build_second_lane_decision` signature**

In `src/seektalent/runtime/second_lane_runtime.py`, remove parameters:

```python
    prf_v1_5_mode: str = "disabled",
    shadow_prf_v1_5_artifact_ref: str | None = None,
```

Remove these from every `SecondLaneDecision` constructor call:

```python
                prf_v1_5_mode=prf_v1_5_mode,
                shadow_prf_v1_5_artifact_ref=shadow_prf_v1_5_artifact_ref,
```

- [ ] **Step 5: Remove backend selector from orchestrator**

In `src/seektalent/runtime/orchestrator.py`, change `_require_live_llm_config` to always include PRF proposal:

```python
            extra_stage_names = []
            if self.settings.candidate_feedback_enabled:
                extra_stage_names.append("candidate_feedback")
            extra_stage_names.append("prf_probe_phrase_proposal")
            preflight_models(self.settings, extra_stage_names=extra_stage_names)
```

In `_build_round_query_bundle`, remove parameters:

```python
        prf_v1_5_mode: str = "disabled",
        shadow_prf_v1_5_artifact_ref: str | None = None,
        prf_probe_proposal_backend: str | None = None,
```

Pass only LLM metadata to `build_second_lane_decision`.

Replace `_select_prf_backend_decision` with:

```python
    async def _select_prf_backend_decision(
        self,
        *,
        run_state: RunState,
        retrieval_plan,
        tracer: RunTracer,
    ) -> _PRFBackendSelection:
        if not self._prf_second_lane_eligible(retrieval_plan):
            return _PRFBackendSelection(prf_decision=None)
        return await self._build_llm_prf_policy_decision(
            run_state=run_state,
            retrieval_plan=retrieval_plan,
            tracer=tracer,
        )
```

Delete these methods and their helper references:

```python
_build_sidecar_prf_backend_selection
_build_prf_v1_5_proposal_and_decision
_build_prf_v1_5_metadata
_apply_embedding_response_metadata
_apply_fallback_reason
_family_to_feedback_expression
```

Remove imports for:

```python
build_embedding_similarity
build_prf_proposal_bundle
build_prf_span_extractor
build_sidecar_embedding_backend
build_sidecar_span_backend
sidecar_dependency_gate_allows_mainline
LegacyRegexSpanExtractor
PhraseFamily
ProposalMetadata
HttpEmbeddingBackend
HttpSpanModelBackend
fetch_sidecar_readyz
ReadyResponse
EmbedResponse
```

- [ ] **Step 6: Keep LLM PRF selection metadata constant**

Keep `_llm_prf_backend_selection` returning:

```python
prf_probe_proposal_backend="llm_deepseek_v4_flash"
```

This is diagnostic metadata, not a setting.

- [ ] **Step 7: Verify runtime tests pass**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py -k "llm_prf or prf_probe or second_lane_decision or candidate_feedback" -q
```

Expected: tests pass after removing old backend expectations and preserving LLM PRF behavior.

- [ ] **Step 8: Commit runtime cleanup**

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/second_lane_runtime.py src/seektalent/models.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py
git commit -m "refactor: make llm prf the only prf proposal path"
```

---

### Task 6: Historical Replay Compatibility

**Files:**
- Create: `src/seektalent/legacy_artifacts.py`
- Modify: `src/seektalent/evaluation.py`
- Modify: `src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `src/seektalent/models.py`
- Test: `tests/test_evaluation.py`
- Test: `tests/test_runtime_audit.py`

- [ ] **Step 1: Write failing historical replay parsing test**

Add to `tests/test_evaluation.py`:

```python
def test_historical_prf_sidecar_replay_metadata_is_tolerated(tmp_path: Path) -> None:
    payload = {
        "run_id": "run",
        "round_no": 2,
        "retrieval_snapshot_id": "snapshot",
        "provider_request": {},
        "provider_response_resume_ids": [],
        "provider_response_raw_rank": [],
        "dedupe_version": "v1",
        "scoring_model_version": "scoring-v1",
        "query_plan_version": "plan-v1",
        "prf_gate_version": "prf-policy-v1",
        "prf_model_backend": "http_sidecar",
        "prf_sidecar_endpoint_contract_version": "prf-sidecar-http-v1",
        "prf_sidecar_dependency_manifest_hash": "manifest-hash",
        "prf_sidecar_image_digest": "sha256:image",
        "prf_span_model_name": "fastino/gliner2-multi-v1",
        "prf_embedding_model_name": "Alibaba-NLP/gte-multilingual-base",
    }

    snapshot, legacy_metadata = parse_replay_snapshot_payload(payload)

    assert snapshot.run_id == "run"
    assert legacy_metadata.prf_model_backend == "http_sidecar"
    assert legacy_metadata.prf_sidecar_dependency_manifest_hash == "manifest-hash"
```

Import:

```python
from seektalent.evaluation import parse_replay_snapshot_payload
```

- [ ] **Step 2: Run replay compatibility test and verify failure**

Run:

```bash
uv run pytest tests/test_evaluation.py -k historical_prf_sidecar_replay_metadata_is_tolerated -q
```

Expected: test fails because `parse_replay_snapshot_payload` and legacy metadata model do not exist.

- [ ] **Step 3: Create read-only legacy artifact parser**

Create `src/seektalent/legacy_artifacts.py`:

```python
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


LEGACY_PRF_REPLAY_KEYS = {
    "prf_span_model_name",
    "prf_span_model_revision",
    "prf_span_schema_version",
    "prf_embedding_model_name",
    "prf_embedding_model_revision",
    "prf_familying_version",
    "prf_model_backend",
    "prf_sidecar_endpoint_contract_version",
    "prf_sidecar_dependency_manifest_hash",
    "prf_sidecar_image_digest",
    "prf_span_tokenizer_revision",
    "prf_embedding_dimension",
    "prf_embedding_normalized",
    "prf_embedding_dtype",
    "prf_embedding_pooling",
    "prf_embedding_truncation",
    "prf_fallback_reason",
    "prf_candidate_span_artifact_ref",
    "prf_expression_family_artifact_ref",
    "prf_policy_decision_artifact_ref",
}


class LegacyPRFReplayMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prf_span_model_name: str | None = None
    prf_span_model_revision: str | None = None
    prf_span_schema_version: str | None = None
    prf_embedding_model_name: str | None = None
    prf_embedding_model_revision: str | None = None
    prf_familying_version: str | None = None
    prf_model_backend: str | None = None
    prf_sidecar_endpoint_contract_version: str | None = None
    prf_sidecar_dependency_manifest_hash: str | None = None
    prf_sidecar_image_digest: str | None = None
    prf_span_tokenizer_revision: str | None = None
    prf_embedding_dimension: int | None = None
    prf_embedding_normalized: bool | None = None
    prf_embedding_dtype: str | None = None
    prf_embedding_pooling: str | None = None
    prf_embedding_truncation: bool | None = None
    prf_fallback_reason: str | None = None
    prf_candidate_span_artifact_ref: str | None = None
    prf_expression_family_artifact_ref: str | None = None
    prf_policy_decision_artifact_ref: str | None = None


def split_legacy_prf_replay_metadata(payload: dict[str, Any]) -> tuple[dict[str, Any], LegacyPRFReplayMetadata]:
    legacy_payload = {key: payload[key] for key in LEGACY_PRF_REPLAY_KEYS if key in payload}
    active_payload = {key: value for key, value in payload.items() if key not in LEGACY_PRF_REPLAY_KEYS}
    return active_payload, LegacyPRFReplayMetadata.model_validate(legacy_payload)
```

- [ ] **Step 4: Remove active legacy fields from `ReplaySnapshot`**

In `src/seektalent/models.py`, remove old PRF sidecar/span fields from `ReplaySnapshot`. Add a generic holder:

```python
    legacy_prf_replay_metadata: dict[str, object] = Field(default_factory=dict)
```

Keep LLM PRF fields.

- [ ] **Step 5: Add parser function in evaluation**

In `src/seektalent/evaluation.py`, add:

```python
from seektalent.legacy_artifacts import LegacyPRFReplayMetadata, split_legacy_prf_replay_metadata
```

Add:

```python
def parse_replay_snapshot_payload(payload: dict[str, object]) -> tuple[ReplaySnapshot, LegacyPRFReplayMetadata]:
    active_payload, legacy_metadata = split_legacy_prf_replay_metadata(dict(payload))
    snapshot = ReplaySnapshot.model_validate(active_payload)
    snapshot = snapshot.model_copy(
        update={"legacy_prf_replay_metadata": legacy_metadata.model_dump(mode="json", exclude_none=True)}
    )
    return snapshot, legacy_metadata
```

In `_load_replay_snapshot`, replace direct `ReplaySnapshot.model_validate(payload)` with:

```python
snapshot, _legacy_metadata = parse_replay_snapshot_payload(payload)
return snapshot
```

In `build_replay_rows`, if legacy metadata is present, merge it into the row only for export:

```python
row = {
    "run_id": snapshot.run_id,
    "round_no": snapshot.round_no,
    "retrieval_snapshot_id": snapshot.retrieval_snapshot_id,
    "second_lane_query_fingerprint": snapshot.second_lane_query_fingerprint,
    "provider_request": snapshot.provider_request,
    "provider_response_resume_ids": snapshot.provider_response_resume_ids,
    "provider_response_raw_rank": snapshot.provider_response_raw_rank,
    "dedupe_version": snapshot.dedupe_version,
    "scoring_model_version": snapshot.scoring_model_version,
    "query_plan_version": snapshot.query_plan_version,
    "prf_gate_version": snapshot.prf_gate_version,
    "generic_explore_version": snapshot.generic_explore_version,
    "prf_probe_proposal_backend": snapshot.prf_probe_proposal_backend,
    "llm_prf_extractor_version": snapshot.llm_prf_extractor_version,
    "llm_prf_grounding_validator_version": snapshot.llm_prf_grounding_validator_version,
    "llm_prf_familying_version": snapshot.llm_prf_familying_version,
    "llm_prf_model_id": snapshot.llm_prf_model_id,
    "llm_prf_protocol_family": snapshot.llm_prf_protocol_family,
    "llm_prf_endpoint_kind": snapshot.llm_prf_endpoint_kind,
    "llm_prf_endpoint_region": snapshot.llm_prf_endpoint_region,
    "llm_prf_structured_output_mode": snapshot.llm_prf_structured_output_mode,
    "llm_prf_prompt_hash": snapshot.llm_prf_prompt_hash,
    "llm_prf_output_retry_count": snapshot.llm_prf_output_retry_count,
    "llm_prf_failure_kind": snapshot.llm_prf_failure_kind,
    "llm_prf_input_artifact_ref": snapshot.llm_prf_input_artifact_ref,
    "llm_prf_call_artifact_ref": snapshot.llm_prf_call_artifact_ref,
    "llm_prf_candidates_artifact_ref": snapshot.llm_prf_candidates_artifact_ref,
    "llm_prf_grounding_artifact_ref": snapshot.llm_prf_grounding_artifact_ref,
}
if snapshot.legacy_prf_replay_metadata:
    row.update(snapshot.legacy_prf_replay_metadata)
return row
```

Do not import old runtime sidecar modules.

- [ ] **Step 6: Remove active sidecar metadata from runtime diagnostics**

In `src/seektalent/runtime/runtime_diagnostics.py`, remove the import:

```python
from seektalent.candidate_feedback.proposal_runtime import PRFProposalOutput
```

Remove the `prf_proposal` parameter from snapshot-building functions and delete the update block that reads `prf_proposal.version_vector.*`.

Keep `_validate_llm_prf_snapshot_metadata` unchanged.

- [ ] **Step 7: Verify replay compatibility tests pass**

Run:

```bash
uv run pytest tests/test_evaluation.py tests/test_runtime_audit.py -k "historical_prf_sidecar or replay or llm_prf" -q
```

Expected: tests pass after updating assertions away from active sidecar runtime metadata.

- [ ] **Step 8: Commit legacy compatibility changes**

```bash
git add src/seektalent/legacy_artifacts.py src/seektalent/evaluation.py src/seektalent/runtime/runtime_diagnostics.py src/seektalent/models.py tests/test_evaluation.py tests/test_runtime_audit.py
git commit -m "refactor: preserve historical prf metadata read-only"
```

---

### Task 7: Delete Sidecar/Span/Legacy Proposal Code

**Files:**
- Delete: `src/seektalent/prf_sidecar/`
- Delete: `src/seektalent/candidate_feedback/proposal_runtime.py`
- Delete: `src/seektalent/candidate_feedback/span_extractors.py`
- Delete: `src/seektalent/candidate_feedback/span_models.py`
- Delete: `src/seektalent/candidate_feedback/familying.py`
- Delete: `docker/prf-model-sidecar/`
- Delete tests listed in File Structure
- Modify: `pyproject.toml`
- Modify: `src/seektalent/cli.py`
- Modify: `docs/outputs.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_artifact_store.py`
- Modify: `tests/test_artifact_path_contract.py`

- [ ] **Step 1: Write import graph guard before deleting files**

Create `tests/test_prf_cleanup_import_graph.py`:

```python
from __future__ import annotations

from pathlib import Path


FORBIDDEN_TERMS = (
    "legacy_regex",
    "sidecar_span",
    "prf_v1_5_mode",
    "prf_model_backend",
    "prf_sidecar",
    "span_extractors",
    "span_models",
    "proposal_runtime",
)

ALLOWED_PATH_PARTS = {
    "docs/superpowers",
    "src/seektalent/legacy_artifacts.py",
    "tests/test_prf_cleanup_import_graph.py",
}


def _is_allowed(path: Path) -> bool:
    normalized = path.as_posix()
    return any(part in normalized for part in ALLOWED_PATH_PARTS)


def test_removed_prf_backends_are_not_imported_by_active_code() -> None:
    roots = [Path("src/seektalent"), Path("tests")]
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if _is_allowed(path):
                continue
            text = path.read_text(encoding="utf-8")
            for term in FORBIDDEN_TERMS:
                if term in text:
                    offenders.append(f"{path}:{term}")

    assert offenders == []
```

- [ ] **Step 2: Run import graph guard and verify failure**

Run:

```bash
uv run pytest tests/test_prf_cleanup_import_graph.py -q
```

Expected: test fails and lists active old PRF references.

- [ ] **Step 3: Remove sidecar CLI and pyproject surfaces**

In `pyproject.toml`, remove:

```toml
[project.optional-dependencies]
prf-sidecar = [
    "fastapi>=0.118.0",
    "gliner2>=0.2.0",
    "huggingface-hub>=0.35.3",
    "sentence-transformers>=5.1.1",
    "torch>=2.8.0",
    "transformers>=4.57.0",
    "uvicorn>=0.37.0",
]
```

Remove the script:

```toml
seektalent-prf-sidecar = "seektalent.prf_sidecar.app:main"
```

In `src/seektalent/cli.py`, delete `_prf_sidecar_prefetch_command` and the `prf-sidecar-prefetch` parser registration. Add a live validation CLI command:

```python
def _llm_prf_live_validate_command(args: argparse.Namespace) -> int:
    from seektalent.candidate_feedback.llm_prf_bakeoff import main as llm_prf_live_main

    argv = [
        "--live",
        "--case-format",
        "llm-prf-input",
        "--cases",
        str(args.cases),
        "--output-dir",
        str(args.output_dir),
        "--env-file",
        str(args.env_file),
    ]
    return llm_prf_live_main(argv)
```

Register:

```python
    live_prf_parser = subparsers.add_parser("llm-prf-live-validate")
    live_prf_parser.add_argument("--cases", type=Path, required=True)
    live_prf_parser.add_argument("--output-dir", type=Path, required=True)
    live_prf_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    live_prf_parser.set_defaults(handler=_llm_prf_live_validate_command)
```

Update `tests/test_cli.py` by deleting sidecar prefetch tests and adding:

```python
def test_llm_prf_live_validate_command_dispatches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}

    def fake_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("seektalent.candidate_feedback.llm_prf_bakeoff.main", fake_main)

    result = main(
        [
            "llm-prf-live-validate",
            "--cases",
            str(tmp_path / "cases.jsonl"),
            "--output-dir",
            str(tmp_path / "out"),
            "--env-file",
            str(tmp_path / ".env"),
        ]
    )

    assert result == 0
    assert "--live" in captured["argv"]
    assert "llm-prf-input" in captured["argv"]
```

- [ ] **Step 4: Delete old files**

Run:

```bash
git rm -r src/seektalent/prf_sidecar
git rm src/seektalent/candidate_feedback/proposal_runtime.py
git rm src/seektalent/candidate_feedback/span_extractors.py
git rm src/seektalent/candidate_feedback/span_models.py
git rm src/seektalent/candidate_feedback/familying.py
git rm -r docker/prf-model-sidecar
git rm tests/test_prf_sidecar_app.py tests/test_prf_sidecar_boundary.py tests/test_prf_sidecar_models.py tests/test_prf_sidecar_service.py
git rm tests/test_candidate_feedback_span_models.py tests/test_candidate_feedback_familying.py
```

- [ ] **Step 5: Remove old sidecar artifact registration tests and docs**

In `tests/test_artifact_store.py`, remove assertions for:

```python
"runtime.prf_sidecar_dependency_manifest"
```

In `tests/test_artifact_path_contract.py`, remove sidecar manifest stitching checks, or convert them to assert the logical name is absent from fresh manifests.

In `docs/outputs.md`, remove the active sidecar dependency manifest row and add:

```markdown
Historical PRF v1.5 sidecar metadata may still appear in old run replay exports. Current runtime no longer writes sidecar dependency manifests or active sidecar/span PRF artifacts.
```

- [ ] **Step 6: Update lockfile after pyproject cleanup**

Run:

```bash
uv lock
```

Expected: `uv.lock` updates to remove unused `prf-sidecar` optional dependency metadata if present.

- [ ] **Step 7: Verify import graph guard passes**

Run:

```bash
uv run pytest tests/test_prf_cleanup_import_graph.py -q
```

Expected: pass. Only `src/seektalent/legacy_artifacts.py`, the guard test itself, and docs may mention removed terms.

- [ ] **Step 8: Verify CLI and artifact tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_artifact_store.py tests/test_artifact_path_contract.py tests/test_prf_cleanup_import_graph.py -q
```

Expected: pass.

- [ ] **Step 9: Commit deletion cleanup**

```bash
git add pyproject.toml uv.lock src/seektalent/cli.py docs/outputs.md tests/test_cli.py tests/test_artifact_store.py tests/test_artifact_path_contract.py tests/test_prf_cleanup_import_graph.py
git add -u src/seektalent tests docker
git commit -m "refactor: delete prf sidecar proposal runtime"
```

---

### Task 8: Live Validation Harness And Fixtures

**Files:**
- Modify: `src/seektalent/candidate_feedback/llm_prf_bakeoff.py`
- Create: `tests/fixtures/llm_prf_live_validation/cases.jsonl`
- Test: `tests/test_llm_prf_bakeoff.py`

- [ ] **Step 1: Write failing fixture-format tests**

Add to `tests/test_llm_prf_bakeoff.py`:

```python
from seektalent.candidate_feedback.llm_prf import LLMPRFInput
from seektalent.candidate_feedback.llm_prf_bakeoff import (
    LLMPRFLiveValidationCase,
    LLMPRFLiveValidationResult,
    classify_live_validation_blockers,
    load_live_validation_cases,
    score_live_validation_results,
)


def test_live_validation_case_loads_llm_prf_input_format(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    payload = LLMPRFInput(round_no=2, role_title="Agent Engineer", seed_resume_ids=["seed-1", "seed-2"])
    row = {
        "case_id": "shared_langgraph",
        "expected_behavior": "should_activate",
        "input": payload.model_dump(mode="json"),
        "blocked_terms": ["阿里云"],
        "notes": "sanitized fixture",
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    cases = load_live_validation_cases(path)

    assert len(cases) == 1
    assert isinstance(cases[0].input, LLMPRFInput)
    assert cases[0].expected_behavior == "should_activate"


def test_live_validation_provider_failures_are_not_product_blockers() -> None:
    result = LLMPRFLiveValidationResult(
        case_id="case",
        expected_behavior="should_activate",
        status="provider_failed",
        provider_failure=True,
        blockers=[],
        warnings=[],
    )

    summary = score_live_validation_results([result])

    assert summary["blocker_count"] == 0
    assert summary["provider_failure_count"] == 1


def test_activation_fixture_fallback_is_blocker_when_expected() -> None:
    result = LLMPRFLiveValidationResult(
        case_id="case",
        expected_behavior="should_activate",
        status="fallback",
        fallback_reason="no_safe_llm_prf_expression",
        blockers=[],
        warnings=[],
    )

    blockers, warnings = classify_live_validation_blockers(result)

    assert "expected_activation_fell_back" in blockers
    assert warnings == []
```

- [ ] **Step 2: Run harness tests and verify failure**

Run:

```bash
uv run pytest tests/test_llm_prf_bakeoff.py -k "live_validation" -q
```

Expected: tests fail because live validation models/functions are missing.

- [ ] **Step 3: Add live validation case/result models**

In `src/seektalent/candidate_feedback/llm_prf_bakeoff.py`, add:

```python
LiveExpectedBehavior = Literal[
    "should_activate",
    "should_fallback",
    "should_reject_existing",
    "should_reject_single_seed",
    "should_handle_cjk_ascii",
]


class LLMPRFLiveValidationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    expected_behavior: LiveExpectedBehavior
    input: LLMPRFInput
    blocked_terms: list[str] = Field(default_factory=list)
    notes: str = ""


class LLMPRFLiveValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    expected_behavior: LiveExpectedBehavior
    status: Literal["passed", "fallback", "provider_failed", "schema_failed", "blocked"]
    provider_failure: bool = False
    fallback_reason: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    candidate_surfaces: list[str] = Field(default_factory=list)
    accepted_expression: str | None = None
    accepted_positive_seed_support_count: int | None = None
    accepted_negative_support_count: int | None = None
    reject_reasons: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
```

Add loader:

```python
def load_live_validation_cases(path: Path) -> list[LLMPRFLiveValidationCase]:
    cases: list[LLMPRFLiveValidationCase] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            cases.append(LLMPRFLiveValidationCase.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"invalid live validation case at {path}:{line_no}") from exc
    return cases
```

- [ ] **Step 4: Implement blocker and summary functions**

Add:

```python
def classify_live_validation_blockers(result: LLMPRFLiveValidationResult) -> tuple[list[str], list[str]]:
    blockers = list(result.blockers)
    warnings = list(result.warnings)
    if result.provider_failure:
        return blockers, warnings
    if result.expected_behavior == "should_activate" and result.accepted_expression is None:
        blockers.append("expected_activation_fell_back")
    if result.accepted_positive_seed_support_count is not None and result.accepted_positive_seed_support_count < 2:
        blockers.append("accepted_expression_insufficient_seed_support")
    if result.expected_behavior in {"should_fallback", "should_reject_existing", "should_reject_single_seed"}:
        if result.accepted_expression is not None:
            blockers.append("unexpected_accepted_expression")
    return unique_strings(blockers), unique_strings(warnings)


def score_live_validation_results(results: list[LLMPRFLiveValidationResult]) -> dict[str, object]:
    blocker_count = 0
    warning_count = 0
    provider_failure_count = 0
    schema_failure_count = 0
    fallback_count = 0
    accepted_count = 0
    latencies = [item.latency_ms for item in results if item.latency_ms is not None]
    for result in results:
        blockers, warnings = classify_live_validation_blockers(result)
        blocker_count += len(blockers)
        warning_count += len(warnings)
        provider_failure_count += int(result.provider_failure)
        schema_failure_count += int(result.status == "schema_failed")
        fallback_count += int(result.accepted_expression is None and not result.provider_failure)
        accepted_count += int(result.accepted_expression is not None)
    return {
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item.status == "passed"),
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "provider_failure_count": provider_failure_count,
        "schema_failure_count": schema_failure_count,
        "fallback_count": fallback_count,
        "accepted_count": accepted_count,
        "max_latency_ms": max(latencies) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 0.95),
    }
```

- [ ] **Step 5: Add live input format CLI branch**

Update the `main` parser to accept:

```python
parser.add_argument("--case-format", choices=["bakeoff", "llm-prf-input"], default="bakeoff")
```

In `main`, branch:

```python
if args.case_format == "llm-prf-input":
    cases = load_live_validation_cases(args.cases)
    results = run_live_validation(settings=settings, cases=cases, output_dir=args.output_dir)
    summary = score_live_validation_results(results)
    _write_jsonl(args.output_dir / "llm_prf_live_validation_results.jsonl", [item.model_dump(mode="json") for item in results])
    _write_json(args.output_dir / "llm_prf_live_validation_summary.json", summary)
    return 0 if summary["blocker_count"] == 0 and summary["provider_failure_count"] == 0 else 1
```

Use `settings.model_copy(update={"prf_probe_phrase_proposal_timeout_seconds": settings.prf_probe_phrase_proposal_live_harness_timeout_seconds})` for live validation calls.

- [ ] **Step 6: Add sanitized fixtures**

Generate `tests/fixtures/llm_prf_live_validation/cases.jsonl` with this command after Task 2 source helpers exist:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

from seektalent.candidate_feedback.llm_prf import (
    LLM_PRF_SOURCE_PREPARATION_VERSION,
    build_llm_prf_source_text_id,
    text_sha256,
)

OUT = Path("tests/fixtures/llm_prf_live_validation/cases.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)


def source(resume_id, section, index, text, field_path, *, eligible=True, hint=False):
    return {
        "resume_id": resume_id,
        "source_section": section,
        "source_text_id": build_llm_prf_source_text_id(
            resume_id=resume_id,
            source_section=section,
            original_field_path=field_path,
            normalized_text=text,
            preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
        ),
        "source_text_index": index,
        "source_text_raw": text,
        "source_text_hash": text_sha256(text),
        "original_field_path": field_path,
        "source_kind": "hint_only" if hint else "grounding_eligible",
        "support_eligible": eligible,
        "hint_only": hint,
        "preparation_version": LLM_PRF_SOURCE_PREPARATION_VERSION,
        "dedupe_key": text.casefold(),
        "rank_reason": "fixture",
    }


def input_payload(case_id, role_title, sources, *, existing=(), sent=(), tried=()):
    seed_ids = sorted({item["resume_id"] for item in sources if item["resume_id"].startswith("seed-")})
    return {
        "schema_version": "llm-prf-v1",
        "round_no": 2,
        "role_title": role_title,
        "role_summary": "",
        "must_have_capabilities": ["LangGraph", "Agent workflows"],
        "retrieval_query_terms": ["AI Agent", "Python"],
        "existing_query_terms": list(existing),
        "sent_query_terms": list(sent),
        "tried_term_family_ids": list(tried),
        "seed_resume_ids": seed_ids,
        "negative_resume_ids": [],
        "source_texts": sources,
        "negative_source_texts": [],
        "source_preparation": {
            "preparation_version": LLM_PRF_SOURCE_PREPARATION_VERSION,
            "sanitizer_version": "llm-prf-source-sanitizer-v1",
            "dropped_reason_counts": {},
        },
    }


rows = [
    {
        "case_id": "should_activate_shared_exact_phrase",
        "expected_behavior": "should_activate",
        "input": input_payload(
            "should_activate_shared_exact_phrase",
            "AI Agent Engineer",
            [
                source("seed-1", "recent_experience_summary", 0, "Built LangGraph workflows for multi-agent retrieval.", "recent_experiences[0].summary"),
                source("seed-2", "key_achievement", 0, "Delivered LangGraph orchestration for customer support agents.", "key_achievements[0]"),
            ],
            existing=("AI Agent",),
            sent=("Python",),
            tried=("feedback.aiagent", "feedback.python"),
        ),
        "blocked_terms": ["阿里云"],
        "notes": "shared exact technical phrase should be proposed and grounded",
    },
    {
        "case_id": "should_fallback_no_safe_phrase",
        "expected_behavior": "should_fallback",
        "input": input_payload(
            "should_fallback_no_safe_phrase",
            "AI Agent Engineer",
            [
                source("seed-1", "recent_experience_summary", 0, "Built LangGraph workflows for agent routing.", "recent_experiences[0].summary"),
                source("seed-2", "recent_experience_summary", 0, "Optimized PostgreSQL indexes for analytics dashboards.", "recent_experiences[0].summary"),
            ],
        ),
        "blocked_terms": [],
        "notes": "no shared safe phrase across seeds",
    },
    {
        "case_id": "should_reject_existing_query_term",
        "expected_behavior": "should_reject_existing",
        "input": input_payload(
            "should_reject_existing_query_term",
            "AI Agent Engineer",
            [
                source("seed-1", "skill", 0, "Multi-Agent", "skills[0]"),
                source("seed-2", "recent_experience_summary", 0, "Multi-Agent collaboration workflow.", "recent_experiences[0].summary"),
            ],
            existing=("Multi-Agent",),
            tried=("feedback.multiagent",),
        ),
        "blocked_terms": ["Multi-Agent"],
        "notes": "shared phrase already exists and must not be promoted",
    },
    {
        "case_id": "should_reject_single_seed_support",
        "expected_behavior": "should_reject_single_seed",
        "input": input_payload(
            "should_reject_single_seed_support",
            "AI Agent Engineer",
            [
                source("seed-1", "recent_experience_summary", 0, "Implemented ReAct planning for support agents.", "recent_experiences[0].summary"),
                source("seed-2", "recent_experience_summary", 0, "Built evaluation dashboards for agent quality.", "recent_experiences[0].summary"),
            ],
        ),
        "blocked_terms": ["ReAct"],
        "notes": "ReAct appears in only one support-eligible seed",
    },
    {
        "case_id": "should_handle_cjk_ascii_boundaries",
        "expected_behavior": "should_handle_cjk_ascii",
        "input": input_payload(
            "should_handle_cjk_ascii_boundaries",
            "AI Agent Engineer",
            [
                source("seed-1", "recent_experience_summary", 0, "使用Langgraph框架构建Agent工作流", "recent_experiences[0].summary"),
                source("seed-2", "key_achievement", 0, "落地Langgraph框架的多Agent协作", "key_achievements[0]"),
            ],
        ),
        "blocked_terms": [],
        "notes": "mixed CJK/ASCII surfaces should ground through deterministic boundaries",
    },
]

OUT.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")
PY
```

- [ ] **Step 7: Verify harness tests pass**

Run:

```bash
uv run pytest tests/test_llm_prf_bakeoff.py -q
```

Expected: pass.

- [ ] **Step 8: Commit live validation harness**

```bash
git add src/seektalent/candidate_feedback/llm_prf_bakeoff.py tests/test_llm_prf_bakeoff.py tests/fixtures/llm_prf_live_validation/cases.jsonl
git commit -m "feat: add llm prf live validation fixtures"
```

---

### Task 9: Final Regression And Manual Validation Gate

**Files:**
- No new source files unless previous tasks reveal a focused missing test.

- [ ] **Step 1: Run focused regression**

Run:

```bash
uv run pytest tests/test_llm_prf.py tests/test_candidate_feedback.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py tests/test_llm_provider_config.py tests/test_llm_prf_bakeoff.py tests/test_evaluation.py tests/test_prf_cleanup_import_graph.py -q
```

Expected: pass.

- [ ] **Step 2: Run lint**

Run:

```bash
uv run ruff check src tests
```

Expected: `All checks passed!`

- [ ] **Step 3: Run active import graph guard**

Run:

```bash
uv run pytest tests/test_prf_cleanup_import_graph.py -q
```

Expected: pass. Any offender outside `src/seektalent/legacy_artifacts.py`, docs, or the guard test is a blocker.

- [ ] **Step 4: Run manual live validation with real provider**

Run only after `.env` has valid Bailian/DeepSeek credentials and stale PRF keys have been removed:

```bash
uv run seektalent llm-prf-live-validate \
  --cases tests/fixtures/llm_prf_live_validation/cases.jsonl \
  --env-file /Users/frankqdwang/Agents/SeekTalent-0.2.4/.env \
  --output-dir artifacts/manual/llm-prf-live-validation-0.6.2
```

Expected:

- command exits `0`;
- `llm_prf_live_validation_summary.json` has `blocker_count == 0`;
- `provider_failure_count == 0`;
- activation fixture either accepts a grounded phrase or reports a fixture-defined blocker for review.

- [ ] **Step 5: Stop before 12-JD benchmark if product tradeoff appears**

If live validation shows repeated activation fallback, company/entity leakage, too many schema failures, or accepted phrases that look semantically wrong despite passing deterministic checks, stop and summarize the artifact paths for product review.

- [ ] **Step 6: Run one full JD smoke with eval disabled**

Use the existing benchmark command for one JD and set eval disabled through env/config. Use version `0.6.2` in output directory naming. Do not run all 12 JDs in this task.

Expected:

- run completes;
- round 2+ writes `llm_prf_input`, `llm_prf_call`, `llm_prf_candidates`, `llm_prf_grounding`, `prf_policy_decision`, and `second_lane_decision`;
- if PRF does not activate, `second_lane_decision` explains deterministic fallback.

- [ ] **Step 7: Commit final test/docs adjustments**

If Step 1-6 required test or doc tweaks, commit them:

```bash
git add src tests docs .env.example src/seektalent/default.env pyproject.toml uv.lock
git commit -m "test: verify llm prf cleanup regression"
```

If no files changed after verification, skip this commit.

---

## Self-Review Checklist

- Spec coverage:
  - LLM-only active PRF path: Tasks 1, 5, 7.
  - Old sidecar/span/legacy cleanup: Tasks 1, 5, 7.
  - Historical read compatibility: Task 6.
  - `source_section`, `source_text_id`, `support_eligible`, `hint_only`: Tasks 2, 3, 4.
  - Sanitizer and source prep metadata: Task 3.
  - 3s runtime timeout and 30s live harness timeout: Tasks 1, 8.
  - `candidates=[]` success semantics: Task 4.
  - Conservative familying: Task 4.
  - Stale env scanner: Task 1.
  - Live validation harness: Task 8.
  - Final validation before 12-JD benchmark: Task 9.

- Stand-in text scan:
  - No stand-in markers or open-ended delayed implementation steps are used.
  - Every task lists exact files and exact commands.
  - Code-bearing steps include concrete snippets.

- Type consistency:
  - `LLMPRFSourceSection`, `source_text_id`, `support_eligible`, `hint_only`, and `LLM_PRF_SOURCE_PREPARATION_VERSION` are introduced before later tasks use them.
  - `CandidateTermType` is moved before `span_models.py` is deleted.
  - `LegacyPRFReplayMetadata` exists before old replay fields are removed from active models.
