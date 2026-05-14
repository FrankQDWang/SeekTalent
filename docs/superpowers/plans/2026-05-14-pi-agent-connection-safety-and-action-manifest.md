# PI Agent Connection Safety And Action Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the runtime hard gates that must exist before live Liepin browser actions are allowed: verified user-owned connection safety, strict DokoBot-compatible action manifest binding, local-only transport defaults, and safe validation-error rendering.

**Product Boundary:** This plan must not add a user-facing legal/compliance confirmation. The user has already authorized the product path by logging into their own Liepin account and binding that connection. Runtime checks are internal: connection ownership, verified login state, stable provider account identity, local-only transport, and sensitive-material protection.

**Architecture:** WorkflowRuntime and Liepin adapter code must prove a live provider connection belongs to the active user/workspace and has a fresh verified login before any PI Agent, DokoBot, or legacy worker path receives a live task. DokoBot action mode remains fail-closed unless a trusted manifest explicitly grants text entry, click, navigation, and pagination while denying network inspection, script evaluation, cookie/header injection, and direct API replay. Liepin provider snapshots default to local-only transport. Validation failures from PI boundary models are rendered through a safe error adapter, never raw Pydantic `errors()` or `json()`.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, existing Liepin adapter/compliance/session store, DokoBot capability models.

**Spec:** `docs/superpowers/specs/2026-05-13-provider-interaction-agent-dokobot-design.md`

**Depends On:**
- `docs/superpowers/plans/2026-05-13-pi-agent-contracts-and-skill-recipes.md`
- `docs/superpowers/plans/2026-05-13-dokobot-capability-and-protected-artifacts.md`
- `docs/superpowers/plans/2026-05-13-detail-grants-and-backend-dispatch.md`
- `docs/superpowers/plans/2026-05-13-pi-agent-boundary-guards-and-compat.md`

---

## File Structure

- Add: `src/seektalent/providers/pi_agent/connection_safety.py`
  - Verified connection safety model and validation helper.
- Modify: `src/seektalent/providers/pi_agent/capabilities.py`
  - Harden `DokoBotActionToolManifest` with schema version, declared operations, forbidden capability checks, trust, transport, expiry, and signature policy.
- Modify: `src/seektalent/providers/pi_agent/dokobot_client.py`
  - Add explicit transport mode and default Liepin read execution to `--local`.
- Add: `src/seektalent/providers/pi_agent/validation_errors.py`
  - Safe Pydantic validation-error renderer.
- Modify: `src/seektalent/providers/liepin/adapter.py`
  - Enforce verified connection safety before live Liepin source runs and detail fetches.
- Test: `tests/test_pi_agent_connection_safety.py`
  - Connection safety and adapter gate tests.
- Modify/Test: `tests/test_dokobot_capabilities.py`
  - Manifest trust, forbidden operation, and local-only transport tests.
- Modify/Test: `tests/test_pi_agent_contracts.py`
  - Safe validation-error renderer tests.

## Task 1: Add Connection Safety, Manifest Trust, And Local-Only Transport

**Files:**
- Create: `src/seektalent/providers/pi_agent/connection_safety.py`
- Modify: `src/seektalent/providers/pi_agent/capabilities.py`
- Modify: `src/seektalent/providers/pi_agent/dokobot_client.py`
- Create: `src/seektalent/providers/pi_agent/validation_errors.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Test: `tests/test_pi_agent_connection_safety.py`
- Test/Modify: `tests/test_dokobot_capabilities.py`
- Test/Modify: `tests/test_pi_agent_contracts.py`

- [ ] **Step 1: Write failing connection safety tests**

Add `tests/test_pi_agent_connection_safety.py` covering:

```python
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from seektalent.providers.pi_agent.connection_safety import (
    ProviderConnectionSafetyRecord,
    ProviderConnectionSafetyValidationError,
    validate_provider_connection_safety,
)


def _connection_safety(**overrides: object) -> ProviderConnectionSafetyRecord:
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
        "verified_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(hours=12),
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
    with pytest.raises(ProviderConnectionSafetyValidationError, match="connection_safety_missing"):
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

    with pytest.raises(ProviderConnectionSafetyValidationError, match="connection_safety_user_mismatch"):
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


