# Liepin Legacy Path Cleanup Design

## Summary

Safely remove the failed Liepin Pi/DokoBot product path and the stale public contract around it, while preserving the deterministic OpenCLI Liepin path and the explicitly named Bun worker compatibility path.

The cleanup is not a blind deletion. The current runtime no longer accepts `pi_agent` or `dokobot_action` as live Liepin modes, but active OpenCLI code and a few Liepin policy models still live under `src/seektalent/providers/pi_agent`. This work turns that accidental namespace overlap into clear ownership:

- OpenCLI deterministic retrieval is the product path.
- `managed_local` and `external_http` are worker compatibility modes, not the failed Pi/DokoBot fallback.
- Pi/DokoBot setup, doctor, docs, tests, and stale e2e fixture language are removed from active product surfaces.
- Live Liepin browser behavior remains protected by unit, integration, boundary, and focused Playwright e2e checks.

## Current Code Facts

- `src/seektalent/config.py` accepts `disabled`, `fake_fixture`, `managed_local`, `external_http`, and `opencli` for `liepin_worker_mode`; `pi_agent` and `dokobot_action` are rejected.
- `src/seektalent/providers/liepin/client.py` builds live Liepin clients for `managed_local`, `external_http`, and `opencli`.
- The OpenCLI client is built through `LiepinOpenCliWorkerClient` and `LiepinOpenCliResumeRetriever`, but imports `OpenCliBrowserRunner` from `src/seektalent/providers/pi_agent/opencli_browser.py`.
- `src/seektalent/providers/liepin/policy.py` imports `DetailOpenGrant` and `PiAgentFailureCode` from `seektalent.providers.pi_agent.contracts`.
- `src/seektalent/providers/liepin/adapter.py` imports connection-safety models from `seektalent.providers.pi_agent.connection_safety`.
- The old Liepin-specific Pi worker and executor files are already gone.
- `src/seektalent/providers/pi_agent/pi_external.py`, `local_setup.py`, `dokobot_client.py`, `capabilities.py`, `contracts.py`, and related tests remain.
- `src/seektalent/cli.py` still exposes `pi-agent`, `doctor --live-pi-agent`, Pi/DokoBot setup checks, and a stale `liepin-smoke --worker-mode pi_agent` choice.
- `docs/configuration.md` and `docs/development.md` still describe Pi/DokoBot setup.
- The Svelte Playwright suite currently lists 13 tests. A baseline run produced 11 passing and 2 failing tests; both failures came from stale e2e fixtures that did not provide the current runtime graph route.
- Existing boundary tests already prevent runtime/workbench product paths from directly using DokoBot and prevent direct OpenCLI execution outside the provider helper.

## Problem

The repository now has three different stories about Liepin:

1. The runtime and config layer say deterministic `opencli` is the current path.
2. CLI, docs, and some tests still imply Pi/DokoBot is setup-supported.
3. Active OpenCLI code still lives under `providers/pi_agent`, which makes correct code look like legacy code.

This creates cleanup risk in both directions:

- deleting by string match can remove active OpenCLI behavior;
- preserving all old Pi/DokoBot code keeps failed paths visible and makes future regressions harder to reason about.

The current e2e baseline is also not strong enough to protect the deletion. The suite has useful UI parity checks, but two tests are already failing, and one fixture still contains `Liepin Pi Agent` and `DokoBot lives inside Pi` as backend mock data.

## Goals

- Restore the focused Svelte e2e baseline before deleting legacy code.
- Add or strengthen e2e coverage for the current user-visible Liepin behavior:
  - OpenCLI-style blocked/degraded Liepin source;
  - CTS remains usable in a dual-source session;
  - final queue can still render merged candidates;
  - UI does not leak Pi, DokoBot, legacy login relay, worker internals, raw artifacts, cookies, or auth material.
- Remove `pi_agent` and `dokobot_action` from active CLI, docs, env examples, and tests.
- Keep `opencli` as the deterministic local Liepin path.
- Keep `managed_local` and `external_http` only as explicitly named worker compatibility modes.
- Move active OpenCLI browser code out of the `pi_agent` namespace.
- Move Liepin-owned policy and connection-safety models out of `pi_agent`.
- Delete Pi/DokoBot setup, RPC, capability, direct-read, payload-firewall, and artifact modules once no active product code imports them.
- Preserve or replace the current boundary checks so runtime/workbench cannot reintroduce direct DokoBot, direct OpenCLI execution, or stale Pi worker modes.

## Non-Goals

