from __future__ import annotations

import json
from dataclasses import dataclass

from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.providers.liepin.filter_compiler import LiepinNativeFilterTarget, compile_liepin_native_filters
from seektalent.runtime.source_filters import UnsupportedSourceFilter
from seektalent.runtime.source_lanes import DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent


@dataclass(frozen=True)
class LiepinCompiledQuery:
    intent: RuntimeSourceQueryIntent
    search_request: SearchRequest
    unsupported_filters: tuple[UnsupportedSourceFilter, ...] = ()


@dataclass(frozen=True)
class LiepinCompiledQueryBundle:
    queries: tuple[LiepinCompiledQuery, ...]
    unsupported_filters: tuple[UnsupportedSourceFilter, ...]


def compile_liepin_source_query_intents(
    intents: tuple[RuntimeSourceQueryIntent, ...],
) -> LiepinCompiledQueryBundle:
    queries: list[LiepinCompiledQuery] = []
    unsupported_filters: list[UnsupportedSourceFilter] = []
    for intent in intents:
        if intent.source_kind != "liepin":
            raise ValueError(f"liepin_source_compiler_wrong_source:{intent.source_kind}")
        native_filter_plan = compile_liepin_native_filters(
            intent,
            budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY,
        )
        for target_index, target in enumerate(native_filter_plan.targets, start=1):
            native_filters = target.to_safe_payload()
            query_unsupported = _unsupported_filters(intent, native_filter_target=target)
            search_request = SearchRequest(
                query_terms=list(intent.query_terms),
                query_role="primary" if intent.query_role == "exploit" else "expansion",
                keyword_query=intent.keyword_query,
                adapter_notes=[item.detail for item in query_unsupported if item.detail],
                runtime_constraints=[],
                fetch_mode="detail",
                page_size=min(intent.requested_count, target.requested_count),
                provider_filters={},
                provider_context={
                    "liepin_max_cards": str(target.requested_count),
                    "liepin_fetch_strategy": "detail_backed_resume_search",
                    "query_instance_id": intent.query_instance_id,
                    "query_fingerprint": intent.query_fingerprint,
                    "runtime_query_role": intent.query_role,
                    "lane_type": intent.lane_type,
                    "source_plan_version": intent.source_plan_version,
                    "liepin_native_filters_json": json.dumps(native_filters, ensure_ascii=False, sort_keys=True),
                    "liepin_source_filter_target_index": str(target_index),
                    "liepin_must_haves_json": json.dumps(
                        list(intent.must_have_capabilities),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "liepin_nice_to_haves_json": json.dumps(
                        list(intent.preferred_capabilities),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )
            queries.append(
                LiepinCompiledQuery(
                    intent=intent,
                    search_request=search_request,
                    unsupported_filters=query_unsupported,
                )
            )
            unsupported_filters.extend(query_unsupported)
    return LiepinCompiledQueryBundle(queries=tuple(queries), unsupported_filters=tuple(unsupported_filters))


def _unsupported_filters(
    intent: RuntimeSourceQueryIntent,
    *,
    native_filter_target: LiepinNativeFilterTarget,
) -> tuple[UnsupportedSourceFilter, ...]:
    unsupported: list[UnsupportedSourceFilter] = [
        UnsupportedSourceFilter(
            source_kind="liepin",
            field=partial.field,
            query_instance_id=intent.query_instance_id,
            safe_reason_code=partial.safe_reason_code,
            detail=partial.detail,
        )
        for partial in native_filter_target.partial_reasons
    ]
    partial_fields = {partial.field for partial in native_filter_target.partial_reasons}
    supported_fields: set[str] = set()
    if native_filter_target.city is not None:
        supported_fields.add("location")
    if native_filter_target.experience_label is not None:
        supported_fields.add("experience_requirement")
    if (
        native_filter_target.age_label is not None
        or native_filter_target.age_min is not None
        or native_filter_target.age_max is not None
    ):
        supported_fields.add("age_requirement")
    if native_filter_target.degree_label is not None:
        supported_fields.add("degree_requirement")
    if native_filter_target.recruitment_type_label is not None or native_filter_target.school_type_labels:
        supported_fields.add("school_type_requirement")

    if (
        intent.location_intent is not None
        and intent.location_intent.allowed_locations
        and native_filter_target.city is None
    ):
        unsupported.append(
            UnsupportedSourceFilter(
                source_kind="liepin",
                field="location",
                query_instance_id=intent.query_instance_id,
                safe_reason_code="source_location_filter_unsupported",
                detail="Liepin browser card search does not yet support Runtime location filters.",
            )
        )
    for filter_intent in intent.filter_intents:
        if filter_intent.field in supported_fields or filter_intent.field in partial_fields:
            continue
        if filter_intent.field == "age_requirement":
            unsupported.append(
                UnsupportedSourceFilter(
                    source_kind="liepin",
                    field=filter_intent.field,
                    query_instance_id=intent.query_instance_id,
                    safe_reason_code="source_age_filter_unsupported",
                    detail="Liepin browser card search does not yet support Runtime age filters.",
                )
            )
            continue
        unsupported.append(
            UnsupportedSourceFilter(
                source_kind="liepin",
                field=filter_intent.field,
                query_instance_id=intent.query_instance_id,
                safe_reason_code="source_filter_unsupported",
                detail=f"Liepin browser card search does not yet support Runtime {filter_intent.field} filters.",
            )
        )
    return tuple(unsupported)
