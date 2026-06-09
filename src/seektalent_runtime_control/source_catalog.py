from __future__ import annotations

from collections.abc import Sequence

from seektalent.source_contracts import RegisteredSource, SourceRegistry

from seektalent_runtime_control.errors import RuntimeControlError


def validate_runtime_source_ids(
    registry: SourceRegistry,
    source_ids: Sequence[str] | None,
) -> tuple[RegisteredSource, ...]:
    try:
        return registry.enabled_sources(source_ids)
    except ValueError as exc:
        raise RuntimeControlError("source_id_unavailable", str(exc)) from exc
