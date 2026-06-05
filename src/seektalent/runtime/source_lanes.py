from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re

from seektalent.models import (
    NormalizedResume,
    ResumeCandidate,
    RuntimeCandidateIdentity,
    RuntimeCanonicalResumeSelection,
    RunState,
    RuntimeIdentityConflict,
    RuntimeIdentitySignals,
    RuntimeSourceEvidence,
)
from seektalent.runtime.candidate_intake import normalize_runtime_candidates
from seektalent.source_contracts import (
    DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY,
    RuntimeApprovedDetailLease,
    RuntimeDetailEnrichmentResult,
    RuntimeDetailRecommendation,
    RuntimeEvidenceLevel,
    RuntimeSourceBudgetPolicy,
    RuntimeSourceLaneEvent,
    RuntimeSourceLaneEventType,
    RuntimeSourceLaneMode,
    RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
    RuntimeSourceLaneStatus,
    SourceLaneResult,
)

SourceKind = str

__all__ = [
    "DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY",
    "RuntimeApprovedDetailLease",
    "RuntimeDetailEnrichmentResult",
    "RuntimeDetailRecommendation",
    "RuntimeEvidenceLevel",
    "RuntimeSourceBudgetPolicy",
    "RuntimeSourceLaneEvent",
    "RuntimeSourceLaneEventType",
    "RuntimeSourceLaneMode",
    "RuntimeSourceLanePlan",
    "RuntimeSourceLaneRequest",
    "RuntimeSourceLaneResult",
    "RuntimeSourceLaneStatus",
    "SourceKind",
    "apply_source_lane_result",
    "append_source_evidence_once",
    "build_runtime_source_plan",
    "clone_run_state_for_source_lane",
    "merge_source_lane_result_updates",
    "normalize_source_kinds",
    "rebuild_candidate_identities",
    "runtime_source_lane_result_from_source_result",
]


def runtime_source_lane_result_from_source_result(result: SourceLaneResult) -> RuntimeSourceLaneResult:
    return RuntimeSourceLaneResult(
        runtime_run_id=result.runtime_run_id,
        source_plan_id=result.source_plan_id,
        source_lane_run_id=result.source_lane_run_id,
        source=result.source_id,
        lane_mode=result.lane_mode,
        attempt=result.attempt,
        status=result.status,
        candidate_store_updates=result.candidate_store_updates,
        normalized_store_updates=result.normalized_store_updates,
        source_evidence_updates=result.source_evidence_updates,
        raw_candidate_count=result.raw_candidate_count,
        provider_snapshot_refs=result.provider_snapshot_refs,
        safe_summary_refs=result.safe_summary_refs,
        blocked_reason_code=result.blocked_reason_code,
        stop_reason_code=result.stop_reason_code,
        retryable=result.retryable,
        safe_error_summary=result.safe_error_summary,
        error_ref=result.error_ref,
    )


def normalize_source_kinds(source_kinds: Sequence[str] | None) -> tuple[SourceKind, ...]:
    if not source_kinds:
        return ()
    normalized: list[SourceKind] = []
    for source in source_kinds:
        source_id = str(source).strip()
        if not source_id:
            raise ValueError("runtime_source_empty")
        if source_id in normalized:
            raise ValueError(f"Duplicate runtime source: {source_id}")
        normalized.append(source_id)
    return tuple(normalized)


def build_runtime_source_plan(
    *,
    source_kinds: Sequence[str] | None,
    settings: object,
    runtime_run_id: str,
    source_context: Mapping[str, str | int | bool | None] | None = None,
    **extra_context: object,
) -> tuple[RuntimeSourceLanePlan, ...]:
    del settings, source_context, extra_context
    plans: list[RuntimeSourceLanePlan] = []
    for index, source in enumerate(normalize_source_kinds(source_kinds)):
        plans.append(
            RuntimeSourceLanePlan(
                source_plan_id=f"{runtime_run_id}:source:{index}:{source}",
                runtime_run_id=runtime_run_id,
                source=source,
                label=source,
            )
        )
    return tuple(plans)


