from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from seektalent_conversation_agent.errors import ConversationAgentError


SourceKind = str


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


def normalize_source_kinds(values: Sequence[str], *, allow_empty: bool = False) -> list[SourceKind]:
    normalized: list[SourceKind] = []
    seen: set[str] = set()
    for raw_value in values:
        value = raw_value.strip()
        if not value:
            raise ConversationAgentError(
                "job_request_source_kind_invalid",
                payload={"sourceKind": raw_value},
            )
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized and not allow_empty:
        raise ConversationAgentError("job_request_source_kinds_required")
    return normalized
