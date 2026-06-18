from __future__ import annotations

from dataclasses import dataclass

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.job_requests import SourceKind, normalize_source_kinds


@dataclass(frozen=True)
class RuntimeSourceSelection:
    source_kinds: tuple[SourceKind, ...]
    runtime_source_ids: tuple[str, ...]
    workspace_source_policy_id: str | None


class SourceSelectionError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def resolve_runtime_source_selection(
    *,
    source_kinds: list[SourceKind],
    workspace_source_policy_id: str | None,
    registered_runtime_source_ids: set[str],
) -> RuntimeSourceSelection:
    if not source_kinds and workspace_source_policy_id is None:
        raise SourceSelectionError("source_policy_missing")
    stripped = [source_kind.strip() for source_kind in source_kinds]
    if len(set(stripped)) != len(stripped):
        raise SourceSelectionError("duplicate_source_kind")
    try:
        normalized_source_kinds = tuple(normalize_source_kinds(stripped)) if stripped else ()
    except ConversationAgentError as exc:
        raise SourceSelectionError("source_policy_disallowed") from exc
    runtime_source_ids = tuple(normalized_source_kinds)
    unknown = [source_id for source_id in runtime_source_ids if source_id not in registered_runtime_source_ids]
    if unknown:
        raise SourceSelectionError("source_policy_disallowed")
    return RuntimeSourceSelection(
        source_kinds=normalized_source_kinds,
        runtime_source_ids=runtime_source_ids,
        workspace_source_policy_id=workspace_source_policy_id,
    )


class RuntimeSourceSelectionResolver:
    def __init__(self, *, registered_runtime_source_ids: set[str]) -> None:
        self.registered_runtime_source_ids = set(registered_runtime_source_ids)

    def resolve_runtime_source_selection(
        self,
        *,
        source_kinds: list[SourceKind],
        workspace_source_policy_id: str | None,
    ) -> RuntimeSourceSelection:
        return resolve_runtime_source_selection(
            source_kinds=source_kinds,
            workspace_source_policy_id=workspace_source_policy_id,
            registered_runtime_source_ids=self.registered_runtime_source_ids,
        )