def apply_source_lane_result(
    *,
    run_state: RunState,
    result: RuntimeSourceLaneResult,
    source_order: Mapping[SourceKind, int],
) -> None:
    merge_source_lane_result_updates(run_state=run_state, result=result, source_order=source_order)


def merge_source_lane_result_updates(
    *,
    run_state: RunState,
    result: RuntimeSourceLaneResult,
    source_order: Mapping[SourceKind, int],
    rebuild_identity: bool = True,
) -> None:
    _append_source_lane_public_payload_once(run_state, result)
    if result.status == "blocked":
        return

    for resume_id, candidate in result.candidate_store_updates.items():
        run_state.candidate_store[resume_id] = candidate
        if resume_id not in run_state.seen_resume_ids:
            run_state.seen_resume_ids.append(resume_id)

    run_state.normalized_store.update(result.normalized_store_updates)
    normalize_runtime_candidates(
        run_state=run_state,
        candidates=result.candidate_store_updates.values(),
        round_no=0,
        tracer=None,
    )
    append_source_evidence_once(
        run_state,
        result.source_evidence_updates,
        source_order=source_order,
    )
    if rebuild_identity:
        _rebuild_identity_state(run_state, source_order=source_order)


def rebuild_candidate_identities(
    run_state: RunState,
    *,
    source_order: Mapping[SourceKind, int],
) -> None:
    _rebuild_identity_state(run_state, source_order=source_order)


def clone_run_state_for_source_lane(run_state: RunState) -> RunState:
    return run_state.model_copy(
        deep=True,
        update={
            "seen_resume_ids": [],
            "candidate_store": {},
            "normalized_store": {},
            "source_evidence_by_resume_id": {},
            "source_evidence_by_identity_id": {},
            "candidate_identity_by_resume_id": {},
            "candidate_identities": {},
            "identity_aliases_by_canonical_id": {},
            "identity_conflicts": [],
            "canonical_resume_by_identity_id": {},
            "source_coverage_summary": None,
            "finalization_revisions": [],
            "runtime_source_lane_results": [],
            "scorecards_by_resume_id": {},
            "top_pool_ids": [],
            "round_history": [],
        },
    )


def _append_source_lane_public_payload_once(run_state: RunState, result: RuntimeSourceLaneResult) -> None:
    payload = result.to_public_payload()
    existing_keys = {
        (
            str(item.get("runtime_run_id") or ""),
            str(item.get("source") or ""),
            str(item.get("source_lane_run_id") or ""),
            _payload_int(item.get("attempt")),
        )
        for item in run_state.runtime_source_lane_results
        if isinstance(item, dict)
    }
    key = (
        str(payload.get("runtime_run_id") or ""),
        str(payload.get("source") or ""),
        str(payload.get("source_lane_run_id") or ""),
        _payload_int(payload.get("attempt")),
    )
    if key not in existing_keys:
        run_state.runtime_source_lane_results.append(payload)


def _payload_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def append_source_evidence_once(
    run_state: RunState,
    evidence_updates: tuple[RuntimeSourceEvidence, ...],
    *,
    source_order: Mapping[SourceKind, int],
) -> None:
    for evidence in evidence_updates:
        entries = run_state.source_evidence_by_resume_id.setdefault(evidence.candidate_resume_id, [])
        if any(item.evidence_id == evidence.evidence_id for item in entries):
            continue
        entries.append(evidence)
        entries.sort(key=lambda item: _evidence_sort_key(item, source_order))


