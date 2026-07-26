"""Boundary-neutral RFC 8785 canonical JSON bytes."""

from __future__ import annotations

from typing import Literal, TypeAlias

import rfc8785


CanonicalJsonScalar: TypeAlias = bool | int | str | float | None
CanonicalJsonValue: TypeAlias = (
    CanonicalJsonScalar
    | list["CanonicalJsonValue"]
    | tuple["CanonicalJsonValue", ...]
    | dict[str, "CanonicalJsonValue"]
)


class CanonicalJsonError(ValueError):
    def __init__(self, kind: Literal["invalid", "recursion"]) -> None:
        self.kind = kind
        super().__init__("canonical_json_invalid")


def _canonical_json_value(value: object) -> CanonicalJsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_canonical_json_value(item) for item in value)
    if isinstance(value, dict):
        normalized: dict[str, CanonicalJsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError("invalid")
            normalized[key] = _canonical_json_value(item)
        return normalized
    raise CanonicalJsonError("invalid")


def canonical_json_bytes(payload: object) -> bytes:
    """Return the repository's single RFC 8785 byte representation."""
    try:
        return rfc8785.dumps(_canonical_json_value(payload))
    except RecursionError:
        raise CanonicalJsonError("recursion") from None
    except (rfc8785.CanonicalizationError, ValueError):
        raise CanonicalJsonError("invalid") from None
