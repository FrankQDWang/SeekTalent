from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from seektalent.providers.liepin.worker_contracts import (
    LiepinCardSearchResponse,
    LiepinSafeCardSummary,
    LiepinWorkerCandidateCard,
)
from seektalent.providers.pi_agent.payload_firewall import (
    ArtifactRefRegistry,
    SafePayloadFirewall,
    SafePayloadViolation,
    validate_public_artifact_ref,
)
from seektalent.providers.pi_agent.pi_external import (
    PiExternalAgentErrorCode,
    PiExternalTaskResult,
    PiRpcAgentClient,
)


class PiLiepinStopReason(StrEnum):
    COMPLETED = "completed"
    PARTIAL_TIMEOUT = "partial_timeout"
    BLOCKED_LOGIN_REQUIRED = "blocked_login_required"
    BLOCKED_PERMISSION_REQUIRED = "blocked_permission_required"
    BLOCKED_BACKEND_UNAVAILABLE = "blocked_backend_unavailable"
    BLOCKED_BUDGET_EXHAUSTED = "blocked_budget_exhausted"
    FAILED_PROVIDER_ERROR = "failed_provider_error"
    FAILED_MALFORMED_OUTPUT = "failed_malformed_output"


class PiLiepinResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, kw_only=True)
class PiLiepinCapabilityProbeResult:
    ready: bool
    safe_reason_code: str | None = None


@dataclass(frozen=True, kw_only=True)
class PiLiepinSessionProbeResult:
    status: Literal["ready", "login_required", "revoked", "missing", "failed"]
    connection_id: str
    provider_account_hash: str | None = None
    safe_reason_code: str | None = None


@dataclass(frozen=True, kw_only=True)
class LiepinPiCardSearchResult:
    status: PiLiepinResultStatus
    stop_reason: PiLiepinStopReason
    safe_reason_code: str
    action_trace_ref: str | None = None
    card_search: LiepinCardSearchResponse | None = None


@dataclass(frozen=True)
class HmacProviderKeyHasher:
    secret: str
    material_resolver: ArtifactRefRegistry

    def provider_candidate_hash(self, *, provider: str, material_ref: str) -> str:
        return self._digest(kind="candidate", provider=provider, material_ref=material_ref)

    def provider_account_hash(self, *, provider: str, material_ref: str) -> str:
        return self._digest(kind="account", provider=provider, material_ref=material_ref)

    def _digest(self, *, kind: str, provider: str, material_ref: str) -> str:
        material = self.material_resolver.resolve_material(material_ref)
        message = b"\x1f".join([kind.encode(), provider.encode(), material])
        return hmac.new(self.secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True, hide_input_in_errors=True)


class _PiSafeCardSummary(_StrictModel):
    display_title: str | None = None
    current_or_recent_company: str | None = None
    current_or_recent_title: str | None = None
    work_years: int | None = None
    age: int | None = None
    city: str | None = None
    expected_city: str | None = None
    education_level: str | None = None
    school_names: list[str] = Field(default_factory=list)
    major_names: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    job_intention: str | None = None
    recent_experience_text: str | None = None
    normalized_card_text: str


class _PiLiepinCard(_StrictModel):
    provider_rank: int = Field(ge=1)
    provider_candidate_key_material_ref: str
    candidate_resume_id: str
    display_name_masked: bool
    safe_card_summary: _PiSafeCardSummary
    safe_card_summary_ref: str
    protected_snapshot_ref: str