def _rebuild_identity_state(
    run_state: RunState,
    *,
    source_order: Mapping[SourceKind, int],
) -> None:
    index = RuntimeCandidateIdentityIndex()
    candidate_identity_by_resume_id: dict[str, str] = {}
    source_evidence_by_identity_id: dict[str, list[RuntimeSourceEvidence]] = {}

    for resume_id in run_state.seen_resume_ids:
        if resume_id not in run_state.candidate_store:
            continue
        candidate = run_state.candidate_store[resume_id]
        evidence_items = run_state.source_evidence_by_resume_id.get(resume_id, [])
        if not evidence_items:
            identity = index.upsert_candidate(
                resume_id=resume_id,
                evidence_id=f"candidate:{resume_id}",
                signals=_identity_signals_for_candidate(candidate=candidate, normalized=run_state.normalized_store.get(resume_id)),
            )
            candidate_identity_by_resume_id[resume_id] = identity.identity_id
            continue

        identity_id: str | None = None
        for evidence in evidence_items:
            identity = index.upsert_candidate(
                resume_id=resume_id,
                evidence_id=evidence.evidence_id,
                signals=_identity_signals_for_candidate(
                    candidate=candidate,
                    normalized=run_state.normalized_store.get(resume_id),
                    evidence=evidence,
                ),
            )
            identity_id = identity.identity_id
        if identity_id is not None:
            candidate_identity_by_resume_id[resume_id] = identity_id

    identities = index.identities()
    aliases_by_canonical_id = index.aliases_by_canonical_id()
    alias_to_canonical_id = _alias_to_canonical_id(aliases_by_canonical_id)
    candidate_identity_by_resume_id = {
        resume_id: alias_to_canonical_id.get(identity_id, identity_id)
        for resume_id, identity_id in candidate_identity_by_resume_id.items()
    }
    for resume_id, identity_id in candidate_identity_by_resume_id.items():
        source_evidence_by_identity_id.setdefault(identity_id, []).extend(
            run_state.source_evidence_by_resume_id.get(resume_id, [])
        )
    for identity_id, evidence_items in source_evidence_by_identity_id.items():
        unique: dict[str, RuntimeSourceEvidence] = {item.evidence_id: item for item in evidence_items}
        source_evidence_by_identity_id[identity_id] = sorted(
            unique.values(),
            key=lambda item: _evidence_sort_key(item, source_order),
        )

    run_state.candidate_identities = identities
    run_state.identity_aliases_by_canonical_id = aliases_by_canonical_id
    run_state.identity_conflicts = list(index.conflicts())
    run_state.candidate_identity_by_resume_id = candidate_identity_by_resume_id
    run_state.source_evidence_by_identity_id = source_evidence_by_identity_id
    run_state.canonical_resume_by_identity_id = {
        identity_id: choose_canonical_resume_for_identity(
            identity_id=identity_id,
            resume_ids=identity.resume_ids,
            candidates=run_state.candidate_store,
            normalized_store=run_state.normalized_store,
            evidence=source_evidence_by_identity_id.get(identity_id, []),
        )
        for identity_id, identity in identities.items()
        if identity.resume_ids
    }


def _alias_to_canonical_id(aliases_by_canonical_id: Mapping[str, Sequence[str]]) -> dict[str, str]:
    alias_to_canonical: dict[str, str] = {}
    for canonical_id, aliases in aliases_by_canonical_id.items():
        alias_to_canonical[canonical_id] = canonical_id
        for alias_id in aliases:
            alias_to_canonical[alias_id] = canonical_id
    return alias_to_canonical