def test_connection_safety_blocks_expired_unverified_login_account_mismatch_and_remote_transport() -> None:
    now = datetime.now(UTC)

    cases = [
        (_connection_safety(expires_at=now - timedelta(seconds=1)), "connection_safety_expired"),
        (_connection_safety(login_state="expired"), "connection_safety_login_unverified"),
        (_connection_safety(provider_account_hash="other"), "connection_safety_provider_account_mismatch"),
        (_connection_safety(transport_policy="local_only"), "connection_safety_transport_denied"),
    ]
    for record, code in cases:
        with pytest.raises(ProviderConnectionSafetyValidationError, match=code):
            validate_provider_connection_safety(
                record,
                provider="liepin",
                connection_id="connection_1",
                workspace_id="workspace_1",
                user_id="user_1",
                provider_account_hash="account_hash_1",
                transport="remote_e2e_allowed" if code.endswith("transport_denied") else "local_only",
                now=now,
            )


def test_connection_safety_errors_hide_raw_input_values() -> None:
    with pytest.raises(ValidationError) as error:
        _connection_safety(connection_id="", sensitive_material_policy_id="candidate_secret_value")

    assert "candidate_secret_value" not in str(error.value)
```

Add adapter-level tests proving live Liepin search/detail paths refuse to dispatch when connection safety is missing, expired, owner-mismatched, login-unverified, provider-account-mismatched, or transport-denied. Use the existing `LiepinProviderAdapter` and fake worker/store pattern from `tests/test_liepin_provider_adapter.py`.

- [ ] **Step 2: Implement connection safety model and validator**

Create `src/seektalent/providers/pi_agent/connection_safety.py`:

- `ProviderConnectionSafetyRecord`
  - `schema_version: Literal["provider-connection-safety-v1"]`
  - `provider: Literal["liepin"]`
  - `connection_id: NonEmptyStr`
  - `workspace_id: NonEmptyStr`
  - `user_id: NonEmptyStr`
  - `provider_account_hash: NonEmptyStr`
  - `login_state: Literal["verified", "expired", "verification_required"]`
  - `connection_owner_verified: bool`
  - `sensitive_material_policy_id: NonEmptyStr`
  - `transport_policy: Literal["local_only", "remote_e2e_allowed", "remote_forbidden"]`
  - `verified_at: datetime`
  - `expires_at: datetime`
  - `issued_by: Literal["workflow_runtime"]`
  - `policy_version: NonEmptyStr`
- `ProviderConnectionSafetyValidationError(code: str)`
- `validate_provider_connection_safety(...)`

Use `ConfigDict(extra="forbid", hide_input_in_errors=True)` and require timezone-aware `verified_at` and `expires_at`. Reuse the existing PI `NonEmptyStr` boundary alias from `contracts.py` or define the same `Annotated[str, Field(min_length=1)]` shape locally if importing it would create an unwanted dependency.

Validation rules:

- missing record -> `connection_safety_missing`;
- wrong provider -> `connection_safety_provider_mismatch`;
- wrong connection -> `connection_safety_connection_mismatch`;
- wrong workspace -> `connection_safety_workspace_mismatch`;
- wrong user -> `connection_safety_user_mismatch`;
- `connection_owner_verified is False` -> `connection_safety_owner_unverified`;
- expired record -> `connection_safety_expired`;
- `login_state != "verified"` -> `connection_safety_login_unverified`;
- provider account hash mismatch -> `connection_safety_provider_account_mismatch`;
- sensitive material policy mismatch or missing expected policy -> `connection_safety_material_policy_mismatch`;
- transport mismatch -> `connection_safety_transport_denied`.

Transport validation should use this matrix:

- requested `local_only` is allowed when the record policy is `local_only`, `remote_e2e_allowed`, or `remote_forbidden`;
- requested `remote_e2e_allowed` is allowed only when the record policy is exactly `remote_e2e_allowed`;
- no validation path may silently switch a requested local run to remote.

- [ ] **Step 3: Wire connection safety into Liepin live dispatch**

Modify `src/seektalent/providers/liepin/adapter.py` so live Liepin search and detail fetch require connection safety derived from the existing runtime-owned connection/login state before worker dispatch.

Rules:

- fixture mode remains fixture-only and does not pretend to authorize live access;
- do not add a user-facing confirmation or consent screen;
- live search/detail requires a verified connection safety record or a resolver that can derive one from the existing connection/session/compliance state;
- wrong connection, wrong user/workspace, expired record, unverified login, provider account mismatch, or transport mismatch raises a stable `LiepinWorkerModeError` machine code;
- the worker client must not receive a live request when connection safety validation fails.

If the current store lacks a durable connection safety table, implement a narrow resolver protocol first and leave persistence to a later migration plan. Do not invent broad persistence infrastructure in this plan.

Use an adapter-local protocol shaped like:

```python
class ProviderConnectionSafetyResolver(Protocol):
    def resolve_liepin_connection_safety(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        compliance_gate_ref: str,
        provider_account_hash: str,
        requested_transport: Literal["local_only", "remote_e2e_allowed"],
        now: datetime,
    ) -> ProviderConnectionSafetyRecord | None: ...