class _PiLiepinCardsEnvelope(_StrictModel):
    schema_version: Literal["seektalent.pi_liepin_cards.v1"]
    status: Literal["succeeded", "partial", "blocked", "failed"]
    stop_reason: Literal[
        "completed",
        "partial_timeout",
        "blocked_login_required",
        "blocked_permission_required",
        "blocked_backend_unavailable",
        "blocked_budget_exhausted",
        "failed_provider_error",
        "failed_malformed_output",
    ] | None = None
    source_run_id: str
    query: str
    cards_seen: int = Field(ge=0)
    cards_returned: int = Field(ge=0)
    pages_visited: int = Field(ge=0)
    action_trace_ref: str
    safe_summary_refs: list[str] = Field(default_factory=list)
    protected_snapshot_refs: list[str] = Field(default_factory=list)
    cards: list[_PiLiepinCard] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> "_PiLiepinCardsEnvelope":
        if self.cards_returned != len(self.cards):
            raise ValueError("cards_returned must equal len(cards)")
        if self.cards_returned > self.cards_seen:
            raise ValueError("cards_returned must not exceed cards_seen")
        ranks = [card.provider_rank for card in self.cards]
        if len(ranks) != len(set(ranks)):
            raise ValueError("provider_rank must be unique")
        stop_reason = self.stop_reason
        if self.status == "succeeded" and stop_reason not in {None, "completed"}:
            raise ValueError("succeeded card search must use completed stop_reason")
        if self.status == "partial" and stop_reason not in {"partial_timeout", "blocked_budget_exhausted"}:
            raise ValueError("partial card search requires a partial stop_reason")
        if self.status == "blocked" and (stop_reason is None or not stop_reason.startswith("blocked_")):
            raise ValueError("blocked card search requires a blocked stop_reason")
        if self.status == "failed" and stop_reason not in {"failed_provider_error", "failed_malformed_output"}:
            raise ValueError("failed card search requires a failed stop_reason")
        if self.status in {"blocked", "failed"} and self.cards_returned:
            raise ValueError("blocked or failed card search must not return cards")
        return self


class _PiCapabilityProbeEnvelope(_StrictModel):
    schema_version: Literal["seektalent.pi_capability_probe.v1"]
    status: Literal["ready", "blocked", "failed"]
    pi_version: str | None = None
    read_tool_name: str | None = None
    action_tool_names: list[str] = Field(default_factory=list)
    proof_kind: Literal["trusted_manifest_and_observed_tool_event", "none"]
    capability_manifest_ref: str | None = None
    tool_evidence_ref: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    stop_reason: str | None = None


class _PiSessionProbeEnvelope(_StrictModel):
    schema_version: Literal["seektalent.pi_liepin_session_probe.v1"]
    status: Literal["ready", "login_required", "revoked", "missing", "failed"]
    connection_id: str
    provider_account_material_ref: str | None = None
    page_origin: str | None = None
    stop_reason: str | None = None

    @field_validator("page_origin")
    @classmethod
    def validate_page_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        host = urlparse(value).hostname or ""
        if not host.endswith("liepin.com"):
            raise ValueError("page_origin must be a Liepin origin")
        return value

    @model_validator(mode="after")
    def validate_account_ref_scope(self) -> "_PiSessionProbeEnvelope":
        if self.status != "ready" and self.provider_account_material_ref is not None:
            raise ValueError("non-ready session probe must not include account material")
        if self.status == "ready" and self.provider_account_material_ref is None:
            raise ValueError("ready session probe requires account material")
        return self


