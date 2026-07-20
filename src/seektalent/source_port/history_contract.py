from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal, TypeAlias

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator


JSON_SAFE_INTEGER = 2**53 - 1
SQLITE_MAX_INTEGER = 2**63 - 1

OperationKind: TypeAlias = Literal[
    "verify_session",
    "search",
    "cards",
    "details",
    "continuation",
    "cleanup",
]
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


def _bounded_text(*, max_bytes: int):
    def validate(value: str) -> str:
        if not value:
            raise ValueError("source_history_text_empty")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("source_history_text_control_character")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("source_history_text_invalid_unicode") from exc
        if len(encoded) > max_bytes:
            raise ValueError("source_history_text_too_large")
        return value

    return validate


def _sha256(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("source_history_invalid_sha256")
    return value


def _literal_one(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("source_history_expected_exact_integer_one")
    return value


def _literal_true(value: object) -> object:
    if type(value) is not bool or value is not True:
        raise ValueError("source_history_expected_exact_true")
    return value


def _literal_false(value: object) -> object:
    if type(value) is not bool or value is not False:
        raise ValueError("source_history_expected_exact_false")
    return value


Opaque96 = Annotated[str, Field(strict=True), AfterValidator(_bounded_text(max_bytes=96))]
Opaque128 = Annotated[str, Field(strict=True), AfterValidator(_bounded_text(max_bytes=128))]
Opaque256 = Annotated[str, Field(strict=True), AfterValidator(_bounded_text(max_bytes=256))]
Sha256 = Annotated[str, Field(strict=True), AfterValidator(_sha256)]
PositiveJsonInteger = Annotated[int, Field(strict=True, ge=1, le=JSON_SAFE_INTEGER)]
NonNegativeJsonInteger = Annotated[int, Field(strict=True, ge=0, le=JSON_SAFE_INTEGER)]
ExactIntegerOne = Annotated[Literal[1], BeforeValidator(_literal_one)]
ExactTrue = Annotated[Literal[True], BeforeValidator(_literal_true)]
ExactFalse = Annotated[Literal[False], BeforeValidator(_literal_false)]


class _HistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always", strict=True)


class ExactAuthorizationSelector(_HistoryModel):
    kind: Literal["exact"]
    ordinal: ExactIntegerOne


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
    dispatch_authorization_ordinal: ExactIntegerOne
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
        for fact in self.facts:
            if (
                fact.run_id != self.run_id
                or fact.operation_id != self.operation_id
                or fact.source != self.source
                or fact.operation_kind != self.operation_kind
                or fact.idempotency_key != self.idempotency_key
                or fact.request_hash != self.request_hash
                or fact.attempt_no != self.attempt_no
            ):
                raise ValueError("source_history_matched_identity_mismatch")
            if not (self.searched_first_generation <= fact.accepted_generation <= self.searched_last_generation):
                raise ValueError("source_history_fact_outside_searched_range")
            if fact.head_generation > self.newest_known_generation:
                raise ValueError("source_history_fact_head_after_newest_generation")
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
