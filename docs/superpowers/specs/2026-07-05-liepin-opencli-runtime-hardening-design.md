# Liepin OpenCLI Runtime Hardening Design

## Summary

This design hardens the live Liepin source by making the OpenCLI boundary deterministic across development and shipped production builds.

The current product failure is not a single Liepin API bug. It is a boundary problem across three external surfaces:

- the installed OpenCLI runtime and browser daemon;
- the user's local Chrome/OpenCLI extension state;
- Liepin's recruiter web page shape and URL routing.

The fix is a breaking cleanup of that boundary. OpenCLI must use one project-controlled version, Liepin page state must be classified from current DOM evidence, Workbench must stop treating command existence as readiness, and removed compatibility paths must stay removed. The paired implementation plan is:

```text
docs/superpowers/plans/2026-07-05-liepin-opencli-runtime-hardening.md
```

## Confirmed Decisions

- Pin all project-controlled OpenCLI installs to `@jackwener/opencli@1.8.6`.
- Dev and prod use the same OpenCLI launcher and Liepin preflight core. Dev may differ only in source layout and process orchestration.
- No old compatibility behavior is preserved through fallback shims.
- `managed_local` is removed as a live compatibility mode. Live local Liepin is `opencli`.
- Liepin tab cleanup automation is removed from this module. SeekTalent may leave opened/reused tabs for the user to close manually.
- Workbench and CLI readiness must expose raw `liepin_opencli_*` reason codes where the failure comes from Liepin/OpenCLI.

## Current Failure Modes

### 1. OpenCLI Version Drift

The repo currently has more than one OpenCLI version source:

- managed Python runtime;
- repo-local web package dependency;
- latest npm package available to new installs.

That makes dev/prod comparisons weak. A developer can exercise one OpenCLI version while a shipped product bootstraps another. A runtime bug or daemon behavior change can then appear as "Liepin does not work on another machine."

### 2. Fake OpenCLI Session Readiness

The OpenCLI worker currently reports `session_status=ready` from local configuration state. That does not prove:

- daemon is running;
- extension is connected;
- current browser page is a Liepin page;
- Liepin is logged in;
- the user is in a recruiter identity/session;
- the page is not an identity selector, risk page, captcha, or modal;
- a recruiter search/result surface is reachable.

This creates a false-green path: Workbench can bind a connection and start a source run even though the browser cannot actually search Liepin.

### 3. Liepin URL/DOM Classification Is Too Narrow

The current implementation treats one canonical URL as the search surface:

```text
https://h.liepin.com/search/getConditionItem#session
```

Liepin can serve recruiter search or result pages under nearby surfaces such as `h.liepin.com/resume/search`. A URL-only terminal check that runs before DOM observation can misclassify a valid search/result page as an unknown modal because the path contains `resume`.

The classifier must treat search/result DOM evidence as stronger than broad forbidden path fragments for known recruiter search surfaces.

### 4. Cleanup Is The Wrong Ownership Model

Automatic cleanup of browser tabs has proven unreliable and is not a required product behavior. Trying to close tabs safely creates more state machinery than it repays:

- lease and owned-marker data can go stale;
- OpenCLI tab ids are not a durable ownership proof;
- user-created and SeekTalent-created Liepin tabs are hard to distinguish with enough confidence;
- cleanup errors distract from the real readiness/search path.

This module should stop trying to close tabs. Manual user cleanup is the accepted operating model.

### 5. Local State Writes Are Race-Prone

Lease files, owned-page markers, and action trace event files are read-modify-write JSON files. Multiple OpenCLI/Liepin processes can touch them. Atomic replace protects against partial writes, but it does not protect against lost updates when two processes read the same old value and both write a different next value.

## Goals

- Make dev and prod use the same OpenCLI runtime core.
- Make installed product startup fail with a concrete OpenCLI bootstrap or readiness reason when OpenCLI cannot be installed or started.
- Make `session_status()` a real browser/Liepin readiness preflight.
- Prevent valid recruiter search/result pages from being blocked by URL-only `resume` path checks.
- Keep generic OpenCLI code independent from Liepin page semantics.
- Preserve raw internal `liepin_opencli_*` reasons through Workbench start probing and CLI preflight.
- Lock local OpenCLI JSON state updates across processes.
- Remove dead cleanup and compatibility paths instead of wrapping them with more fallback behavior.

