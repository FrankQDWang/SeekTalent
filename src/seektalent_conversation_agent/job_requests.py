from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from seektalent_conversation_agent.errors import ConversationAgentError


SourceKind = Literal["cts", "liepin"]

_SOURCE_KINDS = {"cts", "liepin"}


@dataclass(frozen=True)
class JobRequestRevision:
    job_request_revision_id: str
    conversation_id: str
    owner_user_id: str
    workspace_id: str
    jd_text: str
    user_job_title: str | None
    extracted_job_title: str | None
    notes: str | None
    source_kinds: list[SourceKind]
    workspace_source_policy_id: str | None
    request_hash: str
    idempotency_key: str
    created_at: str
    updated_at: str

    @property
    def effective_job_title(self) -> str | None:
        return self.extracted_job_title or self.user_job_title


@dataclass(frozen=True)
class RequirementDraftJobRequestLink:
    draft_revision_id: str
    workspace_id: str
    conversation_id: str
    job_request_revision_id: str
    created_at: str

    @property
    def requirement_draft_revision_id(self) -> str:
        return self.draft_revision_id


def normalize_source_kinds(values: Sequence[str]) -> list[SourceKind]:
    normalized: list[SourceKind] = []
    seen: set[str] = set()
    for raw_value in values:
        value = raw_value.strip()
        if value not in _SOURCE_KINDS:
            raise ConversationAgentError(
                "job_request_source_kind_invalid",
                payload={"sourceKind": raw_value},
            )
        if value not in seen:
            normalized.append(_source_kind(value))
            seen.add(value)
    if not normalized:
        raise ConversationAgentError("job_request_source_kinds_required")
    return normalized


def _source_kind(value: str) -> SourceKind:
    if value == "cts":
        return "cts"
    if value == "liepin":
        return "liepin"
    raise ConversationAgentError(
        "job_request_source_kind_invalid",
        payload={"sourceKind": value},
    )
