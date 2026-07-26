"""Closed scalar contracts shared by diagnostics schemas and projection."""

from __future__ import annotations

from dataclasses import dataclass
import re


MAX_SAFE_INTEGER = (1 << 53) - 1
_SHA256_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
_RANDOM_REF_RE = re.compile(r"(?!0{32})[0-9a-f]{32}")


@dataclass(frozen=True)
class ScalarContract:
    kind: str
    values: frozenset[str] = frozenset()


BOOLEAN = ScalarContract("boolean")
NON_NEGATIVE_INTEGER = ScalarContract("non_negative_integer")
POSITIVE_INTEGER = ScalarContract("positive_integer")
SHA256_REFERENCE = ScalarContract("sha256_reference")
RANDOM_REFERENCE = ScalarContract("random_reference")


def enum_values(*values: str) -> ScalarContract:
    if not values:
        raise ValueError("diagnostics_empty_scalar_enum")
    return ScalarContract("enum", frozenset(values))


def validate_scalar(value: object, contract: ScalarContract) -> object:
    if contract.kind == "boolean":
        valid = type(value) is bool
    elif contract.kind == "non_negative_integer":
        valid = type(value) is int and 0 <= value <= MAX_SAFE_INTEGER
    elif contract.kind == "positive_integer":
        valid = type(value) is int and 1 <= value <= MAX_SAFE_INTEGER
    elif contract.kind == "enum":
        valid = type(value) is str and value in contract.values
    elif contract.kind == "sha256_reference":
        valid = type(value) is str and _SHA256_RE.fullmatch(value) is not None
    elif contract.kind == "random_reference":
        valid = type(value) is str and _RANDOM_REF_RE.fullmatch(value) is not None
    else:
        raise ValueError("diagnostics_unknown_scalar_contract")
    if not valid:
        raise ValueError("diagnostics_scalar_contract_mismatch")
    return value
