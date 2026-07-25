from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import AfterValidator, Field, model_validator

from seektalent.source_port.wire_primitives import (
    ExactFalse,
    ExactTrue,
    JSON_SAFE_INTEGER as JSON_SAFE_INTEGER,
    NonNegativeJsonInteger,
    Opaque96,
    Opaque128,
    Opaque256,
    OperationKind,
    PositiveJsonInteger,
    SQLITE_MAX_INTEGER as SQLITE_MAX_INTEGER,
    Sha256,
    StrictWireModel,
)


def _non_bearer_retry_ref(value: str) -> str:
    lowered = value.casefold()
    if (
        value != value.strip()
        or lowered.startswith(("bearer ", "basic ", "authorization:", "authorization="))
        or "authorization=" in lowered
    ):
        raise ValueError("source_history_safe_retry_commit_ref_invalid")
    return value


SafeRetryCommitRef: TypeAlias = Annotated[Opaque256, AfterValidator(_non_bearer_retry_ref)]

HistoryUnavailableReason: TypeAlias = Literal[
    "unknown_generation",
    "retention_gap",
    "truncated",
    "corrupt",
    "unreadable",
    "schema_mismatch",
    "pragma_mismatch",
    "busy",
]
IdentityConflictReason: TypeAlias = Literal[
    "run_id_mismatch",
    "operation_id_mismatch",
    "source_mismatch",
    "operation_kind_mismatch",
    "idempotency_key_mismatch",
    "request_hash_mismatch",
    "attempt_no_mismatch",
    "accepted_requirement_revision_mismatch",
    "accepted_fact_mismatch",
]


class _HistoryModel(StrictWireModel):
    pass


class ExactAuthorizationSelector(_HistoryModel):
    kind: Literal["exact"]
    ordinal: PositiveJsonInteger


class AllAuthorizationsSelector(_HistoryModel):
    kind: Literal["all"]


AuthorizationSelector: TypeAlias = Annotated[
    ExactAuthorizationSelector | AllAuthorizationsSelector,
    Field(discriminator="kind"),
]


class SourceHistoryQueryV1(_HistoryModel):
    contract_version: Literal["seektalent.source-port.query.request/v1"]
    run_id: Opaque96
    operation_id: Opaque96
    source: Literal["liepin"]
    operation_kind: OperationKind
    idempotency_key: Opaque128
    request_hash: Sha256
    attempt_no: PositiveJsonInteger
    authorization_selector: AuthorizationSelector
    accepted_generation_hint: PositiveJsonInteger | None = None
    searched_first_generation: PositiveJsonInteger
    searched_last_generation: PositiveJsonInteger
    expected_source_operation_ledger_revision: PositiveJsonInteger
    expected_reconciliation_revision: NonNegativeJsonInteger

    @model_validator(mode="after")
    def validate_generation_range(self) -> SourceHistoryQueryV1:
        if self.searched_first_generation > self.searched_last_generation:
            raise ValueError("source_history_invalid_generation_range")
        if self.accepted_generation_hint is not None and not (
            self.searched_first_generation <= self.accepted_generation_hint <= self.searched_last_generation
        ):
            raise ValueError("source_history_generation_hint_out_of_range")
        return self


class _QueryResultBase(_HistoryModel):
    contract_version: Literal["seektalent.source-port.query.result/v1"]
    run_id: Opaque96
    operation_id: Opaque96
    source: Literal["liepin"]
    operation_kind: OperationKind
    idempotency_key: Opaque128
    request_hash: Sha256
    attempt_no: PositiveJsonInteger
    authorization_selector: AuthorizationSelector
    accepted_generation_hint: PositiveJsonInteger | None = None
    searched_first_generation: PositiveJsonInteger
    searched_last_generation: PositiveJsonInteger
    expected_source_operation_ledger_revision: PositiveJsonInteger
    expected_reconciliation_revision: NonNegativeJsonInteger

    @model_validator(mode="after")
    def validate_echoed_generation_range(self) -> _QueryResultBase:
        if self.searched_first_generation > self.searched_last_generation:
            raise ValueError("source_history_invalid_generation_range")
        if self.accepted_generation_hint is not None and not (
            self.searched_first_generation <= self.accepted_generation_hint <= self.searched_last_generation
        ):
            raise ValueError("source_history_generation_hint_out_of_range")
        return self


class _CompleteCoverageResult(_QueryResultBase):
    oldest_retained_generation: PositiveJsonInteger
    newest_known_generation: PositiveJsonInteger
    history_complete: ExactTrue
    history_truncated: ExactFalse

    @model_validator(mode="after")
    def validate_complete_coverage(self) -> _CompleteCoverageResult:
        if not (
            self.oldest_retained_generation
            <= self.searched_first_generation
            <= self.searched_last_generation
            <= self.newest_known_generation
        ):
            raise ValueError("source_history_incomplete_coverage")
        return self


