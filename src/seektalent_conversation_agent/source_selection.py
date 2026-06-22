from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.job_requests import SourceKind, normalize_source_kinds
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.source_catalog import RuntimeSourcePolicyResolver


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
    source_kinds: Sequence[str] | None,
    workspace_source_policy_id: str | None,
    source_policy_resolver: RuntimeSourcePolicyResolver | None = None,
    registered_runtime_source_ids: set[str] | None = None,
    default_runtime_source_ids: Sequence[str] = (),
) -> RuntimeSourceSelection:
    if source_kinds is None:
        stripped: list[str] | None = None
    else:
        stripped = [source_kind.strip() for source_kind in source_kinds]
        if not stripped:
            raise SourceSelectionError("source_selection_empty")
    normalized_source_kinds: tuple[SourceKind, ...]
    if stripped is None:
        normalized_source_kinds = ()
    else:
        if len(set(stripped)) != len(stripped):
            raise SourceSelectionError("duplicate_source_kind")
        try:
            normalized_source_kinds = tuple(normalize_source_kinds(stripped))
        except ConversationAgentError as exc:
            raise SourceSelectionError("source_policy_disallowed") from exc
    if source_policy_resolver is not None:
        try:
            runtime_source_ids = source_policy_resolver.resolve_source_ids(
                None if source_kinds is None else normalized_source_kinds
            )
        except RuntimeControlError as exc:
            raise SourceSelectionError("source_policy_disallowed") from exc
        normalized_source_kinds = tuple(runtime_source_ids)
    else:
        if source_kinds is None and workspace_source_policy_id is None:
            if not default_runtime_source_ids:
                raise SourceSelectionError("source_policy_missing")
            runtime_source_ids = tuple(default_runtime_source_ids)
            normalized_source_kinds = tuple(default_runtime_source_ids)
        else:
            runtime_source_ids = tuple(normalized_source_kinds)
        unknown = [source_id for source_id in runtime_source_ids if source_id not in (registered_runtime_source_ids or set())]
        if unknown:
            raise SourceSelectionError("source_policy_disallowed")
    return RuntimeSourceSelection(
        source_kinds=normalized_source_kinds,
        runtime_source_ids=runtime_source_ids,
        workspace_source_policy_id=workspace_source_policy_id,
    )


class RuntimeSourceSelectionResolver:
    def __init__(
        self,
        *,
        source_policy_resolver: RuntimeSourcePolicyResolver | None = None,
        registered_runtime_source_ids: set[str] | None = None,
        default_runtime_source_ids: Sequence[str] = (),
    ) -> None:
        self.source_policy_resolver = source_policy_resolver
        self.registered_runtime_source_ids = set(registered_runtime_source_ids or ())
        self.default_runtime_source_ids = tuple(default_runtime_source_ids)

    def resolve_runtime_source_selection(
        self,
        *,
        source_kinds: Sequence[str] | None,
        workspace_source_policy_id: str | None,
    ) -> RuntimeSourceSelection:
        return resolve_runtime_source_selection(
            source_kinds=source_kinds,
            workspace_source_policy_id=workspace_source_policy_id,
            source_policy_resolver=self.source_policy_resolver,
            registered_runtime_source_ids=self.registered_runtime_source_ids,
            default_runtime_source_ids=self.default_runtime_source_ids,
        )