class PiLiepinExecutor:
    def __init__(
        self,
        *,
        client: PiRpcAgentClient,
        key_hasher: HmacProviderKeyHasher,
        artifact_registry: ArtifactRefRegistry,
        firewall: SafePayloadFirewall | None = None,
    ) -> None:
        self._client = client
        self._key_hasher = key_hasher
        self._artifact_registry = artifact_registry
        self._firewall = firewall or SafePayloadFirewall()

    def search_cards(
        self,
        *,
        source_run_id: str,
        keyword_query: str,
        query_terms: Sequence[str],
        page_size: int,
        max_pages: int,
        max_cards: int,
        connection_id: str | None = None,
        provider_account_hash: str | None = None,
    ) -> LiepinPiCardSearchResult:
        task = {
            "task": "liepin.search_cards",
            "schema_version": "seektalent.pi_liepin_cards.v1",
            "source_run_id": source_run_id,
            "query": keyword_query,
            "query_terms": list(query_terms),
            "page_size": page_size,
            "max_pages": max_pages,
            "max_cards": max_cards,
            "mode": "card",
        }
        if connection_id is not None or provider_account_hash is not None:
            task["session_context"] = {
                key: value
                for key, value in {
                    "connection_id": connection_id,
                    "provider_account_hash": provider_account_hash,
                }.items()
                if value is not None
            }
        task_result = self._client.run_json_task_result(json.dumps(task, ensure_ascii=False))
        if not task_result.ok or task_result.envelope is None:
            return _result_from_external_error(task_result)
        try:
            envelope = _PiLiepinCardsEnvelope.model_validate(task_result.envelope)
            self._validate_card_envelope(
                envelope,
                source_run_id=source_run_id,
                keyword_query=keyword_query,
                max_pages=max_pages,
                max_cards=max_cards,
            )
            card_search = self._map_card_search(envelope)
        except (ValidationError, ValueError, SafePayloadViolation):
            return LiepinPiCardSearchResult(
                status=PiLiepinResultStatus.FAILED,
                stop_reason=PiLiepinStopReason.FAILED_PROVIDER_ERROR,
                safe_reason_code="failed_provider_error",
            )
        status = PiLiepinResultStatus(envelope.status)
        stop_reason = PiLiepinStopReason(envelope.stop_reason or PiLiepinStopReason.COMPLETED.value)
        safe_reason = _safe_reason_for_stop(stop_reason)
        return LiepinPiCardSearchResult(
            status=status,
            stop_reason=stop_reason,
            safe_reason_code=safe_reason,
            action_trace_ref=envelope.action_trace_ref,
            card_search=card_search,
        )

    def probe_capabilities(self, *, expected_dokobot_tool_name: str) -> PiLiepinCapabilityProbeResult:
        task_result = self._client.run_json_task_result(
            json.dumps(
                {
                    "task": "liepin.probe_capabilities",
                    "expected_dokobot_tool_name": expected_dokobot_tool_name,
                },
                ensure_ascii=False,
            )
        )
        if not task_result.ok or task_result.envelope is None:
            return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
        try:
            envelope = _PiCapabilityProbeEnvelope.model_validate(task_result.envelope)
            if envelope.status != "ready":
                return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
            for ref in (envelope.capability_manifest_ref, envelope.tool_evidence_ref):
                validate_public_artifact_ref(ref, registry=self._artifact_registry)
            required_tools = (
                f"{expected_dokobot_tool_name}.read",
                f"{expected_dokobot_tool_name}.navigate",
                f"{expected_dokobot_tool_name}.click",
                f"{expected_dokobot_tool_name}.type_text",
            )
            observed = set(task_result.observed_tool_names)
            declared = {envelope.read_tool_name, *envelope.action_tool_names}
            if envelope.proof_kind != "trusted_manifest_and_observed_tool_event":
                raise ValueError("capability proof is not trusted")
            if not set(required_tools).issubset(observed) or not set(required_tools).issubset(declared):
                raise ValueError("required DokoBot tools were not observed")
            if "liepin.com" not in envelope.allowed_hosts:
                raise ValueError("Liepin host not allowed")
        except (ValidationError, ValueError, SafePayloadViolation):
            return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
        return PiLiepinCapabilityProbeResult(ready=True)

    def probe_session(self, *, connection_id: str) -> PiLiepinSessionProbeResult:
        task_result = self._client.run_json_task_result(
            json.dumps({"task": "liepin.probe_session", "connection_id": connection_id}, ensure_ascii=False)
        )
        if not task_result.ok or task_result.envelope is None:
            return PiLiepinSessionProbeResult(
                status="failed",
                connection_id=connection_id,
                safe_reason_code="blocked_backend_unavailable",
            )
        try:
            envelope = _PiSessionProbeEnvelope.model_validate(task_result.envelope)
            if envelope.connection_id != connection_id:
                raise ValueError("session probe connection_id mismatch")
            if envelope.status != "ready":
                return PiLiepinSessionProbeResult(
                    status=envelope.status,
                    connection_id=envelope.connection_id,
                    safe_reason_code=envelope.stop_reason or "blocked_login_required",
                )
            material_ref = validate_public_artifact_ref(
                envelope.provider_account_material_ref,
                registry=self._artifact_registry,
            )
            if material_ref is None:
                raise ValueError("ready session is missing account material")
            provider_account_hash = self._key_hasher.provider_account_hash(provider="liepin", material_ref=material_ref)
        except (ValidationError, ValueError, SafePayloadViolation):
            return PiLiepinSessionProbeResult(status="failed", connection_id=connection_id, safe_reason_code="failed_provider_error")
        return PiLiepinSessionProbeResult(
            status="ready",
            connection_id=connection_id,
            provider_account_hash=provider_account_hash,
        )

    def _validate_card_envelope(
        self,
        envelope: _PiLiepinCardsEnvelope,
        *,
        source_run_id: str,
        keyword_query: str,
        max_pages: int,
        max_cards: int,
    ) -> None:
        if envelope.source_run_id != source_run_id:
            raise ValueError("source_run_id mismatch")
        if envelope.query != keyword_query:
            raise ValueError("query mismatch")
        if envelope.pages_visited > max_pages:
            raise ValueError("pages_visited exceeds budget")
        if envelope.cards_returned > max_cards:
            raise ValueError("cards_returned exceeds budget")
        validate_public_artifact_ref(envelope.action_trace_ref, registry=self._artifact_registry)
        _validate_card_mode_trace_ref(envelope.action_trace_ref, self._artifact_registry)
        for ref in (*envelope.safe_summary_refs, *envelope.protected_snapshot_refs):
            validate_public_artifact_ref(ref, registry=self._artifact_registry)
        for card in envelope.cards:
            self._firewall.assert_safe_text(card.candidate_resume_id)
            self._firewall.assert_safe_mapping(card.safe_card_summary.model_dump(mode="json"))
            validate_public_artifact_ref(card.provider_candidate_key_material_ref, registry=self._artifact_registry)
            validate_public_artifact_ref(card.safe_card_summary_ref, registry=self._artifact_registry)
            validate_public_artifact_ref(card.protected_snapshot_ref, registry=self._artifact_registry)

    def _map_card_search(self, envelope: _PiLiepinCardsEnvelope) -> LiepinCardSearchResponse:
        cards = [
            self._map_card(card, source_run_id=envelope.source_run_id, action_trace_ref=envelope.action_trace_ref)
            for card in envelope.cards
        ]
        return LiepinCardSearchResponse(
            cards=cards,
            diagnostics=[],
            exhausted=envelope.status == PiLiepinResultStatus.SUCCEEDED and envelope.stop_reason == PiLiepinStopReason.COMPLETED,
            nextCursor=None,
            requestPayload={"sourceRunId": envelope.source_run_id, "query": envelope.query},
            rawCandidateCount=envelope.cards_seen,
        )

    def _map_card(self, card: _PiLiepinCard, *, source_run_id: str, action_trace_ref: str) -> LiepinWorkerCandidateCard:
        provider_candidate_hash = self._key_hasher.provider_candidate_hash(
            provider="liepin",
            material_ref=card.provider_candidate_key_material_ref,
        )
        safe_summary = LiepinSafeCardSummary(
            display_title=card.safe_card_summary.display_title,
            current_or_recent_company=card.safe_card_summary.current_or_recent_company,
            current_or_recent_title=card.safe_card_summary.current_or_recent_title,
            work_years=card.safe_card_summary.work_years,
            age=card.safe_card_summary.age,
            city=card.safe_card_summary.city,
            expected_city=card.safe_card_summary.expected_city,
            education_level=card.safe_card_summary.education_level,
            school_names=card.safe_card_summary.school_names,
            major_names=card.safe_card_summary.major_names,
            skill_tags=card.safe_card_summary.skill_tags,
            job_intention=card.safe_card_summary.job_intention,
            recent_experience_text=card.safe_card_summary.recent_experience_text,
            masked_name=card.display_name_masked,
        )
        normalized_text = card.safe_card_summary.normalized_card_text
        payload: dict[str, object] = {
            "providerCandidateKeyHash": provider_candidate_hash,
            "providerRank": card.provider_rank,
            "sourceRunId": source_run_id,
            "safeSummaryRef": card.safe_card_summary_ref,
            "protectedSnapshotRef": card.protected_snapshot_ref,
            "actionTraceRef": action_trace_ref,
        }
        fingerprint = hashlib.sha256(f"liepin:{provider_candidate_hash}".encode("utf-8")).hexdigest()
        return LiepinWorkerCandidateCard(
            payload=payload,
            normalized_text=normalized_text,
            provider_subject_id=None,
            provider_listing_id=None,
            synthetic_candidate_fingerprint=fingerprint,
            identity_confidence="synthetic_fingerprint",
            extraction_source="dom_fallback",
            extractor_version="pi-agent-liepin-card-v1",
            pii_classification="no_direct_contact",
            retention_policy="provider_snapshot_30d",
            access_scope="local_run_only",
            redaction_state="redacted",
            safeCardSummary=safe_summary,
        )