class _AcceptedFactBase(_HistoryModel):
    run_id: Opaque96
    operation_id: Opaque96
    source: Literal["liepin"]
    operation_kind: OperationKind
    idempotency_key: Opaque128
    request_hash: Sha256
    attempt_no: PositiveJsonInteger
    accepted_requirement_revision_id: Opaque96
    runtime_attempt_fence_ref: Sha256
    accepted_generation: PositiveJsonInteger
    accepted_journal_revision: PositiveJsonInteger
    head_generation: PositiveJsonInteger
    head_journal_revision: PositiveJsonInteger
    dispatch_authorization_ordinal: PositiveJsonInteger
    safe_retry_commit_ref: SafeRetryCommitRef | None
    expected_source_operation_ledger_revision: PositiveJsonInteger
    expected_reconciliation_revision: NonNegativeJsonInteger
    authorized_dispatch_intent_id: Opaque96
    authorized_dispatch_intent_revision: PositiveJsonInteger
    authorized_dispatch_intent_digest: Sha256
    profile_binding_generation: PositiveJsonInteger
    browser_control_scope_id: Opaque96 | None = None
    controller_fence_ref: Sha256 | None = None

    @model_validator(mode="after")
    def validate_accepted_head(self) -> _AcceptedFactBase:
        if (
            self.head_generation < self.accepted_generation
            or self.head_journal_revision < self.accepted_journal_revision
        ):
            raise ValueError("source_history_head_before_acceptance")
        if self.dispatch_authorization_ordinal == 1:
            if (
                self.safe_retry_commit_ref is not None
                or self.expected_source_operation_ledger_revision != 1
                or self.expected_reconciliation_revision != 0
            ):
                raise ValueError("source_history_initial_authorization_epoch_invalid")
        elif self.safe_retry_commit_ref is None or self.expected_reconciliation_revision == 0:
            raise ValueError("source_history_safe_retry_authorization_epoch_invalid")
        return self


class AcceptedNoDispatchFact(_AcceptedFactBase):
    conclusion: Literal["accepted_no_dispatch"]

    @model_validator(mode="after")
    def validate_exact_accepted_head(self) -> AcceptedNoDispatchFact:
        if (
            self.head_generation != self.accepted_generation
            or self.head_journal_revision != self.accepted_journal_revision
        ):
            raise ValueError("source_history_accepted_head_not_exact")
        return self


class _DispatchedFactBase(_AcceptedFactBase):
    durable_dispatch_intent_ref: Opaque256
    dispatch_intent_generation: PositiveJsonInteger
    dispatch_intent_journal_revision: PositiveJsonInteger

    @model_validator(mode="after")
    def validate_dispatch_head(self) -> _DispatchedFactBase:
        if not (
            self.accepted_generation <= self.dispatch_intent_generation <= self.head_generation
            and self.accepted_journal_revision < self.dispatch_intent_journal_revision <= self.head_journal_revision
        ):
            raise ValueError("source_history_invalid_dispatch_revision")
        return self


class DispatchNotObservedFact(_DispatchedFactBase):
    conclusion: Literal["dispatch_not_observed"]

    @model_validator(mode="after")
    def validate_exact_dispatch_head(self) -> DispatchNotObservedFact:
        if (
            self.head_generation != self.dispatch_intent_generation
            or self.head_journal_revision != self.dispatch_intent_journal_revision
        ):
            raise ValueError("source_history_dispatch_head_not_exact")
        return self


class _ObservedFactBase(_DispatchedFactBase):
    observation_generation: PositiveJsonInteger
    observation_journal_revision: PositiveJsonInteger

    @model_validator(mode="after")
    def validate_observation_head(self) -> _ObservedFactBase:
        if not (
            self.dispatch_intent_generation <= self.observation_generation == self.head_generation
            and self.dispatch_intent_journal_revision < self.observation_journal_revision == self.head_journal_revision
        ):
            raise ValueError("source_history_invalid_observation_revision")
        return self


class ObservedResultFact(_ObservedFactBase):
    conclusion: Literal["observed_result"]
    result_ref: Opaque256
    result_hash: Sha256


class ObservedFailureFact(_ObservedFactBase):
    conclusion: Literal["observed_failure"]
    failure_ref: Opaque256
    failure_hash: Sha256


MatchedHistoryFact: TypeAlias = Annotated[
    AcceptedNoDispatchFact | DispatchNotObservedFact | ObservedResultFact | ObservedFailureFact,
    Field(discriminator="conclusion"),
]


