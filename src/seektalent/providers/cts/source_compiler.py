from __future__ import annotations

from dataclasses import dataclass

from seektalent.models import ConstraintValue, RuntimeConstraint
from seektalent.providers.cts.filter_projection import (
    ENUM_NATIVE_FIELDS,
    TEXT_NATIVE_FIELDS,
    _is_unlimited_value,
    _project_enum_filter,
    _project_text_filter,
)
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent


@dataclass(frozen=True)
class CtsCompiledQuery:
    intent: RuntimeSourceQueryIntent
    provider_filters: dict[str, ConstraintValue]
    runtime_only_constraints: tuple[RuntimeConstraint, ...]
    adapter_notes: tuple[str, ...]


def compile_cts_source_query_intents(
    intents: tuple[RuntimeSourceQueryIntent, ...],
) -> tuple[CtsCompiledQuery, ...]:
    return tuple(_compile_cts_source_query_intent(intent) for intent in intents)


def _compile_cts_source_query_intent(intent: RuntimeSourceQueryIntent) -> CtsCompiledQuery:
    if intent.source_kind != "cts":
        raise ValueError(f"cts_source_compiler_wrong_source:{intent.source_kind}")
    provider_filters: dict[str, ConstraintValue] = {}
    runtime_only_constraints: list[RuntimeConstraint] = []
    adapter_notes: list[str] = []

    for filter_intent in intent.filter_intents:
        field = filter_intent.field
        value = filter_intent.value
        if field in TEXT_NATIVE_FIELDS:
            projected = _project_text_filter(field, value)
            if projected is None:
                adapter_notes.append(f"{field} was selected but empty after normalization.")
                continue
            provider_filters[TEXT_NATIVE_FIELDS[field]] = projected
            continue
        if field in ENUM_NATIVE_FIELDS:
            projected, note, skip_runtime_only = _project_enum_filter(field, value)
            if note:
                adapter_notes.append(note)
            if projected is None:
                if skip_runtime_only or _is_unlimited_value(value):
                    continue
                runtime_only_constraints.append(
                    RuntimeConstraint(
                        field=field,
                        normalized_value=value,
                        source="jd",
                        rationale="Field stays runtime-only because no stable CTS enum mapping is available.",
                        blocking=filter_intent.required,
                    )
                )
                continue
            provider_filters[ENUM_NATIVE_FIELDS[field]] = projected
            continue
        raise ValueError(f"unsupported_cts_filter_field:{field}")

    return CtsCompiledQuery(
        intent=intent,
        provider_filters=provider_filters,
        runtime_only_constraints=tuple(runtime_only_constraints),
        adapter_notes=tuple(adapter_notes),
    )

