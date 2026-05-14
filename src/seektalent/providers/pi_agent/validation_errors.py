from __future__ import annotations

from pydantic import ValidationError

from seektalent.providers.pi_agent.contracts import NonEmptyStr, PiBoundaryModel


class SafeValidationIssue(PiBoundaryModel):
    model_name: NonEmptyStr
    field_path: NonEmptyStr
    error_type: NonEmptyStr
    schema_version: NonEmptyStr | None = None
    correlation_id: NonEmptyStr


def render_safe_validation_error(
    error: ValidationError,
    *,
    model_name: str,
    schema_version: str | None,
    correlation_id: str,
) -> list[SafeValidationIssue]:
    issues: list[SafeValidationIssue] = []
    for issue in error.errors():
        issues.append(
            SafeValidationIssue(
                model_name=model_name,
                field_path=_field_path(issue.get("loc")),
                error_type=_error_type(issue.get("type")),
                schema_version=schema_version,
                correlation_id=correlation_id,
            )
        )
    return issues


def _field_path(loc: object) -> str:
    if isinstance(loc, tuple) and loc:
        return ".".join(str(part) for part in loc)
    if isinstance(loc, list) and loc:
        return ".".join(str(part) for part in loc)
    return "__root__"


def _error_type(value: object) -> str:
    return value if isinstance(value, str) and value else "validation_error"