class RuntimeCandidateIdentityIndex:
    def __init__(self, identities: Mapping[str, RuntimeCandidateIdentity] | None = None) -> None:
        self._identities: dict[str, RuntimeCandidateIdentity] = dict(identities or {})
        self._key_to_identity_id: dict[str, str] = {}
        self._keys_by_identity_id: dict[str, set[str]] = {}
        self._resume_id_to_identity_id: dict[str, str] = {}
        self._evidence_id_to_identity_id: dict[str, str] = {}
        self._aliases_by_canonical_id: dict[str, set[str]] = {
            identity_id: set(identity.alias_identity_ids)
            for identity_id, identity in self._identities.items()
        }
        self._alias_to_canonical_id: dict[str, str] = {}
        self._signals_by_identity_id: dict[str, RuntimeIdentitySignals] = {}
        self._fuzzy_identity_ids_by_name: dict[str, set[str]] = {}
        self._fuzzy_name_by_identity_id: dict[str, str] = {}
        self._conflicts_by_id: dict[str, RuntimeIdentityConflict] = {}
        for identity_id, identity in self._identities.items():
            aliases = self._aliases_by_canonical_id.setdefault(identity_id, set(identity.alias_identity_ids))
            aliases.add(identity_id)
            for alias_identity_id in aliases:
                self._alias_to_canonical_id[alias_identity_id] = identity_id
            self._index_identity_membership(identity_id, identity, keys=())

    def upsert_candidate(
        self,
        *,
        resume_id: str,
        evidence_id: str,
        signals: RuntimeIdentitySignals,
    ) -> RuntimeCandidateIdentity:
        keys = _identity_keys(signals=signals, evidence_id=evidence_id)
        primary_key = _primary_identity_key(signals=signals, evidence_id=evidence_id)
        target_identity_id = _stable_identity_id(primary_key)
        existing_identity_ids: set[str] = set()
        for key in keys:
            identity_id = self._key_to_identity_id.get(key)
            if identity_id is not None:
                existing_identity_ids.add(self._canonical_identity_id(identity_id))
        for identity_id in (
            self._resume_id_to_identity_id.get(resume_id),
            self._evidence_id_to_identity_id.get(evidence_id),
        ):
            if identity_id is not None:
                existing_identity_ids.add(self._canonical_identity_id(identity_id))

        scored_identity_ids: list[tuple[int, str]] = []
        for identity_id in sorted(self._candidate_fuzzy_identity_ids(signals)):
            if identity_id in existing_identity_ids:
                continue
            existing_signals = self._signals_by_identity_id.get(identity_id)
            if existing_signals is None:
                continue
            score = _identity_match_score(existing_signals, signals)
            if score >= 85:
                existing_identity_ids.add(identity_id)
            elif score >= 70:
                scored_identity_ids.append((score, identity_id))
        if not signals.protected_contact_hashes and existing_identity_ids:
            target_identity_id = sorted(existing_identity_ids)[0]

        self._ensure_identity(target_identity_id, strongest_signal=_strongest_signal_code(signals))
        for old_identity_id in sorted(existing_identity_ids):
            if old_identity_id != target_identity_id:
                self._merge_identity(old_identity_id=old_identity_id, target_identity_id=target_identity_id)
        for score, conflict_identity_id in scored_identity_ids:
            if conflict_identity_id == target_identity_id:
                continue
            conflict_identity = self._identities[conflict_identity_id]
            conflict_id = _stable_identity_id(
                "conflict:" + "||".join(sorted([target_identity_id, conflict_identity_id, resume_id, evidence_id]))
            )
            self._conflicts_by_id[conflict_id] = RuntimeIdentityConflict(
                conflict_id=conflict_id,
                candidate_identity_ids=tuple(sorted([target_identity_id, conflict_identity_id])),
                resume_ids=tuple(sorted(set(conflict_identity.resume_ids) | {resume_id})),
                reason_code="medium_confidence_identity_match",
                evidence_ids=tuple(sorted(set(conflict_identity.evidence_ids) | {evidence_id})),
                match_score=score,
            )

        identity = self._identities[target_identity_id]
        resume_ids = _append_sorted_once(identity.resume_ids, resume_id)
        evidence_ids = _append_sorted_once(identity.evidence_ids, evidence_id)
        aliases = sorted(self._aliases_by_canonical_id.get(target_identity_id, set()))
        updated = identity.model_copy(
            update={
                "resume_ids": resume_ids,
                "evidence_ids": evidence_ids,
                "alias_identity_ids": aliases,
                "strongest_signal": _strongest_signal_code(signals),
            }
        )
        self._identities[target_identity_id] = updated
        self._set_identity_signals(
            target_identity_id,
            _merge_identity_signals(
                self._signals_by_identity_id.get(target_identity_id),
                signals,
            ),
        )
        self._index_identity_membership(target_identity_id, updated, keys=keys)
        return updated

    def conflicts(self) -> tuple[RuntimeIdentityConflict, ...]:
        self._drop_resolved_conflicts()
        return tuple(self._conflicts_by_id[key] for key in sorted(self._conflicts_by_id))

    def aliases_for(self, canonical_identity_id: str) -> tuple[str, ...]:
        aliases = set(self._aliases_by_canonical_id.get(canonical_identity_id, set()))
        aliases.add(canonical_identity_id)
        return tuple(sorted(aliases))

    def identity_for_resume_id(self, resume_id: str) -> str | None:
        identity_id = self._resume_id_to_identity_id.get(resume_id)
        if identity_id is None:
            return None
        return self._canonical_identity_id(identity_id)

    def identities(self) -> dict[str, RuntimeCandidateIdentity]:
        return dict(self._identities)

    def aliases_by_canonical_id(self) -> dict[str, list[str]]:
        return {identity_id: sorted(aliases | {identity_id}) for identity_id, aliases in self._aliases_by_canonical_id.items()}

    def _candidate_fuzzy_identity_ids(self, signals: RuntimeIdentitySignals) -> set[str]:
        bucket_name = self._fuzzy_bucket_name(signals)
        if bucket_name is None:
            return set()
        return {
            self._canonical_identity_id(identity_id)
            for identity_id in self._fuzzy_identity_ids_by_name.get(bucket_name, set())
        }

    def _ensure_identity(self, identity_id: str, *, strongest_signal: str | None) -> None:
        if identity_id not in self._identities:
            self._identities[identity_id] = RuntimeCandidateIdentity(
                identity_id=identity_id,
                canonical_identity_id=identity_id,
                strongest_signal=strongest_signal,
            )
        self._aliases_by_canonical_id.setdefault(identity_id, {identity_id}).add(identity_id)
        self._alias_to_canonical_id[identity_id] = identity_id

    def _merge_identity(self, *, old_identity_id: str, target_identity_id: str) -> None:
        old_identity = self._identities.pop(old_identity_id, None)
        if old_identity is None:
            return
        target_identity = self._identities[target_identity_id]
        target_aliases = self._aliases_by_canonical_id.setdefault(target_identity_id, {target_identity_id})
        old_aliases = self._aliases_by_canonical_id.pop(old_identity_id, {old_identity_id})
        target_aliases.update(old_aliases)
        target_aliases.add(old_identity_id)
        target_aliases.add(target_identity_id)
        for alias_identity_id in target_aliases:
            self._alias_to_canonical_id[alias_identity_id] = target_identity_id

        updated = target_identity.model_copy(
            update={
                "resume_ids": sorted(set(target_identity.resume_ids) | set(old_identity.resume_ids)),
                "evidence_ids": sorted(set(target_identity.evidence_ids) | set(old_identity.evidence_ids)),
                "alias_identity_ids": sorted(target_aliases),
            }
        )
        self._identities[target_identity_id] = updated

        old_keys = self._keys_by_identity_id.pop(old_identity_id, set())
        old_signals = self._remove_identity_signals(old_identity_id)
        self._set_identity_signals(
            target_identity_id,
            _merge_identity_signals(
                self._signals_by_identity_id.get(target_identity_id),
                old_signals,
            ),
        )
        self._index_identity_membership(target_identity_id, updated, keys=old_keys)
        self._drop_resolved_conflicts()

    def _index_identity_membership(
        self,
        identity_id: str,
        identity: RuntimeCandidateIdentity,
        *,
        keys: Sequence[str] | set[str],
    ) -> None:
        canonical_identity_id = self._canonical_identity_id(identity_id)
        for resume_id in identity.resume_ids:
            self._resume_id_to_identity_id[resume_id] = canonical_identity_id
        for evidence_id in identity.evidence_ids:
            self._evidence_id_to_identity_id[evidence_id] = canonical_identity_id
        self._index_identity_keys(canonical_identity_id, keys)

    def _index_identity_keys(self, identity_id: str, keys: Sequence[str] | set[str]) -> None:
        if not keys:
            return
        canonical_identity_id = self._canonical_identity_id(identity_id)
        indexed_keys = self._keys_by_identity_id.setdefault(canonical_identity_id, set())
        for key in keys:
            self._key_to_identity_id[key] = canonical_identity_id
            indexed_keys.add(key)

    def _set_identity_signals(self, identity_id: str, signals: RuntimeIdentitySignals) -> None:
        canonical_identity_id = self._canonical_identity_id(identity_id)
        self._remove_identity_signals(canonical_identity_id)
        self._signals_by_identity_id[canonical_identity_id] = signals
        bucket_name = self._fuzzy_bucket_name(signals)
        if bucket_name is None:
            return
        self._fuzzy_identity_ids_by_name.setdefault(bucket_name, set()).add(canonical_identity_id)
        self._fuzzy_name_by_identity_id[canonical_identity_id] = bucket_name

    def _remove_identity_signals(self, identity_id: str) -> RuntimeIdentitySignals | None:
        canonical_identity_id = self._canonical_identity_id(identity_id)
        previous_bucket = self._fuzzy_name_by_identity_id.pop(canonical_identity_id, None)
        if previous_bucket is not None:
            identity_ids = self._fuzzy_identity_ids_by_name.get(previous_bucket)
            if identity_ids is not None:
                identity_ids.discard(canonical_identity_id)
                if not identity_ids:
                    self._fuzzy_identity_ids_by_name.pop(previous_bucket, None)
        return self._signals_by_identity_id.pop(canonical_identity_id, None)

    @staticmethod
    def _fuzzy_bucket_name(signals: RuntimeIdentitySignals) -> str | None:
        if signals.is_masked_name or not signals.normalized_name:
            return None
        return signals.normalized_name

    def _drop_resolved_conflicts(self) -> None:
        updated: dict[str, RuntimeIdentityConflict] = {}
        for conflict_id, conflict in self._conflicts_by_id.items():
            identity_ids = tuple(
                sorted(
                    {
                        self._canonical_identity_id(identity_id)
                        for identity_id in conflict.candidate_identity_ids
                    }
                )
            )
            if len(identity_ids) < 2:
                continue
            updated[conflict_id] = conflict.model_copy(update={"candidate_identity_ids": identity_ids})
        self._conflicts_by_id = updated

    def _canonical_identity_id(self, identity_id: str) -> str:
        return self._alias_to_canonical_id.get(identity_id, identity_id)


