from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Any, Literal, Mapping, cast

from pydantic_ai import Agent, PromptedOutput
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from seektalent.candidate_feedback.extraction import classify_feedback_expressions
from seektalent.candidate_feedback.models import CandidateTermType, FeedbackCandidateExpression
from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec, resolve_stage_model_config
from seektalent.models import NormalizedResume, ScoredCandidate, unique_strings
from seektalent.prompting import LoadedPrompt
from seektalent.tracing import ProviderUsageSnapshot, provider_usage_from_result

LLM_PRF_SCHEMA_VERSION = "llm-prf-v1"
LLM_PRF_EXTRACTOR_VERSION = "llm-prf-deepseek-v4-flash-v1"
GROUNDING_VALIDATOR_VERSION = "llm-prf-grounding-v1"
LLM_PRF_FAMILYING_VERSION = "llm-prf-conservative-surface-family-v1"
LLM_PRF_OUTPUT_RETRIES = 2
LLM_PRF_TOP_N_CANDIDATE_CAP = 4
LLM_PRF_STAGE = "prf_probe_phrase_proposal"
LLM_PRF_MAX_SOURCE_TEXTS_PER_SEED_RESUME = 4
LLM_PRF_MAX_SOURCE_TEXTS_PER_NEGATIVE_RESUME = 2
LLM_PRF_MAX_SOURCE_TEXT_CHARS = 260
LLM_PRF_SOURCE_PREPARATION_VERSION = "llm-prf-source-prep-v1"

LLMPRFSourceKind = Literal["grounding_eligible", "hint_only"]
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
LLMPRFFailureKind = Literal[
    "timeout",
    "transport_error",
    "provider_error",
    "response_validation_error",
    "structured_output_parse_error",
    "insufficient_prf_seed_support",
    "settings_migration_error",
    "unsupported_capability",
]

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
_UNSAFE_SUBSTRING_PAIRS = (
    ("Java", "JavaScript"),
    ("React", "React Native"),
    ("阿里", "阿里云"),
)
_METADATA_ONLY_RE = re.compile(
    r"^(?:[\u4e00-\u9fffA-Za-z0-9&.\- ]{1,24})?(?:北京|上海|广州|深圳|杭州|成都|南京|苏州|武汉|西安|团队|部门|高级工程师|工程师|经理|总监|本科|硕士|博士|大学|学院)[\u4e00-\u9fffA-Za-z0-9&.\- ]{0,24}$",
    re.IGNORECASE,
)
_CAPABILITY_CONTEXT_RE = re.compile(
    r"(?:使用|基于|构建|开发|落地|负责|built|used|using|developed|implemented|deployed|workflow|pipeline|agent|retrieval|系统|平台)",
    re.IGNORECASE,
)


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
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class LLMPRFSourceText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_section: LLMPRFSourceSection
    source_text_id: str = Field(min_length=1)
    source_text_index: int = Field(ge=0)
    source_text_raw: str = Field(min_length=1)
    source_text_hash: str = Field(min_length=1)
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

    @model_validator(mode="after")
    def _validate_support_flags(self) -> LLMPRFSourceText:
        if self.source_kind == "grounding_eligible" and (not self.support_eligible or self.hint_only):
            raise ValueError("grounding_eligible sources must be support eligible and not hint only")
        if self.source_kind == "hint_only" and (self.support_eligible or not self.hint_only):
            raise ValueError("hint_only sources must be hint only and not support eligible")
        return self


class LLMPRFInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["llm-prf-v1"] = LLM_PRF_SCHEMA_VERSION
    round_no: int = 0
    role_title: str = ""
    role_summary: str = ""
    must_have_capabilities: list[str] = Field(default_factory=list)
    retrieval_query_terms: list[str] = Field(default_factory=list)
    existing_query_terms: list[str] = Field(default_factory=list)
    sent_query_terms: list[str] = Field(default_factory=list)
    tried_term_family_ids: list[str] = Field(default_factory=list)
    seed_resume_ids: list[str] = Field(default_factory=list)
    negative_resume_ids: list[str] = Field(default_factory=list)
    source_texts: list[LLMPRFSourceText] = Field(default_factory=list)
    negative_source_texts: list[LLMPRFSourceText] = Field(default_factory=list)
    source_preparation: dict[str, object] = Field(default_factory=dict)


class LLMPRFSourceEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_section: LLMPRFSourceSection
    source_text_id: str = Field(min_length=1)
    source_text_index: int = Field(ge=0)
    source_text_hash: str = Field(min_length=1)


class LLMPRFCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str = Field(min_length=1, max_length=80)
    normalized_surface: str = Field(min_length=1, max_length=80)
    candidate_term_type: CandidateTermType = "unknown"
    source_evidence_refs: list[LLMPRFSourceEvidenceRef] = Field(default_factory=list, max_length=8)
    source_resume_ids: list[str] = Field(default_factory=list, max_length=8)
    linked_requirements: list[str] = Field(default_factory=list, max_length=4)
    rationale: str = Field(default="", max_length=120)
    risk_flags: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("surface", "normalized_surface")
    @classmethod
    def _reject_normalization_empty_surface(cls, value: str) -> str:
        if not _normalize_surface(value):
            raise ValueError("surface must not normalize to empty")
        return value


class LLMPRFExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["llm-prf-v1"] = LLM_PRF_SCHEMA_VERSION
    extractor_version: str = LLM_PRF_EXTRACTOR_VERSION
    candidates: list[LLMPRFCandidate] = Field(default_factory=list, max_length=LLM_PRF_TOP_N_CANDIDATE_CAP)


class LLMPRFGroundingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str = Field(min_length=1)
    normalized_surface: str = Field(min_length=1)
    advisory_candidate_term_type: CandidateTermType
    accepted: bool
    reject_reasons: list[str] = Field(default_factory=list)
    resume_id: str
    source_section: LLMPRFSourceSection
    source_text_id: str
    source_text_index: int = Field(ge=0)
    source_text_hash: str
    support_eligible: bool = False
    hint_only: bool = False
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, gt=0)
    raw_surface: str = ""


class LLMPRFGroundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["llm-prf-v1"] = LLM_PRF_SCHEMA_VERSION
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


class LLMPRFExtractor:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.last_provider_usage: ProviderUsageSnapshot | None = None

    async def propose(self, payload: LLMPRFInput) -> LLMPRFExtraction:
        result = await self._build_agent().run(render_llm_prf_prompt(payload))
        self.last_provider_usage = provider_usage_from_result(result)
        return cast(LLMPRFExtraction, result.output)

    def _build_agent(self) -> Agent[None, LLMPRFExtraction]:
        config = resolve_stage_model_config(self.settings, stage=LLM_PRF_STAGE)
        model = build_model(config, provider_max_retries=0)
        output_spec = build_output_spec(config, model, LLMPRFExtraction)
        if not isinstance(output_spec, PromptedOutput):
            raise ValueError(f"{LLM_PRF_STAGE} must use PromptedOutput for prompted JSON extraction.")
        model_settings = dict(build_model_settings(config))
        model_settings["temperature"] = 0
        model_settings["max_tokens"] = self.settings.prf_probe_phrase_proposal_max_output_tokens
        return cast(
            "Agent[None, LLMPRFExtraction]",
            Agent(
                model=model,
                output_type=output_spec,
                system_prompt=self.prompt.content,
                model_settings=model_settings,
                retries=0,
                output_retries=LLM_PRF_OUTPUT_RETRIES,
            ),
        )


def render_llm_prf_prompt(payload: LLMPRFInput) -> str:
    payload_json = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "Return valid json for this PRF phrase proposal payload.\n"
        "Hard constraints:\n"
        "- Return at most 4 candidates.\n"
        "- Only include candidates supported by at least two distinct fit seed resumes.\n"
        "- candidate.surface must be copied exactly from every referenced source_text_raw.\n"
        "- Do not propose existing_query_terms, sent_query_terms, or tried_term_family_ids.\n"
        "- Do not synthesize descriptive phrases; use the shortest exact shared phrase.\n"
        "- Prefer an empty candidates list over ungrounded or generic candidates.\n"
        f"Payload:\n{payload_json}"
    )


def build_llm_prf_success_call_artifact(
    *,
    settings: AppSettings,
    payload: LLMPRFInput,
    user_prompt_text: str,
    extraction: LLMPRFExtraction,
    started_at: str,
    latency_ms: int | None,
    round_no: int,
    provider_usage: ProviderUsageSnapshot | dict[str, Any] | None,
) -> dict[str, Any]:
    artifact = _base_llm_prf_call_artifact(
        settings=settings,
        payload=payload,
        user_prompt_text=user_prompt_text,
        started_at=started_at,
        latency_ms=latency_ms,
        round_no=round_no,
        status="succeeded",
    )
    artifact["structured_output"] = extraction.model_dump(mode="json")
    artifact["provider_usage"] = _provider_usage_payload(provider_usage)
    return artifact


