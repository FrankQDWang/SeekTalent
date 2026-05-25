from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from seektalent.providers.liepin.worker_contracts import (
    LiepinCardSearchResponse,
    LiepinResumeSearchResponse,
    LiepinSafeCardSummary,
    LiepinWorkerCandidateCard,
    LiepinWorkerCandidateDetail,
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
from seektalent.providers.liepin.pi_resume_contract import (
    PiResumeRepairRequest,
    validation_gap_for_resume_payload,
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


SAFE_CAPABILITY_STOP_REASONS = frozenset(
    {
        "liepin_pi_mcp_adapter_unavailable",
        "liepin_pi_dokobot_mcp_command_missing",
        "liepin_pi_dokobot_mcp_config_mismatch",
        "liepin_pi_dokobot_mcp_tool_names_missing",
        "liepin_pi_dokobot_mcp_missing",
        "liepin_pi_dokobot_tool_unobserved",
    }
)


OPENCLI_SAFE_REASON_CODES = frozenset(
    {
        "liepin_opencli_backend_disabled",
        "liepin_opencli_command_missing",
        "liepin_opencli_extension_disconnected",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_forbidden_command",
        "liepin_opencli_forbidden_text",
        "liepin_opencli_host_blocked",
        "liepin_opencli_start_url_blocked",
        "liepin_opencli_window_policy_blocked",
        "liepin_opencli_budget_exhausted",
        "liepin_opencli_timeout",
        "liepin_opencli_login_required",
        "liepin_opencli_identity_intercept",
        "liepin_opencli_risk_page",
        "liepin_opencli_unknown_modal",
        "liepin_opencli_source_policy_missing",
        "liepin_opencli_malformed_state",
        "liepin_opencli_detail_not_opened",
    }
)


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


@dataclass(frozen=True, kw_only=True)
class LiepinPiResumeSearchResult:
    status: PiLiepinResultStatus
    stop_reason: PiLiepinStopReason
    safe_reason_code: str
    action_trace_ref: str | None = None
    resume_search: LiepinResumeSearchResponse | None = None


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
    safe_reason_code: str | None = None

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
        if self.safe_reason_code is not None and self.safe_reason_code not in OPENCLI_SAFE_REASON_CODES:
            raise ValueError("safe_reason_code must be an allowlisted OpenCLI reason")
        return self


class _PiLiepinResume(_StrictModel):
    provider_rank: int = Field(ge=1)
    provider_candidate_key_material_ref: str
    candidate_resume_id: str
    protected_snapshot_ref: str
    detail_payload: dict[str, object]
    normalized_text: str


class _PiLiepinResumeLane(_StrictModel):
    query_instance_id: str | None = None
    query_role: str | None = None
    target_resumes: int | None = Field(default=None, ge=0)


class _PiLiepinResumesEnvelope(_StrictModel):
    schema_version: Literal["seektalent.pi_liepin_resumes.v2"]
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
    lane: _PiLiepinResumeLane | None = None
    cards_seen: int = Field(ge=0)
    cards_excluded: list[dict[str, object]] = Field(default_factory=list)
    resumes_returned: int = Field(ge=0)
    pages_visited: int = Field(ge=0)
    detail_pages_opened: int | None = Field(default=None, ge=0)
    action_trace_ref: str
    protected_snapshot_refs: list[str] = Field(default_factory=list)
    resumes: list[_PiLiepinResume] = Field(default_factory=list)
    safe_reason_code: str | None = None

    @model_validator(mode="after")
    def validate_counts(self) -> "_PiLiepinResumesEnvelope":
        if self.resumes_returned != len(self.resumes):
            raise ValueError("resumes_returned must equal len(resumes)")
        if self.resumes_returned > self.cards_seen:
            raise ValueError("resumes_returned must not exceed cards_seen")
        ranks = [resume.provider_rank for resume in self.resumes]
        if len(ranks) != len(set(ranks)):
            raise ValueError("provider_rank must be unique")
        stop_reason = self.stop_reason
        if self.status == "succeeded" and stop_reason not in {None, "completed"}:
            raise ValueError("succeeded resume search must use completed stop_reason")
        if self.status == "partial" and stop_reason not in {"partial_timeout", "blocked_budget_exhausted"}:
            raise ValueError("partial resume search requires a partial stop_reason")
        if self.status == "blocked" and (stop_reason is None or not stop_reason.startswith("blocked_")):
            raise ValueError("blocked resume search requires a blocked stop_reason")
        if self.status == "failed" and stop_reason not in {"failed_provider_error", "failed_malformed_output"}:
            raise ValueError("failed resume search requires a failed stop_reason")
        if self.status in {"blocked", "failed"} and self.resumes_returned:
            raise ValueError("blocked or failed resume search must not return resumes")
        if self.safe_reason_code is not None and self.safe_reason_code not in OPENCLI_SAFE_REASON_CODES:
            raise ValueError("safe_reason_code must be an allowlisted OpenCLI reason")
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
        native_filters: Mapping[str, object] | None = None,
    ) -> LiepinPiCardSearchResult:
        tool_source_run_id = _tool_source_run_id(source_run_id)
        task: dict[str, object] = {
            "task": "liepin.search_cards",
            "schema_version": "seektalent.pi_liepin_cards.v1",
            "source_run_id": tool_source_run_id,
            "query": keyword_query,
            "query_terms": list(query_terms),
            "page_size": page_size,
            "max_pages": max_pages,
            "max_cards": max_cards,
            "mode": "card",
        }
        if native_filters:
            task["native_filters"] = dict(native_filters)
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
                source_run_id=tool_source_run_id,
                keyword_query=keyword_query,
                max_pages=max_pages,
                max_cards=max_cards,
            )
            card_search = self._map_card_search(envelope, runtime_source_run_id=source_run_id)
        except (ValidationError, ValueError, SafePayloadViolation):
            return LiepinPiCardSearchResult(
                status=PiLiepinResultStatus.FAILED,
                stop_reason=PiLiepinStopReason.FAILED_PROVIDER_ERROR,
                safe_reason_code="failed_provider_error",
            )
        status = PiLiepinResultStatus(envelope.status)
        stop_reason = PiLiepinStopReason(envelope.stop_reason or PiLiepinStopReason.COMPLETED.value)
        safe_reason = envelope.safe_reason_code or _safe_reason_for_stop(stop_reason)
        return LiepinPiCardSearchResult(
            status=status,
            stop_reason=stop_reason,
            safe_reason_code=safe_reason,
            action_trace_ref=envelope.action_trace_ref,
            card_search=card_search,
        )

    def search_resumes(
        self,
        *,
        source_run_id: str,
        keyword_query: str,
        query_terms: Sequence[str],
        target_resumes: int,
        max_cards: int,
        max_pages: int,
        requirement_sheet: Mapping[str, object],
        connection_id: str | None = None,
        provider_account_hash: str | None = None,
        native_filters: Mapping[str, object] | None = None,
    ) -> LiepinPiResumeSearchResult:
        tool_source_run_id = _tool_source_run_id(source_run_id)
        task: dict[str, object] = {
            "task": "liepin.search_resumes",
            "schema_version": "seektalent.pi_liepin_resumes.v2",
            "source_run_id": tool_source_run_id,
            "query": keyword_query,
            "query_terms": list(query_terms),
            "target_resumes": target_resumes,
            "max_cards": max_cards,
            "max_pages": max_pages,
            "requirement_sheet": dict(requirement_sheet),
            "mode": "detail_backed_resume_search",
            "rank_policy": "preserve_provider_rank_exclude_clear_mismatch_only",
        }
        if native_filters:
            task["native_filters"] = dict(native_filters)
        if connection_id is not None or provider_account_hash is not None:
            task["session_context"] = {
                key: value
                for key, value in {
                    "connection_id": connection_id,
                    "provider_account_hash": provider_account_hash,
                }.items()
                if value is not None
            }
        task_json = json.dumps(task, ensure_ascii=False)
        with self._client.open_json_task_session(cleanup_prompt=task_json) as pi_session:
            task_result = pi_session.run_json_task_result(task_json)
            if not task_result.ok or task_result.envelope is None:
                recovered = self._recover_partial_resume_search_from_collected_artifacts(
                    task_result=task_result,
                    source_run_id=source_run_id,
                    tool_source_run_id=tool_source_run_id,
                    keyword_query=keyword_query,
                    target_resumes=target_resumes,
                    max_pages=max_pages,
                    max_cards=max_cards,
                )
                if recovered is not None:
                    return recovered
                return _resume_result_from_external_error(task_result)
            try:
                _validate_resume_opencli_tool_usage(task_result.observed_tool_names)
            except ValueError:
                return LiepinPiResumeSearchResult(
                    status=PiLiepinResultStatus.FAILED,
                    stop_reason=PiLiepinStopReason.FAILED_PROVIDER_ERROR,
                    safe_reason_code="failed_provider_error",
                )
            raw_envelope = task_result.envelope
            gap = validation_gap_for_resume_payload(raw_envelope, target=target_resumes)
            if raw_envelope.get("status") == "succeeded" and gap.needs_repair:
                repair = PiResumeRepairRequest(
                    source_run_id=tool_source_run_id,
                    query=keyword_query,
                    missing=gap,
                )
                repair_result = pi_session.run_json_task_result(repair.model_dump_json())
                if not repair_result.ok or repair_result.envelope is None:
                    return _resume_result_from_external_error(repair_result)
                raw_envelope = repair_result.envelope
        try:
            _validate_resume_opencli_tool_usage(task_result.observed_tool_names)
            envelope = _PiLiepinResumesEnvelope.model_validate(raw_envelope)
            self._validate_resume_envelope(
                envelope,
                source_run_id=tool_source_run_id,
                keyword_query=keyword_query,
                max_pages=max_pages,
                max_cards=max_cards,
                target_resumes=target_resumes,
            )
            resume_search = self._map_resume_search(envelope, runtime_source_run_id=source_run_id)
        except (ValidationError, ValueError, SafePayloadViolation):
            return LiepinPiResumeSearchResult(
                status=PiLiepinResultStatus.FAILED,
                stop_reason=PiLiepinStopReason.FAILED_PROVIDER_ERROR,
                safe_reason_code="failed_provider_error",
            )
        status = PiLiepinResultStatus(envelope.status)
        stop_reason = PiLiepinStopReason(envelope.stop_reason or PiLiepinStopReason.COMPLETED.value)
        safe_reason = envelope.safe_reason_code or _safe_reason_for_stop(stop_reason)
        return LiepinPiResumeSearchResult(
            status=status,
            stop_reason=stop_reason,
            safe_reason_code=safe_reason,
            action_trace_ref=envelope.action_trace_ref,
            resume_search=resume_search,
        )

    def _recover_partial_resume_search_from_collected_artifacts(
        self,
        *,
        task_result: PiExternalTaskResult,
        source_run_id: str,
        tool_source_run_id: str,
        keyword_query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
    ) -> LiepinPiResumeSearchResult | None:
        if not _resume_timeout_can_recover(task_result):
            return None
        collected_ref = f"artifact://protected/pi-detail/{tool_source_run_id}/collected-resumes.json"
        action_trace_ref = f"artifact://protected/pi-trace/{tool_source_run_id}/agent-events.json"
        try:
            collected = json.loads(self._artifact_registry.resolve_material(collected_ref).decode("utf-8"))
        except (SafePayloadViolation, UnicodeDecodeError, json.JSONDecodeError):
            return None
        raw_resumes = collected.get("resumes") if isinstance(collected, Mapping) else None
        if not isinstance(raw_resumes, list) or not raw_resumes:
            return None
        resumes = [resume for resume in raw_resumes if isinstance(resume, Mapping)]
        if not resumes:
            return None
        protected_snapshot_refs = [
            str(resume["protected_snapshot_ref"])
            for resume in resumes
            if isinstance(resume.get("protected_snapshot_ref"), str)
        ]
        stop_reason = PiLiepinStopReason.PARTIAL_TIMEOUT
        envelope_payload = {
            "schema_version": "seektalent.pi_liepin_resumes.v2",
            "status": PiLiepinResultStatus.PARTIAL.value,
            "stop_reason": stop_reason.value,
            "source_run_id": tool_source_run_id,
            "query": keyword_query,
            "cards_seen": min(max_cards, max(len(resumes), max(_resume_rank(resume) for resume in resumes))),
            "cards_excluded": [],
            "resumes_returned": len(resumes),
            "pages_visited": max(1, min(max_pages, max_pages or 1)),
            "detail_pages_opened": len(resumes),
            "action_trace_ref": action_trace_ref,
            "protected_snapshot_refs": protected_snapshot_refs,
            "resumes": resumes,
        }
        try:
            envelope = _PiLiepinResumesEnvelope.model_validate(envelope_payload)
            self._validate_resume_envelope(
                envelope,
                source_run_id=tool_source_run_id,
                keyword_query=keyword_query,
                max_pages=max_pages,
                max_cards=max_cards,
                target_resumes=target_resumes,
            )
            resume_search = self._map_resume_search(envelope, runtime_source_run_id=source_run_id)
        except (ValidationError, ValueError, SafePayloadViolation):
            return None
        return LiepinPiResumeSearchResult(
            status=PiLiepinResultStatus.PARTIAL,
            stop_reason=stop_reason,
            safe_reason_code=_safe_reason_for_stop(stop_reason),
            action_trace_ref=action_trace_ref,
            resume_search=resume_search,
        )

    def probe_capabilities(
        self,
        *,
        expected_dokobot_tool_name: str,
        expected_observed_tool_names: Sequence[str] = (),
        expected_opencli_observed_tool_names: Sequence[str] = (),
        expected_opencli_declared_tool_names: Sequence[str] = (),
    ) -> PiLiepinCapabilityProbeResult:
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
                if envelope.stop_reason in SAFE_CAPABILITY_STOP_REASONS or envelope.stop_reason in OPENCLI_SAFE_REASON_CODES:
                    return PiLiepinCapabilityProbeResult(
                        ready=False,
                        safe_reason_code=envelope.stop_reason,
                    )
                return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
            for ref in (envelope.capability_manifest_ref, envelope.tool_evidence_ref):
                validate_public_artifact_ref(ref, registry=self._artifact_registry)
            if expected_opencli_observed_tool_names or expected_opencli_declared_tool_names:
                if envelope.proof_kind != "trusted_manifest_and_observed_tool_event":
                    raise ValueError("capability proof is not trusted")
                declared = {tool for tool in (envelope.read_tool_name, *envelope.action_tool_names) if tool}
                if not set(expected_opencli_declared_tool_names).issubset(declared):
                    return PiLiepinCapabilityProbeResult(
                        ready=False,
                        safe_reason_code="liepin_opencli_status_unavailable",
                    )
                observed = set(task_result.observed_tool_names)
                if not set(expected_opencli_observed_tool_names).issubset(observed):
                    return PiLiepinCapabilityProbeResult(
                        ready=False,
                        safe_reason_code="liepin_opencli_status_unavailable",
                    )
                if not any(host == "liepin.com" or host.endswith(".liepin.com") for host in envelope.allowed_hosts):
                    raise ValueError("Liepin host not allowed")
                return PiLiepinCapabilityProbeResult(ready=True)
            required_tools = tuple(expected_observed_tool_names)
            if not required_tools:
                return PiLiepinCapabilityProbeResult(
                    ready=False,
                    safe_reason_code="liepin_pi_dokobot_mcp_tool_names_missing",
                )
            observed = set(task_result.observed_tool_names)
            declared = {envelope.read_tool_name, *envelope.action_tool_names}
            if envelope.proof_kind != "trusted_manifest_and_observed_tool_event":
                raise ValueError("capability proof is not trusted")
            required = set(required_tools)
            if not required.issubset(declared):
                raise ValueError("required DokoBot tools were not declared")
            if not required.issubset(observed):
                return PiLiepinCapabilityProbeResult(
                    ready=False,
                    safe_reason_code="liepin_pi_dokobot_tool_unobserved",
                )
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

    def _validate_resume_envelope(
        self,
        envelope: _PiLiepinResumesEnvelope,
        *,
        source_run_id: str,
        keyword_query: str,
        max_pages: int,
        max_cards: int,
        target_resumes: int,
    ) -> None:
        if envelope.source_run_id != source_run_id:
            raise ValueError("source_run_id mismatch")
        if envelope.query != keyword_query:
            raise ValueError("query mismatch")
        if envelope.pages_visited > max_pages:
            raise ValueError("pages_visited exceeds budget")
        if envelope.cards_seen > max_cards:
            raise ValueError("cards_seen exceeds budget")
        if envelope.resumes_returned > target_resumes:
            raise ValueError("resumes_returned exceeds budget")
        if envelope.status == PiLiepinResultStatus.SUCCEEDED and envelope.resumes_returned < target_resumes:
            raise ValueError("succeeded resume search must satisfy target_resumes")
        validate_public_artifact_ref(envelope.action_trace_ref, registry=self._artifact_registry)
        _validate_resume_mode_trace_ref(
            envelope.action_trace_ref,
            self._artifact_registry,
            requires_detail_open=envelope.resumes_returned > 0,
        )
        for ref in envelope.protected_snapshot_refs:
            validate_public_artifact_ref(ref, registry=self._artifact_registry)
        for resume in envelope.resumes:
            self._firewall.assert_safe_text(resume.candidate_resume_id)
            validate_public_artifact_ref(resume.provider_candidate_key_material_ref, registry=self._artifact_registry)
            validate_public_artifact_ref(resume.protected_snapshot_ref, registry=self._artifact_registry)
            _assert_safe_resume_detail_payload(resume.detail_payload)

    def _map_card_search(
        self,
        envelope: _PiLiepinCardsEnvelope,
        *,
        runtime_source_run_id: str,
    ) -> LiepinCardSearchResponse:
        cards = [
            self._map_card(card, source_run_id=runtime_source_run_id, action_trace_ref=envelope.action_trace_ref)
            for card in envelope.cards
        ]
        return LiepinCardSearchResponse(
            cards=cards,
            diagnostics=[],
            exhausted=envelope.status == PiLiepinResultStatus.SUCCEEDED and envelope.stop_reason == PiLiepinStopReason.COMPLETED,
            next_cursor=None,
            requestPayload={"sourceRunId": runtime_source_run_id, "query": envelope.query},
            raw_candidate_count=envelope.cards_seen,
        )

    def _map_resume_search(
        self,
        envelope: _PiLiepinResumesEnvelope,
        *,
        runtime_source_run_id: str,
    ) -> LiepinResumeSearchResponse:
        resumes = [
            self._map_resume(resume, source_run_id=runtime_source_run_id, action_trace_ref=envelope.action_trace_ref)
            for resume in envelope.resumes
        ]
        return LiepinResumeSearchResponse(
            resumes=resumes,
            diagnostics=[],
            exhausted=envelope.status == PiLiepinResultStatus.SUCCEEDED
            and envelope.stop_reason == PiLiepinStopReason.COMPLETED,
            next_cursor=None,
            requestPayload={"sourceRunId": runtime_source_run_id, "query": envelope.query},
            raw_candidate_count=envelope.cards_seen,
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
            school_names=tuple(card.safe_card_summary.school_names),
            major_names=tuple(card.safe_card_summary.major_names),
            skill_tags=tuple(card.safe_card_summary.skill_tags),
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
        return LiepinWorkerCandidateCard.model_validate(
            {
                "payload": payload,
                "normalized_text": normalized_text,
                "provider_subject_id": None,
                "provider_listing_id": None,
                "synthetic_candidate_fingerprint": fingerprint,
                "identity_confidence": "synthetic_fingerprint",
                "extraction_source": "dom_fallback",
                "extractor_version": "pi-agent-liepin-card-v1",
                "pii_classification": "no_direct_contact",
                "retention_policy": "provider_snapshot_30d",
                "access_scope": "local_run_only",
                "redaction_state": "redacted",
                "safeCardSummary": safe_summary,
            }
        )

    def _map_resume(
        self,
        resume: _PiLiepinResume,
        *,
        source_run_id: str,
        action_trace_ref: str,
    ) -> LiepinWorkerCandidateDetail:
        provider_candidate_hash = self._key_hasher.provider_candidate_hash(
            provider="liepin",
            material_ref=resume.provider_candidate_key_material_ref,
        )
        payload: dict[str, object] = {
            **resume.detail_payload,
            "providerCandidateKeyHash": provider_candidate_hash,
            "providerRank": resume.provider_rank,
            "sourceRunId": source_run_id,
            "protectedSnapshotRef": resume.protected_snapshot_ref,
            "actionTraceRef": action_trace_ref,
        }
        fingerprint = hashlib.sha256(f"liepin:{provider_candidate_hash}".encode("utf-8")).hexdigest()
        return LiepinWorkerCandidateDetail.model_validate(
            {
                "payload": payload,
                "normalized_text": resume.normalized_text,
                "provider_subject_id": provider_candidate_hash,
                "provider_listing_id": None,
                "synthetic_candidate_fingerprint": fingerprint,
                "identity_confidence": "provider_subject_id",
                "extraction_source": "dom_fallback",
                "extractor_version": "pi-agent-liepin-detail-v1",
                "pii_classification": "no_direct_contact",
                "retention_policy": "provider_snapshot_30d",
                "access_scope": "local_run_only",
                "redaction_state": "redacted",
            }
        )


def _result_from_external_error(task_result: PiExternalTaskResult) -> LiepinPiCardSearchResult:
    if task_result.safe_reason_code in OPENCLI_SAFE_REASON_CODES:
        return LiepinPiCardSearchResult(
            status=PiLiepinResultStatus.BLOCKED,
            stop_reason=PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
            safe_reason_code=task_result.safe_reason_code,
        )
    if task_result.error_code == PiExternalAgentErrorCode.TIMEOUT and any(
        name.startswith("seektalent_opencli_") for name in task_result.observed_tool_names
    ):
        return LiepinPiCardSearchResult(
            status=PiLiepinResultStatus.BLOCKED,
            stop_reason=PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
            safe_reason_code="liepin_opencli_timeout",
        )
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


def _validate_resume_opencli_tool_usage(observed_tool_names: Sequence[str]) -> None:
    if "seektalent_opencli_search_liepin_resumes" in observed_tool_names:
        raise ValueError("monolithic Liepin resume search tool is not allowed")
    opencli_tool_names = tuple(name for name in observed_tool_names if name.startswith("seektalent_opencli_"))
    if opencli_tool_names and "seektalent_opencli_finalize_liepin_resumes" not in opencli_tool_names:
        raise ValueError("agent-driven Liepin resume search must finalize through the bounded resume finalizer")


def _resume_result_from_external_error(task_result: PiExternalTaskResult) -> LiepinPiResumeSearchResult:
    if task_result.safe_reason_code in OPENCLI_SAFE_REASON_CODES:
        return LiepinPiResumeSearchResult(
            status=PiLiepinResultStatus.BLOCKED,
            stop_reason=PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
            safe_reason_code=task_result.safe_reason_code,
        )
    if task_result.error_code == PiExternalAgentErrorCode.TIMEOUT and any(
        name.startswith("seektalent_opencli_") for name in task_result.observed_tool_names
    ):
        return LiepinPiResumeSearchResult(
            status=PiLiepinResultStatus.BLOCKED,
            stop_reason=PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
            safe_reason_code="liepin_opencli_timeout",
        )
    stop_reason = _stop_reason_for_external_error(task_result.error_code)
    return LiepinPiResumeSearchResult(
        status=PiLiepinResultStatus.BLOCKED
        if stop_reason in {
            PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
            PiLiepinStopReason.BLOCKED_PERMISSION_REQUIRED,
        }
        else PiLiepinResultStatus.FAILED,
        stop_reason=stop_reason,
        safe_reason_code=_safe_reason_for_stop(stop_reason),
    )


def _resume_timeout_can_recover(task_result: PiExternalTaskResult) -> bool:
    if task_result.error_code != PiExternalAgentErrorCode.TIMEOUT:
        return False
    observed = set(task_result.observed_tool_names)
    return "seektalent_opencli_capture_liepin_detail_resume" in observed


def _resume_rank(resume: Mapping[str, object]) -> int:
    rank = resume.get("provider_rank")
    return rank if isinstance(rank, int) and rank > 0 else 1


def _tool_source_run_id(source_run_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", source_run_id).strip("-._")
    if cleaned == source_run_id and 0 < len(cleaned) <= 48:
        return cleaned
    digest = hashlib.sha256(source_run_id.encode("utf-8")).hexdigest()[:20]
    return f"st-{digest}"


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


def _validate_resume_mode_trace_ref(ref: str, registry: ArtifactRefRegistry, *, requires_detail_open: bool) -> None:
    material = registry.resolve_material(ref)
    try:
        parsed = json.loads(material.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafePayloadViolation("resume mode trace must be JSON") from exc
    if not isinstance(parsed, Mapping):
        raise SafePayloadViolation("resume mode trace must be an object")
    if parsed.get("mode") != "detail_backed_resume_search" and parsed.get("schema_version") != (
        "seektalent.opencli_agent_events.v1"
    ):
        raise SafePayloadViolation("resume mode trace has unexpected mode")
    events = parsed.get("events")
    if not isinstance(events, Sequence) or isinstance(events, str | bytes | bytearray):
        raise SafePayloadViolation("resume mode trace requires events")
    has_detail_open = False
    for event in events:
        if not isinstance(event, Mapping):
            continue
        action_kind = str(event.get("action_kind") or event.get("actionKind") or "").casefold()
        route_kind = str(event.get("route_kind") or event.get("routeKind") or "").casefold()
        if action_kind == "open_detail" or route_kind == "detail":
            has_detail_open = True
        if action_kind in {"contact", "chat", "download", "payment", "pay", "phone", "email"}:
            raise SafePayloadViolation("resume mode trace contains forbidden action")
    if requires_detail_open and not has_detail_open:
        raise SafePayloadViolation("resume mode trace missing detail open event")


_FORBIDDEN_RESUME_TEXT_PATTERNS = (
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}\b"),
    re.compile(r"\b(?:wechat|weixin|wx)[-_:\s]?[A-Za-z0-9_]{4,}\b", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"\bcookie\b|\bsession=", re.IGNORECASE),
    re.compile(r"\blocalStorage\b|\bsessionStorage\b", re.IGNORECASE),
    re.compile(r"<(?:html|script|body|div|span|table|input|button)\b", re.IGNORECASE),
    re.compile(r"(?:/Users/|/private/var/|C:\\Users\\)", re.IGNORECASE),
)
_FORBIDDEN_RESUME_KEYS = frozenset(
    {
        "phone",
        "mobile",
        "email",
        "wechat",
        "weixin",
        "contact",
        "cookie",
        "authorization",
        "rawproviderpayload",
        "rawhtml",
    }
)


def _assert_safe_resume_detail_payload(value: object, *, depth: int = 0) -> None:
    if depth > 8:
        raise SafePayloadViolation("resume detail payload is too deeply nested")
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "", str(key).casefold())
            if normalized_key in _FORBIDDEN_RESUME_KEYS and item is not None and item != "" and item != () and item != []:
                raise SafePayloadViolation("resume detail payload contains forbidden key")
            _assert_safe_resume_detail_payload(item, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            _assert_safe_resume_detail_payload(item, depth=depth + 1)
        return
    if isinstance(value, str):
        if len(value) > 30_000:
            raise SafePayloadViolation("resume detail payload text exceeds limit")
        if any(pattern.search(value) for pattern in _FORBIDDEN_RESUME_TEXT_PATTERNS):
            raise SafePayloadViolation("resume detail payload contains forbidden text")


def _contains_forbidden_detail_trace(value: object) -> bool:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        route_kind = str(mapping.get("route_kind") or mapping.get("routeKind") or "").lower()
        action_kind = str(mapping.get("action_kind") or mapping.get("actionKind") or "").lower()
        url = str(mapping.get("url") or mapping.get("href") or "").lower()
        if route_kind == "detail" or action_kind in {"contact", "download", "open_detail"}:
            return True
        if any(marker in url for marker in ("/resume", "/candidate/detail", "/detail", "contact")):
            return True
        return any(_contains_forbidden_detail_trace(item) for item in mapping.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_forbidden_detail_trace(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in ("candidate detail", "contact button", "/candidate/detail"))
    return False