def _result_from_external_error(task_result: PiExternalTaskResult) -> LiepinPiCardSearchResult:
    stop_reason = _stop_reason_for_external_error(task_result.error_code)
    return LiepinPiCardSearchResult(
        status=PiLiepinResultStatus.BLOCKED
        if stop_reason in {
            PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
            PiLiepinStopReason.BLOCKED_PERMISSION_REQUIRED,
        }
        else PiLiepinResultStatus.FAILED,
        stop_reason=stop_reason,
        safe_reason_code=_safe_reason_for_stop(stop_reason),
    )


def _stop_reason_for_external_error(error_code: PiExternalAgentErrorCode | None) -> PiLiepinStopReason:
    if error_code in {
        PiExternalAgentErrorCode.PI_UNAVAILABLE,
        PiExternalAgentErrorCode.PROMPT_REJECTED,
        PiExternalAgentErrorCode.MISSING_AGENT_END,
    }:
        return PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE
    if error_code == PiExternalAgentErrorCode.UI_REQUEST_DENIED:
        return PiLiepinStopReason.BLOCKED_PERMISSION_REQUIRED
    if error_code == PiExternalAgentErrorCode.MALFORMED_OUTPUT:
        return PiLiepinStopReason.FAILED_MALFORMED_OUTPUT
    return PiLiepinStopReason.FAILED_PROVIDER_ERROR