def choose_canonical_resume_for_identity(
    *,
    identity_id: str,
    resume_ids: Sequence[str],
    candidates: Mapping[str, ResumeCandidate],
    normalized_store: Mapping[str, NormalizedResume],
    evidence: Sequence[RuntimeSourceEvidence],
) -> RuntimeCanonicalResumeSelection:
    evidence_by_resume_id: dict[str, list[RuntimeSourceEvidence]] = {}
    for item in evidence:
        evidence_by_resume_id.setdefault(item.candidate_resume_id, []).append(item)

    def sort_key(resume_id: str) -> tuple[int, str, int, int, int, str]:
        resume_evidence = evidence_by_resume_id.get(resume_id, [])
        best_level = 1 if any(item.evidence_level == "detail" for item in resume_evidence) else 0
        newest_collected_at = max((item.collected_at for item in resume_evidence), default="")
        normalized = normalized_store.get(resume_id)
        completeness = normalized.completeness_score if normalized is not None else 0
        source_trust = max((_source_trust(item.source) for item in resume_evidence), default=0)
        provider_rank = min((item.provider_rank for item in resume_evidence if item.provider_rank is not None), default=9999)
        return (best_level, newest_collected_at, completeness, source_trust, -provider_rank, resume_id)

    selected_resume_id = max(resume_ids, key=sort_key)
    selected_evidence = sorted(
        evidence_by_resume_id.get(selected_resume_id, []),
        key=lambda item: (1 if item.evidence_level == "detail" else 0, item.collected_at, item.evidence_id),
        reverse=True,
    )
    reason_codes = ["detail_evidence"] if selected_evidence and selected_evidence[0].evidence_level == "detail" else [
        "provider_rank_preserved"
    ]
    return RuntimeCanonicalResumeSelection(
        identity_id=identity_id,
        canonical_resume_id=selected_resume_id,
        selected_evidence_id=selected_evidence[0].evidence_id if selected_evidence else None,
        selected_at=selected_evidence[0].collected_at if selected_evidence else None,
        safe_reason_codes=tuple(reason_codes),
    )


