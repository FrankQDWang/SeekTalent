# Entitlement And Key Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate local workbench auth from remote entitlement and support platform-managed capability access without storing platform keys locally.

**Architecture:** Add a narrow entitlement and credential-mode boundary in Python, keep BYOK env keys working, and make platform-managed mode use safe capability handles rather than local secrets. The first slice uses a fixture entitlement provider for status, UI, and deterministic tests; live runtime calls fail closed unless a real capability provider or an explicitly test-only runtime fixture is selected.

**Tech Stack:** Python 3.12, Pydantic, argparse CLI, FastAPI workbench settings routes, SQLite workbench store, pytest.

**Spec:** `docs/superpowers/specs/2026-05-13-entitlement-and-key-control-design.md`

---

## File Structure

- Add: `src/seektalent/entitlement.py`
  - Entitlement status, credential mode, runtime credential state, safe provider interface, fixture provider.
- Modify: `src/seektalent/config.py`
  - Add credential mode settings and platform-managed fixture switch.
- Modify: `src/seektalent/cli.py`
  - Update missing credential checks, doctor, and inspect output.
- Modify: `src/seektalent_ui/models.py`
  - Add safe entitlement response model.
- Modify: `src/seektalent_ui/workbench_routes.py`
  - Add settings/status route for entitlement.
- Modify: `src/seektalent_ui/redaction.py`
  - Ensure entitlement token and capability handle names are redacted.
- Modify: `docs/configuration.md`
  - Document BYOK versus platform-managed mode.
- Test: `tests/test_entitlement_key_control.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_workbench_api.py`
- Test: `tests/test_workbench_security_audit.py`

## Task 1: Add Entitlement Models

**Files:**

- Add: `src/seektalent/entitlement.py`
- Test: `tests/test_entitlement_key_control.py`

- [ ] **Step 1: Write model tests**

  Add:

  ```python
  from seektalent.entitlement import CredentialMode, EntitlementStatus, RuntimeCredentialState, redact_entitlement_payload


  def test_platform_managed_status_has_no_secret_fields() -> None:
      status = EntitlementStatus(
          account_id="acct_123",
          credential_mode=CredentialMode.PLATFORM_MANAGED,
          active=True,
          expires_at="2026-05-14T00:00:00Z",
          reason_code="active",
      )

      payload = status.model_dump(mode="json")

      assert payload["credential_mode"] == "platform_managed"
      assert "api_key" not in str(payload).lower()
      assert "secret" not in str(payload).lower()


  def test_redaction_removes_capability_material() -> None:
      payload = {"capabilityToken": "secret-token", "mode": "platform_managed"}

      redacted = redact_entitlement_payload(payload)

      assert "secret-token" not in str(redacted)


  def test_status_fixture_is_not_runtime_authority() -> None:
      state = RuntimeCredentialState(
          mode=CredentialMode.PLATFORM_MANAGED,
          can_run=False,
          reason_code="platform_managed_fixture_not_runtime_authority",
      )

      assert state.can_run is False
      assert state.reason_code == "platform_managed_fixture_not_runtime_authority"
  ```

- [ ] **Step 2: Run failing tests**

  ```bash
  uv run pytest tests/test_entitlement_key_control.py -q
  ```

  Expected: import failure.

- [ ] **Step 3: Implement models**

  Create `src/seektalent/entitlement.py` with:

  ```python
  from enum import StrEnum
  from typing import Any

  from pydantic import BaseModel, ConfigDict


  class CredentialMode(StrEnum):
      PLATFORM_MANAGED = "platform_managed"
      BRING_YOUR_OWN_KEY = "bring_your_own_key"
      NOT_CONFIGURED = "not_configured"


  class EntitlementStatus(BaseModel):
      model_config = ConfigDict(extra="forbid")

      account_id: str | None
      credential_mode: CredentialMode
      active: bool
      expires_at: str | None = None
      reason_code: str


  class RuntimeCredentialState(BaseModel):
      model_config = ConfigDict(extra="forbid")

      mode: CredentialMode
      can_run: bool
      reason_code: str


  def redact_entitlement_payload(payload: dict[str, Any]) -> dict[str, Any]:
      redacted: dict[str, Any] = {}
      for key, value in payload.items():
          lowered = key.lower()
          if "token" in lowered or "secret" in lowered or "key" in lowered:
              redacted[key] = "[REDACTED]"
          else:
              redacted[key] = value
      return redacted
  ```

- [ ] **Step 4: Run model tests**

  ```bash
  uv run pytest tests/test_entitlement_key_control.py -q
  ```

  Expected: pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/seektalent/entitlement.py tests/test_entitlement_key_control.py
  git commit -m "feat: add entitlement status models"
  ```

## Task 2: Add Credential Mode Settings And CLI Validation

**Files:**

- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/cli.py`
- Modify: `.env.example`
- Modify: `src/seektalent/default.env`
- Modify: `docs/configuration.md`
- Test: `tests/test_cli.py`
- Test: `tests/test_entitlement_key_control.py`

