from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from inspect import signature
from typing import Protocol, runtime_checkable
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.candidate_quality import is_recommendation_eligible
from seektalent.models import RequirementSheet
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.detail import RuntimeDetailService
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.normalizer import merge_requirement_sheet_supplement
from seektalent_runtime_control.models import (
    RuntimeControlCandidateEvidence,
    RuntimeControlCandidateIdentity,
    RuntimeRunRecord,
)
from seektalent_runtime_control.requirements import (
    ApprovedRequirementRevision,
    RequirementDraft,
    draft_from_requirement_sheet,
    requirement_sheet_from_draft,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_workbench_v2.agent_loop import WorkbenchV2RuntimeInput
from seektalent_workbench_v2.runtime_display import (
    normalize_runtime_progress_payload,
    runtime_event_terminal_summary,
    safe_runtime_progress_details,
    safe_runtime_progress_reason_code,
)


REQUIREMENT_DRAFT_SOURCE = "workbench_v2_agent"
RUNTIME_INPUT_REQUIRED = "workbench_v2_runtime_input_required"
REQUIREMENT_EXTRACTOR_UNAVAILABLE = "workbench_v2_requirement_extractor_unavailable"
DEFAULT_SOURCE_IDS = ["liepin"]


@runtime_checkable
class _RequirementExtractorWithJdText(Protocol):
    def __call__(
        self,
        *,
        job_title: str,
        jd_text: str,
        notes: str | None,
        requirement_cache_scope: str,
    ) -> object: ...


@runtime_checkable
class _RequirementExtractorWithJd(Protocol):
    def __call__(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        requirement_cache_scope: str,
    ) -> object: ...


@dataclass(frozen=True)
class WorkbenchV2RequirementExtraction:
    draft: RequirementDraft
    requirement_sheet: RequirementSheet


@dataclass(frozen=True)
class _NextRoundRequirementExtractorAdapter:
    extractor: object
    requirement_cache_scope: str

    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
    ) -> RequirementSheet:
        return _extract_requirements(
            self.extractor,
            job_title=job_title or "",
            jd_text=jd_text,
            notes=notes,
            requirement_cache_scope=self.requirement_cache_scope,
        )