def _evidence_sort_key(
    evidence: RuntimeSourceEvidence,
    source_order: Mapping[SourceKind, int],
) -> tuple[int, int, str, str]:
    level_order = {"card": 0, "detail": 1, "final": 2}
    source_index = source_order.get(evidence.source, 999)
    return (
        source_index,
        level_order.get(evidence.evidence_level, 999),
        evidence.collected_at,
        evidence.evidence_id,
    )


def _identity_keys(*, signals: RuntimeIdentitySignals, evidence_id: str) -> tuple[str, ...]:
    keys: list[str] = []
    for contact_hash in sorted(signals.protected_contact_hashes):
        keys.append(f"contact:{contact_hash}")
    if signals.provider_candidate_key_hash:
        keys.append(f"provider:{signals.provider_candidate_key_hash}")
    if not keys and not signals.is_masked_name and signals.normalized_name:
        distinctive_parts = [
            signals.normalized_name,
            signals.current_company_norm or "",
            signals.current_title_norm or "",
            "|".join(signals.school_norms),
            "|".join(signals.work_chronology_fingerprints),
        ]
        if any(distinctive_parts[1:]):
            keys.append("identity-fields:" + hashlib.sha256("||".join(distinctive_parts).encode("utf-8")).hexdigest())
    return tuple(keys or [f"evidence:{evidence_id}"])


