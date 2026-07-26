"""Allowlist-first recursive projection for canonical diagnostics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from seektalent.diagnostics_registry import REDACTION_RULES
from seektalent.diagnostics_scalar import ScalarContract, validate_scalar


Allowlist: TypeAlias = Mapping[str, object]

_MAX_DEPTH = 3
_MAX_KEYS = 64
_MAX_ARRAY_ITEMS = 32
_MAX_REPORT_ITEMS = 32
_MAX_PATH_LENGTH = 128
_MAX_TOTAL_NODES = 1024
_MAX_TOTAL_KEYS = 512

_CREDENTIAL_KEY = re.compile(
    r"(?:^|_)(?:authorization|auth|cookie|password|secret|token|api_?key|"
    r"handoff_?key|control_?key|hmac_?key|nonce|fence(?:_token)?)(?:$|_)",
    re.IGNORECASE,
)
_BUSINESS_KEY = re.compile(
    r"(?:prompt|job_?description|^jd$|resume|candidate|company|school|"
    r"search_?(?:query|term)|business_?content)",
    re.IGNORECASE,
)
_ACCOUNT_KEY = re.compile(
    r"(?:observed_provider_account_subject|account_subject|email|phone)",
    re.IGNORECASE,
)
_BROWSER_KEY = re.compile(
    r"(?:dom|html|visible_?text|screenshot|download|clipboard|page_?content)",
    re.IGNORECASE,
)
_NETWORK_KEY = re.compile(
    r"(?:^url$|_url$|^ip$|ip_address|ssid|proxy|certificate_subject)",
    re.IGNORECASE,
)
_MACHINE_KEY = re.compile(
    r"(?:hostname|username|user_name|absolute_path|home_path|workspace_path|"
    r"profile_path|chrome_profile)",
    re.IGNORECASE,
)
_RAW_DIAGNOSTIC_KEY = re.compile(
    r"(?:sqlite|database|^db$|^wal$|^shm$|stdout|stderr|^log$|raw_log|"
    r"exception_detail|exception_message|process_env|^env$|command_line)",
    re.IGNORECASE,
)
class DiagnosticsRedactionError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class RedactionReportItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rule: str
    path: str = Field(max_length=_MAX_PATH_LENGTH)
    count: int = Field(strict=True, ge=1, le=2**53 - 1)


class DiagnosticsProjection(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
    )

    value: object = Field(repr=False)
    redacted_count: int = Field(strict=True, ge=0, le=2**53 - 1)
    report: tuple[RedactionReportItem, ...]


@dataclass
class _Budget:
    nodes: int = 0
    keys: int = 0

    def add_node(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_TOTAL_NODES:
            raise DiagnosticsRedactionError("diagnostics_redaction_budget_exceeded")

    def add_keys(self, count: int) -> None:
        self.keys += count
        if self.keys > _MAX_TOTAL_KEYS:
            raise DiagnosticsRedactionError("diagnostics_redaction_budget_exceeded")


def project_diagnostics(payload: object, *, allowlist: Allowlist) -> DiagnosticsProjection:
    """Project one value through an explicit recursive allowlist.

    Unknown fields are omitted and counted. A sensitive value under an
    allowlisted field rejects the complete projection rather than attempting
    value rewriting.
    """
    if not isinstance(allowlist, Mapping):
        raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
    _validate_allowlist(allowlist, depth=0, budget=_Budget())
    reports: Counter[tuple[str, str]] = Counter()
    projected = _project(payload, allowlist, "root", 0, reports, _Budget())
    total = sum(reports.values())
    report = tuple(
        RedactionReportItem(rule=rule, path=path, count=count)
        for (rule, path), count in sorted(reports.items())[:_MAX_REPORT_ITEMS]
    )
    return DiagnosticsProjection(value=projected, redacted_count=total, report=report)


def _project(
    value: object,
    allowlist: object,
    path: str,
    depth: int,
    reports: Counter[tuple[str, str]],
    budget: _Budget,
) -> object:
    budget.add_node()
    if depth > _MAX_DEPTH:
        raise DiagnosticsRedactionError("diagnostics_redaction_too_deep")
    if isinstance(allowlist, ScalarContract):
        try:
            return validate_scalar(value, allowlist)
        except ValueError:
            raise DiagnosticsRedactionError("diagnostics_redaction_scalar_contract_mismatch") from None
    if isinstance(allowlist, Mapping):
        allowed_mapping = {
            key: child
            for key, child in allowlist.items()
            if isinstance(key, str)
        }
        if not isinstance(value, Mapping):
            raise DiagnosticsRedactionError("diagnostics_redaction_shape_mismatch")
        if len(value) > _MAX_KEYS:
            raise DiagnosticsRedactionError("diagnostics_redaction_object_too_large")
        budget.add_keys(len(value))
        projected: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise DiagnosticsRedactionError("diagnostics_redaction_invalid_key")
            rule = _sensitive_key_rule(key)
            child_allowlist = allowed_mapping.get(key)
            if rule is not None or child_allowlist is None:
                reports[(rule or "field_not_allowlisted", _redacted_path(path))] += 1
                continue
            projected[key] = _project(
                child,
                child_allowlist,
                _safe_path(path, key),
                depth + 1,
                reports,
                budget,
            )
        return projected
    if isinstance(allowlist, list):
        if not isinstance(value, (list, tuple)):
            raise DiagnosticsRedactionError("diagnostics_redaction_shape_mismatch")
        if len(value) > _MAX_ARRAY_ITEMS:
            raise DiagnosticsRedactionError("diagnostics_redaction_array_too_large")
        return [
            _project(child, allowlist[0], f"{path}.<item>", depth + 1, reports, budget)
            for child in value
        ]
    raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")


def _validate_allowlist(value: object, *, depth: int, budget: _Budget) -> None:
    budget.add_node()
    if depth > _MAX_DEPTH:
        raise DiagnosticsRedactionError("diagnostics_redaction_too_deep")
    if isinstance(value, ScalarContract):
        if value.kind == "enum" and not value.values:
            raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
        return
    if isinstance(value, Mapping):
        if len(value) > _MAX_KEYS:
            raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
        budget.add_keys(len(value))
        for key, child in value.items():
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None
                or _sensitive_key_rule(key) is not None
            ):
                raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
            _validate_allowlist(child, depth=depth + 1, budget=budget)
        return
    if isinstance(value, list) and len(value) == 1:
        _validate_allowlist(value[0], depth=depth + 1, budget=budget)
        return
    raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")


def _sensitive_key_rule(key: str) -> str | None:
    for pattern, rule in (
        (_CREDENTIAL_KEY, "credential_field"),
        (_BUSINESS_KEY, "business_content_field"),
        (_ACCOUNT_KEY, "account_subject_field"),
        (_BROWSER_KEY, "browser_content_field"),
        (_NETWORK_KEY, "network_identity_field"),
        (_MACHINE_KEY, "machine_identity_field"),
        (_RAW_DIAGNOSTIC_KEY, "raw_diagnostic_field"),
    ):
        if pattern.search(key):
            return rule
    return None


def _redacted_path(path: str) -> str:
    return f"{path}.<redacted-field>"[:_MAX_PATH_LENGTH]


def _safe_path(path: str, key: str) -> str:
    del key
    return f"{path}.<field>"[:_MAX_PATH_LENGTH]


assert REDACTION_RULES == frozenset(
    {
        "field_not_allowlisted",
        "credential_field",
        "business_content_field",
        "account_subject_field",
        "browser_content_field",
        "network_identity_field",
        "machine_identity_field",
        "raw_diagnostic_field",
        "sensitive_value",
    }
)
