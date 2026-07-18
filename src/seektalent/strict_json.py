from __future__ import annotations

import json
from enum import StrEnum


MAX_SAFE_INTEGER = (1 << 53) - 1


class StrictJsonReason(StrEnum):
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    ILLEGAL_NUMBER = "illegal_number"
    INVALID_UNICODE = "invalid_unicode"
    ROOT_NOT_OBJECT = "root_not_object"


class StrictJsonError(ValueError):
    def __init__(self, reason: StrictJsonReason, location: tuple[str | int, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason.value)


def strict_json_object_loads(raw: bytes) -> dict[str, object]:
    """Load a JSON object while rejecting ambiguous JSON lexical forms."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise StrictJsonError(StrictJsonReason.INVALID_UTF8) from None
    if text.startswith("\ufeff"):
        raise StrictJsonError(StrictJsonReason.INVALID_UTF8)

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise StrictJsonError(StrictJsonReason.DUPLICATE_KEY, (key,))
            result[key] = value
        return result

    def reject_number(_: str) -> float:
        raise StrictJsonError(StrictJsonReason.ILLEGAL_NUMBER)

    def parse_integer(value: str) -> int:
        if value == "-0":
            raise StrictJsonError(StrictJsonReason.ILLEGAL_NUMBER)
        parsed = int(value)
        if parsed < -MAX_SAFE_INTEGER or parsed > MAX_SAFE_INTEGER:
            raise StrictJsonError(StrictJsonReason.ILLEGAL_NUMBER)
        return parsed

    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_float=reject_number,
            parse_int=parse_integer,
            parse_constant=reject_number,
        )
    except StrictJsonError:
        raise
    except (json.JSONDecodeError, RecursionError):
        raise StrictJsonError(StrictJsonReason.INVALID_JSON) from None
    if not isinstance(payload, dict):
        raise StrictJsonError(StrictJsonReason.ROOT_NOT_OBJECT)
    _validate_json_value(payload)
    return payload


def _validate_json_value(value: object) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise StrictJsonError(StrictJsonReason.INVALID_UNICODE)
    elif isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise StrictJsonError(StrictJsonReason.INVALID_JSON)
            _validate_json_value(key)
            _validate_json_value(child)
    elif isinstance(value, list):
        for child in value:
            _validate_json_value(child)