## Non-Goals

- Do not add tab-closing or orphan-tab cleanup.
- Do not repair or enhance cleanup worker behavior.
- Do not add modal-closing behavior.
- Do not support old `managed_local` behavior as an alias for `opencli`.
- Do not add another browser backend in this slice.
- Do not make OpenCLI generic code understand Liepin selectors, DOM, or account states.
- Do not hide Liepin/OpenCLI readiness errors behind `source_browser_backend_unavailable` before Workbench and CLI have recorded the raw reason.
- Do not add fallback chains for old Liepin URLs, old worker modes, or old OpenCLI package versions.

## Design

### 1. Single OpenCLI Runtime Version

`src/seektalent/opencli_launcher.py` owns the shipped managed runtime version. The web package dependency and lockfile must match it exactly:

```text
@jackwener/opencli@1.8.6
```

Development startup should call the same managed launcher by default:

```text
python -m seektalent.opencli_launcher
```

Repo-local `node_modules/.bin/opencli` is not the default live browser path. This removes the current split where dev can silently exercise a different OpenCLI package than prod.

### 2. Shared Liepin OpenCLI Policy

Liepin/OpenCLI policy belongs in the Liepin provider package, not in scripts or generic OpenCLI helpers. It should define:

- allowed Liepin hosts;
- canonical recruiter search URL;
- recruiter search surface family;
- tab reuse fragments;
- allowed start URLs.

The recruiter search surface is a family, not one literal URL. At minimum it includes:

```text
https://h.liepin.com/search/getConditionItem#session
https://h.liepin.com/resume/search
```

Consumers should import this policy instead of repeating defaults. This includes the worker client construction, helper CLI, config defaults, and tests.

### 3. DOM-First State Classification

`LiepinSiteAdapter.state()` must read the current page state before making terminal decisions for allowed Liepin hosts. URL-only classification is still valid for hard host boundaries, such as a non-allowed host or known risk host, but not for broad path fragments on allowed hosts.

The classifier should evaluate in this order:

1. hard risk host;
2. allowed host check;
3. identity, login, captcha, risk markers from DOM text;
4. known recruiter search/result surface with search/result DOM evidence;
5. forbidden non-search URL fragments;
6. no terminal state.

This means a `resume/search` URL with result-list DOM is allowed, while a non-search `resume/detail` page is still blocked unless it is an explicitly owned/allowed detail surface.

### 4. Real Session Status Preflight

`LiepinOpenCliWorkerClient.session_status()` must delegate to the OpenCLI runner/retriever and perform real checks:

- OpenCLI daemon status;
- extension connectivity;
- current URL;
- page state text;
- login-required markers;
- recruiter identity or identity-selection intercept;
- risk/captcha page;
- recruiter search/result surface readiness.

The returned `SessionStatus` remains the Workbench contract, but gains OpenCLI/Liepin evidence fields:

- `safeReasonCode`;
- `currentUrl`;
- `searchSurfaceReady`;
- `resultSurfaceReady`.

`status="ready"` is only valid when the preflight sees a usable recruiter search/result surface or a verified equivalent page state. For local OpenCLI, the provider account subject can remain the local stable subject after real readiness is proven.

### 5. Raw Reason Propagation

Workbench start probes and CLI preflight should preserve raw `liepin_opencli_*` reasons for operator/user diagnosis. Public runtime event normalization can still map to public-safe reason codes at the final event boundary, but internal readiness and start gating must not flatten these cases early.

Examples that must remain distinguishable:

- `liepin_opencli_bootstrap_failed`;
- `liepin_opencli_daemon_not_running`;
- `liepin_opencli_extension_disconnected`;
- `liepin_opencli_login_required`;
- `liepin_opencli_identity_intercept`;
- `liepin_opencli_risk_page`;
- `liepin_opencli_search_not_ready`;
- `liepin_opencli_results_not_ready`.

### 6. Cleanup Removal