def _safe_reason_for_stop(stop_reason: PiLiepinStopReason) -> str:
    if stop_reason in {
        PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
        PiLiepinStopReason.BLOCKED_PERMISSION_REQUIRED,
        PiLiepinStopReason.BLOCKED_LOGIN_REQUIRED,
    }:
        return stop_reason.value
    if stop_reason == PiLiepinStopReason.PARTIAL_TIMEOUT:
        return "partial_timeout"
    if stop_reason == PiLiepinStopReason.BLOCKED_BUDGET_EXHAUSTED:
        return "blocked_budget_exhausted"
    if stop_reason == PiLiepinStopReason.FAILED_MALFORMED_OUTPUT:
        return "failed_provider_error"
    return "failed_provider_error" if stop_reason == PiLiepinStopReason.FAILED_PROVIDER_ERROR else "completed"


def _validate_card_mode_trace_ref(ref: str, registry: ArtifactRefRegistry) -> None:
    material = registry.resolve_material(ref)
    try:
        parsed = json.loads(material.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = material.decode("utf-8", errors="ignore")
    if _contains_forbidden_detail_trace(parsed):
        raise SafePayloadViolation("card mode trace contains detail/contact route")


def _contains_forbidden_detail_trace(value: object) -> bool:
    if isinstance(value, Mapping):
        route_kind = str(value.get("route_kind") or value.get("routeKind") or "").lower()
        action_kind = str(value.get("action_kind") or value.get("actionKind") or "").lower()
        url = str(value.get("url") or value.get("href") or "").lower()
        if route_kind == "detail" or action_kind in {"contact", "download", "open_detail"}:
            return True
        if any(marker in url for marker in ("/resume", "/candidate/detail", "/detail", "contact")):
            return True
        return any(_contains_forbidden_detail_trace(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_forbidden_detail_trace(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in ("candidate detail", "contact button", "/candidate/detail"))
    return False
