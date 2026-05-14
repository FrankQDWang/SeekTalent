from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from seektalent.providers.pi_agent.connection_safety import (
    ProviderConnectionSafetyRecord,
    ProviderConnectionSafetyValidationError,
    validate_provider_connection_safety,
)


def _connection_safety(**overrides: object) -> ProviderConnectionSafetyRecord:
    now = datetime.now(UTC)
    payload = {
        "schema_version": "provider-connection-safety-v1",
        "provider": "liepin",
        "connection_id": "connection_1",
        "workspace_id": "workspace_1",
        "user_id": "user_1",
        "provider_account_hash": "account_hash_1",
        "login_state": "verified",
        "connection_owner_verified": True,
        "sensitive_material_policy_id": "liepin-sensitive-material-protection-v1",
        "transport_policy": "local_only",
        "verified_at": now,
        "expires_at": now + timedelta(hours=12),
        "issued_by": "workflow_runtime",
        "policy_version": "liepin-connection-safety-policy-v1",
    }
    payload.update(overrides)
    return ProviderConnectionSafetyRecord(**payload)


def test_connection_safety_allows_matching_verified_connection() -> None:
    record = _connection_safety()

    validate_provider_connection_safety(
        record,
        provider="liepin",
        connection_id="connection_1",
        workspace_id="workspace_1",
        user_id="user_1",
        provider_account_hash="account_hash_1",
        transport="local_only",
        now=datetime.now(UTC),
    )


def test_connection_safety_blocks_missing_or_mismatched_owner() -> None:
    with pytest.raises(ProviderConnectionSafetyValidationError) as error:
        validate_provider_connection_safety(
            None,
            provider="liepin",
            connection_id="connection_1",
            workspace_id="workspace_1",
            user_id="user_1",
            provider_account_hash="account_hash_1",
            transport="local_only",
            now=datetime.now(UTC),
        )
    assert error.value.code == "connection_safety_missing"

    with pytest.raises(ProviderConnectionSafetyValidationError) as error:
        validate_provider_connection_safety(
            _connection_safety(user_id="other"),
            provider="liepin",
            connection_id="connection_1",
            workspace_id="workspace_1",
            user_id="user_1",
            provider_account_hash="account_hash_1",
            transport="local_only",
            now=datetime.now(UTC),
        )
    assert error.value.code == "connection_safety_user_mismatch"


@pytest.mark.parametrize(
    ("record", "transport", "expected_code"),
    [
        (
            lambda now: _connection_safety(expires_at=now - timedelta(seconds=1)),
            "local_only",
            "connection_safety_expired",
        ),
        (
            lambda now: _connection_safety(login_state="expired"),
            "local_only",
            "connection_safety_login_unverified",
        ),
        (
            lambda now: _connection_safety(provider_account_hash="other"),
            "local_only",
            "connection_safety_provider_account_mismatch",
        ),
        (
            lambda now: _connection_safety(connection_owner_verified=False),
            "local_only",
            "connection_safety_owner_unverified",
        ),
        (
            lambda now: _connection_safety(transport_policy="local_only"),
            "remote_e2e_allowed",
            "connection_safety_transport_denied",
        ),
        (
            lambda now: _connection_safety(transport_policy="remote_forbidden"),
            "remote_e2e_allowed",
            "connection_safety_transport_denied",
        ),
    ],
)
def test_connection_safety_blocks_invalid_state(
    record: object,
    transport: str,
    expected_code: str,
) -> None:
    now = datetime.now(UTC)

    with pytest.raises(ProviderConnectionSafetyValidationError) as error:
        validate_provider_connection_safety(
            record(now),
            provider="liepin",
            connection_id="connection_1",
            workspace_id="workspace_1",
            user_id="user_1",
            provider_account_hash="account_hash_1",
            transport=transport,
            now=now,
        )

    assert error.value.code == expected_code


def test_connection_safety_remote_allowed_still_permits_local() -> None:
    record = _connection_safety(transport_policy="remote_e2e_allowed")

    validate_provider_connection_safety(
        record,
        provider="liepin",
        connection_id="connection_1",
        workspace_id="workspace_1",
        user_id="user_1",
        provider_account_hash="account_hash_1",
        transport="local_only",
        now=datetime.now(UTC),
    )
    validate_provider_connection_safety(
        record,
        provider="liepin",
        connection_id="connection_1",
        workspace_id="workspace_1",
        user_id="user_1",
        provider_account_hash="account_hash_1",
        transport="remote_e2e_allowed",
        now=datetime.now(UTC),
    )


def test_connection_safety_errors_hide_raw_input_values() -> None:
    with pytest.raises(ValidationError) as error:
        _connection_safety(connection_id="", sensitive_material_policy_id="candidate_secret_value")

    assert "candidate_secret_value" not in str(error.value)


def test_connection_safety_rejects_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        _connection_safety(verified_at=datetime.now())
    with pytest.raises(ValidationError):
        _connection_safety(expires_at=datetime.now() + timedelta(hours=1))
