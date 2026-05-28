# OpenCLI Liepin Execution Contract Design

## Summary

Make the OpenCLI Liepin path a clear, fail-closed, diagnosable execution contract for the BYOK local product.

The product direction is explicit: local Workbench should use real OpenCLI-driven Liepin behavior by default when Liepin is selected and OpenCLI is configured. This work should not add or preserve a product-level "live behavior disabled" gate for OpenCLI. Instead, correctness comes from honoring controller-authored query/filter decisions, verifying each browser action before moving to the next step, surfacing precise blocked reasons, and documenting the OpenCLI readiness path accurately.

This spec extends the existing Liepin observable source subworkflow work. That work makes the source lane visible; this work defines the lower-level execution rules that make those visible steps trustworthy.

## Current Code Facts

- OpenCLI real browser execution lives mainly in `src/seektalent/providers/pi_agent/opencli_browser.py`.
- The runtime Liepin worker path uses `LiepinOpenCliWorkerClient` and `LiepinOpenCliResumeRetriever`.
- Controller-authored request data reaches Liepin through `SearchRequest.provider_context`, including `liepin_native_filters_json`.
- CTS and Liepin should both use the controller-authored query/filter contract. This work must not replace that with deterministic query/filter rewriting.
- `_apply_liepin_native_filters()` currently catches `OpenCliBrowserError`, records an `apply_native_filter` event with `ok: false`, then continues and returns the last working state.
- `_select_liepin_native_filter()` waits and reads state after clicks, but does not explicitly verify that the requested filter became selected before committing the state.
- `_liepin_detail_url_for_ref()` uses an internal OpenCLI `eval` call through `_run_browser_eval()`, while the public OpenCLI command allowlist forbids external arbitrary `eval`.
- `OpenCliBrowserRunner.status()` collapses daemon-not-running, daemon-stale, extension-disconnected, and other command failures into broad unavailable/disconnected reason codes.
- `LiepinOpenCliWorkerClient.ensure_ready()` is currently a no-op.
- `scripts/start-dev-workbench.sh` checks extension connectivity but does not explicitly detect `Daemon: stale`.
- `.env.example` and `src/seektalent/default.env` still say `SEEKTALENT_LIEPIN_LIVE_ENABLED=false` is a live gate, which contradicts the current product direction.
- `README.md` still contains older Pi-oriented launcher wording.
- Source-run-owned Liepin detail tabs are intentionally left open for user inspection in this slice. Verified automatic detail-tab closing is deferred until the OpenCLI fork exposes a safe tab lifecycle primitive.

## Problem

The current OpenCLI path can produce misleading outcomes:

- A required Liepin native filter can fail, but the search can still return candidates from the unfiltered result page.
- A filter click can appear to succeed at the command level even when the page did not apply the requested selection.
- OpenCLI errors are often reported as generic status unavailable, which slows debugging during live demos.
- The code forbids external `eval` but still uses a hidden internal `eval` helper without documenting or testing that boundary.
- Readiness screens and launcher docs can tell the user that OpenCLI is configured while the daemon or extension is not actually ready.
- Documentation still suggests a disabled-by-default live gate, but local BYOK Workbench is supposed to run real Liepin behavior when configured.

The result is not just a logging problem. It can change business semantics by scoring the wrong set of candidates.

## Goals

- Treat known Liepin native filters emitted by the controller as required runtime constraints.
- Fail closed when a required native filter cannot be applied or cannot be verified.
- After each mutating browser action, wait, read state, verify the expected condition, then commit the new state before continuing.
- Preserve successful transient retry behavior for short OpenCLI status interruptions.
- Keep unknown or unsupported Liepin native filter keys visible as skipped diagnostics, not as silently required constraints.
- Keep OpenCLI as the default real Liepin path for the local BYOK product. Do not add a live-behavior kill switch.
- Make internal fixed read-only DOM probing explicit if `eval` remains necessary, while keeping arbitrary external `eval` forbidden.
- Parse OpenCLI structured errors when available and map them to specific safe reason codes.
- Distinguish OpenCLI daemon-not-running, daemon-stale, extension-disconnected, command-missing, timeout, and generic unavailable states.
- Make `ensure_ready()` perform a real OpenCLI readiness check without opening Liepin pages.
- Update launcher and setup documentation so users see the actual OpenCLI default behavior and setup path.

## Non-Goals

- Do not change CTS query/filter strategy.
- Do not change controller-authored query/filter ownership.
- Do not add deterministic fallback query/filter rewriting.
- Do not add system-level tab cleanup, AppleScript, OS automation, or browser process manipulation.
- Do not auto-close user-owned or workflow-created Liepin detail tabs in this work.
- Do not implement durable Liepin subworkflow checkpoint/resume in this work.
- Do not redesign the runtime graph UI.
- Do not expose normalized resumes in the frontend.
- Do not expose raw resume data inside workflow step events.
- Do not add a product live-gate that disables real OpenCLI Liepin behavior by default.
- Do not implement detail-tab lifecycle cleanup before the OpenCLI fork work tracked in `TODOS.md`.

## Required Filter Contract

Known native filters are required once they are present in `liepin_native_filters_json` and compile to a browser action:

- city filters
- experience filters
- age filters
- degree filters
- recruitment type filters
- school type filters

For each required filter:

