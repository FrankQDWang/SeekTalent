"""Private command-journal values shared by the facade and SQLite engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from seektalent.source_port.history_contract import OperationKind


class CommandJournalErrorReason(StrEnum):
    BUSY = "busy"
    CANNOT_OPEN = "cannot_open"
    CORRUPT = "corrupt"
    FULL = "full"
    IO_ERROR = "io_error"
    PRAGMA_MISMATCH = "pragma_mismatch"
    READONLY = "readonly"
    SCHEMA_MISMATCH = "schema_mismatch"


class CommandJournalConflictReason(StrEnum):
    ACCEPTANCE_REPLAY_CONFLICT = "acceptance_replay_conflict"
    CREATE_PATH_EXISTS = "create_path_exists"
    DISPATCH_REPLAY_CONFLICT = "dispatch_replay_conflict"
    GENERATION_EXHAUSTED = "generation_exhausted"
    HEAD_CAS_FAILED = "head_cas_failed"
    HEAD_MISSING = "head_missing"
    IDENTITY_CONFLICT = "identity_conflict"
    INSTANCE_ID_CONFLICT = "instance_id_conflict"
    OBSERVATION_REPLAY_CONFLICT = "observation_replay_conflict"
    OBSERVATION_WITHOUT_DISPATCH = "observation_without_dispatch"
    PHASE_ROLLBACK = "phase_rollback"
    REVISION_EXHAUSTED = "revision_exhausted"
    SESSION_GENERATION_INVALID = "session_generation_invalid"
    STALE_HEAD_REVISION = "stale_head_revision"


class CommandJournalTransitionDisposition(StrEnum):
    CREATED = "created"
    EXACT_REPLAY = "exact_replay"


class CommandJournalError(RuntimeError):
    """A closed SQLite lifecycle or storage failure."""

    def __init__(self, reason: CommandJournalErrorReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class CommandJournalConflict(RuntimeError):
    """A durable command transition did not match the current journal head."""

    def __init__(self, reason: CommandJournalConflictReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class CommandJournalTransitionResult:
    """Private durable transition facts used to mint a public sealed receipt."""

    disposition: CommandJournalTransitionDisposition
    startup_generation: int
    revision: int
    head_phase: Literal["accepted", "dispatch_intent", "observed_result", "observed_failure"]
    accepted_ack_bytes: bytes | None
    terminal_reply_bytes: bytes | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedCommand:
    """Allowlisted command identity and acceptance facts for ordinal one."""

    run_id: str
    operation_id: str
    source: Literal["liepin"]
    operation_kind: OperationKind
    idempotency_key: str
    request_hash: str
    attempt_no: int
    accepted_requirement_revision_id: str
    runtime_attempt_fence_ref: str
    authorized_dispatch_intent_id: str
    authorized_dispatch_intent_revision: int
    authorized_dispatch_intent_digest: str
    profile_binding_generation: int
    browser_control_scope_id: str | None = None
    controller_fence_ref: str | None = None
    dispatch_authorization_ordinal: Literal[1] = 1
