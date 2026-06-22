from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from seektalent_runtime_control.models import RuntimeStageOutput


PUBLIC_ROUND_SUMMARY_OUTPUT_KINDS = {
    "runtime_public_round_query",
    "runtime_public_source_result",
    "runtime_public_merge",
    "runtime_public_scoring",
    "runtime_public_feedback",
}
PUBLIC_FINALIZATION_OUTPUT_KIND = "runtime_public_finalization"
PUBLIC_STAGE_OUTPUT_SCHEMA = "runtime-public-stage-output/v1"
PUBLIC_EVENT_SCHEMA = "runtime_public_event_v1"


class AgentWorkbenchProjectionError(RuntimeError):
    def __init__(self, reason_code: str, *, output_id: str | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.output_id = output_id


@dataclass(frozen=True)
class AgentWorkbenchQueryPackageProjection:
    source_kind: str | None = None
    query_role: str | None = None
    lane_type: str | None = None
    query_terms: tuple[str, ...] = ()
    keyword_query: str | None = None


@dataclass(frozen=True)
class AgentWorkbenchRoundSummaryProjection:
    round_no: int
    status: str
    query_terms: tuple[str, ...] = ()
    keyword_query: str | None = None
    planned_queries: tuple[AgentWorkbenchQueryPackageProjection, ...] = ()
    executed_queries: tuple[AgentWorkbenchQueryPackageProjection, ...] = ()
    raw_candidate_count: int | None = None
    source_identity_count: int | None = None
    unique_new_count: int | None = None
    total_merged_identity_count: int | None = None
    newly_scored_count: int | None = None
    top_pool_count: int | None = None
    resume_quality_comment: str | None = None
    reflection_summary: str | None = None
    reflection_rationale: str | None = None
    suggested_activate_terms: tuple[str, ...] = ()
    suggested_keep_terms: tuple[str, ...] = ()
    suggested_deprioritize_terms: tuple[str, ...] = ()
    suggested_drop_terms: tuple[str, ...] = ()
    suggested_add_filter_fields: tuple[str, ...] = ()
    suggested_keep_filter_fields: tuple[str, ...] = ()
    suggested_drop_filter_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentWorkbenchRunFinalizationProjection:
    selected_identity_count: int | None = None
    revision: int | None = None
    reason_code: str | None = None
    status: str = "completed"


@dataclass
class _RoundFacts:
    round_no: int
    stages: set[str] = field(default_factory=set)
    failed: bool = False
    query_terms: tuple[str, ...] = ()
    keyword_query: str | None = None
    planned_queries: tuple[AgentWorkbenchQueryPackageProjection, ...] = ()
    executed_queries: tuple[AgentWorkbenchQueryPackageProjection, ...] = ()
    raw_candidate_count: int | None = None
    source_identity_count: int | None = None
    unique_new_count: int | None = None
    total_merged_identity_count: int | None = None
    newly_scored_count: int | None = None
    top_pool_count: int | None = None
    resume_quality_comment: str | None = None
    reflection_summary: str | None = None
    reflection_rationale: str | None = None
    suggested_activate_terms: tuple[str, ...] = ()
    suggested_keep_terms: tuple[str, ...] = ()
    suggested_deprioritize_terms: tuple[str, ...] = ()
    suggested_drop_terms: tuple[str, ...] = ()
    suggested_add_filter_fields: tuple[str, ...] = ()
    suggested_keep_filter_fields: tuple[str, ...] = ()
    suggested_drop_filter_fields: tuple[str, ...] = ()

    def freeze(self) -> AgentWorkbenchRoundSummaryProjection:
        return AgentWorkbenchRoundSummaryProjection(
            round_no=self.round_no,
            status=_derive_status(self.stages, failed=self.failed),
            query_terms=self.query_terms,
            keyword_query=self.keyword_query,
            planned_queries=self.planned_queries,
            executed_queries=self.executed_queries,
            raw_candidate_count=self.raw_candidate_count,
            source_identity_count=self.source_identity_count,
            unique_new_count=self.unique_new_count,
            total_merged_identity_count=self.total_merged_identity_count,
            newly_scored_count=self.newly_scored_count,
            top_pool_count=self.top_pool_count,
            resume_quality_comment=self.resume_quality_comment,
            reflection_summary=self.reflection_summary,
            reflection_rationale=self.reflection_rationale,
            suggested_activate_terms=self.suggested_activate_terms,
            suggested_keep_terms=self.suggested_keep_terms,
            suggested_deprioritize_terms=self.suggested_deprioritize_terms,
            suggested_drop_terms=self.suggested_drop_terms,
            suggested_add_filter_fields=self.suggested_add_filter_fields,
            suggested_keep_filter_fields=self.suggested_keep_filter_fields,
            suggested_drop_filter_fields=self.suggested_drop_filter_fields,
        )


def round_summaries_from_stage_outputs(
    outputs: Sequence[RuntimeStageOutput],
    *,
    expected_runtime_run_id: str | None = None,
) -> tuple[AgentWorkbenchRoundSummaryProjection, ...]:
    by_round: dict[tuple[str, int], _RoundFacts] = {}
    for output in outputs:
        _validate_expected_runtime_run_id(output, expected_runtime_run_id=expected_runtime_run_id)
        if output.output_kind == PUBLIC_FINALIZATION_OUTPUT_KIND:
            continue
        if output.output_kind not in PUBLIC_ROUND_SUMMARY_OUTPUT_KINDS:
            continue
        payload = _validated_payload(output)
        if output.round_no is None:
            raise AgentWorkbenchProjectionError("workbench_round_output_round_required", output_id=output.output_id)
        key = (output.runtime_run_id, output.round_no)
        summary = by_round.setdefault(key, _RoundFacts(round_no=output.round_no))
        _apply_round_output(summary, output=output, payload=payload)
    return tuple(by_round[key].freeze() for key in sorted(by_round, key=lambda item: (item[0], item[1])))


def deterministic_finalization_from_stage_outputs(
    outputs: Sequence[RuntimeStageOutput],
    *,
    expected_runtime_run_id: str | None = None,
) -> AgentWorkbenchRunFinalizationProjection | None:
    for output in outputs:
        _validate_expected_runtime_run_id(output, expected_runtime_run_id=expected_runtime_run_id)
    finalizations = [output for output in outputs if output.output_kind == PUBLIC_FINALIZATION_OUTPUT_KIND]
    if not finalizations:
        return None
    output = sorted(finalizations, key=lambda item: (item.created_at, item.output_id))[-1]
    payload = _validated_payload(output)
    if output.round_no is not None:
        raise AgentWorkbenchProjectionError("workbench_finalization_output_must_be_run_level", output_id=output.output_id)
    counts = _mapping(payload.get("counts"))
    details = _mapping(payload.get("details"))
    return AgentWorkbenchRunFinalizationProjection(
        selected_identity_count=_int_or_none(counts.get("selectedIdentityCount")),
        revision=_int_or_none(details.get("finalizationRevision")),
        reason_code=_text(details.get("finalizationReasonCode")),
        status=_text(payload.get("status")) or "completed",
    )


def _apply_round_output(summary: _RoundFacts, *, output: RuntimeStageOutput, payload: Mapping[str, object]) -> None:
    stage = str(output.stage)
    status = _text(payload.get("status"))
    if status in {"failed", "cancelled"}:
        summary.failed = True
    summary.stages.add(stage)
    counts = _mapping(payload.get("counts"))
    details = _mapping(payload.get("details"))
    if stage == "round_query":
        terms = _string_tuple(details.get("queryTerms"))
        if terms:
            summary.query_terms = terms
        keyword_query = _text(details.get("keywordQuery"))
        if keyword_query is not None:
            summary.keyword_query = keyword_query
        planned = _query_packages(details.get("plannedQueries"))
        if planned:
            summary.planned_queries = planned
    elif stage == "source_result":
        raw_count = _int_or_none(counts.get("roundReturned"))
        if raw_count is not None:
            summary.raw_candidate_count = (summary.raw_candidate_count or 0) + raw_count
        source_identity_count = _int_or_none(counts.get("roundIdentities"))
        if source_identity_count is not None:
            summary.source_identity_count = (summary.source_identity_count or 0) + source_identity_count
    elif stage == "merge":
        round_unique = _int_or_none(counts.get("roundUniqueIdentities"))
        if round_unique is not None:
            summary.unique_new_count = round_unique
        total_merged = _int_or_none(counts.get("mergedIdentities"))
        if total_merged is not None:
            summary.total_merged_identity_count = total_merged
    elif stage == "scoring":
        newly_scored = _int_or_none(counts.get("roundIdentities"))
        if newly_scored is not None:
            summary.newly_scored_count = newly_scored
        top_pool = _int_or_none(counts.get("topPoolCount"))
        if top_pool is not None:
            summary.top_pool_count = top_pool
    elif stage == "feedback":
        executed = _query_packages(details.get("executedQueries"))
        if executed:
            summary.executed_queries = executed
        summary.resume_quality_comment = _replace_text(summary.resume_quality_comment, details.get("resumeQualityComment"))
        summary.reflection_summary = _replace_text(summary.reflection_summary, details.get("reflectionSummary"))
        summary.reflection_rationale = _replace_text(summary.reflection_rationale, details.get("reflectionRationale"))
        summary.suggested_activate_terms = _string_tuple(details.get("suggestedActivateTerms"))
        summary.suggested_keep_terms = _string_tuple(details.get("suggestedKeepTerms"))
        summary.suggested_deprioritize_terms = _string_tuple(details.get("suggestedDeprioritizeTerms"))
        summary.suggested_drop_terms = _string_tuple(details.get("suggestedDropTerms"))
        summary.suggested_add_filter_fields = _string_tuple(details.get("suggestedAddFilterFields"))
        summary.suggested_keep_filter_fields = _string_tuple(details.get("suggestedKeepFilterFields"))
        summary.suggested_drop_filter_fields = _string_tuple(details.get("suggestedDropFilterFields"))


def _validated_payload(output: RuntimeStageOutput) -> Mapping[str, object]:
    payload = _mapping(output.output)
    stage = _text(payload.get("stage"))
    if stage is None:
        raise AgentWorkbenchProjectionError("workbench_round_output_stage_missing", output_id=output.output_id)
    expected_output_kind = f"runtime_public_{stage}"
    payload_round = payload.get("roundNo")
    payload_source = payload.get("sourceKind")
    if (
        output.schema_version != PUBLIC_STAGE_OUTPUT_SCHEMA
        or output.output_kind != expected_output_kind
        or output.stage != stage
        or payload_round != output.round_no
        or payload_source != output.node_id
        or payload.get("schemaVersion") != PUBLIC_STAGE_OUTPUT_SCHEMA
        or payload.get("publicEventSchemaVersion") != PUBLIC_EVENT_SCHEMA
    ):
        raise AgentWorkbenchProjectionError("workbench_round_output_metadata_mismatch", output_id=output.output_id)
    return payload


def _validate_expected_runtime_run_id(
    output: RuntimeStageOutput,
    *,
    expected_runtime_run_id: str | None,
) -> None:
    if expected_runtime_run_id is not None and output.runtime_run_id != expected_runtime_run_id:
        raise AgentWorkbenchProjectionError("workbench_round_output_runtime_run_mismatch", output_id=output.output_id)


def _derive_status(stages: set[str], *, failed: bool) -> str:
    if failed:
        return "failed"
    if "feedback" in stages:
        return "completed"
    return "running"


def _query_packages(value: object) -> tuple[AgentWorkbenchQueryPackageProjection, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    packages: list[AgentWorkbenchQueryPackageProjection] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        item_mapping = _mapping(item)
        package = AgentWorkbenchQueryPackageProjection(
            source_kind=_text(item_mapping.get("sourceKind")),
            query_role=_text(item_mapping.get("queryRole")),
            lane_type=_text(item_mapping.get("laneType")),
            query_terms=_string_tuple(item_mapping.get("queryTerms")),
            keyword_query=_text(item_mapping.get("keywordQuery")),
        )
        if package.query_terms or package.keyword_query is not None:
            packages.append(package)
    return tuple(packages)


def _replace_text(current: str | None, value: object) -> str | None:
    text = _text(value)
    return text if text is not None else current


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None