class WorkbenchV2RuntimeService:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        settings: AppSettings | None = None,
        runtime_factory: Callable[[], object] | None = None,
        requirement_extractor: object | None = None,
        executor: WorkflowRuntimeExecutor | None = None,
        draft_revision_id_factory: Callable[[], str] | None = None,
        approved_requirement_revision_id_factory: Callable[[], str] | None = None,
        runtime_run_id_factory: Callable[[], str] | None = None,
        on_run_queued: Callable[[str], None] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.runtime_factory = runtime_factory
        self.requirement_extractor = requirement_extractor
        self._runtime_executor = executor
        self._custom_draft_revision_id_factory = draft_revision_id_factory is not None
        self._custom_approved_requirement_revision_id_factory = approved_requirement_revision_id_factory is not None
        self.draft_revision_id_factory = draft_revision_id_factory or (lambda: _new_id("reqdraft"))
        self.approved_requirement_revision_id_factory = approved_requirement_revision_id_factory or (
            lambda: _new_id("reqapproved")
        )
        self.runtime_run_id_factory = runtime_run_id_factory
        self.on_run_queued = on_run_queued
        self.now = now or _now_iso

    def extract_requirements(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> RequirementDraft:
        return self.extract_requirement_bundle(conversation_id, runtime_input).draft

    def extract_requirement_bundle(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> WorkbenchV2RequirementExtraction:
        job_title, jd_text, notes = _runtime_input_values(runtime_input)
        sheet = _extract_requirements(
            self._requirement_extractor(),
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            requirement_cache_scope=conversation_id,
        )
        draft = draft_from_requirement_sheet(
            conversation_id=conversation_id,
            draft_revision_id=self.draft_revision_id_factory(),
            base_revision_id=None,
            requirement_sheet=sheet,
            source=REQUIREMENT_DRAFT_SOURCE,
            created_at=self.now(),
        )
        return WorkbenchV2RequirementExtraction(draft=draft, requirement_sheet=sheet)

    def amend_requirement_bundle(
        self,
        conversation_id: str,
        *,
        base_draft: RequirementDraft,
        base_requirement_sheet: RequirementSheet,
        text: str,
        idempotency_key: str,
    ) -> WorkbenchV2RequirementExtraction:
        base_sheet = requirement_sheet_from_draft(base_draft, base_requirement_sheet)
        supplement = _extract_requirements(
            self._requirement_extractor(),
            job_title=base_sheet.job_title,
            jd_text=text,
            notes=None,
            requirement_cache_scope=f"{conversation_id}:{idempotency_key}",
        )
        merged_sheet = merge_requirement_sheet_supplement(base_sheet, supplement)
        draft = _draft_with_supplement_items(
            base_draft,
            supplement=supplement,
            draft_revision_id=self.draft_revision_id_factory(),
            created_at=self.now(),
        )
        return WorkbenchV2RequirementExtraction(
            draft=draft,
            requirement_sheet=requirement_sheet_from_draft(draft, merged_sheet),
        )

    def start_run(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput | None,
        requirement_sheet: RequirementSheet,
        *,
        idempotency_key: str | None = None,
        draft_revision_id: str | None = None,
        selected_item_ids: list[str] | None = None,
        deselected_item_ids: list[str] | None = None,
    ) -> RuntimeRunRecord:
        job_title, jd_text, notes = _runtime_input_values(runtime_input)
        created_at = self.now()
        operation_key = _start_operation_key(conversation_id=conversation_id, idempotency_key=idempotency_key)
        approved_idempotency_key = f"workbench-v2-runtime-approved:{operation_key}"
        start_idempotency_key = f"workbench-v2-runtime-start:{operation_key}"
        saved = self.store.get_approved_requirement_by_idempotency(
            conversation_id=conversation_id,
            idempotency_key=approved_idempotency_key,
        )
        if saved is None:
            resolved_draft_revision_id = draft_revision_id or self._draft_revision_id(operation_key)
            approved_revision_id = self._approved_requirement_revision_id(operation_key)
            draft = draft_from_requirement_sheet(
                conversation_id=conversation_id,
                draft_revision_id=resolved_draft_revision_id,
                base_revision_id=None,
                requirement_sheet=requirement_sheet,
                source=REQUIREMENT_DRAFT_SOURCE,
                created_at=created_at,
            )
            approved = ApprovedRequirementRevision(
                approved_requirement_revision_id=approved_revision_id,
                draft_revision_id=draft.draft_revision_id,
                agent_conversation_id=conversation_id,
                requirement_sheet=requirement_sheet,
                selected_item_ids=list(selected_item_ids) if selected_item_ids is not None else _selected_item_ids(draft),
                deselected_item_ids=(
                    list(deselected_item_ids) if deselected_item_ids is not None else _deselected_item_ids(draft)
                ),
                created_at=created_at,
            )
            saved = self.store.save_approved_requirement(
                approved,
                idempotency_key=approved_idempotency_key,
            )
        run = self._executor().enqueue_workflow_run(
            conversation_id=conversation_id,
            workbench_session_id=None,
            approved_requirement=saved,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=DEFAULT_SOURCE_IDS,
            start_idempotency_key=start_idempotency_key,
        )
        if run.status in {"queued", "resume_requested"} and self.on_run_queued is not None:
            self.on_run_queued(run.runtime_run_id)
        return run

    def start_run_from_runtime_input(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
        *,
        idempotency_key: str | None = None,
        draft_revision_id: str | None = None,
        selected_item_ids: list[str] | None = None,
        deselected_item_ids: list[str] | None = None,
    ) -> RuntimeRunRecord:
        job_title, jd_text, notes = _runtime_input_values(runtime_input)
        requirement_sheet = _extract_requirements(
            self._requirement_extractor(),
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            requirement_cache_scope=conversation_id,
        )
        return self.start_run(
            conversation_id,
            runtime_input,
            requirement_sheet,
            idempotency_key=idempotency_key,
            draft_revision_id=draft_revision_id,
            selected_item_ids=selected_item_ids,
            deselected_item_ids=deselected_item_ids,
        )

    def _draft_revision_id(self, operation_key: str) -> str:
        if self._custom_draft_revision_id_factory:
            return self.draft_revision_id_factory()
        return _stable_id("reqdraft", operation_key)

    def _approved_requirement_revision_id(self, operation_key: str) -> str:
        if self._custom_approved_requirement_revision_id_factory:
            return self.approved_requirement_revision_id_factory()
        return _stable_id("reqapproved", operation_key)

    def get_status(self, runtime_run_id: str) -> dict[str, object]:
        run = self.store.get_run(runtime_run_id)
        status, stage = _safe_public_runtime_metadata(run.status, run.current_stage)
        event_summary = _latest_runtime_event_summary(
            self.store,
            runtime_run_id=run.runtime_run_id,
            latest_event_seq=run.latest_event_seq,
            run_status=run.status,
        )
        return {
            "runtimeRunId": run.runtime_run_id,
            "status": status,
            "stage": stage,
            "summary": event_summary or _status_summary(run.status, stage),
        }

    def list_progress_events(
        self,
        runtime_run_id: str,
        *,
        after_seq: int,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        page = self.store.list_public_events(runtime_run_id=runtime_run_id, after_seq=after_seq, limit=limit)
        events: list[dict[str, object]] = []
        for event in page.events:
            payload = _progress_payload_from_runtime_event(event)
            if payload is None:
                continue
            payload["state"] = _runtime_state_from_event_status(str(getattr(event, "status", "")))
            events.append(payload)
        return events

    def list_candidate_summaries(self, runtime_run_id: str, *, limit: int = 20) -> list[dict[str, object]]:
        identities = self.store.list_candidate_identities(runtime_run_id=runtime_run_id)
        evidence_by_identity: dict[str, list[RuntimeControlCandidateEvidence]] = {}
        for evidence in self.store.list_candidate_evidence(runtime_run_id=runtime_run_id):
            evidence_by_identity.setdefault(evidence.identity_id, []).append(evidence)
        eligible_identities: list[
            tuple[RuntimeControlCandidateIdentity, list[RuntimeControlCandidateEvidence], int]
        ] = []
        for identity in identities:
            evidence = _canonical_candidate_evidence(
                identity,
                evidence_by_identity.get(identity.identity_id, []),
            )
            score = _candidate_score(identity, evidence)
            if not is_recommendation_eligible(score=score, fit_bucket=identity.fit_bucket):
                continue
            assert score is not None
            eligible_identities.append((identity, evidence, score))
        eligible_identities.sort(key=lambda row: (-row[2], row[0].identity_id))
        candidates: list[dict[str, object]] = []
        for index, (identity, evidence, score) in enumerate(
            eligible_identities[: max(0, limit)], start=1
        ):
            source_kinds = _candidate_source_kinds(evidence)
            headline = _candidate_headline(identity, evidence)
            display_name = _candidate_display_name(identity, evidence, fallback=f"候选人 {index}")
            evidence_level = _candidate_evidence_level(evidence)
            detail_availability = _candidate_detail_availability(identity, evidence)
            city = identity.location or _candidate_location(evidence)
            work_years = _candidate_experience_years(evidence)
            candidates.append(
                {
                    "candidateId": identity.identity_id,
                    "rank": index,
                    "displayName": display_name,
                    "avatarLabel": _candidate_avatar_label(display_name),
                    "avatarColorKey": _candidate_avatar_color_key(identity.identity_id),
                    "headline": headline,
                    "company": identity.company or None,
                    "currentTitle": _candidate_current_title(identity, evidence),
                    "currentCompany": _candidate_current_company(identity, evidence),
                    "location": city,
                    "city": city,
                    "education": _candidate_education(evidence),
                    "experienceYears": work_years,
                    "workYears": work_years,
                    "age": _candidate_age(evidence),
                    "gender": _candidate_gender(evidence),
                    "activeStatus": _candidate_active_status(evidence),
                    "jobStatus": _candidate_job_status(evidence),
                    "sourceKinds": source_kinds,
                    "sourceLabel": _candidate_source_label(source_kinds),
                    "matchScore": score,
                    "matchSummary": identity.summary or None,
                    "status": identity.fit_bucket or "scored",
                    "detailAvailability": detail_availability,
                    "accessState": "allowed" if detail_availability != "unavailable" else "denied",
                    "evidenceLevel": evidence_level,
                }
            )
        return candidates

    def get_candidate_detail(self, runtime_run_id: str, candidate_id: str) -> dict[str, object]:
        identities = self.store.list_candidate_identities(runtime_run_id=runtime_run_id)
        identity = next((item for item in identities if item.identity_id == candidate_id), None)
        if identity is None:
            raise KeyError(candidate_id)
        retained_evidence = [
            item
            for item in self.store.list_candidate_evidence(runtime_run_id=runtime_run_id)
            if item.identity_id == candidate_id
        ]
        evidence = [
            item
            for item in retained_evidence
            if item.identity_id == candidate_id and item.resume_id == identity.canonical_resume_id
        ]
        detail_availability = _candidate_detail_availability(identity, evidence)
        display_name = _candidate_display_name(identity, evidence, fallback="候选人")
        city = identity.location or _candidate_location(evidence)
        work_years = _candidate_experience_years(evidence)
        source_kinds = _candidate_source_kinds(evidence)
        return {
            "candidateId": identity.identity_id,
            "displayName": display_name,
            "avatarLabel": _candidate_avatar_label(display_name),
            "avatarColorKey": _candidate_avatar_color_key(identity.identity_id),
            "headline": _candidate_headline(identity, evidence),
            "company": identity.company or _candidate_company(evidence),
            "currentTitle": _candidate_current_title(identity, evidence),
            "currentCompany": _candidate_current_company(identity, evidence),
            "location": city,
            "city": city,
            "education": _candidate_education(evidence),
            "experienceYears": work_years,
            "workYears": work_years,
            "age": _candidate_age(evidence),
            "gender": _candidate_gender(evidence),
            "activeStatus": _candidate_active_status(evidence),
            "jobStatus": _candidate_job_status(evidence),
            "sourceKinds": source_kinds,
            "sourceLabel": _candidate_source_label(source_kinds),
            "matchScore": _candidate_score(identity, evidence),
            "match": _candidate_match(identity, evidence),
            "jobIntention": _candidate_job_intention(evidence),
            "workExperience": _candidate_timeline(evidence, "workExperience"),
            "projectExperience": _candidate_timeline(evidence, "projectExperience"),
            "educationExperience": _candidate_timeline(evidence, "educationExperience"),
            "skills": _candidate_skills(evidence),
            "sourceReferences": _candidate_source_references(identity, retained_evidence),
            "sections": _candidate_detail_sections(identity, evidence),
            "evidence": _candidate_detail_evidence(evidence),
            "detailAvailability": detail_availability,
            "accessState": "allowed" if detail_availability != "unavailable" else "denied",
            "evidenceLevel": _candidate_evidence_level(evidence),
            "reasonCode": _candidate_reason_code(evidence),
        }

    def submit_next_round_requirement(
        self,
        runtime_run_id: str,
        text: str,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        command_service = RuntimeCommandService(
            store=self.store,
            requirement_extractor=_NextRoundRequirementExtractorAdapter(
                extractor=self._requirement_extractor(),
                requirement_cache_scope=runtime_run_id,
            ),
            now=self.now,
        )
        result = command_service.submit_next_round_requirement(
            runtime_run_id=runtime_run_id,
            text=text,
            target_section_hint=None,
            idempotency_key=idempotency_key,
            provenance={"source": "workbench_v2_agent", "runtimeRunId": runtime_run_id},
        )
        return {
            "amendmentId": result.amendment_id,
            "status": result.status,
            "targetRoundNo": result.target_round_no,
            "effectiveBoundary": result.effective_boundary,
            "approvedRequirementRevisionId": result.approved_requirement_revision_id,
            "reviewRequired": result.review_required,
        }

    def get_results(self, runtime_run_id: str) -> dict[str, object]:
        run = self.store.get_run(runtime_run_id)
        if run.status != "completed":
            return {
                "runtimeRunId": run.runtime_run_id,
                "status": run.status,
                "stage": run.current_stage,
                "summary": _status_summary(run.status, run.current_stage),
            }
        detail_service = RuntimeDetailService(store=self.store, now=self.now)
        try:
            summary = detail_service.prepare_final_summary(
                runtime_run_id=runtime_run_id,
                user_instruction=None,
                source_snapshot_event_seq=run.latest_event_seq,
                idempotency_key=f"workbench-v2-runtime-results:{runtime_run_id}:{run.latest_event_seq}",
            )
        except RuntimeControlError:
            return {
                "runtimeRunId": run.runtime_run_id,
                "status": run.status,
                "stage": run.current_stage,
                "summary": _status_summary(run.status, run.current_stage),
            }
        payload = summary.model_dump(mode="json")
        payload.setdefault("runtimeRunId", run.runtime_run_id)
        payload.setdefault("stage", run.current_stage)
        return payload

    def _requirement_extractor(self) -> object:
        extractor = self.requirement_extractor
        if extractor is None and self.runtime_factory is not None:
            extractor = self.runtime_factory()
        return getattr(extractor, "extract_requirements", None)

    def _executor(self) -> WorkflowRuntimeExecutor:
        if self._runtime_executor is None:
            self._runtime_executor = WorkflowRuntimeExecutor(
                store=self.store,
                settings=self.settings,
                runtime_factory=self.runtime_factory,
                runtime_run_id_factory=self.runtime_run_id_factory,
                now=self.now,
            )
        return self._runtime_executor


def _candidate_source_kinds(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    source_kinds = sorted(
        {
            "liepin" if item.source_kind == "liepin" else "cts"
            for item in evidence
            if item.source_kind in {"liepin", "cts"}
        }
    )
    return source_kinds


def _canonical_candidate_evidence(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> list[RuntimeControlCandidateEvidence]:
    return [item for item in evidence if item.resume_id == identity.canonical_resume_id]


def _candidate_source_references(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> list[dict[str, str]]:
    display_evidence_ids = set(identity.display_source_evidence_ids)
    references = sorted(
        (
            reference
            for item in evidence
            if item.evidence_id in display_evidence_ids
            for reference in item.source_references
        ),
        key=lambda reference: (reference.display_label, reference.source_kind, reference.url),
    )
    seen: set[tuple[str, str]] = set()
    projected: list[dict[str, str]] = []
    for reference in references:
        key = (reference.source_kind, reference.url)
        if key in seen:
            continue
        seen.add(key)
        projected.append(
            {
                "sourceKind": reference.source_kind,
                "displayLabel": reference.display_label,
                "url": reference.url,
            }
        )
    return projected


def _candidate_source_label(source_kinds: Sequence[str]) -> str | None:
    if "liepin" in source_kinds:
        return "猎聘"
    if "cts" in source_kinds:
        return "CTS 实验"
    return None


def _candidate_avatar_label(display_name: str) -> str:
    clean = display_name.strip()
    if not clean or clean.startswith("候选人 "):
        return "候"
    return clean[0]


def _candidate_avatar_color_key(identity_id: str) -> str:
    bucket = sum(ord(character) for character in identity_id) % 6
    return f"avatar-{bucket}"


def _candidate_display_name(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
    *,
    fallback: str,
) -> str:
    wts_name = _text_from_mapping(_candidate_wts_detail(evidence), "candidateName")
    detail_name = _first_text_from_payloads(evidence, ("safeDetail", "candidateName"))
    normalized_name = _first_text_from_payloads(evidence, ("normalizedProfile", "candidateName"))
    if identity.display_name and not identity.display_name.startswith("Candidate "):
        return identity.display_name
    return wts_name or detail_name or normalized_name or identity.display_name or fallback


def _candidate_headline(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> str | None:
    wts = _candidate_wts_detail(evidence)
    title = (
        _clean_text(identity.title)
        or _text_from_mapping(wts, "currentTitle")
        or _first_text_from_payloads(evidence, ("safeDetail", "currentTitle"))
        or _first_text_from_payloads(evidence, ("normalizedProfile", "currentTitle"))
        or _first_text_from_payloads(evidence, ("safeSummary", "currentOrRecentTitle"))
        or _first_text_from_payloads(evidence, ("candidateProfile", "expectedJobCategory"))
        or _first_text_from_payloads(evidence, ("safeSummary", "displayTitle"))
    )
    company = (
        _clean_text(identity.company)
        or _text_from_mapping(wts, "currentCompany")
        or _first_text_from_payloads(evidence, ("safeDetail", "currentCompany"))
        or _first_text_from_payloads(evidence, ("normalizedProfile", "currentCompany"))
        or _first_text_from_payloads(evidence, ("safeSummary", "currentOrRecentCompany"))
    )
    if title and company:
        return f"{title} · {company}"
    return title or company


def _candidate_current_title(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> str | None:
    return (
        _clean_text(identity.title)
        or _text_from_mapping(_candidate_wts_detail(evidence), "currentTitle")
        or _first_text_from_payloads(evidence, ("safeDetail", "currentTitle"))
        or _first_text_from_payloads(evidence, ("normalizedProfile", "currentTitle"))
        or _first_text_from_payloads(evidence, ("safeSummary", "currentOrRecentTitle"))
    )


def _candidate_current_company(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> str | None:
    return (
        _clean_text(identity.company)
        or _text_from_mapping(_candidate_wts_detail(evidence), "currentCompany")
        or _first_text_from_payloads(evidence, ("safeDetail", "currentCompany"))
        or _first_text_from_payloads(evidence, ("normalizedProfile", "currentCompany"))
        or _first_text_from_payloads(evidence, ("safeSummary", "currentOrRecentCompany"))
    )


def _candidate_location(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str | None:
    return (
        _text_from_mapping(_candidate_wts_detail(evidence), "city")
        or _first_text_from_payloads(evidence, ("candidateProfile", "nowLocation"))
        or _first_text_from_payloads(evidence, ("safeSummary", "city"))
        or _first_list_text_from_payloads(evidence, ("normalizedProfile", "locations"))
        or _first_list_text_from_payloads(evidence, ("safeDetail", "locations"))
    )


def _candidate_company(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str | None:
    return (
        _text_from_mapping(_candidate_wts_detail(evidence), "currentCompany")
        or _first_text_from_payloads(evidence, ("safeDetail", "currentCompany"))
        or _first_text_from_payloads(evidence, ("normalizedProfile", "currentCompany"))
        or _first_text_from_payloads(evidence, ("safeSummary", "currentOrRecentCompany"))
    )


def _candidate_education(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str | None:
    return (
        _text_from_mapping(_candidate_wts_detail(evidence), "education")
        or _first_text_from_payloads(evidence, ("safeSummary", "educationLevel"))
        or _first_text_from_payloads(evidence, ("normalizedProfile", "educationSummary"))
        or _first_list_text_from_payloads(evidence, ("candidateProfile", "educationSummaries"))
    )


def _candidate_experience_years(evidence: Sequence[RuntimeControlCandidateEvidence]) -> int | None:
    return (
        _int_from_mapping(_candidate_wts_detail(evidence), "workYears")
        or _first_int_from_payloads(evidence, ("safeSummary", "workYears"))
        or _first_int_from_payloads(evidence, ("normalizedProfile", "yearsOfExperience"))
        or _first_int_from_payloads(evidence, ("candidateProfile", "workYear"))
    )


def _candidate_age(evidence: Sequence[RuntimeControlCandidateEvidence]) -> int | None:
    return (
        _int_from_mapping(_candidate_wts_detail(evidence), "age")
        or _first_int_from_payloads(evidence, ("candidateProfile", "age"))
        or _first_int_from_payloads(
            evidence,
            ("safeSummary", "age"),
        )
    )


def _candidate_gender(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str | None:
    return _text_from_mapping(_candidate_wts_detail(evidence), "gender") or _first_text_from_payloads(
        evidence,
        ("candidateProfile", "gender"),
    )


def _candidate_active_status(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str | None:
    return _text_from_mapping(_candidate_wts_detail(evidence), "activeStatus") or _first_text_from_payloads(
        evidence,
        ("candidateProfile", "activeStatus"),
    )


def _candidate_job_status(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str | None:
    return _text_from_mapping(_candidate_wts_detail(evidence), "jobStatus") or _first_text_from_payloads(
        evidence,
        ("candidateProfile", "jobState"),
    )


def _candidate_score(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> int | None:
    if identity.score is not None:
        return identity.score
    scores = [item.score for item in evidence if item.score is not None]
    if scores:
        return max(scores)
    return _int_from_mapping(_candidate_match_payload(evidence), "score")


def _candidate_match(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> dict[str, object] | None:
    match_payload = _candidate_match_payload(evidence)
    summary = _text_from_mapping(match_payload, "reasoningSummary") or _text_from_mapping(
        match_payload,
        "summary",
    ) or _clean_text(identity.summary)
    payload: dict[str, object] = {
        "summary": summary,
        "strengths": _list_texts_from_mapping(match_payload, "strengths"),
        "weaknesses": _list_texts_from_mapping(match_payload, "weaknesses"),
        "score": _candidate_score(identity, evidence),
        "fitBucket": _text_from_mapping(match_payload, "fitBucket") or _clean_text(identity.fit_bucket),
    }
    return {key: value for key, value in payload.items() if value not in (None, [], "")} or None


def _candidate_match_payload(evidence: Sequence[RuntimeControlCandidateEvidence]) -> Mapping[str, object]:
    payloads = [(item, _mapping_value(item.payload.get("match"))) for item in evidence]
    ranked_payloads = [
        (item, payload)
        for item, payload in payloads
        if payload
    ]
    ranked_payloads.sort(key=lambda candidate: _match_payload_rank(candidate[0], candidate[1]))
    return _merge_payload_mappings(payload for _item, payload in ranked_payloads)


def _candidate_wts_detail(evidence: Sequence[RuntimeControlCandidateEvidence]) -> Mapping[str, object]:
    payloads = [(item, _mapping_value(item.payload.get("wtsDetail"))) for item in evidence]
    ranked_payloads = [
        (item, payload)
        for item, payload in payloads
        if payload
    ]
    ranked_payloads.sort(key=lambda candidate: _wts_detail_payload_rank(candidate[0], candidate[1]))
    return _merge_payload_mappings(payload for _item, payload in ranked_payloads)


def _wts_detail_payload_rank(
    evidence: RuntimeControlCandidateEvidence,
    payload: Mapping[str, object],
) -> tuple[int, str, str]:
    del payload
    return (
        _candidate_evidence_level_priority(evidence.evidence_level),
        evidence.updated_at,
        evidence.evidence_id,
    )


def _match_payload_rank(
    evidence: RuntimeControlCandidateEvidence,
    payload: Mapping[str, object],
) -> tuple[int, int, int, str, str]:
    return (
        _match_signal_priority(payload),
        _int_from_mapping(payload, "score") or evidence.score or -1,
        _candidate_evidence_level_priority(evidence.evidence_level),
        evidence.updated_at,
        evidence.evidence_id,
    )


def _match_signal_priority(payload: Mapping[str, object]) -> int:
    signals = 0
    if _text_from_mapping(payload, "reasoningSummary") or _text_from_mapping(payload, "summary"):
        signals += 1
    if _list_texts_from_mapping(payload, "strengths"):
        signals += 1
    if _list_texts_from_mapping(payload, "weaknesses"):
        signals += 1
    return signals


def _candidate_evidence_level_priority(evidence_level: str) -> int:
    if evidence_level == "final":
        return 4
    if evidence_level == "detail":
        return 3
    if evidence_level in {"summary", "card"}:
        return 2
    return 1


def _merge_payload_mappings(payloads: Iterable[Mapping[str, object]]) -> dict[str, object]:
    merged: dict[str, object] = {}
    for payload in payloads:
        _merge_payload_mapping(merged, payload)
    return merged


def _merge_payload_mapping(target: dict[str, object], source: Mapping[str, object]) -> None:
    for key, value in source.items():
        if not _payload_value_present(value):
            continue
        current = target.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            nested = dict(_mapping_value(current))
            _merge_payload_mapping(nested, _mapping_value(value))
            target[key] = nested
            continue
        target[key] = value


def _payload_value_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_payload_value_present(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_payload_value_present(item) for item in value)
    return True


def _candidate_job_intention(evidence: Sequence[RuntimeControlCandidateEvidence]) -> dict[str, object] | None:
    intention = _mapping_value(_candidate_wts_detail(evidence).get("jobIntention"))
    payload: dict[str, object] = {
        key: text
        for key in ("expectedRole", "expectedIndustry", "expectedCity", "expectedSalary")
        if (text := _text_from_mapping(intention, key)) is not None
    }
    return payload or None


def _candidate_timeline(evidence: Sequence[RuntimeControlCandidateEvidence], key: str) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for item in _mapping_sequence(_candidate_wts_detail(evidence).get(key)):
        entry: dict[str, object | None] = {
            "dateRange": _text_from_mapping(item, "dateRange") or _text_from_mapping(item, "duration"),
            "title": _text_from_mapping(item, "title"),
            "company": _text_from_mapping(item, "company"),
            "school": _text_from_mapping(item, "school"),
            "major": _text_from_mapping(item, "major"),
            "degree": _text_from_mapping(item, "degree"),
            "name": _text_from_mapping(item, "name"),
            "role": _text_from_mapping(item, "role"),
            "description": _text_from_mapping(item, "description") or _text_from_mapping(item, "summary"),
        }
        clean_entry: dict[str, object] = {field: text for field, text in entry.items() if text is not None}
        if clean_entry:
            entries.append(clean_entry)
    return entries


def _candidate_skills(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    skills = _string_sequence(_candidate_wts_detail(evidence).get("skills"))
    return _unique_strings(skills)[:24] or _skill_items(evidence)


def _candidate_evidence_level(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str:
    levels = {item.evidence_level for item in evidence}
    if "final" in levels:
        return "final"
    if "detail" in levels:
        return "detail"
    if levels & {"summary", "card"}:
        return "summary"
    return "unknown"


def _candidate_detail_availability(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> str:
    if (
        identity.summary
        or _candidate_wts_detail(evidence)
        or any(_candidate_evidence_sections(item) for item in evidence)
    ):
        return "available"
    return "unavailable"


def _candidate_detail_sections(
    identity: RuntimeControlCandidateIdentity,
    evidence: Sequence[RuntimeControlCandidateEvidence],
) -> list[dict[str, object]]:
    sections = [
        _section(
            "匹配程度",
            [
                _prefix("推荐理由", identity.summary),
            ],
        ),
        _section(
            "求职意向",
            [
                *_texts_from_payloads(evidence, ("candidateProfile", "expectedJobCategory"), prefix="期望岗位"),
                *_texts_from_payloads(evidence, ("candidateProfile", "expectedIndustry"), prefix="期望行业"),
                *_texts_from_payloads(evidence, ("candidateProfile", "expectedLocation"), prefix="期望城市"),
                *_texts_from_payloads(evidence, ("safeSummary", "expectedCity"), prefix="期望城市"),
                *_texts_from_payloads(evidence, ("candidateProfile", "expectedSalary"), prefix="期望薪资"),
                *_texts_from_payloads(evidence, ("safeSummary", "jobIntention"), prefix="求职意向"),
            ],
        ),
        _section("工作经历", _work_experience_items(evidence)),
        _section("项目经验", _project_items(evidence)),
        _section("教育经历", _education_items(evidence)),
        _section("技能标签", _skill_items(evidence)),
    ]
    return [section for section in sections if section["items"]]


def _candidate_detail_evidence(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    items: list[str] = []
    for item in sorted(evidence, key=lambda item: item.evidence_id):
        source = "猎聘" if item.source_kind == "liepin" else "CTS" if item.source_kind == "cts" else item.source_kind
        level = "detail" if item.evidence_level == "detail" else "final" if item.evidence_level == "final" else "summary"
        items.append(f"来源：{source} {level} 证据")
        provider_rank = _int_from_mapping(item.payload, "providerRank")
        if provider_rank is not None:
            items.append(f"检索排名：第 {provider_rank} 位")
    return _unique_strings(items)[:8]


def _candidate_reason_code(evidence: Sequence[RuntimeControlCandidateEvidence]) -> str | None:
    for item in evidence:
        reason = _text_from_mapping(item.payload, "reasonCode")
        if reason is not None:
            return reason
    return None


def _candidate_evidence_sections(item: RuntimeControlCandidateEvidence) -> list[dict[str, object]]:
    return _candidate_detail_sections(
        RuntimeControlCandidateIdentity(
            runtime_run_id=item.runtime_run_id,
            identity_id=item.identity_id,
            canonical_resume_id=item.resume_id,
            display_name="",
            payload_hash="",
            updated_at=item.updated_at,
        ),
        [item],
    )


def _work_experience_items(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    items: list[str] = []
    for payload in _payload_mappings(evidence, "safeDetail"):
        for entry in _mapping_sequence(payload.get("workExperienceList")):
            text = _format_experience_entry(entry)
            if text:
                items.append(text)
    for payload in _payload_mappings(evidence, "normalizedProfile"):
        for entry in _mapping_sequence(payload.get("recentExperiences")):
            text = _format_experience_entry(entry)
            if text:
                items.append(text)
    items.extend(_list_texts_from_payloads(evidence, ("candidateProfile", "workExperienceSummaries")))
    items.extend(_list_texts_from_payloads(evidence, ("candidateProfile", "workSummaries")))
    items.extend(
        text
        for item in _texts_from_payloads(evidence, ("safeSummary", "recentExperienceText"))
        if (text := _clean_candidate_detail_text(item))
    )
    return _unique_strings(items)[:8]


def _project_items(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    return _unique_strings(_list_texts_from_payloads(evidence, ("candidateProfile", "projectNames")))[:8]


def _education_items(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    items: list[str] = []
    for payload in _payload_mappings(evidence, "safeDetail"):
        for entry in _mapping_sequence(payload.get("educationList")):
            text = _format_education_entry(entry)
            if text:
                items.append(text)
    school_names = _list_texts_from_payloads(evidence, ("safeSummary", "schoolNames"))
    major_names = _list_texts_from_payloads(evidence, ("safeSummary", "majorNames"))
    education_level = _texts_from_payloads(evidence, ("safeSummary", "educationLevel"))
    if school_names or major_names or education_level:
        school = "、".join(school_names)
        major = "、".join(major_names)
        level = "、".join(education_level)
        items.append("｜".join(part for part in [school, major, level] if part))
    items.extend(_list_texts_from_payloads(evidence, ("candidateProfile", "educationSummaries")))
    items.extend(_texts_from_payloads(evidence, ("normalizedProfile", "educationSummary")))
    return _unique_strings(items)[:8]


def _skill_items(evidence: Sequence[RuntimeControlCandidateEvidence]) -> list[str]:
    items: list[str] = []
    for path in (
        ("safeDetail", "skills"),
        ("safeDetail", "skillTags"),
        ("safeDetail", "tags"),
        ("safeDetail", "keywords"),
        ("safeSummary", "skillTags"),
        ("normalizedProfile", "skills"),
    ):
        items.extend(_list_texts_from_payloads(evidence, path))
    return _unique_strings(items)[:20]


def _format_experience_entry(entry: Mapping[str, object]) -> str | None:
    parts = [
        _clean_candidate_detail_text(_first_present_text(entry, ("duration", "time", "dateRange", "startEndTime"))),
        _clean_candidate_detail_text(_first_present_text(entry, ("company", "companyName", "org", "organization"))),
        _clean_candidate_detail_text(_first_present_text(entry, ("title", "position", "positionName", "jobTitle"))),
    ]
    summary = _clean_candidate_detail_text(_first_present_text(entry, ("summary", "description", "workContent", "content")))
    heading = "｜".join(part for part in parts if part)
    if heading and summary:
        return f"{heading}。工作内容：{summary}"
    return heading or summary


def _format_education_entry(entry: Mapping[str, object]) -> str | None:
    parts = [
        _clean_candidate_detail_text(_first_present_text(entry, ("duration", "time", "dateRange", "startEndTime"))),
        _clean_candidate_detail_text(_first_present_text(entry, ("school", "schoolName", "college", "university"))),
        _clean_candidate_detail_text(_first_present_text(entry, ("major", "majorName", "speciality"))),
        _clean_candidate_detail_text(_first_present_text(entry, ("degree", "education", "educationLevel"))),
    ]
    return "｜".join(part for part in parts if part) or None


def _section(title: str, items: Sequence[str | None]) -> dict[str, object]:
    return {"title": title, "items": _unique_strings([item for item in items if item])}


def _prefix(label: str, value: object) -> str | None:
    text = _clean_text(value)
    return f"{label}：{text}" if text else None


def _texts_from_payloads(
    evidence: Sequence[RuntimeControlCandidateEvidence],
    path: tuple[str, str],
    *,
    prefix: str | None = None,
) -> list[str]:
    items = [_text_from_mapping(payload, path[1]) for payload in _payload_mappings(evidence, path[0])]
    texts = [item for item in items if item is not None]
    if prefix is None:
        return texts
    return [f"{prefix}：{item}" for item in texts]


def _list_texts_from_mapping(payload: Mapping[str, object], key: str) -> list[str]:
    return _string_sequence(payload.get(key))


def _list_texts_from_payloads(
    evidence: Sequence[RuntimeControlCandidateEvidence],
    path: tuple[str, str],
) -> list[str]:
    items: list[str] = []
    for payload in _payload_mappings(evidence, path[0]):
        items.extend(_candidate_detail_string_sequence(payload.get(path[1])))
    return items


def _first_text_from_payloads(
    evidence: Sequence[RuntimeControlCandidateEvidence],
    path: tuple[str, str],
) -> str | None:
    texts = _texts_from_payloads(evidence, path)
    return texts[0] if texts else None


def _first_list_text_from_payloads(
    evidence: Sequence[RuntimeControlCandidateEvidence],
    path: tuple[str, str],
) -> str | None:
    texts = _list_texts_from_payloads(evidence, path)
    return texts[0] if texts else None


def _first_int_from_payloads(
    evidence: Sequence[RuntimeControlCandidateEvidence],
    path: tuple[str, str],
) -> int | None:
    for payload in _payload_mappings(evidence, path[0]):
        value = _int_from_mapping(payload, path[1])
        if value is not None:
            return value
    return None


def _payload_mappings(
    evidence: Sequence[RuntimeControlCandidateEvidence],
    key: str,
) -> list[Mapping[str, object]]:
    return [_mapping_value(item.payload.get(key)) for item in evidence if _mapping_value(item.payload.get(key))]


def _mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [_mapping_value(item) for item in value if _mapping_value(item)]


def _mapping_value(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _string_sequence(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [text for item in value if (text := _clean_text(item))]


def _candidate_detail_string_sequence(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [text for item in value if (text := _clean_candidate_detail_text(item))]


def _first_present_text(value: Mapping[str, object], keys: Sequence[str]) -> str | None:
    for key in keys:
        text = _text_from_mapping(value, key)
        if text is not None:
            return text
    return None


def _text_from_mapping(value: Mapping[str, object], key: str) -> str | None:
    return _clean_text(value.get(key))


def _int_from_mapping(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if isinstance(item, bool):
        return None
    if isinstance(item, int):
        return item
    if isinstance(item, str):
        try:
            return int(item)
        except ValueError:
            return None
    return None


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


_CANDIDATE_DETAIL_STOP_PREFIXES = (
    "声明：",
    "简历备注",
    "简历洞察",
    "简历信息",
    "教育经历",
    "语言能力",
    "自我评价",
    "津ICP备",
    "ICP备",
    "ICP经营许可证",
)

_CANDIDATE_DETAIL_NOISE_LINES = {
    "去查看",
    "添加备注",
    "共 条",
    "0",
    "新手任务",
    "账号问候",
    "页面导航",
}


def _clean_candidate_detail_text(value: object) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    kept: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(_CANDIDATE_DETAIL_STOP_PREFIXES):
            break
        if line.startswith(("http://", "https://", "URL:")):
            continue
        if line in _CANDIDATE_DETAIL_NOISE_LINES:
            continue
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    return cleaned or None


def _unique_strings(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _extract_requirements(
    extract_requirements: object,
    *,
    job_title: str,
    jd_text: str,
    notes: str | None,
    requirement_cache_scope: str,
) -> RequirementSheet:
    if not isinstance(extract_requirements, Callable):
        raise RuntimeError(REQUIREMENT_EXTRACTOR_UNAVAILABLE)
    parameters = signature(extract_requirements).parameters
    if "jd_text" in parameters:
        if not isinstance(extract_requirements, _RequirementExtractorWithJdText):
            raise RuntimeError(REQUIREMENT_EXTRACTOR_UNAVAILABLE)
        return _requirement_sheet_result(
            extract_requirements(
                job_title=job_title,
                jd_text=jd_text,
                notes=notes,
                requirement_cache_scope=requirement_cache_scope,
            )
        )
    if "jd" in parameters:
        if not isinstance(extract_requirements, _RequirementExtractorWithJd):
            raise RuntimeError(REQUIREMENT_EXTRACTOR_UNAVAILABLE)
        return _requirement_sheet_result(
            extract_requirements(
                job_title=job_title,
                jd=jd_text,
                notes=notes or "",
                requirement_cache_scope=requirement_cache_scope,
            )
        )
    raise RuntimeError(REQUIREMENT_EXTRACTOR_UNAVAILABLE)


def _requirement_sheet_result(value: object) -> RequirementSheet:
    if isinstance(value, RequirementSheet):
        return value
    return RequirementSheet.model_validate(value)


def _draft_with_supplement_items(
    base_draft: RequirementDraft,
    *,
    supplement: RequirementSheet,
    draft_revision_id: str,
    created_at: str,
) -> RequirementDraft:
    draft = base_draft.model_copy(
        deep=True,
        update={
            "draft_revision_id": draft_revision_id,
            "base_revision_id": base_draft.draft_revision_id,
            "created_at": created_at,
        },
    )
    supplement_draft = draft_from_requirement_sheet(
        conversation_id=base_draft.conversation_id,
        draft_revision_id=f"{draft_revision_id}_supplement",
        base_revision_id=None,
        requirement_sheet=supplement,
        source="workbench_v2_agent",
        created_at=created_at,
    )
    for supplement_section in supplement_draft.sections:
        target_section = draft.section(supplement_section.section_id)
        seen = {_draft_item_key(item) for item in target_section.items if item.status != "deleted"}
        for item in supplement_section.items:
            key = _draft_item_key(item)
            if key in seen:
                continue
            target_section.items.append(
                item.model_copy(
                    deep=True,
                    update={
                        "item_id": _new_id("reqitem"),
                        "source": "workbench_v2_agent",
                        "sort_order": (len(target_section.items) + 1) * 10,
                    },
                )
            )
            seen.add(key)
    return draft


def _draft_item_key(item: object) -> tuple[str, str, str]:
    text = str(getattr(item, "text", "") or "").strip().casefold()
    value = getattr(item, "value", None)
    encoded_value = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else repr(value)
    return (text, type(value).__name__, encoded_value)


def _start_operation_key(*, conversation_id: str, idempotency_key: str | None) -> str:
    if idempotency_key is None:
        return f"{conversation_id}:primary"
    return f"{conversation_id}:primary:{idempotency_key}"


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode('utf-8')).hexdigest()[:32]}"


def _runtime_input_values(runtime_input: WorkbenchV2RuntimeInput | None) -> tuple[str, str, str | None]:
    if runtime_input is None:
        raise ValueError(RUNTIME_INPUT_REQUIRED)
    job_title = _required_input_text(getattr(runtime_input, "jobTitle", None))
    jd_text = _required_input_text(getattr(runtime_input, "jd", None))
    notes = getattr(runtime_input, "notes", None)
    if isinstance(notes, str):
        notes = notes.strip() or None
    return job_title, jd_text, notes


def _required_input_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(RUNTIME_INPUT_REQUIRED)
    text = value.strip()
    if not text:
        raise ValueError(RUNTIME_INPUT_REQUIRED)
    return text


def _selected_item_ids(draft: RequirementDraft) -> list[str]:
    return [
        item.item_id
        for section in draft.sections
        for item in section.items
        if item.selected and item.status == "resolved"
    ]


def _deselected_item_ids(draft: RequirementDraft) -> list[str]:
    return [
        item.item_id
        for section in draft.sections
        for item in section.items
        if not item.selected or item.status in {"deleted", "moved", "rejected"}
    ]


def _runtime_state_from_run_status(status: str) -> str:
    if status == "queued":
        return "queued"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return "running"


def _runtime_state_from_event_status(status: str) -> str:
    if status == "queued":
        return "queued"
    if status == "completed":
        return "completed"
    if status in {"blocked", "failed"}:
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return "running"


def _progress_payload_from_runtime_event(event: object) -> dict[str, object] | None:
    if getattr(event, "visibility", "internal") != "public":
        return None
    event_type = str(getattr(event, "event_type", ""))
    if event_type not in _PROJECTED_RUNTIME_EVENT_TYPES:
        return None
    summary = _runtime_event_user_summary(event)
    if summary is None:
        return None
    status, stage = _safe_public_runtime_metadata(
        getattr(event, "status", ""),
        getattr(event, "stage", ""),
    )
    payload: dict[str, object] = {
        "runtimeRunId": getattr(event, "runtime_run_id"),
        "runtimeEventSeq": int(getattr(event, "event_seq")),
        "runtimeEventType": event_type,
        "status": status,
        "stage": stage,
        "summary": summary,
    }
    round_no = getattr(event, "round_no", None)
    if isinstance(round_no, int):
        payload["roundNo"] = round_no
    source_id = getattr(event, "source_id", None)
    if isinstance(source_id, str) and source_id:
        payload["sourceId"] = source_id
    public_payload = getattr(event, "payload", {})
    public_payload = public_payload if isinstance(public_payload, dict) else {}
    source_kind = _payload_text(public_payload.get("sourceKind"))
    if source_kind is not None:
        payload["sourceKind"] = source_kind
    counts = public_payload.get("counts")
    if isinstance(counts, dict):
        payload["counts"] = {str(key): value for key, value in counts.items() if isinstance(value, int)}
    details = public_payload.get("details")
    safe_details = safe_runtime_progress_details(details, stage=payload["stage"])
    if safe_details:
        payload["details"] = safe_details
    safe_reason_code = safe_runtime_progress_reason_code(public_payload.get("safeReasonCode"))
    if safe_reason_code is not None:
        payload["safeReasonCode"] = safe_reason_code
    return payload


_PROJECTED_RUNTIME_EVENT_TYPES = {
    "runtime_run_started",
    "runtime_requirements_started",
    "runtime_requirements_completed",
    "runtime_controller_started",
    "runtime_search_started",
    "runtime_round_query_ready",
    "runtime_round_source_dispatch",
    "runtime_round_source_result",
    "runtime_round_merge_completed",
    "runtime_search_completed",
    "runtime_scoring_started",
    "runtime_scoring_completed",
    "runtime_round_scoring_completed",
    "runtime_resume_quality_comment_completed",
    "runtime_reflection_started",
    "runtime_reflection_completed",
    "runtime_round_feedback_completed",
    "runtime_round_completed",
    "runtime_search_failed",
    "runtime_run_failed",
    "runtime_run_completed",
    "runtime_finalization_completed",
}


def _runtime_event_user_summary(event: object) -> str | None:
    event_type = str(getattr(event, "event_type", ""))
    stage = str(getattr(event, "stage", ""))
    status = str(getattr(event, "status", ""))
    summary = str(getattr(event, "summary", "") or "").strip()
    payload = getattr(event, "payload", {})
    payload = payload if isinstance(payload, dict) else {}
    round_no = getattr(event, "round_no", None)
    round_prefix = f"第 {round_no} 轮" if isinstance(round_no, int) else "本轮"

    if event_type == "runtime_run_started":
        return "招聘流程已开始。"
    if event_type == "runtime_requirements_started":
        return "正在解析确认后的岗位需求。"
    if event_type == "runtime_requirements_completed":
        return "岗位需求解析完成。"
    if event_type == "runtime_controller_started":
        return f"{round_prefix}正在规划检索策略。"
    if event_type == "runtime_search_started":
        return f"{round_prefix}开始检索候选人。"
    if event_type == "runtime_round_query_ready":
        return f"{round_prefix}查询策略已生成。"
    if event_type == "runtime_round_source_dispatch":
        return f"{round_prefix}已向猎聘发起候选人检索。"
    if event_type == "runtime_round_source_result":
        counts = payload.get("counts")
        counts = counts if isinstance(counts, dict) else {}
        if status == "blocked":
            reason = safe_runtime_progress_reason_code(payload.get("safeReasonCode"))
            failure_reason = _runtime_failure_reason(reason) if reason is not None else "猎聘检索受阻，请稍后重试。"
            return f"{round_prefix}猎聘检索受阻：{failure_reason}"
        returned = counts.get("roundReturned")
        identities = counts.get("roundIdentities")
        if isinstance(returned, int) and isinstance(identities, int):
            return f"{round_prefix}猎聘检索完成：返回 {returned} 条，新增 {identities} 位候选人。"
        return f"{round_prefix}猎聘检索结果已更新。"
    if event_type == "runtime_round_merge_completed":
        counts = payload.get("counts")
        counts = counts if isinstance(counts, dict) else {}
        merged = counts.get("mergedIdentities")
        if isinstance(merged, int):
            return f"{round_prefix}候选人合并完成：新增 {merged} 位候选人。"
        return f"{round_prefix}候选人合并完成。"
    if event_type == "runtime_search_completed":
        return f"{round_prefix}检索完成。"
    if event_type == "runtime_scoring_started":
        return f"{round_prefix}开始候选人评分。"
    if event_type == "runtime_scoring_completed":
        return f"{round_prefix}候选人评分完成。"
    if event_type == "runtime_round_scoring_completed":
        counts = payload.get("counts")
        counts = counts if isinstance(counts, dict) else {}
        top_pool_count = counts.get("topPoolCount")
        if isinstance(top_pool_count, int):
            return f"{round_prefix}评分完成，{top_pool_count} 位候选人进入 Top Pool。"
        return f"{round_prefix}评分完成。"
    if event_type == "runtime_resume_quality_comment_completed":
        return f"{round_prefix}简历质量评估完成。"
    if event_type == "runtime_reflection_started":
        return f"{round_prefix}开始复盘检索效果。"
    if event_type == "runtime_reflection_completed":
        return f"{round_prefix}复盘完成，准备调整下一轮检索策略。"
    if event_type == "runtime_round_feedback_completed":
        return f"{round_prefix}复盘完成，准备调整下一轮检索策略。"
    if event_type == "runtime_round_completed":
        return f"{round_prefix}完成。"
    if event_type == "runtime_search_failed":
        return f"{round_prefix}检索失败：{_runtime_failure_summary(payload, summary=summary)}"
    if event_type == "runtime_run_failed":
        return f"招聘流程失败：{_runtime_failure_summary(payload, summary=summary)}"
    terminal_summary = runtime_event_terminal_summary(event_type)
    if terminal_summary is not None:
        return terminal_summary
    if summary and summary not in _INTERNAL_RUNTIME_EVENT_SUMMARIES:
        return _runtime_event_summary(status, stage)
    return None


def _payload_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _safe_public_runtime_metadata(status: object, stage: object) -> tuple[str, str]:
    normalized = normalize_runtime_progress_payload({"status": status, "stage": stage})
    public_status = normalized.get("status")
    public_stage = normalized.get("stage")
    return (
        public_status if isinstance(public_status, str) else "running",
        public_stage if isinstance(public_stage, str) else "runtime",
    )


def _runtime_failure_summary(payload: Mapping[str, object], *, summary: str) -> str:
    for value in (payload.get("safeReasonCode"), payload.get("reasonCode"), payload.get("errorCode"), summary):
        reason = safe_runtime_progress_reason_code(value)
        if reason is not None:
            return _runtime_failure_reason(reason)
    return "运行失败，请查看详情。"


def _runtime_failure_reason(reason: str) -> str:
    if reason == "liepin_opencli_stale_ref":
        return "猎聘页面引用已失效，需要刷新检索页面后重试。"
    if reason in {"liepin_opencli_extension_disconnected", "source_browser_extension_disconnected"}:
        return "猎聘浏览器桥扩展未连接，请确认扩展已连接后重试。"
    if reason in {"liepin_opencli_filter_unapplied", "source_filter_unavailable", "source_filter_partial"}:
        return "猎聘筛选条件未成功应用，请刷新猎聘页面后重试。"
    return reason


def _status_summary(status: str, stage: str) -> str:
    summaries = {
        "queued": "招聘流程已排队，等待开始。",
        "starting": f"招聘流程正在启动，当前阶段：{_stage_label(stage)}。",
        "running": f"招聘流程运行中，当前阶段：{_stage_label(stage)}。",
        "pause_requested": "招聘流程正在暂停。",
        "paused": "招聘流程已暂停。",
        "resume_requested": "招聘流程正在恢复。",
        "cancellation_requested": "招聘流程正在取消。",
        "cancelled": "招聘流程已取消。",
        "completed": "招聘流程已完成。",
        "failed": "招聘流程失败，请查看运行详情。",
    }
    return summaries.get(status, "招聘流程状态未知。")


def _latest_runtime_event_summary(
    store: RuntimeControlStore,
    runtime_run_id: str,
    latest_event_seq: int,
    run_status: str,
) -> str | None:
    if latest_event_seq <= 0:
        return None
    page = store.list_public_events(
        runtime_run_id=runtime_run_id,
        after_seq=max(0, latest_event_seq - 20),
        limit=20,
    )
    for event in reversed(page.events):
        if event.status not in _summary_event_statuses_for_run(run_status) and event.event_type not in {
            "runtime_search_failed",
            "runtime_run_failed",
        }:
            continue
        summary = _runtime_event_user_summary(event)
        if summary:
            return summary
    return None


_INTERNAL_RUNTIME_EVENT_SUMMARIES = {
    "workflow run queued",
    "executor starting",
    "executor started",
    "checkpoint written",
    "runtime worker claimed run",
    "run completed",
}


def _summary_event_statuses_for_run(run_status: str) -> set[str]:
    if run_status == "running":
        return {"running", "blocked", "partial"}
    if run_status == "failed":
        return {"failed"}
    if run_status == "completed":
        return {"completed", "partial"}
    return set()


def _runtime_event_summary(status: str, stage: str) -> str:
    stage_label = _stage_label(stage)
    if status == "failed":
        return "招聘流程失败，请查看运行详情。"
    if status in {"blocked", "partial"}:
        return f"招聘流程{_status_label(status)}。"
    if status == "running":
        return f"招聘流程运行中，当前阶段：{stage_label}。"
    return _status_summary(status, stage)


def _status_label(status: str) -> str:
    labels = {
        "blocked": "已阻塞",
        "partial": "部分完成",
    }
    return labels.get(status, "未知状态")


def _stage_label(stage: str) -> str:
    labels = {
        "queued": "排队中",
        "starting": "启动中",
        "startup": "启动中",
        "runtime": "运行中",
        "round": "检索轮次",
        "command": "指令处理",
        "resume": "恢复运行",
        "finalization": "结果汇总",
    }
    return labels.get(stage, "未标记")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
