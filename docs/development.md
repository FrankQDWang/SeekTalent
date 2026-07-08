# Development

This project is optimized for local iteration, small diffs, and readable Python.

## Prerequisites

- Python `3.12+`
- `uv`
- Node `>=24.16`
- pnpm `11.6.0`

Install development dependencies:

```bash
uv sync --group dev
```

## Common commands

Run Ruff lint checks:

```bash
uv run ruff check src tests experiments
```

Ruff is a standalone quality check, not part of `pytest`.
`experiments/` is included in the required Ruff gate.
It includes required anti-silent-exception checks: do not add swallowed exceptions, empty catches, broad catches,
or useless `try`/`except` wrappers. A local `noqa` is acceptable only at a clear runtime or CLI boundary.

Run ty type checks:

```bash
uv run ty check src tests
uv run ty check src/seektalent/runtime/orchestrator.py
uv run ty check --watch src tests
```

ty is a standalone required CI check, not part of `pytest`.

Run the required architecture import guard:

```bash
uv run python tools/check_arch_imports.py
```

The architecture import guard prevents core `src/seektalent` code from importing UI or experiment modules.

Run the source boundary and Tach architecture gates:

```bash
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
scripts/verify-source-decoupling.sh
```

For local dependency investigation, use Tach directly:

```bash
uv run tach report src/seektalent/runtime --raw
uv run tach report src/seektalent_ui --raw
uv run tach show --mermaid -o /tmp/seektalent-tach-stage2-graph.md
uv run tach map -o /tmp/seektalent-tach-stage2-map.json
```

Tach tracks coarse `src/` module direction only; `tests/`, `experiments/`, and generated graph/map files stay out of the committed checks. If the Tach baseline reports dependency drift, either update `tach.toml` to match the intended dependency direction or simplify the import that crossed a boundary.

Run fast local Python tests during normal iteration:

```bash
scripts/test-fast.sh
```

This uses Tach impact analysis through `pytest --tach` and skips tests that are unaffected by the current diff. Use it for edit-test loops, then run the full suite before opening or updating a PR:

```bash
uv run --group dev python -m pytest -q
```

If Tach reports a surprising skip, run the focused test file directly without `--tach`.
Passing paths or test ids to `scripts/test-fast.sh` does this automatically.

## CI Workflow Shape

Direct `main` pushes use a trimmed fast-iteration CI shape:

- `quality-python`
- `workbench-contract`

`quality-python` is an aggregate check over architecture import checks, Ruff, ty, pytest, Workbench schema validation, and push-time privacy/agent-safety quick diff scans. It intentionally does not run the Tach baseline as a default hard gate; use `uv run python tools/check_tach_baseline.py` for architecture drift checks when working on source boundaries or red-zone architecture.

`workbench-contract` runs only when Workbench-relevant paths changed, including on direct `main` pushes. Do not force a full Workbench contract run only because the event is `push`.

`pr-governance` is advisory/manual for the direct-main workflow. Use it when you want PR-shape feedback, file-count/layer-spread review, or red-zone manifest validation; do not treat it as a required direct-push gate. CodeQL runs in its own workflow for Python and JavaScript/TypeScript and should remain non-blocking for fast direct-main iteration or run on its scheduled cadence.

## Test Typing

Use `tests.settings_factory.make_settings()` when tests need `AppSettings`. Do not call `AppSettings(_env_file=None)` directly in tests.

Keep dynamic test boundaries local. For monkeypatches, stubs, fake clients, or third-party typing gaps, prefer a local `cast(Any, ...)` at the boundary. Do not add global ty ignores, bulk suppressions, or production abstractions just to satisfy tests.

Run the CLI help:

```bash
uv run seektalent --help
uv run seektalent exec --help
```

Run the canonical `run` help:

```bash
uv run seektalent run --help
uv run seektalent exec run --help
```

Run the UI API help:

```bash
uv run seektalent-ui-api --help
```

Start the local React workbench with the repo-local OpenCLI browser helper:

```bash
scripts/start-dev-workbench.sh
```

This launcher is the product development preset for the CTS + Liepin local Workbench. It installs React dependencies with pnpm when needed, exports `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` and `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`, points `SEEKTALENT_LIEPIN_OPENCLI_COMMAND` at the repo-local dependency, and then starts both the backend and React frontend. A plain low-level `seektalent-ui-api` command only reads its explicit configuration and does not silently enable Liepin when `SEEKTALENT_LIEPIN_WORKER_MODE=disabled`.

For local Liepin browser readiness, run the Workbench launcher and check the OpenCLI browser helper directly:

```bash
scripts/start-dev-workbench.sh
apps/web-react/node_modules/.bin/opencli daemon status
```

Automated tests do not run live Liepin website e2e. Use targeted smoke checks only when a human operator has prepared a local Chrome/OpenCLI session.

Run frontend tests:

```bash
cd apps/web-react
pnpm test
```

Build packaged Workbench assets before building release distributions:

```bash
python scripts/build_packaged_workbench.py
```

## Domi Runtime Smoke

### Prepared-Machine Domi Workbench