def _primary_identity_key(*, signals: RuntimeIdentitySignals, evidence_id: str) -> str:
    if signals.protected_contact_hashes:
        return f"contact:{sorted(signals.protected_contact_hashes)[0]}"
    if signals.provider_candidate_key_hash:
        return f"provider:{signals.provider_candidate_key_hash}"
    return _identity_keys(signals=signals, evidence_id=evidence_id)[0]


def _stable_identity_id(identity_key: str) -> str:
    return "identity-" + hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:16]


def _strongest_signal_code(signals: RuntimeIdentitySignals) -> str:
    if signals.protected_contact_hashes:
        return "protected_contact_hash"
    if signals.provider_candidate_key_hash:
        return "provider_candidate_key_hash"
    if signals.is_masked_name:
        return "masked_name_only"
    return "normalized_identity_fields"


def _identity_match_score(left: RuntimeIdentitySignals, right: RuntimeIdentitySignals) -> int:
    if set(left.protected_contact_hashes) & set(right.protected_contact_hashes):
        return 100
    if left.provider_candidate_key_hash and left.provider_candidate_key_hash == right.provider_candidate_key_hash:
        return 95
    if left.is_masked_name or right.is_masked_name:
        return 0
    if not left.normalized_name or left.normalized_name != right.normalized_name:
        return 0

    score = 40
    if left.current_company_norm and left.current_company_norm == right.current_company_norm:
        score += 20
    if _same_or_similar_text(left.current_title_norm, right.current_title_norm, threshold=0.6):
        score += 15
    if set(left.school_norms) & set(right.school_norms):
        score += 15
    if set(left.work_chronology_fingerprints) & set(right.work_chronology_fingerprints):
        score += 15
    if (
        left.years_of_experience is not None
        and right.years_of_experience is not None
        and abs(left.years_of_experience - right.years_of_experience) <= 1
    ):
        score += 5
    if set(left.location_norms) & set(right.location_norms):
        score += 5
    if _token_overlap(left.skill_norms, right.skill_norms) >= 0.3:
        score += 5
    return min(score, 95)


def _same_or_similar_text(left: str | None, right: str | None, *, threshold: float) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return _token_overlap((left,), (right,)) >= threshold


