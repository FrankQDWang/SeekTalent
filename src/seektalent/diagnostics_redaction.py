"""Allowlist-first recursive projection for canonical diagnostics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import re
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from seektalent.diagnostics_registry import REDACTION_RULES


Allowlist: TypeAlias = Mapping[str, object]

_MAX_DEPTH = 3
_MAX_KEYS = 64
_MAX_ARRAY_ITEMS = 32
_MAX_STRING_LENGTH = 256
_MAX_REPORT_ITEMS = 32
_MAX_PATH_LENGTH = 128

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
_URL = re.compile(r"(?:https?|file)://", re.IGNORECASE)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_IP_ADDRESS = re.compile(
    r"(?<![0-9])(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}(?![0-9])"
)
_SENSITIVE_VALUE = re.compile(
    r"(?:\bBearer\s+\S+|<\s*(?:html|body|script)\b|"
    r"(?:password|api[_-]?key|authorization|token)\s*[=:]\s*\S+)",
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


def project_diagnostics(payload: object, *, allowlist: Allowlist) -> DiagnosticsProjection:
    """Project one value through an explicit recursive allowlist.

    Unknown fields are omitted and counted. A sensitive value under an
    allowlisted field rejects the complete projection rather than attempting
    value rewriting.
    """
    if not isinstance(allowlist, Mapping):
        raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
    reports: Counter[tuple[str, str]] = Counter()
    projected = _project(payload, allowlist, "$", 0, reports)
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
) -> object:
    if depth > _MAX_DEPTH:
        raise DiagnosticsRedactionError("diagnostics_redaction_too_deep")
    if allowlist is True:
        return _safe_scalar(value)
    if isinstance(allowlist, Mapping):
        allowed_fields: dict[str, object] = {}
        for allowed_key, allowed_value in allowlist.items():
            if not isinstance(allowed_key, str):
                raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
            allowed_fields[allowed_key] = allowed_value
        if not isinstance(value, Mapping):
            raise DiagnosticsRedactionError("diagnostics_redaction_shape_mismatch")
        if len(value) > _MAX_KEYS:
            raise DiagnosticsRedactionError("diagnostics_redaction_object_too_large")
        projected: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise DiagnosticsRedactionError("diagnostics_redaction_invalid_key")
            rule = _sensitive_key_rule(key)
            child_allowlist = allowed_fields.get(key)
            if rule is not None or child_allowlist is None:
                reports[(rule or "field_not_allowlisted", _redacted_path(path))] += 1
                continue
            if not _valid_allowlist_value(child_allowlist):
                raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
            projected[key] = _project(
                child,
                child_allowlist,
                _safe_path(path, key),
                depth + 1,
                reports,
            )
        return projected
    if isinstance(allowlist, list):
        if len(allowlist) != 1 or not _valid_allowlist_value(allowlist[0]):
            raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")
        if not isinstance(value, (list, tuple)):
            raise DiagnosticsRedactionError("diagnostics_redaction_shape_mismatch")
        if len(value) > _MAX_ARRAY_ITEMS:
            raise DiagnosticsRedactionError("diagnostics_redaction_array_too_large")
        return [
            _project(child, allowlist[0], f"{path}[{index}]", depth + 1, reports)
            for index, child in enumerate(value)
        ]
    raise DiagnosticsRedactionError("diagnostics_redaction_invalid_allowlist")


def _safe_scalar(value: object) -> object:
    if value is None or type(value) in {bool, int}:
        return value
    if not isinstance(value, str):
        raise DiagnosticsRedactionError("diagnostics_redaction_scalar_required")
    if len(value) > _MAX_STRING_LENGTH:
        raise DiagnosticsRedactionError("diagnostics_redaction_string_too_large")
    if _is_sensitive_value(value):
        raise DiagnosticsRedactionError("diagnostics_redaction_sensitive_value")
    return value


def _is_sensitive_value(value: str) -> bool:
    return bool(
        _URL.search(value)
        or value.startswith("/")
        or _WINDOWS_ABSOLUTE_PATH.search(value)
        or _IP_ADDRESS.search(value)
        or _SENSITIVE_VALUE.search(value)
    )


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


def _valid_allowlist_value(value: object) -> bool:
    return value is True or isinstance(value, (Mapping, list))


def _redacted_path(path: str) -> str:
    return f"{path}.<redacted-field>"[:_MAX_PATH_LENGTH]


def _safe_path(path: str, key: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None:
        return _redacted_path(path)
    return f"{path}.{key}"[:_MAX_PATH_LENGTH]


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