class SourceHistoryMatched(_CompleteCoverageResult):
    outcome: Literal["matched"]
    facts: tuple[MatchedHistoryFact, ...]

    @model_validator(mode="after")
    def validate_matched_facts(self) -> SourceHistoryMatched:
        if not self.facts:
            raise ValueError("source_history_matched_without_facts")
        ordinals = tuple(fact.dispatch_authorization_ordinal for fact in self.facts)
        if tuple(sorted(set(ordinals))) != ordinals:
            raise ValueError("source_history_matched_ordinals_not_unique_ascending")
        if isinstance(self.authorization_selector, ExactAuthorizationSelector) and ordinals != (
            self.authorization_selector.ordinal,
        ):
            raise ValueError("source_history_exact_selector_mismatch")
        if isinstance(self.authorization_selector, AllAuthorizationsSelector) and ordinals != tuple(
            range(1, len(ordinals) + 1)
        ):
            raise ValueError("source_history_all_selector_ordinal_gap")
        accepted_requirement_revision_id = self.facts[0].accepted_requirement_revision_id
        for fact in self.facts:
            if (
                fact.run_id != self.run_id
                or fact.operation_id != self.operation_id
                or fact.source != self.source
                or fact.operation_kind != self.operation_kind
                or fact.idempotency_key != self.idempotency_key
                or fact.request_hash != self.request_hash
            ):
                raise ValueError("source_history_matched_identity_mismatch")
            if fact.accepted_requirement_revision_id != accepted_requirement_revision_id:
                raise ValueError("source_history_matched_requirement_revision_mismatch")
            if not (self.searched_first_generation <= fact.accepted_generation <= self.searched_last_generation):
                raise ValueError("source_history_fact_outside_searched_range")
            if fact.head_generation > self.newest_known_generation:
                raise ValueError("source_history_fact_head_after_newest_generation")
        if isinstance(self.authorization_selector, ExactAuthorizationSelector):
            if self.facts[0].attempt_no != self.attempt_no:
                raise ValueError("source_history_exact_attempt_mismatch")
            return self
        if self.facts[-1].attempt_no != self.attempt_no:
            raise ValueError("source_history_all_latest_attempt_mismatch")
        retry_refs = tuple(fact.safe_retry_commit_ref for fact in self.facts if fact.safe_retry_commit_ref is not None)
        if len(set(retry_refs)) != len(retry_refs):
            raise ValueError("source_history_safe_retry_commit_ref_reused")
        monotonic_fields = (
            "attempt_no",
            "authorized_dispatch_intent_revision",
            "expected_source_operation_ledger_revision",
            "expected_reconciliation_revision",
        )
        for field in monotonic_fields:
            values = tuple(getattr(fact, field) for fact in self.facts)
            if any(current <= previous for previous, current in zip(values, values[1:], strict=False)):
                raise ValueError(f"source_history_{field}_not_increasing")
        return self


class SourceHistoryNotFound(_CompleteCoverageResult):
    outcome: Literal["not_found"]


class SourceHistoryIdentityConflict(_QueryResultBase):
    outcome: Literal["identity_conflict"]
    conflict_reasons: tuple[IdentityConflictReason, ...]
    oldest_retained_generation: PositiveJsonInteger | None = None
    newest_known_generation: PositiveJsonInteger | None = None

    @model_validator(mode="after")
    def validate_conflicts(self) -> SourceHistoryIdentityConflict:
        if not self.conflict_reasons:
            raise ValueError("source_history_conflict_without_reason")
        if tuple(dict.fromkeys(self.conflict_reasons)) != self.conflict_reasons:
            raise ValueError("source_history_duplicate_conflict_reason")
        if (
            self.oldest_retained_generation is not None
            and self.newest_known_generation is not None
            and self.oldest_retained_generation > self.newest_known_generation
        ):
            raise ValueError("source_history_invalid_available_bounds")
        return self


class SourceHistoryUnavailable(_QueryResultBase):
    outcome: Literal["history_unavailable"]
    reason: HistoryUnavailableReason
    oldest_retained_generation: PositiveJsonInteger | None = None
    newest_known_generation: PositiveJsonInteger | None = None

    @model_validator(mode="after")
    def validate_unavailable_bounds(self) -> SourceHistoryUnavailable:
        if (
            self.oldest_retained_generation is not None
            and self.newest_known_generation is not None
            and self.oldest_retained_generation > self.newest_known_generation
        ):
            raise ValueError("source_history_invalid_available_bounds")
        if (
            self.reason == "unknown_generation"
            and self.newest_known_generation is not None
            and self.searched_last_generation <= self.newest_known_generation
        ):
            raise ValueError("source_history_unknown_generation_within_known_range")
        return self


SourceHistoryQueryResultV1: TypeAlias = Annotated[
    SourceHistoryMatched | SourceHistoryNotFound | SourceHistoryIdentityConflict | SourceHistoryUnavailable,
    Field(discriminator="outcome"),
]