- [ ] **Step 1: Add failing CLI tests**

  Add tests proving status fixture alone does not authorize live provider calls, while an explicitly test-only runtime fixture can bypass local BYOK env keys:

  ```python
  def test_platform_managed_status_fixture_does_not_authorize_live_runtime(monkeypatch) -> None:
      monkeypatch.delenv("SEEKTALENT_TEXT_LLM_API_KEY", raising=False)
      monkeypatch.delenv("SEEKTALENT_CTS_TENANT_KEY", raising=False)
      monkeypatch.delenv("SEEKTALENT_CTS_TENANT_SECRET", raising=False)

      settings = AppSettings(credential_mode="platform_managed", entitlement_fixture_active=True)

      assert _runtime_credential_state(settings).can_run is False
      assert _runtime_credential_state(settings).reason_code == "platform_managed_fixture_not_runtime_authority"


  def test_platform_managed_test_runtime_fixture_skips_local_provider_keys(monkeypatch) -> None:
      monkeypatch.delenv("SEEKTALENT_TEXT_LLM_API_KEY", raising=False)
      monkeypatch.delenv("SEEKTALENT_CTS_TENANT_KEY", raising=False)
      monkeypatch.delenv("SEEKTALENT_CTS_TENANT_SECRET", raising=False)

      settings = AppSettings(
          credential_mode="platform_managed",
          entitlement_fixture_active=True,
          entitlement_fixture_allow_runtime=True,
      )

      assert _missing_active_provider_env_vars(settings) == []
  ```

- [ ] **Step 2: Run failing tests**

  ```bash
  uv run pytest tests/test_cli.py -k platform_managed -q
  ```

  Expected: failure because settings fields do not exist or credential checks still require local keys.

- [ ] **Step 3: Add settings**

  Add to `AppSettings`:

  ```python
  credential_mode: Literal["platform_managed", "bring_your_own_key", "not_configured"] = "bring_your_own_key"
  entitlement_fixture_active: bool = False
  entitlement_fixture_allow_runtime: bool = False
  entitlement_account_id: str | None = None
  entitlement_expires_at: str | None = None
  ```

- [ ] **Step 4: Update credential checks**

  In `src/seektalent/cli.py`, add `_runtime_credential_state(settings)`. Make `_missing_text_llm_env_vars` and `_missing_active_provider_env_vars` skip local key checks only when `_runtime_credential_state(settings).can_run` is true. Ordinary `entitlement_fixture_active=True` reports status but returns `can_run=False` unless `entitlement_fixture_allow_runtime=True`; that flag is for tests and local fixture demos only. Return an entitlement-specific doctor failure when platform-managed mode is selected but runtime capability is inactive.

- [ ] **Step 5: Update env templates and docs**

  Add:

  ```dotenv
  SEEKTALENT_CREDENTIAL_MODE=bring_your_own_key
  SEEKTALENT_ENTITLEMENT_FIXTURE_ACTIVE=false
  SEEKTALENT_ENTITLEMENT_FIXTURE_ALLOW_RUNTIME=false
  SEEKTALENT_ENTITLEMENT_ACCOUNT_ID=
  SEEKTALENT_ENTITLEMENT_EXPIRES_AT=
  ```

  Then run:

  ```bash
  uv run python tools/sync_env_example.py
  ```

- [ ] **Step 6: Run verification**

  ```bash
  uv run pytest tests/test_cli.py tests/test_entitlement_key_control.py -q
  uv run ruff check src/seektalent/config.py src/seektalent/cli.py src/seektalent/entitlement.py tests/test_cli.py tests/test_entitlement_key_control.py
  ```

  Expected: pass.

- [ ] **Step 7: Commit**

  ```bash
  git add src/seektalent/config.py src/seektalent/cli.py src/seektalent/entitlement.py .env.example src/seektalent/default.env docs/configuration.md tests/test_cli.py tests/test_entitlement_key_control.py
  git commit -m "feat: support credential modes"
  ```

## Task 3: Add Workbench Entitlement Status API

**Files:**

- Modify: `src/seektalent_ui/models.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `src/seektalent_ui/redaction.py`
- Test: `tests/test_workbench_api.py`
- Test: `tests/test_workbench_security_audit.py`

- [ ] **Step 1: Add API test**

  Add:

  ```python
  def test_workbench_entitlement_status_is_safe(client) -> None:
      response = client.get("/api/workbench/settings/entitlement")

      assert response.status_code == 200
      text = response.text.lower()
      assert "secret" not in text
      assert "api_key" not in text
      assert "token" not in text
      assert response.json()["credentialMode"] in {
          "platform_managed",
          "bring_your_own_key",
          "not_configured",
      }
  ```

- [ ] **Step 2: Run failing test**

  ```bash
  uv run pytest tests/test_workbench_api.py -k entitlement_status -q
  ```

  Expected: 404.

- [ ] **Step 3: Add response model**

  Add to `src/seektalent_ui/models.py`:

  ```python
  class WorkbenchEntitlementStatusResponse(BaseModel):
      credentialMode: Literal["platform_managed", "bring_your_own_key", "not_configured"]
      active: bool
      accountId: str | None = None
      expiresAt: str | None = None
      reasonCode: str
  ```

- [ ] **Step 4: Add route**

  Add `GET /api/workbench/settings/entitlement` in `workbench_routes.py`, scoped to current user. Build the response from `AppSettings` and the entitlement fixture provider. Do not return capability tokens or local key values.

- [ ] **Step 5: Run verification**

  ```bash
  uv run pytest tests/test_workbench_api.py tests/test_workbench_security_audit.py -q
  ```

  Expected: pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/seektalent_ui/models.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/redaction.py tests/test_workbench_api.py tests/test_workbench_security_audit.py
  git commit -m "feat: expose safe entitlement status"
  ```

## Self-Review

- Spec coverage: credential modes, no local platform key storage, BYOK preservation, doctor/inspect/UI status, and redaction tests are covered.
- Placeholder scan: every task has paths, tests, and expected outcomes.
- Type consistency: `CredentialMode` values match API response literals.
