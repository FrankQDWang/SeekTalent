from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from seektalent.source_contracts import RegisteredSource, SourceRegistry

from seektalent_runtime_control.errors import RuntimeControlError


def validate_runtime_source_ids(
    registry: SourceRegistry,
    source_ids: Sequence[str] | None,
) -> tuple[RegisteredSource, ...]:
    try:
        return registry.enabled_sources(source_ids)
    except ValueError as exc:
        if str(exc) == "empty_source_selection":
            raise RuntimeControlError("source_selection_empty") from exc
        raise RuntimeControlError("source_id_unavailable", str(exc)) from exc


@dataclass(frozen=True)
class RuntimeSourcePolicyResolver:
    registry: SourceRegistry

    def resolve_source_ids(self, source_ids: Sequence[str] | None) -> tuple[str, ...]:
        if source_ids is not None and not source_ids:
            raise RuntimeControlError("source_selection_empty")
        return tuple(source.source_id for source in validate_runtime_source_ids(self.registry, source_ids))
