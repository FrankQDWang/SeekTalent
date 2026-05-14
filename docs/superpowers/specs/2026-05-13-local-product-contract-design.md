# Local Product Contract Design

## Purpose

SeekTalent is shifting from an experimental local-first engine with a secondary web UI into a local-first recruiter workbench product. Users should download and run the product locally, use the CLI directly, and start a browser UI from the terminal when they need the workbench.

This spec makes that product contract explicit so later Liepin, entitlement, runtime, and frontend work does not keep inheriting the older "cloud connector" or "minimal UI" framing.

## Product Contract

- The primary deployment shape is a local application installed on a user's machine.
- The supported entrypoints are:
  - `seektalent` CLI for terminal workflows.
  - `seektalent-ui-api` plus `apps/web` for local workbench use during source checkout development.
  - a future packaged command that starts backend and frontend together for non-developer local users.
- The web UI is a first-class local workbench, not a SaaS dashboard and not a throwaway debug surface.
- Default serving is loopback-only. LAN mode remains explicit and trusted-network-only.
- The local data root owns workbench SQLite, corpus/flywheel SQLite, artifacts, backups, browser session metadata, and local caches.
- The repository is not an acceptable default data root for packaged users.
- The product may use a minimal remote control plane for account entitlement and key access, but recruiter data, provider sessions, run artifacts, and raw candidate material stay local unless a later explicit export feature is approved.

## Current Code Facts

- `README.md` still says the primary product is local CLI and that a minimal local web UI is secondary.
- `docs/ui.md` already describes a substantial local recruiter workbench with scoped accounts, sessions, CTS and Liepin source cards, audit events, backup/restore, and rollout readiness checks.
- `src/seektalent/config.py` already resolves production artifacts to `~/.seektalent/artifacts` when `runtime_mode == "prod"`.
- `src/seektalent_ui/network_guard.py` already enforces loopback-by-default and explicit LAN binding.
- `src/seektalent_ui/server.py` exposes `seektalent-ui-api`.
- `apps/web` is a Vite/TanStack workbench app, not a static report.

## Decisions

1. Update public docs to call the product a local recruiter workbench with CLI and UI entrypoints.
2. Keep local-first distinct from offline-only. Local-first means business data and execution state are local; entitlement checks may be remote if key access requires it.
3. Keep the local UI as a product surface. Do not describe it as a thin shim once the workbench path is the main recruiting workflow.
4. Add a single local startup contract that can later be packaged, without forcing the source-checkout developer workflow to disappear.
5. Make data-root safety a product requirement. Startup and doctor checks should warn when the configured root is the repo, a sync folder, or another risky shared path.

## Non-Goals

- This spec does not build the entitlement service.
- This spec does not package a desktop app.
- This spec does not remove the existing CLI.
- This spec does not make the app available on the public internet.
- This spec does not migrate existing local databases.

## User-Visible Behavior

Terminal users can run:

```bash
seektalent --help
seektalent doctor
seektalent run --job-title-file ./job_title.md --jd-file ./jd.md
```

Workbench users can run a documented local UI startup flow and see:

- setup/login;
- session rail;
- JD/source panel;
- strategy graph;
- running notes;
- node detail;
- final shortlist;
- source connection state.

The docs should not imply that a business recruiter needs to install Bun, Playwright, or understand the internal Liepin worker. Source checkout developers may still use Bun directly.

## Boundaries

- CLI runtime output artifacts remain auditable local files.
- Workbench state remains scoped by tenant, workspace, user, session, and source run.
- Local accounts are product accounts for the local workbench, not cloud SaaS tenant accounts.
- Remote entitlement status is separate from local session identity.
- Provider credentials and sessions are not displayed in UI or ordinary logs.

## Acceptance Criteria

- `README.md`, `docs/ui.md`, `docs/cli.md`, and `docs/configuration.md` use one consistent local-product vocabulary.
- `seektalent doctor` or a dedicated local readiness check reports the resolved local data root and flags risky locations.
- The source-checkout startup path remains documented and working.
- The future packaged startup command has a named contract even if packaging is implemented in a later slice.
- Tests cover data-root classification and docs/inspect contract drift where practical.