```

The initial resolver can derive the safety record from existing runtime-owned Liepin state:

- `LiepinStore.get_connection(...)` must return the bound connection for the same tenant/workspace/actor/connection;
- `LiepinStore.get_compliance_gate(...)` must return the matching gate and the gate must allow the requested purpose;
- `LiepinStore.get_session_metadata(...)` must return `status == "connected"`, matching `provider_account_hash`, non-empty `session_updated_at`, and `revoked_at is None`;
- `worker_client.session_status(...)` must return `status == "ready"`, `fixture_only is False`, and matching `provider_account_hash`;
- `verified_at` should come from the trusted session metadata timestamp when present, otherwise the current runtime timestamp after worker session verification;
- `expires_at` should be `verified_at + configured TTL`, with the first implementation using a local constant if `AppSettings` does not yet have a dedicated setting.

For stable adapter errors, either add a `code` field to `LiepinWorkerModeError` or explicitly use `setup_status` as the machine-readable code. Tests must assert the chosen field, not only a message substring. Error messages should remain safe and must not include candidate material, provider snapshots, session state blobs, or raw DokoBot output.

- [ ] **Step 4: Write failing DokoBot action manifest trust tests**

Extend `tests/test_dokobot_capabilities.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from seektalent.providers.pi_agent.capabilities import DokoBotActionToolManifest, DokoBotCapabilityProbe


def _manifest(**overrides: object) -> DokoBotActionToolManifest:
    payload = {
        "schema_version": "dokobot-action-manifest-v1",
        "manifest_id": "manifest_1",
        "manifest_version": "2026.05.1",
        "provider": "dokobot_compatible",
        "transport": "local_only",
        "declared_operations": {
            "navigate": True,
            "click": True,
            "type_text": True,
            "pagination": True,
            "read_snapshot": True,
            "network_inspection": False,
            "script_evaluation": False,
            "direct_api_replay": False,
            "cookie_header_injection": False,
            "cdp_access": False,
            "stealth_or_proxy_evasion": False,
            "auto_install": False,
            "update_or_config_mutation": False,
            "permission_mutation": False,
            "fallback_mode_mutation": False,
        },
        "forbidden_operations_ack": (
            "network_inspection",
            "script_evaluation",
            "direct_api_replay",
            "cookie_header_injection",
            "cdp_access",
            "stealth_or_proxy_evasion",
            "arbitrary_script_eval",
            "auto_install",
            "update_or_config_mutation",
            "permission_mutation",
            "fallback_mode_mutation",
        ),
        "trust_source": "preconfigured_admin",
        "signature_required": True,
        "manifest_signature": "signature_1",
        "expires_at": datetime.now(UTC) + timedelta(days=30),
        "auto_install_allowed": False,
    }
    payload.update(overrides)
    return DokoBotActionToolManifest(**payload)


def test_manifest_with_required_actions_enables_liepin_action_capability() -> None:
    manifest = _manifest()

    capabilities = DokoBotCapabilityProbe(
        run_command=fake_successful_help,
        action_tool_manifest=manifest,
    ).discover()

    assert capabilities.can_execute_liepin_actions is True


def test_read_only_or_partial_manifest_fails_closed() -> None:
    valid = _manifest()
    manifest = _manifest(declared_operations={**valid.declared_operations.model_dump(), "type_text": False})

    capabilities = DokoBotCapabilityProbe(
        run_command=fake_successful_help,
        action_tool_manifest=manifest,
    ).discover()

    assert capabilities.can_execute_liepin_actions is False


@pytest.mark.parametrize(
    "operation",
    [
        "network_inspection",
        "script_evaluation",
        "direct_api_replay",
        "cookie_header_injection",
        "cdp_access",
        "stealth_or_proxy_evasion",
        "auto_install",
        "update_or_config_mutation",
        "permission_mutation",
        "fallback_mode_mutation",
    ],
)
def test_manifest_rejects_forbidden_enabled_operations(operation: str) -> None:
    manifest = _manifest()
    operations = {**manifest.declared_operations.model_dump(), operation: True}

    with pytest.raises(ValidationError):
        _manifest(declared_operations=operations)


