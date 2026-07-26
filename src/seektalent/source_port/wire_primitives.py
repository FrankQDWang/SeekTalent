"""Strict source-port scalar and canonical JSON primitives."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal, TypeAlias

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from seektalent.canonical_json import CanonicalJsonError, canonical_json_bytes

__all__ = ["CanonicalJsonError", "canonical_json_bytes"]


JSON_SAFE_INTEGER = 2**53 - 1
SQLITE_MAX_INTEGER = 2**63 - 1

OperationKind: TypeAlias = Literal[
    "verify_session",
    "search",
    "cards",
    "details",
    "continuation",
    "cleanup",
]


def _bounded_text(*, max_bytes: int):
    def validate(value: str) -> str:
        if not value:
            raise ValueError("source_history_text_empty")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("source_history_text_control_character")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("source_history_text_invalid_unicode") from exc
        if len(encoded) > max_bytes:
            raise ValueError("source_history_text_too_large")
        return value

    return validate


def _sha256(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("source_history_invalid_sha256")
    return value


def _literal_zero(value: object) -> object:
    if type(value) is not int or value != 0:
        raise ValueError("source_history_expected_exact_integer_zero")
    return value


def _literal_one(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("source_history_expected_exact_integer_one")
    return value


def _literal_true(value: object) -> object:
    if type(value) is not bool or value is not True:
        raise ValueError("source_history_expected_exact_true")
    return value


def _literal_false(value: object) -> object:
    if type(value) is not bool or value is not False:
        raise ValueError("source_history_expected_exact_false")
    return value


Opaque96 = Annotated[str, Field(strict=True), AfterValidator(_bounded_text(max_bytes=96))]
Opaque128 = Annotated[str, Field(strict=True), AfterValidator(_bounded_text(max_bytes=128))]
Opaque256 = Annotated[str, Field(strict=True), AfterValidator(_bounded_text(max_bytes=256))]
Sha256 = Annotated[str, Field(strict=True), AfterValidator(_sha256)]
PositiveJsonInteger = Annotated[int, Field(strict=True, ge=1, le=JSON_SAFE_INTEGER)]
NonNegativeJsonInteger = Annotated[int, Field(strict=True, ge=0, le=JSON_SAFE_INTEGER)]
ExactIntegerZero = Annotated[Literal[0], BeforeValidator(_literal_zero)]
ExactIntegerOne = Annotated[Literal[1], BeforeValidator(_literal_one)]
ExactTrue = Annotated[Literal[True], BeforeValidator(_literal_true)]
ExactFalse = Annotated[Literal[False], BeforeValidator(_literal_false)]


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always", strict=True)