1. Start from the current committed `OpenCliBrowserResult`.
2. Locate the option in the correct section, opening the native filter menu when the option is not already visible.
3. Click the intended option.
4. Wait for the UI to settle.
5. Read a fresh OpenCLI state.
6. Verify that the expected selected label or selected-chip evidence is present in the fresh state.
7. Commit the fresh state only after verification succeeds.
8. If any step fails after the bounded transient retry, return a blocked envelope with `safe_reason_code="liepin_opencli_filter_unapplied"`.

The blocked envelope should include safe event metadata for the failed filter name, section, and sanitized label. It must not open candidate detail pages after a required filter failure.

Unsupported keys remain diagnostics:

- They should produce `skip_native_filter` events.
- They should not block the search.
- They should not be treated as successfully applied filters.

## Internal Probe Contract

External OpenCLI tool capabilities should continue to forbid arbitrary `eval`.

If the detail URL extraction still needs DOM access that cannot be expressed through stable OpenCLI `find` or `get` commands, the code may keep one internal fixed read-only probe with these constraints:

- The helper name must make the boundary explicit, for example `_run_fixed_readonly_eval_probe`.
- The helper only accepts named static templates owned by the codebase, not caller-provided JavaScript.
- Inputs are validated before interpolation.
- The script performs no mutation, navigation, storage access, cookie access, network access, form submission, or click.
- Output is limited to a Liepin detail URL or `null`.
- Output is rejected if it looks sensitive or is not an allowed Liepin detail URL.
- Tests must assert that public `_run_browser_command("eval", ...)` remains forbidden.
- Capabilities/docs must distinguish "external arbitrary eval forbidden" from "internal fixed read-only probe allowed".

If a stable `find`-based implementation can replace the internal probe with a smaller diff, that is preferred.

## OpenCLI Readiness Contract

`OpenCliBrowserRunner.status()` should return specific safe reason codes:

- `configured` when daemon is running and extension is connected.
- `liepin_opencli_command_missing` when the command cannot be executed.
- `liepin_opencli_timeout` when status command times out.
- `liepin_opencli_daemon_not_running` when daemon status says not running or the status command cannot contact the daemon.
- `liepin_opencli_daemon_stale` when daemon status says stale.
- `liepin_opencli_extension_disconnected` when daemon is running but extension is disconnected or missing.
- `liepin_opencli_status_unavailable` for malformed or unclassified status failures.

`LiepinOpenCliWorkerClient.ensure_ready()` should call the retriever/runner readiness path and raise `LiepinWorkerModeError` with the same code when not ready. It should not open a Liepin tab or mutate browser state.

The dev launcher should detect stale daemon output and restart the OpenCLI daemon in the same way it already restarts when the extension is disconnected.

## OpenCLI Error Mapping Contract

When OpenCLI returns a structured JSON error envelope such as:

```json
{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}
```

the runner should parse the envelope from stdout or stderr and map common codes to safe reason codes:

- `stale_ref` -> `liepin_opencli_stale_ref`
- `selector_not_found` or `not_found` -> `liepin_opencli_selector_not_found`
- `selector_ambiguous` -> `liepin_opencli_selector_ambiguous`
- `target_not_found` -> `liepin_opencli_target_not_found`
- extension disconnect messages -> `liepin_opencli_extension_disconnected`

Action-level code may retry once for stale or unavailable state only where it already has a fresh-state recovery path. It should not add broad fallback chains.

## Documentation Contract

- `.env.example` and `src/seektalent/default.env` must no longer describe `SEEKTALENT_LIEPIN_LIVE_ENABLED=false` as a default live behavior gate for OpenCLI.
- Because current code uses `liepin_live_enabled=True` only to reject `fake_fixture`, the setting may remain as a fixture-safety flag. It must be documented as ignored by OpenCLI local Workbench and not as a switch that disables real Liepin behavior.
- `README.md` must describe `apps/web-svelte/node_modules/.bin/opencli`, Svelte Workbench ports, and OpenCLI extension setup.
- Old Pi launcher claims should be removed from the OpenCLI dev launcher section.
- Setup diagnostics may still distinguish static "configured" from live "ready", but the wording must not imply fake or disabled Liepin behavior when Workbench is launched in OpenCLI mode.

## Acceptance Criteria

- A required native filter click failure returns a blocked Liepin cards/resume envelope with `safe_reason_code="liepin_opencli_filter_unapplied"`.
- A native filter click that returns command success but does not produce selected-filter evidence also blocks with `liepin_opencli_filter_unapplied`.
- No Liepin detail tabs are opened after required filter failure.
- Existing transient retry behavior still succeeds when the second attempt applies the filter and verification passes.
- Unknown native filter keys still produce skipped diagnostics and do not block.
- Public OpenCLI command execution still rejects arbitrary `eval`.
- Any remaining internal detail URL probe is fixed-template, read-only, output-validated, and covered by tests.
- OpenCLI daemon stale, daemon not running, extension disconnected, command missing, timeout, and generic unavailable states map to distinct safe reason codes.
- `LiepinOpenCliWorkerClient.ensure_ready()` fails with the specific safe code when OpenCLI is not ready and succeeds when status is configured.
- `scripts/start-dev-workbench.sh` detects `Daemon: stale` and restarts the daemon.
- `.env.example`, `src/seektalent/default.env`, and `README.md` align with real OpenCLI-by-default local Workbench behavior.
- Relevant targeted pytest suites pass.
- Full backend pytest passes before handoff.
