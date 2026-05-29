from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from seektalent.providers.liepin.detail_grants import DetailOpenGrant, LiepinDetailFailureCode


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "detail-open-grant-v1",
        "approval_id": "approval_1",
        "budget_reservation_id": "budget_1",
        "candidate_ref": "candidate_1",
        "source_run_id": "source_run_1",
        "provider": "liepin",
        "expires_at": datetime(2026, 1, 1, tzinfo=UTC),
        "issued_by": "workflow_runtime",
        "idempotency_key": "detail_candidate_1_approval_1",
        "grant_signature": "signature_1",
    }
    payload.update(overrides)
    return payload


def test_detail_open_grant_accepts_valid_payload() -> None:
    grant = DetailOpenGrant(**_payload())

    assert grant.schema_version == "detail-open-grant-v1"
    assert grant.provider == "liepin"
    assert grant.max_detail_opens == 1
    assert grant.expires_at.tzinfo is not None


def test_detail_open_grant_rejects_naive_expires_at() -> None:
    with pytest.raises(ValidationError) as error:
        DetailOpenGrant(**_payload(expires_at=datetime(2026, 1, 1)))

    assert "expires_at must be timezone-aware" in str(error.value)


def test_detail_open_grant_forbids_extra_fields_and_hides_input_values() -> None:
    with pytest.raises(ValidationError) as error:
        DetailOpenGrant(**_payload(unexpected="candidate_secret_value"))

    error_text = str(error.value)
    assert "extra_forbidden" in error_text
    assert "candidate_secret_value" not in error_text


def test_liepin_detail_failure_code_values_are_liepin_detail_grant_only() -> None:
    assert [code.value for code in LiepinDetailFailureCode] == [
        "detail_open_grant_missing",
        "detail_open_grant_expired",
        "detail_open_grant_candidate_mismatch",
        "detail_open_grant_source_run_mismatch",
    ]


def test_detail_open_grant_requires_non_blank_signature() -> None:
    payload = _payload()
    del payload["grant_signature"]
    with pytest.raises(ValidationError):
        DetailOpenGrant(**payload)

    with pytest.raises(ValidationError):
        DetailOpenGrant(**_payload(grant_signature=""))
