from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from seektalent.source_contracts.contracts import RegisteredSource, SourcePlan


class SourceRegistry:
    def __init__(
        self,
        sources: Iterable[RegisteredSource],
        *,
        default_source_ids: Sequence[str] = (),
    ) -> None:
        registered: dict[str, RegisteredSource] = {}
        for source in sources:
            if source.source_id in registered:
                raise ValueError(f"duplicate_source:{source.source_id}")
            registered[source.source_id] = source
        self._sources = registered
        self._default_source_ids = tuple(default_source_ids)
        for source_id in self._default_source_ids:
            if source_id not in self._sources:
                raise ValueError(f"unknown_default_source:{source_id}")

    def get(self, source_id: str) -> RegisteredSource:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise ValueError(f"unknown_source:{source_id}") from exc

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self._sources)

    @property
    def default_source_ids(self) -> tuple[str, ...]:
        return self._default_source_ids

    def enabled_sources(self, source_ids: Sequence[str] | None) -> tuple[RegisteredSource, ...]:
        if source_ids is None:
            requested_source_ids = self._default_source_ids
        else:
            requested_source_ids = tuple(source_ids)
            if not requested_source_ids:
                raise ValueError("empty_source_selection")
        seen: set[str] = set()
        enabled: list[RegisteredSource] = []
        for source_id in requested_source_ids:
            if source_id in seen:
                raise ValueError(f"duplicate_source:{source_id}")
            seen.add(source_id)
            enabled.append(self.get(source_id))
        return tuple(enabled)

    def build_plans(
        self,
        *,
        source_ids: Sequence[str] | None,
        runtime_run_id: str,
        budget_overrides_by_source: Mapping[str, Mapping[str, int]] | None = None,
    ) -> tuple[SourcePlan, ...]:
        plans: list[SourcePlan] = []
        for source_index, source in enumerate(self.enabled_sources(source_ids)):
            plans.append(
                source.plan(
                    runtime_run_id=runtime_run_id,
                    source_index=source_index,
                    budget_overrides=(
                        budget_overrides_by_source.get(source.source_id)
                        if budget_overrides_by_source is not None
                        else None
                    ),
                )
            )
        return tuple(plans)