Remove the Liepin OpenCLI cleanup actions:

- `cleanup_idle_lease`;
- `cleanup_orphaned_tabs`;
- `watch_idle_lease`.

Remove the dev-script cleanup call. Remove site-config fields and helper methods whose only job is tab cleanup. Remove generic OpenCLI AppleScript cleanup/window helpers after callers are gone.

Lease and owned-page markers may still exist as active-run coordination state, stale-tab recovery hints, and audit evidence. They are not a promise that SeekTalent will close Chrome tabs.

### 7. Locked Local JSON State

Use a small local file-lock helper for Liepin OpenCLI JSON state:

- lease writes and deletes;
- owned-page marker read-modify-write updates;
- action trace event appends.

The lock should cover the full read-modify-write operation. Atomic replace remains useful, but it is not enough without a cross-process lock.

### 8. Compatibility Removal

Remove `managed_local` from live mode choices and construction paths. Tests that only used `managed_local` as a historical way to reach live local Liepin should use `opencli`.

Do not add aliasing such as:

```text
managed_local -> opencli
```

That alias is exactly the kind of compatibility padding this hardening pass is removing.

## Boundaries

### Generic OpenCLI Layer

Owns:

- subprocess command execution;
- daemon status and restart;
- command shape validation;
- generic OpenCLI error parsing.

Does not own:

- Liepin hosts;
- Liepin URLs;
- Liepin selectors;
- Liepin login or identity states;
- recruiter search readiness.

### Liepin Provider Layer

Owns:

- Liepin URL policy;
- state classification;
- recruiter search/result readiness;
- detail route policy;
- local OpenCLI state files used by Liepin runs;
- translating OpenCLI errors to `liepin_opencli_*` reasons.

### Workbench Layer

Owns:

- source connection state;
- source start gating;
- user-facing warning messages;
- consuming `SessionStatus`.

Does not own:

- direct browser commands;
- Liepin DOM parsing;
- OpenCLI process implementation.

## Error Handling

- OpenCLI bootstrap failure surfaces as `liepin_opencli_bootstrap_failed`.
- Daemon/extension failures surface as their raw `liepin_opencli_*` reasons.
- Login, identity, and risk states are not treated as backend unavailable.
- Malformed helper output remains a helper/backend reason.
- Unknown removed actions fail closed as `liepin_opencli_forbidden_command`.

No new retry ladder is introduced. The only retained recovery is explicit OpenCLI daemon restart for known daemon/extension stale states.

## Testing Strategy

Focused tests must cover:

- OpenCLI version pin in Python and web package files.
- Dev launcher uses the managed OpenCLI launcher by default.
- Cleanup actions are rejected and dev cleanup invocation is gone.
- `resume/search` with result DOM is not terminal.
- non-search resume detail URLs still block after DOM observation.
- `session_status()` delegates to real runner preflight.
- Workbench preserves raw `liepin_opencli_*` start-probe reasons.
- `managed_local` is rejected.
- helper CLI and app settings use the same Liepin policy defaults.
- concurrent local JSON appends do not lose events.

Verification should include the focused Liepin/OpenCLI suite, boundary tests, static drift checks for removed modes/actions, and a managed launcher smoke command.

## Acceptance Criteria

- `rg` finds no project-controlled OpenCLI pins other than `1.8.6`.
- `rg` finds no live `managed_local` compatibility path.
- `rg` finds no live cleanup actions or cleanup worker wiring in `src`, `scripts`, or focused tests.
- Workbench cannot mark OpenCLI Liepin as ready without a real browser/Liepin readiness probe.
- A valid recruiter search/result page under the supported surface family is not blocked by URL-only `resume` path classification.
- Other-user machine failures report actionable raw reasons such as daemon missing, extension disconnected, login required, identity intercept, risk page, or search not ready.
- No new browser backend, modal closer, tab closer, or compatibility fallback is added.

## Implementation Plan

Execute the paired plan task-by-task:

```text
docs/superpowers/plans/2026-07-05-liepin-opencli-runtime-hardening.md
```

Use one task owner at a time. Each task should leave the repo in a testable state and commit only the files it owns.
