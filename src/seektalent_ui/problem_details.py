from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from seektalent_conversation_agent.errors import ConversationAgentError


PROBLEM_TYPE_BASE = "https://seektalent.local/problems/"


class ProblemRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    message: str
    code: str = "invalid"


class ProblemDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    reasonCode: str
    instance: str
    correlationId: str
    regions: list[ProblemRegion] = Field(default_factory=list)


def problem_from_reason(
    *,
    reason_code: str,
    status: int,
    instance: str,
    correlation_id: str,
    detail: str | None = None,
    regions: Sequence[ProblemRegion] | None = None,
) -> ProblemDetails:
    return ProblemDetails(
        type=f"{PROBLEM_TYPE_BASE}{reason_code}",
        title=_title_from_reason(reason_code),
        status=status,
        detail=detail or _detail_from_reason(reason_code),
        reasonCode=reason_code,
        instance=instance,
        correlationId=correlation_id,
        regions=list(regions or []),
    )


def problem_http_error_from_reason(
    *,
    reason_code: str,
    status: int,
    request: Request,
    correlation_id: str,
    detail: str | None = None,
    regions: Sequence[ProblemRegion] | None = None,
    headers: Mapping[str, str] | None = None,
) -> HTTPException:
    problem = problem_from_reason(
        reason_code=reason_code,
        status=status,
        instance=_request_path(request),
        correlation_id=correlation_id,
        detail=detail,
        regions=regions,
    )
    return HTTPException(
        status_code=status,
        detail=problem.model_dump(mode="json", exclude_none=True),
        headers=dict(headers or {}),
    )


def problem_from_conversation_error(
    *,
    exc: ConversationAgentError,
    request: Request,
    correlation_id: str,
) -> ProblemDetails:
    status = problem_status_from_reason(exc.reason_code)
    return problem_from_reason(
        reason_code=exc.reason_code,
        status=status,
        instance=_request_path(request),
        correlation_id=correlation_id,
        regions=regions_from_validation_errors(_payload_errors(exc.payload)),
    )


def problem_http_error_from_conversation_error(
    *,
    exc: ConversationAgentError,
    request: Request,
    correlation_id: str,
) -> HTTPException:
    problem = problem_from_conversation_error(exc=exc, request=request, correlation_id=correlation_id)
    return HTTPException(
        status_code=problem.status,
        detail=problem.model_dump(mode="json", exclude_none=True),
    )


def regions_from_validation_errors(errors: Sequence[Mapping[str, object]]) -> list[ProblemRegion]:
    regions: list[ProblemRegion] = []
    for error in errors:
        loc = error.get("loc", ())
        regions.append(
            ProblemRegion(
                field=_field_from_loc(loc),
                message=_str_or_default(error.get("msg"), "Invalid input."),
                code=_str_or_default(error.get("type"), "invalid"),
            )
        )
    return regions


def no_store_json_response(*, status_code: int, content: Mapping[str, object]) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=dict(content),
        headers={"Cache-Control": "no-store"},
    )


def problem_status_from_reason(reason_code: str) -> int:
    if reason_code == "agent_rate_limited":
        return 429
    if reason_code in {
        "idempotency_key_conflict",
        "agent_request_in_progress",
        "requirement_draft_stale",
        "requirement_sheet_patch_conflict",
        "runtime_stage_output_conflict",
    }:
        return 409
    if reason_code in {
        "candidate_detail_denied",
        "source_policy_disallowed",
    }:
        return 403
    if reason_code in {
        "conversation_not_found",
        "confirm_request_not_found",
        "job_request_revision_not_found",
        "job_request_missing",
        "requirement_draft_not_found",
        "workflow_start_intent_not_found",
        "workbench_outbox_item_not_found",
    }:
        return 404
    if reason_code == "stream_replay_gap":
        return 410
    if reason_code in {
        "projection_unavailable",
        "runtime_projection_unavailable",
        "workflow_start_kill_switch_blocked",
    }:
        return 503
    return 400


def _title_from_reason(reason_code: str) -> str:
    words = reason_code.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Workbench request failed"


def _detail_from_reason(reason_code: str) -> str:
    if reason_code == "agent_request_invalid":
        return "The Workbench request body is invalid."
    if reason_code == "stream_replay_gap":
        return "The requested Workbench stream cursor is no longer replayable."
    if reason_code == "projection_unavailable":
        return "The Workbench projection is temporarily unavailable."
    return "The Workbench request could not be completed."


def _payload_errors(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return []
    normalized: list[Mapping[str, object]] = []
    for error in errors:
        if isinstance(error, Mapping):
            normalized.append({str(key): value for key, value in error.items()})
    return normalized


def _field_from_loc(value: object) -> str:
    if isinstance(value, Sequence) and not isinstance(value, str):
        parts = [str(item) for item in value if item not in {"body", "query", "path"}]
        return ".".join(parts) if parts else "request"
    return "request"


def _str_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _request_path(request: object) -> str:
    url = getattr(request, "url", None)
    path = getattr(url, "path", None)
    return path if isinstance(path, str) and path else "/api/agent/workbench"
