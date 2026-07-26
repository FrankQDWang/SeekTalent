"""Model-neutral strict JSON bytes admission."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from seektalent.diagnostics_errors import DiagnosticsReason, DiagnosticsSchemaError
from seektalent.diagnostics_registry import EVENT_DEFINITIONS
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


ArtifactType = TypeVar("ArtifactType", bound=BaseModel)


def _known_location_fields() -> frozenset[str]:
    known: set[str] = set()
    pending = list(BaseModel.__subclasses__())
    while pending:
        model = pending.pop()
        known.update(model.model_fields)
        pending.extend(model.__subclasses__())
    for definition in EVENT_DEFINITIONS.values():
        known.update(definition.attribute_fields)
    return frozenset(known)


def _safe_location(location: tuple[str | int, ...]) -> tuple[str, ...]:
    known = _known_location_fields()
    return tuple(
        item
        if isinstance(item, str) and item in known
        else "<item>"
        if isinstance(item, int)
        else "<redacted-field>"
        for item in location
    )


def load_strict(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes):
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)
    try:
        return strict_json_object_loads(raw)
    except StrictJsonError as exc:
        reason = {
            "invalid_utf8": DiagnosticsReason.INVALID_UTF8,
            "invalid_json": DiagnosticsReason.INVALID_JSON,
            "duplicate_key": DiagnosticsReason.DUPLICATE_KEY,
            "illegal_number": DiagnosticsReason.ILLEGAL_NUMBER,
            "invalid_unicode": DiagnosticsReason.INVALID_UNICODE,
            "root_not_object": DiagnosticsReason.ROOT_NOT_OBJECT,
        }[exc.reason.value]
        raise DiagnosticsSchemaError(reason, _safe_location(exc.location)) from None


def parse_artifact_bytes(
    model_cls: type[ArtifactType],
    raw: bytes,
    *,
    context: object | None = None,
    by_alias: bool | None = None,
    by_name: bool | None = None,
) -> ArtifactType:
    if not isinstance(raw, bytes):
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)
    max_raw_bytes = getattr(model_cls, "_max_raw_bytes", None)
    if type(max_raw_bytes) is not int:
        raise DiagnosticsSchemaError(DiagnosticsReason.SCHEMA_VALIDATION)
    if len(raw) > max_raw_bytes:
        raise DiagnosticsSchemaError(DiagnosticsReason.PAYLOAD_TOO_LARGE)
    load_strict(raw)
    try:
        return BaseModel.model_validate_json.__func__(
            model_cls,
            raw,
            strict=True,
            extra="forbid",
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        raise DiagnosticsSchemaError(
            DiagnosticsReason.SCHEMA_VALIDATION,
            _safe_location(tuple(first["loc"])),
        ) from None
