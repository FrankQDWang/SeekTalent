# Entitlement And Key Control Design

## Purpose

SeekTalent needs account-based authorization while remaining a local-first product. Authorized users may receive access to platform-managed CTS and LLM capability. Unauthorized users must configure their own provider credentials. Platform-managed keys must not be stored as long-lived plaintext secrets on the user's machine.

This spec resolves the central product contradiction: "local program" does not mean "all platform secrets live locally."

## Product Contract

- Local accounts identify local workbench users and protect local sessions.
- Remote entitlement identifies whether a user is allowed to use platform-managed CTS and LLM access.
- Platform-managed CTS and LLM keys do not get written into `.env`, `src/seektalent/default.env`, local SQLite, local artifacts, logs, or ordinary diagnostics.
- Users without entitlement can run in BYOK mode by supplying their own keys locally.
- The app must make the current mode visible:
  - platform-managed;
  - BYOK;
  - missing credentials;
  - entitlement expired;
  - offline entitlement unavailable.

## Current Code Facts

- `.env.example` and `src/seektalent/default.env` currently expose `SEEKTALENT_TEXT_LLM_API_KEY`, `SEEKTALENT_CTS_TENANT_KEY`, and `SEEKTALENT_CTS_TENANT_SECRET` as local configuration keys.
- `src/seektalent/cli.py` validates missing LLM and CTS credentials before active runs.
- `src/seektalent/config.py` centralizes text LLM and CTS settings.
- `src/seektalent_ui/auth.py` and `src/seektalent_ui/workbench_store.py` already provide local user/session/account state.
- `src/seektalent_ui/redaction.py` already centralizes secret redaction patterns.

## Decisions

1. Introduce an entitlement status model distinct from local workbench auth.
2. Add a credential mode model:
   - `platform_managed`;
   - `bring_your_own_key`;
   - `not_configured`.
3. Add a key-provider boundary in Python runtime configuration so callers ask for a capability token or credential handle, not raw environment values.
4. Preserve current BYOK env keys for users who choose local credentials.
5. For platform-managed access, use either a remote proxy call or short-lived scoped token exchange. The implementation plan may start with interfaces and fixture-mode status behavior before any real remote service exists.
6. Fixture entitlement is not live authorization. It may drive tests, local UI status previews, and deterministic documentation examples, but real CTS/LLM runs must fail closed unless a real remote capability provider or an explicitly test-only runtime fixture is selected.
7. Doctor and UI must display credential mode without leaking key material.

## Non-Goals

- This spec does not implement billing.
- This spec does not define public SaaS tenancy.
- This spec does not store platform keys locally in encrypted form as a workaround.
- This spec does not remove BYOK.
- This spec does not allow remote entitlement to access local candidate data by default.

## Security Requirements

- Platform-managed credentials must never appear in local env files.
- Platform-managed credential responses must be short-lived, scoped, and redacted in exception paths.
- Any local cache of entitlement status must contain status, expiry, and account id only, not secrets.
- Logs, artifacts, SSE payloads, workbench events, and audit metadata must pass the existing redaction layer.
- Tests must prove platform-managed mode can run without `SEEKTALENT_TEXT_LLM_API_KEY`, `SEEKTALENT_CTS_TENANT_KEY`, or `SEEKTALENT_CTS_TENANT_SECRET` being present in local env.
- Tests must prove ordinary fixture entitlement alone cannot authorize live platform-managed provider calls.
- Tests must prove BYOK mode still fails fast when required keys are missing.

## User-Visible Behavior

`seektalent doctor` should report:

```text
Entitlement: platform-managed access active until <date>
Credential mode: platform-managed
Local platform keys: none stored
```

or:

```text
Entitlement: not active
Credential mode: BYOK
Missing: SEEKTALENT_TEXT_LLM_API_KEY, SEEKTALENT_CTS_TENANT_KEY, SEEKTALENT_CTS_TENANT_SECRET
```

The workbench settings page should show the same status in business language and should never show raw key values.

## Acceptance Criteria

- `AppSettings` and CLI credential validation distinguish BYOK from platform-managed mode.
- Local auth and remote entitlement are represented as separate models.
- Live platform-managed runs require a real capability provider or an explicitly test-only runtime fixture flag; status fixtures alone fail closed.
- Redaction tests cover entitlement tokens, remote credential responses, local BYOK keys, and provider errors.
- CLI `inspect --json` reports credential mode without secrets.
- Workbench settings can render entitlement and credential status from safe API responses.
- Documentation explains the local-first/key-control distinction plainly.
