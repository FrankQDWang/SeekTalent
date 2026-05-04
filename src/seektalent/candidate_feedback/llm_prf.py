from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Any, Literal, cast

from pydantic_ai import Agent, PromptedOutput
from pydantic import BaseModel, ConfigDict, Field, field_validator

from seektalent.candidate_feedback.extraction import classify_feedback_expressions
from seektalent.candidate_feedback.models import FeedbackCandidateExpression
from seektalent.candidate_feedback.span_models import CandidateTermType, SourceField
from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec, resolve_stage_model_config
from seektalent.models import ScoredCandidate, unique_strings
from seektalent.prompting import LoadedPrompt
from seektalent.tracing import ProviderUsageSnapshot, provider_usage_from_result

LLM_PRF_SCHEMA_VERSION = "llm-prf-v1"
LLM_PRF_EXTRACTOR_VERSION = "llm-prf-deepseek-v4-flash-v1"
GROUNDING_VALIDATOR_VERSION = "llm-prf-grounding-v1"
LLM_PRF_FAMILYING_VERSION = "llm-prf-conservative-surface-family-v1"
LLM_PRF_OUTPUT_RETRIES = 2
LLM_PRF_TOP_N_CANDIDATE_CAP = 16
LLM_PRF_STAGE = "prf_probe_phrase_proposal"

LLMPRFSourceKind = Literal["grounding_eligible", "hint_only"]
LLMPRFFailureKind = Literal[
    "timeout",
    "transport_error",
    "provider_error",
    "response_validation_error",
    "structured_output_parse_error",
    "settings_migration_error",
    "unsupported_capability",
]

_SOURCE_FIELD_ORDER: tuple[SourceField, ...] = ("evidence", "matched_must_haves", "matched_preferences", "strengths")
_UNSAFE_SUBSTRING_PAIRS = (
    ("Java", "JavaScript"),
    ("React", "React Native"),
    ("阿里", "阿里云"),
)


class LLMPRFSourceText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_field: SourceField
    source_text_index: int = Field(ge=0)
    source_text_raw: str = Field(min_length=1)
    source_text_hash: str
    source_kind: LLMPRFSourceKind

    @property
    def source_id(self) -> str:
        return f"{self.resume_id}|{self.source_field}|{self.source_text_index}"


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
    source_field: SourceField
    source_text_index: int = Field(ge=0)
    source_text_hash: str
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
    return f"Return valid json for this PRF phrase proposal payload:\n{payload_json}"


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
) -> LLMPRFInput | None:
    if len(seed_resumes) < 2:
        return None
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
        source_texts=_source_texts_from_resumes(seed_resumes),
        negative_source_texts=_source_texts_from_resumes(negative_resumes),
    )


def ground_llm_prf_candidates(payload: LLMPRFInput, extraction: LLMPRFExtraction) -> LLMPRFGroundingResult:
    sources_by_ref = {
        (source.resume_id, source.source_field, source.source_text_index): source for source in payload.source_texts
    }
    records: list[LLMPRFGroundingRecord] = []

    for candidate in extraction.candidates:
        for evidence_ref in candidate.source_evidence_refs:
            source = sources_by_ref.get((evidence_ref.resume_id, evidence_ref.source_field, evidence_ref.source_text_index))
            if source is None:
                records.append(
                    LLMPRFGroundingRecord(
                        surface=candidate.surface,
                        normalized_surface=_normalize_surface(candidate.surface),
                        advisory_candidate_term_type=candidate.candidate_term_type,
                        accepted=False,
                        reject_reasons=["source_reference_not_found"],
                        resume_id=evidence_ref.resume_id,
                        source_field=evidence_ref.source_field,
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
                        source_field=source.source_field,
                        source_text_index=source.source_text_index,
                        source_text_hash=evidence_ref.source_text_hash,
                    )
                )
                continue

            record = _ground_surface(candidate=candidate, source=source)
            records.append(record)

    records.sort(
        key=lambda record: (
            _source_field_rank(record.source_field),
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
    source_kind_by_ref = {
        (source.resume_id, source.source_field, source.source_text_index, source.source_text_hash): source.source_kind
        for source in payload.source_texts
    }

    for record in grounding.records:
        if not record.accepted:
            continue
        family_id = build_conservative_prf_family_id(record.normalized_surface or record.surface)
        canonical.setdefault(family_id, record.normalized_surface or record.surface)
        surfaces[family_id].add(record.raw_surface or record.surface)
        field_hits[family_id][record.source_field] += 1
        source_kind = source_kind_by_ref.get(
            (record.resume_id, record.source_field, record.source_text_index, record.source_text_hash)
        )
        if source_kind == "grounding_eligible":
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
        if family_id in tried_term_family_ids:
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


def _source_texts_from_resumes(resumes: list[ScoredCandidate]) -> list[LLMPRFSourceText]:
    source_texts: list[LLMPRFSourceText] = []
    for resume in resumes:
        fields = {
            "evidence": resume.evidence,
            "matched_must_haves": resume.matched_must_haves,
            "matched_preferences": resume.matched_preferences,
            "strengths": resume.strengths,
        }
        for source_field in _SOURCE_FIELD_ORDER:
            for source_text_index, text in enumerate(fields[source_field]):
                if not text:
                    continue
                source_texts.append(
                    LLMPRFSourceText(
                        resume_id=resume.resume_id,
                        source_field=source_field,
                        source_text_index=source_text_index,
                        source_text_raw=text,
                        source_text_hash=sha256(text.encode("utf-8")).hexdigest(),
                        source_kind="hint_only" if source_field == "strengths" else "grounding_eligible",
                    )
                )
    return source_texts


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
            source_field=source.source_field,
            source_text_index=source.source_text_index,
            source_text_hash=source.source_text_hash,
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
        source_field=source.source_field,
        source_text_index=source.source_text_index,
        source_text_hash=source.source_text_hash,
        start_char=start_char,
        end_char=end_char,
        raw_surface=raw_surface,
    )


def _find_raw_match(text: str, surface: str) -> tuple[int, int, Literal["exact", "nfkc"]] | None:
    start_char = text.find(surface)
    if start_char != -1:
        return start_char, start_char + len(surface), "exact"

    normalized_text, raw_offset_map = _nfkc_with_raw_offset_map(text)
    normalized_surface = unicodedata.normalize("NFKC", surface)
    normalized_start = normalized_text.find(normalized_surface)
    if normalized_start == -1:
        return None
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


def _is_unsafe_substring_match(text: str, start_char: int, end_char: int, surface: str) -> bool:
    lower_tail = text[start_char:].casefold()
    for unsafe_surface, unsafe_container in _UNSAFE_SUBSTRING_PAIRS:
        if surface.casefold() == unsafe_surface.casefold() and lower_tail.startswith(unsafe_container.casefold()):
            return True
    if surface.isascii():
        before = text[start_char - 1] if start_char > 0 else ""
        after = text[end_char] if end_char < len(text) else ""
        return bool((before and before.isalnum()) or (after and after.isalnum()))
    return False


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


def _source_field_rank(source_field: SourceField | None) -> int:
    if source_field is None:
        return len(_SOURCE_FIELD_ORDER)
    return _SOURCE_FIELD_ORDER.index(source_field)