- Do not perform a live Liepin e2e test against the real site.
- Do not require a logged-in Chrome or real Liepin account in automated e2e.
- Do not remove `managed_local` or `external_http` in this cleanup pass.
- Do not change CTS retrieval behavior.
- Do not change controller query generation, scoring, reflection, finalization, or runtime graph semantics.
- Do not rewrite the Bun `apps/liepin-worker` compatibility worker except for references needed after boundary registry relocation.
- Do not delete historical Superpowers specs/plans that document past experiments.

## Target Architecture

```text
Runtime source dispatch
-> Liepin runtime lane
-> LiepinOpenCliWorkerClient
-> LiepinOpenCliResumeRetriever
-> seektalent.providers.liepin.opencli_browser.OpenCliBrowserRunner
-> SearchResult / RuntimeSourceLaneResult
-> Workbench public API
-> Svelte Workbench
```

Compatibility path remains explicit:

```text
Liepin worker mode managed_local/external_http
-> apps/liepin-worker
-> worker_compat posture
```

Removed active product path:

```text
Pi RPC child agent
-> DokoBot visual read/action tooling
-> Pi-authored Liepin JSON envelope
```

## Cleanup Boundary

Keep:

- `src/seektalent/providers/liepin/opencli_worker_client.py`
- `src/seektalent/providers/liepin/opencli_retriever.py`
- deterministic OpenCLI browser runner and CLI after moving them under `providers/liepin`
- `apps/liepin-worker` while `managed_local` and `external_http` remain valid worker modes
- boundary tests that enforce no direct runtime/workbench DokoBot and no direct runtime/workbench OpenCLI execution

Extract:

- `DetailOpenGrant` and detail-open failure codes into Liepin-owned policy modules.
- `ProviderConnectionSafetyRecord`, `TransportMode`, and `validate_provider_connection_safety` into Liepin-owned connection safety modules.
- boundary registry/pattern files into a neutral browser-action or Liepin boundary location.

Remove from active surfaces:

- `seektalent pi-agent`
- `seektalent doctor --live-pi-agent`
- Pi/DokoBot local setup diagnostics
- `liepin-smoke --worker-mode pi_agent`
- docs that tell users to configure DokoBot or Pi for Liepin
- tests whose only purpose is preserving the failed Pi/DokoBot runtime path

## Regression Guardrail Contract

Before deletion starts:

- `bun run test:e2e -- --list` must show the expected e2e cases.
- Focused e2e files that cover Workbench session detail and dual-source degraded Liepin behavior must pass.
- The full e2e suite should be green or the plan must explicitly remove obsolete failing e2e specs before using the suite as a gate.

After deletion:

- Config tests reject `pi_agent` and `dokobot_action`.
- CLI tests show `opencli`, `managed_local`, and `external_http` are the only live smoke modes.
- Boundary tests fail if runtime/workbench imports DokoBot, runs DokoBot, runs OpenCLI directly, or imports from `providers.pi_agent`.
- Svelte source display tests map OpenCLI safe reason codes to business-facing Chinese copy without exposing `OpenCLI`, `Pi`, `DokoBot`, raw payloads, or local paths.
- Playwright e2e verifies dual-source degraded Liepin behavior with safe copy and no legacy route calls.
- `scripts/verify-dev-workbench.sh` no longer runs Pi/DokoBot tests and still performs backend smoke checks.

## Acceptance Criteria

- No active runtime, Workbench, CLI, or Svelte handwritten source references `pi_agent`, `dokobot_action`, `DokoBot`, `Liepin Pi Agent`, `live-pi-agent`, or Pi/DokoBot setup commands.
- `src/seektalent/providers/pi_agent` is deleted, or only an intentionally empty compatibility package remains with no active imports.
- Active OpenCLI imports come from Liepin-owned modules, not `providers.pi_agent`.
- `seektalent liepin-smoke --worker-mode opencli` is supported; `--worker-mode pi_agent` is rejected by argparse before settings construction.
- `seektalent doctor --json` reports current OpenCLI/static readiness checks and does not advertise a Pi live probe.
- `docs/configuration.md`, `docs/development.md`, `.env.example`, `src/seektalent/default.env`, and `README.md` describe OpenCLI and worker compatibility accurately.
- `managed_local` and `external_http` still work as worker compatibility modes unless a separate gate explicitly sunsets them.
- Existing OpenCLI worker, retriever, runtime source-lane, and Workbench source-status tests pass.
- Focused Playwright e2e for Workbench parity and dual-source degraded Liepin behavior passes.
- Full `scripts/verify-dev-workbench.sh` passes after its obsolete Pi/DokoBot test entries are removed.