def _token_overlap(left_values: tuple[str, ...], right_values: tuple[str, ...]) -> float:
    left_tokens = _identity_tokens(left_values)
    right_tokens = _identity_tokens(right_values)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _identity_tokens(values: tuple[str, ...]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(token for token in re.split(r"[\s,/|:;()_-]+", value) if token)
    return tokens


def _merge_identity_signals(
    left: RuntimeIdentitySignals | None,
    right: RuntimeIdentitySignals | None,
) -> RuntimeIdentitySignals:
    if left is None:
        return right or RuntimeIdentitySignals()
    if right is None:
        return left
    return RuntimeIdentitySignals(
        normalized_name=left.normalized_name or right.normalized_name,
        is_masked_name=left.is_masked_name and right.is_masked_name,
        current_company_norm=left.current_company_norm or right.current_company_norm,
        current_title_norm=left.current_title_norm or right.current_title_norm,
        school_norms=tuple(sorted(set(left.school_norms) | set(right.school_norms))),
        work_chronology_fingerprints=tuple(
            sorted(set(left.work_chronology_fingerprints) | set(right.work_chronology_fingerprints))
        ),
        provider_candidate_key_hash=left.provider_candidate_key_hash or right.provider_candidate_key_hash,
        protected_contact_hashes=tuple(sorted(set(left.protected_contact_hashes) | set(right.protected_contact_hashes))),
        years_of_experience=left.years_of_experience
        if left.years_of_experience is not None
        else right.years_of_experience,
        location_norms=tuple(sorted(set(left.location_norms) | set(right.location_norms))),
        skill_norms=tuple(sorted(set(left.skill_norms) | set(right.skill_norms))),
    )


def _append_sorted_once(values: Sequence[str], value: str) -> list[str]:
    return sorted(set(values) | {value})


def _source_trust(source: SourceKind | str) -> int:
    return 0


def _identity_signals_for_candidate(
    *,
    candidate: ResumeCandidate,
    normalized: NormalizedResume | None,
    evidence: RuntimeSourceEvidence | None = None,
) -> RuntimeIdentitySignals:
    name = normalized.candidate_name.strip() if normalized and normalized.candidate_name else None
    current_company = normalized.current_company.strip() if normalized and normalized.current_company else None
    current_title = normalized.current_title.strip() if normalized and normalized.current_title else None
    school_norms: tuple[str, ...] = ()
    if normalized and normalized.education_summary:
        school_norms = tuple(_normalize_identity_text(part) for part in [normalized.education_summary] if part.strip())
    chronology: list[str] = []
    if normalized:
        for item in normalized.recent_experiences:
            fingerprint = ":".join(
                part
                for part in (
                    _normalize_identity_text(item.company),
                    _normalize_identity_text(item.title),
                    _normalize_identity_text(item.duration),
                )
                if part
            )
            if fingerprint:
                chronology.append(fingerprint)
    if not chronology:
        for summary in candidate.work_experience_summaries:
            text = _normalize_identity_text(summary)
            if text:
                chronology.append(text)
    return RuntimeIdentitySignals(
        normalized_name=_normalize_identity_text(name) if name else None,
        is_masked_name=_is_masked_identity_name(name),
        current_company_norm=_normalize_identity_text(current_company) if current_company else None,
        current_title_norm=_normalize_identity_text(current_title) if current_title else None,
        school_norms=school_norms,
        work_chronology_fingerprints=tuple(sorted(set(chronology))),
        provider_candidate_key_hash=evidence.provider_candidate_key_hash if evidence else None,
        protected_contact_hashes=evidence.protected_contact_hashes if evidence else (),
        years_of_experience=normalized.years_of_experience if normalized else candidate.work_year,
        location_norms=tuple(
            _normalize_identity_text(item)
            for item in (normalized.locations if normalized else [])
            if item
        ),
        skill_norms=tuple(
            _normalize_identity_text(item)
            for item in (normalized.skills if normalized else [])
            if item
        ),
    )


def _normalize_identity_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _is_masked_identity_name(value: str | None) -> bool:
    if value is None:
        return False
    text = value.strip()
    if not text:
        return True
    lowered = text.casefold()
    if lowered in {"匿名", "候选人", "candidate", "-", "--"}:
        return True
    if "*" in text or "某" in text or "女士" in text or "先生" in text:
        return True
    if re.fullmatch(r"候选人\d+", text):
        return True
    return False