def test_manifest_rejects_untrusted_expired_or_unsigned_in_production() -> None:
    with pytest.raises(ValidationError):
        _manifest(trust_source="untrusted")

    with pytest.raises(ValidationError):
        _manifest(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    with pytest.raises(ValidationError):
        _manifest(signature_required=True, manifest_signature="")
```

- [ ] **Step 5: Harden the DokoBot action manifest model**

Modify `src/seektalent/providers/pi_agent/capabilities.py`:

- replace `enabled_tools`-only manifest logic with typed `declared_operations`;
- model `declared_operations` as its own `PiBoundaryModel`/`BaseModel` with required boolean fields and `extra="forbid"`, not as a loose `dict[str, bool]`;
- keep compatibility properties `supports_click`, `supports_type`, `supports_navigation`, `supports_pagination_action`;
- reject forbidden operation flags when true;
- reject `auto_install_allowed=True`;
- reject expired and naive `expires_at`;
- require signature when `signature_required=True`, and expose a small verifier hook or trusted manifest id allowlist so production trust is not just a non-empty string check;
- record manifest id/version/transport/trust source into `DokoBotCapabilities`;
- keep public CLI read/search as read-only unless manifest validation passes.

- [ ] **Step 6: Enforce local-only transport by default**

Modify `src/seektalent/providers/pi_agent/dokobot_client.py`:

- add `transport_mode: Literal["local_only", "remote_e2e_allowed"] = "local_only"` to client construction or read calls;
- when `transport_mode == "local_only"`, append `--local` to `dokobot read`;
- remote mode must require explicit internal runtime selection and connection safety that permits remote transport;
- never silently switch local to remote when local bridge is unavailable;
- existing timeout, session, `--screens`, and `reuse_tab=False` behavior stays unchanged.

Tests:

- local-only read includes `--local`;
- local bridge failure raises a local transport error and does not retry remote;
- remote read requires explicit transport selection;
- connection safety with `transport_policy="local_only"` or `"remote_forbidden"` blocks `remote_e2e_allowed`;
- connection safety with `transport_policy="remote_e2e_allowed"` permits explicit remote and still permits local;
- Liepin protected snapshots default to local-only.

- [ ] **Step 7: Add safe validation-error rendering**

Create `src/seektalent/providers/pi_agent/validation_errors.py`:

- `SafeValidationIssue`
  - `model_name`
  - `field_path`
  - `error_type`
  - `schema_version`
  - `correlation_id`
- `render_safe_validation_error(error: ValidationError, *, model_name: str, schema_version: str | None, correlation_id: str) -> list[SafeValidationIssue]`

The renderer must not include `input`, `ctx` values that may contain raw input, or raw exception messages containing provider material.

Tests must prove:

- `str(error)` hides raw input by model config;
- `error.errors()` and `error.json()` may contain raw input, so they are not safe outputs;
- `render_safe_validation_error(...)` excludes raw candidate material and includes only stable field/error metadata.

- [ ] **Step 8: Run full verification**

```bash
uv run pytest tests/test_pi_agent_connection_safety.py tests/test_dokobot_capabilities.py tests/test_pi_agent_contracts.py tests/test_liepin_provider_adapter.py -q
uv run pytest tests/test_pi_agent_artifacts.py tests/test_liepin_detail_policy.py tests/test_liepin_pi_skills.py -q
git diff --check
```

Expected: pass.

- [ ] **Step 9: Commit connection safety hardening**

```bash
git add src/seektalent/providers/pi_agent/connection_safety.py src/seektalent/providers/pi_agent/capabilities.py src/seektalent/providers/pi_agent/dokobot_client.py src/seektalent/providers/pi_agent/validation_errors.py src/seektalent/providers/liepin/adapter.py tests/test_pi_agent_connection_safety.py tests/test_dokobot_capabilities.py tests/test_pi_agent_contracts.py tests/test_liepin_provider_adapter.py
git commit -m "feat: gate pi agent connection safety"
```

## Self-Review

- No user-facing legal/compliance confirmation is introduced.
- User login and connection binding remain the product authorization event.
- Runtime checks only verify connection ownership, login freshness, provider account identity, transport policy, and sensitive-material protection.
- DokoBot action mode remains manifest-proven and fail-closed.
- Local-only transport is the default for Liepin provider snapshots.
- Remote transport cannot be selected by fallback.
- Raw Pydantic validation errors are not safe log/SSE/stdout payloads.
- This plan does not broaden browser automation scope or add arbitrary web-agent behavior.