def build_llm_prf_failure_call_artifact(
    *,
    settings: AppSettings,
    payload: LLMPRFInput,
    user_prompt_text: str,
    started_at: str,
    latency_ms: int | None,
    round_no: int,
    failure_kind: LLMPRFFailureKind,
    error_message: str,
    provider_usage: ProviderUsageSnapshot | dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = _base_llm_prf_call_artifact(
        settings=settings,
        payload=payload,
        user_prompt_text=user_prompt_text,
        started_at=started_at,
        latency_ms=latency_ms,
        round_no=round_no,
        status="failed",
    )
    artifact["structured_output"] = None
    artifact["failure_kind"] = failure_kind
    artifact["error_message"] = _redact_known_secret(error_message, settings.text_llm_api_key)
    artifact["provider_usage"] = _provider_usage_payload(provider_usage)
    return artifact


def build_llm_prf_artifact_refs(*, round_no: int) -> LLMPRFArtifactRefs:
    prefix = f"round.{round_no:02d}.retrieval"
    return LLMPRFArtifactRefs(
        input_artifact_ref=f"{prefix}.llm_prf_input",
        call_artifact_ref=f"{prefix}.llm_prf_call",
        candidates_artifact_ref=f"{prefix}.llm_prf_candidates",
        grounding_artifact_ref=f"{prefix}.llm_prf_grounding",
        policy_decision_artifact_ref=f"{prefix}.prf_policy_decision",
    )


def select_llm_prf_negative_resumes(candidates: list[ScoredCandidate], *, limit: int = 5) -> list[ScoredCandidate]:
    selected = [candidate for candidate in candidates if candidate.fit_bucket != "fit" or candidate.risk_score >= 60]
    selected.sort(key=lambda candidate: (-candidate.risk_score, candidate.overall_score, candidate.resume_id))
    return selected[: min(limit, 5)]


def build_llm_prf_input(
    *,
    seed_resumes: list[ScoredCandidate],
    negative_resumes: list[ScoredCandidate],
    round_no: int = 0,
    role_title: str = "",
    role_summary: str = "",
    must_have_capabilities: list[str] | None = None,
    retrieval_query_terms: list[str] | None = None,
    existing_query_terms: list[str] | None = None,
    sent_query_terms: list[str] | None = None,
    tried_term_family_ids: list[str] | None = None,
    normalized_resumes_by_id: Mapping[str, NormalizedResume] | None = None,
) -> LLMPRFInput | None:
    if len(seed_resumes) < 2:
        return None
    dropped_reason_counts: Counter[str] = Counter()
    signal_terms = _llm_prf_signal_terms(
        role_title=role_title,
        role_summary=role_summary,
        must_have_capabilities=must_have_capabilities or [],
        retrieval_query_terms=retrieval_query_terms or [],
        existing_query_terms=existing_query_terms or [],
    )
    source_texts = _source_texts_from_resumes(
        seed_resumes,
        normalized_resumes_by_id=normalized_resumes_by_id,
        signal_terms=signal_terms,
        per_normalized_resume_limit=LLM_PRF_MAX_SOURCE_TEXTS_PER_SEED_RESUME,
        dropped_reason_counts=dropped_reason_counts,
    )
    negative_source_texts = _source_texts_from_resumes(
        negative_resumes,
        normalized_resumes_by_id=normalized_resumes_by_id,
        signal_terms=signal_terms,
        per_normalized_resume_limit=LLM_PRF_MAX_SOURCE_TEXTS_PER_NEGATIVE_RESUME,
        dropped_reason_counts=dropped_reason_counts,
    )
    return LLMPRFInput(
        round_no=round_no,
        role_title=role_title,
        role_summary=role_summary,
        must_have_capabilities=list(must_have_capabilities or []),
        retrieval_query_terms=list(retrieval_query_terms or []),
        existing_query_terms=list(existing_query_terms or []),
        sent_query_terms=list(sent_query_terms or []),
        tried_term_family_ids=list(tried_term_family_ids or []),
        seed_resume_ids=[resume.resume_id for resume in seed_resumes],
        negative_resume_ids=[resume.resume_id for resume in negative_resumes],
        source_texts=source_texts,
        negative_source_texts=negative_source_texts,
        source_preparation={
            "preparation_version": LLM_PRF_SOURCE_PREPARATION_VERSION,
            "sanitizer_version": "llm-prf-source-sanitizer-v1",
            "dropped_reason_counts": dict(dropped_reason_counts),
        },
    )


def ground_llm_prf_candidates(payload: LLMPRFInput, extraction: LLMPRFExtraction) -> LLMPRFGroundingResult:
    sources_by_ref = {
        (source.resume_id, source.source_section, source.source_text_id): source for source in payload.source_texts
    }
    records: list[LLMPRFGroundingRecord] = []

    for candidate in extraction.candidates:
        for evidence_ref in candidate.source_evidence_refs:
            source = sources_by_ref.get(
                (evidence_ref.resume_id, evidence_ref.source_section, evidence_ref.source_text_id)
            )
            if source is None:
                records.append(
                    LLMPRFGroundingRecord(
                        surface=candidate.surface,
                        normalized_surface=_normalize_surface(candidate.surface),
                        advisory_candidate_term_type=candidate.candidate_term_type,
                        accepted=False,
                        reject_reasons=["source_reference_not_found"],
                        resume_id=evidence_ref.resume_id,
                        source_section=evidence_ref.source_section,
                        source_text_id=evidence_ref.source_text_id,
                        source_text_index=evidence_ref.source_text_index,
                        source_text_hash=evidence_ref.source_text_hash,
                    )
                )
                continue
            if evidence_ref.source_text_hash != source.source_text_hash:
                records.append(
                    LLMPRFGroundingRecord(
                        surface=candidate.surface,
                        normalized_surface=_normalize_surface(candidate.surface),
                        advisory_candidate_term_type=candidate.candidate_term_type,
                        accepted=False,
                        reject_reasons=["source_hash_mismatch"],
                        resume_id=source.resume_id,
                        source_section=source.source_section,
                        source_text_id=source.source_text_id,
                        source_text_index=source.source_text_index,
                        source_text_hash=evidence_ref.source_text_hash,
                        support_eligible=source.support_eligible,
                        hint_only=source.hint_only,
                    )
                )
                continue
            if evidence_ref.source_text_index != source.source_text_index:
                records.append(
                    LLMPRFGroundingRecord(
                        surface=candidate.surface,
                        normalized_surface=_normalize_surface(candidate.surface),
                        advisory_candidate_term_type=candidate.candidate_term_type,
                        accepted=False,
                        reject_reasons=["source_index_mismatch"],
                        resume_id=source.resume_id,
                        source_section=source.source_section,
                        source_text_id=source.source_text_id,
                        source_text_index=evidence_ref.source_text_index,
                        source_text_hash=evidence_ref.source_text_hash,
                        support_eligible=source.support_eligible,
                        hint_only=source.hint_only,
                    )
                )
                continue

            record = _ground_surface(candidate=candidate, source=source)
            records.append(record)

    records.sort(
        key=lambda record: (
            _source_section_rank(record.source_section),
            record.source_text_index,
            record.start_char if record.start_char is not None else 10**9,
            record.resume_id,
        )
    )
    return LLMPRFGroundingResult(records=records)


def build_conservative_prf_family_id(surface: str) -> str:
    collapsed = _collapse_family_surface(surface)
    return f"feedback.{collapsed or 'unknown'}"


def feedback_expressions_from_llm_grounding(
    payload: LLMPRFInput,
    grounding: LLMPRFGroundingResult,
    *,
    known_company_entities: set[str],
    tried_term_family_ids: set[str],
) -> list[FeedbackCandidateExpression]:
    field_hits: dict[str, Counter[str]] = defaultdict(Counter)
    seed_support: dict[str, set[str]] = defaultdict(set)
    surfaces: dict[str, set[str]] = defaultdict(set)
    canonical: dict[str, str] = {}
    support_eligible_by_ref = {
        (source.resume_id, source.source_text_id, source.source_text_index, source.source_text_hash): source.support_eligible
        for source in payload.source_texts
    }
    conflicting_family_ids = _conflicting_prf_family_ids(payload, tried_term_family_ids)

    for record in grounding.records:
        if not record.accepted:
            continue
        family_id = build_conservative_prf_family_id(record.normalized_surface or record.surface)
        canonical.setdefault(family_id, record.normalized_surface or record.surface)
        surfaces[family_id].add(record.raw_surface or record.surface)
        field_hits[family_id][record.source_section] += 1
        support_eligible = support_eligible_by_ref.get(
            (record.resume_id, record.source_text_id, record.source_text_index, record.source_text_hash),
            record.support_eligible,
        )
        if support_eligible:
            seed_support[family_id].add(record.resume_id)

    expressions: list[FeedbackCandidateExpression] = []
    for family_id, expression in canonical.items():
        classification = classify_feedback_expressions(
            [expression],
            known_company_entities=known_company_entities,
            known_product_platforms=set(),
        )[0]
        candidate_term_type: CandidateTermType = classification.candidate_term_type
        reject_reasons = _normalize_reject_reasons(classification.reject_reasons)
        if _is_ambiguous_company_or_product(expression):
            candidate_term_type = "company_entity"
            reject_reasons = unique_strings([*reject_reasons, "ambiguous_company_or_product_entity", "company_entity_rejected"])
        if family_id in conflicting_family_ids:
            reject_reasons = unique_strings([*reject_reasons, "existing_or_tried_family"])

        seed_ids = sorted(seed_support.get(family_id, set()))
        negative_ids = _negative_support_resume_ids(payload.negative_source_texts, family_id)
        score = float(len(seed_ids) * 4 - len(negative_ids) * 4)
        expressions.append(
            FeedbackCandidateExpression(
                term_family_id=family_id,
                canonical_expression=expression,
                surface_forms=sorted(surfaces.get(family_id, {expression}), key=str.casefold),
                candidate_term_type=candidate_term_type,
                source_seed_resume_ids=seed_ids,
                linked_requirements=[],
                field_hits=dict(field_hits.get(family_id, {})),
                positive_seed_support_count=len(seed_ids),
                negative_support_count=len(negative_ids),
                fit_support_rate=len(seed_ids) / len(payload.seed_resume_ids) if payload.seed_resume_ids else 0.0,
                not_fit_support_rate=len(negative_ids) / len(payload.negative_resume_ids) if payload.negative_resume_ids else 0.0,
                tried_query_fingerprints=[],
                score=score,
                reject_reasons=reject_reasons,
            )
        )

    expressions.sort(key=lambda item: (-item.score, -item.positive_seed_support_count, item.canonical_expression.casefold()))
    return expressions


def _conflicting_prf_family_ids(payload: LLMPRFInput, tried_term_family_ids: set[str]) -> set[str]:
    family_ids = {_normalize_prf_family_id(family_id) for family_id in tried_term_family_ids}
    family_ids.update(build_conservative_prf_family_id(term) for term in payload.existing_query_terms)
    family_ids.update(build_conservative_prf_family_id(term) for term in payload.sent_query_terms)
    return {family_id for family_id in family_ids if family_id}


def _normalize_prf_family_id(family_id: str) -> str:
    raw = family_id.removeprefix("feedback.")
    return build_conservative_prf_family_id(raw)


def _source_texts_from_resumes(
    resumes: list[ScoredCandidate],
    *,
    normalized_resumes_by_id: Mapping[str, NormalizedResume] | None = None,
    signal_terms: list[str] | None = None,
    per_normalized_resume_limit: int = LLM_PRF_MAX_SOURCE_TEXTS_PER_SEED_RESUME,
    dropped_reason_counts: Counter[str],
) -> list[LLMPRFSourceText]:
    source_texts: list[LLMPRFSourceText] = []
    for resume in resumes:
        normalized = normalized_resumes_by_id.get(resume.resume_id) if normalized_resumes_by_id is not None else None
        if normalized is not None:
            normalized_source_texts = _source_texts_from_normalized_resume(
                resume_id=resume.resume_id,
                resume=normalized,
                signal_terms=signal_terms or [],
                limit=per_normalized_resume_limit,
                dropped_reason_counts=dropped_reason_counts,
            )
            if normalized_source_texts:
                source_texts.extend(normalized_source_texts)
                continue
        source_texts.extend(_source_texts_from_scorecard(resume, dropped_reason_counts=dropped_reason_counts))
    return source_texts


def _source_texts_from_scorecard(
    resume: ScoredCandidate,
    *,
    dropped_reason_counts: Counter[str],
) -> list[LLMPRFSourceText]:
    source_texts: list[LLMPRFSourceText] = []
    scorecard_section_map: dict[str, tuple[LLMPRFSourceSection, bool, bool]] = {
        "evidence": ("scorecard_evidence", True, False),
        "matched_must_haves": ("scorecard_matched_must_have", True, False),
        "matched_preferences": ("scorecard_matched_preference", True, False),
        "strengths": ("scorecard_strength", False, True),
    }
    scorecard_values = {
        "evidence": resume.evidence,
        "matched_must_haves": resume.matched_must_haves,
        "matched_preferences": resume.matched_preferences,
        "strengths": resume.strengths,
    }
    section_counts: Counter[LLMPRFSourceSection] = Counter()
    seen: set[str] = set()
    for field_name, (source_section, support_eligible, hint_only) in scorecard_section_map.items():
        for raw_index, text in enumerate(scorecard_values[field_name]):
            try:
                source_text = _make_llm_prf_source_text(
                    resume_id=resume.resume_id,
                    source_section=source_section,
                    source_text_index=section_counts[source_section],
                    text=text,
                    original_field_path=f"{field_name}[{raw_index}]",
                    support_eligible=support_eligible,
                    hint_only=hint_only,
                    rank_reason=f"scorecard:{field_name}",
                )
            except ValueError as exc:
                dropped_reason_counts[str(exc)] += 1
                continue
            if not source_text.dedupe_key or source_text.dedupe_key in seen:
                continue
            seen.add(source_text.dedupe_key)
            source_texts.append(source_text)
            section_counts[source_section] += 1
    return source_texts


def _source_texts_from_normalized_resume(
    *,
    resume_id: str,
    resume: NormalizedResume,
    signal_terms: list[str],
    limit: int,
    dropped_reason_counts: Counter[str],
) -> list[LLMPRFSourceText]:
    ranked: list[tuple[float, int, int, LLMPRFSourceSection, str, str]] = []
    order = 0
    for priority, source_section, original_field_path, text in _normalized_resume_text_sources(resume):
        for snippet in _resume_source_snippets(text):
            ranked.append(
                (
                    _source_text_score(_normalize_source_text(snippet), signal_terms),
                    priority,
                    order,
                    source_section,
                    original_field_path,
                    snippet,
                )
            )
            order += 1

    source_texts: list[LLMPRFSourceText] = []
    section_counts: Counter[LLMPRFSourceSection] = Counter()
    seen: set[str] = set()
    for _score, _priority, _order, source_section, original_field_path, snippet in sorted(
        ranked,
        key=lambda item: (-item[0], item[1], item[2]),
    ):
        try:
            source_text = _make_llm_prf_source_text(
                resume_id=resume_id,
                source_section=source_section,
                source_text_index=section_counts[source_section],
                text=snippet,
                original_field_path=original_field_path,
                support_eligible=True,
                hint_only=False,
                rank_reason="normalized_resume",
            )
        except ValueError as exc:
            dropped_reason_counts[str(exc)] += 1
            continue
        if not source_text.dedupe_key or source_text.dedupe_key in seen:
            continue
        seen.add(source_text.dedupe_key)
        source_texts.append(source_text)
        section_counts[source_section] += 1
        if len(source_texts) >= limit:
            break
    return source_texts


def _normalized_resume_text_sources(resume: NormalizedResume) -> list[tuple[int, LLMPRFSourceSection, str, str]]:
    sources: list[tuple[int, LLMPRFSourceSection, str, str]] = []
    sources.extend((0, "key_achievement", f"key_achievements[{index}]", text) for index, text in enumerate(resume.key_achievements))
    sources.extend(
        (1, "recent_experience_summary", f"recent_experiences[{index}].summary", item.summary)
        for index, item in enumerate(resume.recent_experiences)
    )
    if resume.raw_text_excerpt:
        sources.append((2, "raw_text_excerpt", "raw_text_excerpt", resume.raw_text_excerpt))
    sources.extend((3, "skill", f"skills[{index}]", skill) for index, skill in enumerate(resume.skills))
    return sources


def _resume_source_snippets(text: str) -> list[str]:
    text = _normalize_source_text(text)
    if not text:
        return []
    pieces = [
        _normalize_source_text(piece)
        for piece in re.split(r"(?:\n+|[。；;]|[●•·])", text.replace("\r", "\n"))
    ]
    snippets: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        if len(piece) <= LLM_PRF_MAX_SOURCE_TEXT_CHARS:
            snippets.append(piece)
            continue
        snippets.extend(_split_long_source_piece(piece))
    return snippets


def _split_long_source_piece(text: str) -> list[str]:
    parts = [_normalize_source_text(part) for part in re.split(r"[，,]", text)]
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        candidate = f"{current}，{part}" if current else part
        if len(candidate) <= LLM_PRF_MAX_SOURCE_TEXT_CHARS:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = part[:LLM_PRF_MAX_SOURCE_TEXT_CHARS].rstrip()
    if current:
        chunks.append(current)
    return chunks


def _source_text_score(text: str, signal_terms: list[str]) -> float:
    haystack = _search_key(text)
    score = 1.0 if len(text) <= 80 else 0.3
    for term in signal_terms:
        if term and term in haystack:
            score += 10 + min(len(term), 20)
            if haystack == term:
                score += 20
    return score


def _llm_prf_signal_terms(
    *,
    role_title: str,
    role_summary: str,
    must_have_capabilities: list[str],
    retrieval_query_terms: list[str],
    existing_query_terms: list[str],
) -> list[str]:
    raw_terms = [
        role_title,
        role_summary,
        *must_have_capabilities,
        *retrieval_query_terms,
        *existing_query_terms,
    ]
    terms: list[str] = []
    for raw_term in raw_terms:
        normalized = _search_key(raw_term)
        if 2 <= len(normalized) <= 80:
            terms.append(normalized)
        terms.extend(token for token in _family_keys_in_text(raw_term) if 2 <= len(token) <= 40)
    return unique_strings(terms)


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
    normalized_text, dropped_reason = _sanitize_llm_prf_source_text(text)
    if normalized_text is None:
        raise ValueError(dropped_reason or "unknown")
    source_kind: LLMPRFSourceKind = "grounding_eligible" if support_eligible else "hint_only"
    return LLMPRFSourceText(
        resume_id=resume_id,
        source_section=source_section,
        source_text_id=build_llm_prf_source_text_id(
            resume_id=resume_id,
            source_section=source_section,
            original_field_path=original_field_path,
            normalized_text=normalized_text,
            preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
        ),
        source_text_index=source_text_index,
        source_text_raw=normalized_text,
        source_text_hash=text_sha256(normalized_text),
        original_field_path=original_field_path,
        source_kind=source_kind,
        support_eligible=support_eligible,
        hint_only=hint_only,
        preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
        dedupe_key=_normalize_surface(normalized_text).casefold(),
        rank_reason=rank_reason,
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


def _normalize_source_snippet(text: str) -> str:
    return _normalize_source_text(text)


def _normalize_source_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _search_key(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _base_llm_prf_call_artifact(
    *,
    settings: AppSettings,
    payload: LLMPRFInput,
    user_prompt_text: str,
    started_at: str,
    latency_ms: int | None,
    round_no: int,
    status: Literal["succeeded", "failed"],
) -> dict[str, Any]:
    config = resolve_stage_model_config(settings, stage=LLM_PRF_STAGE)
    return {
        "stage": LLM_PRF_STAGE,
        "call_id": f"llm-prf-{round_no:02d}",
        "model_id": config.model_id,
        "prompt_name": LLM_PRF_STAGE,
        "user_payload": payload.model_dump(mode="json"),
        "user_prompt_text": user_prompt_text,
        "started_at": started_at,
        "latency_ms": latency_ms,
        "status": status,
        "retries": 0,
        "output_retries": LLM_PRF_OUTPUT_RETRIES,
        "validator_retry_count": 0,
        "validator_retry_reasons": [],
    }


def _provider_usage_payload(provider_usage: ProviderUsageSnapshot | dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(provider_usage, ProviderUsageSnapshot):
        return provider_usage.model_dump(mode="json")
    if provider_usage is None:
        return None
    return dict(provider_usage)


def _redact_known_secret(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, "[redacted]")


def _ground_surface(*, candidate: LLMPRFCandidate, source: LLMPRFSourceText) -> LLMPRFGroundingRecord:
    match = _find_raw_match(source.source_text_raw, candidate.surface)
    if match is None:
        return LLMPRFGroundingRecord(
            surface=candidate.surface,
            normalized_surface=_normalize_surface(candidate.surface),
            advisory_candidate_term_type=candidate.candidate_term_type,
            accepted=False,
            reject_reasons=["substring_not_found"],
            resume_id=source.resume_id,
            source_section=source.source_section,
            source_text_id=source.source_text_id,
            source_text_index=source.source_text_index,
            source_text_hash=source.source_text_hash,
            support_eligible=source.support_eligible,
            hint_only=source.hint_only,
        )

    start_char, end_char, _match_kind = match
    raw_surface = source.source_text_raw[start_char:end_char]
    reject_reasons = (
        ["unsafe_substring_match"]
        if _is_unsafe_substring_match(source.source_text_raw, start_char, end_char, candidate.surface)
        else []
    )
    return LLMPRFGroundingRecord(
        surface=candidate.surface,
        normalized_surface=_normalize_surface(raw_surface),
        advisory_candidate_term_type=candidate.candidate_term_type,
        accepted=not reject_reasons,
        reject_reasons=reject_reasons,
        resume_id=source.resume_id,
        source_section=source.source_section,
        source_text_id=source.source_text_id,
        source_text_index=source.source_text_index,
        source_text_hash=source.source_text_hash,
        support_eligible=source.support_eligible,
        hint_only=source.hint_only,
        start_char=start_char,
        end_char=end_char,
        raw_surface=raw_surface,
    )


def _find_raw_match(text: str, surface: str) -> tuple[int, int, Literal["exact", "nfkc", "nfkc_casefold"]] | None:
    start_char = text.find(surface)
    if start_char != -1:
        return start_char, start_char + len(surface), "exact"

    normalized_text, raw_offset_map = _nfkc_with_raw_offset_map(text)
    normalized_surface = unicodedata.normalize("NFKC", surface)
    normalized_start = normalized_text.find(normalized_surface)
    if normalized_start == -1:
        folded_text, folded_offset_map = _casefold_with_offset_map(normalized_text)
        folded_surface = normalized_surface.casefold()
        folded_start = folded_text.find(folded_surface)
        if folded_start == -1:
            return None
        folded_end = folded_start + len(folded_surface)
        normalized_start = folded_offset_map[folded_start]
        normalized_end = folded_offset_map[folded_end - 1] + 1
        return raw_offset_map[normalized_start], raw_offset_map[normalized_end - 1] + 1, "nfkc_casefold"
    normalized_end = normalized_start + len(normalized_surface)
    return raw_offset_map[normalized_start], raw_offset_map[normalized_end - 1] + 1, "nfkc"


def _normalize_surface(surface: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", surface).split())


def _nfkc_with_raw_offset_map(text: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    raw_offset_map: list[int] = []
    for raw_index, char in enumerate(text):
        normalized = unicodedata.normalize("NFKC", char)
        normalized_chars.append(normalized)
        raw_offset_map.extend([raw_index] * len(normalized))
    return "".join(normalized_chars), raw_offset_map


def _casefold_with_offset_map(text: str) -> tuple[str, list[int]]:
    folded_chars: list[str] = []
    offset_map: list[int] = []
    for index, char in enumerate(text):
        folded = char.casefold()
        folded_chars.append(folded)
        offset_map.extend([index] * len(folded))
    return "".join(folded_chars), offset_map


def _is_unsafe_substring_match(text: str, start_char: int, end_char: int, surface: str) -> bool:
    lower_tail = text[start_char:].casefold()
    for unsafe_surface, unsafe_container in _UNSAFE_SUBSTRING_PAIRS:
        if surface.casefold() == unsafe_surface.casefold() and lower_tail.startswith(unsafe_container.casefold()):
            return True
    if surface.isascii():
        before = text[start_char - 1] if start_char > 0 else ""
        after = text[end_char] if end_char < len(text) else ""
        return bool(_is_ascii_token_char(before) or _is_ascii_token_char(after))
    return False


def _is_ascii_token_char(char: str) -> bool:
    return bool(char and char.isascii() and (char.isalnum() or char == "_"))


def _negative_support_resume_ids(negative_source_texts: list[LLMPRFSourceText], family_id: str) -> list[str]:
    family_key = family_id.removeprefix("feedback.")
    resume_ids: set[str] = set()
    for source in negative_source_texts:
        if family_key and family_key in _family_keys_in_text(source.source_text_raw):
            resume_ids.add(source.resume_id)
    return sorted(resume_ids)


def _family_keys_in_text(text: str) -> set[str]:
    tokens = [_collapse_family_surface(token) for token in _family_token_surfaces(text)]
    tokens = [token for token in tokens if token]
    family_keys: set[str] = set(tokens)
    for start_index in range(len(tokens)):
        for end_index in range(start_index + 2, min(len(tokens), start_index + 4) + 1):
            family_keys.add("".join(tokens[start_index:end_index]))
    return family_keys


def _family_token_surfaces(text: str) -> list[str]:
    surfaces: list[str] = []
    current: list[str] = []
    for char in unicodedata.normalize("NFKC", text):
        if char.isalnum():
            current.append(char)
            continue
        if current:
            surfaces.append("".join(current))
            current = []
    if current:
        surfaces.append("".join(current))
    return surfaces


def _collapse_family_surface(surface: str) -> str:
    normalized = unicodedata.normalize("NFKC", surface)
    return "".join(char.casefold() for char in normalized if char.isalnum())


def _is_ambiguous_company_or_product(expression: str) -> bool:
    return expression in {"腾讯云"}


def _normalize_reject_reasons(reject_reasons: list[str]) -> list[str]:
    normalized: list[str] = []
    for reason in reject_reasons:
        if reason == "company_entity":
            normalized.append("company_entity_rejected")
        else:
            normalized.append(reason)
    return unique_strings(normalized)


def _source_section_rank(source_section: LLMPRFSourceSection | None) -> int:
    if source_section is None:
        return len(_SOURCE_SECTION_ORDER)
    return _SOURCE_SECTION_ORDER.index(source_section)