Use this path when the machine already has Domi installed, Chrome is logged in to Liepin, the OpenCLI Chrome extension is installed, and the operator can paste a Domi JWT into the current terminal. It runs the packaged Workbench through the generated Domi shim, which sets the Domi LLM provider and normalized Domi Node path before starting the server. Target-machine testing should use the release-tag script URL rather than requiring a source checkout.

Windows PowerShell:

```powershell
Invoke-Expression (Invoke-RestMethod "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.23/scripts/install-seektalent-domi.ps1"); Install-SeekTalentDomi -Version 0.7.23
seektalent workbench
```

macOS shell:

```bash
source <(curl -fsSL "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.23/scripts/install-seektalent-domi.sh") 0.7.23
seektalent workbench
```

This path does not read Domi Electron storage and does not install a Chrome extension. The installer writes only under `~/.seektalent`, installs the PyPI package with Domi Python, generates the `seektalent` shim, wires it to Domi Python plus Domi Node, and updates `PATH` only for the current terminal session.

When validating from a source checkout, use the checked-in scripts directly:

```powershell
. .\scripts\install-seektalent-domi.ps1; Install-SeekTalentDomi -Version 0.7.23
```

```bash
source scripts/install-seektalent-domi.sh 0.7.23
```

Use this smoke only for validating the packaged Workbench shape inside the Domi-provided runtime on a local Mac with Domi installed.

Required input:

```bash
export SEEKTALENT_DOMI_JWT="<domi jwt>"
```

Run:

```bash
scripts/smoke-domi-runtime.sh
```

Defaults:

- Domi Python: `/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python`
- isolated install root: `~/.seektalent/domi-runtime`
- Domi LLM proxy: `https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1`
- Domi channel: `seek_talent`

The smoke rebuilds the packaged Workbench frontend, builds the current repository wheel, installs it into the isolated Domi runtime venv, runs `seektalent doctor`, sends a Domi LLM proxy hello request, checks OpenCLI daemon status, and starts the packaged Workbench long enough to verify `/openapi.json`.

It does not read Domi Electron storage by default and does not run a complete live Liepin recruiting workflow.

For a foreground Workbench session that stays running until Ctrl+C, use:

```bash
scripts/start-domi-workbench.sh
```

The start script runs the same install/smoke setup first, then `exec`s the installed Domi-runtime `seektalent workbench` process in the foreground.

## Mock CTS for development

`mock CTS` is a development-only path for local testing, regression checks, and prompt/runtime work.

It is not available in the published PyPI CLI.

Example:

```bash
SEEKTALENT_MOCK_CTS=true uv run seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer"
```

Or set it in a source-checkout env file:

```dotenv
SEEKTALENT_MOCK_CTS=true
```

Notes:

- mock CTS avoids live CTS traffic
- mock CTS still requires a valid LLM provider key
- the published CLI rejects `--mock-cts`; use the env setting in a source checkout instead
- this mode is not the recommended path for end users

## Env template source

- `.env.example` is the only env template you should edit by hand.
- `src/seektalent/default.env` is the minimal packaged user template used by installed wheels.
- Tests enforce that the packaged template contains only `SEEKTALENT_TEXT_LLM_API_KEY`, `SEEKTALENT_CTS_TENANT_KEY`, and `SEEKTALENT_CTS_TENANT_SECRET`.

## PyPI Release Build

Build frontend assets before building Python distributions:

```bash
python scripts/build_packaged_workbench.py
uv build --clear
uv publish --dry-run
```

Publishing requires an explicit release gate:

```bash
uv publish --token "$PYPI_TOKEN"
```

## Repo shape

Key directories:

- `src/seektalent/` for the main Agent implementation and CLI
- `src/seektalent_ui/` for the minimal backend API used by the web UI
- `apps/web-react/` for the frontend
- `tests/` for Python tests
- `docs/v-*` for versioned historical design notes

## Contributor expectations

- Prefer small, surgical changes over broad rewrites.
- Keep Agent behavior explicit.
- Do not add defensive fallback layers unless the task genuinely requires them.
- Keep models and configuration close to usage.

## AI Coding Governance

Before opening a non-trivial PR, read `docs/governance/ai-coding-policy.md`.

Repository owners should also keep `docs/governance/github-ruleset-checklist.md` in sync with the active branch protection or ruleset settings.

Use these local checks:

```bash
uv run python tools/check_pr_governance.py --base origin/main
uv run python tools/check_tach_baseline.py
```

For red-zone runtime, provider, prompt, config, CI, or Workbench persistence changes:

```bash
scripts/verify-red-zone.sh
```

`scripts/verify-red-zone.sh` is the focused smoke command for runtime, provider, prompt, config, CI, tools, and Workbench persistence changes. It does not replace the full PR gate; it gives red-zone reviewers a fast signal before broader CI finishes.

For Workbench, BFF, OpenAPI, or React changes:

```bash
scripts/verify-dev-workbench.sh
```

## Release-facing docs

The trusted docs index is `docs/README.md`.

The public entry points for users are:

- `README.md`
- `docs/configuration.md`
- `docs/cli.md`
- `docs/superpowers/artifacts/react-agent-workbench-design/`
- `docs/architecture.md`
- `docs/outputs.md`

When behavior changes, update those docs before adding more design commentary. Historical material under `docs/archive/`, `docs/superpowers/`, `docs/plans/`, and `docs/v-*` is not a source of truth.
