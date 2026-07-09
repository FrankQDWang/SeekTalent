# SeekTalent

`SeekTalent` is a local-first recruiter workbench with a stable CLI and a local browser UI. It turns a required job title, a job description, and optional sourcing notes into a deterministic multi-round shortlist using requirement extraction, local Liepin retrieval through OpenCLI, per-resume scoring, reflection, and finalization.

The current product shape is local-first:

- the CLI remains the stable terminal entrypoint;
- the local recruiter workbench is the primary browser UI for business workflows;
- business data, workflow control state, workbench projections, bounded diagnostics, provider state, and backups stay local by default;
- account entitlement may use a minimal remote control plane, but SeekTalent is not a hosted recruiting SaaS.

## Highlights

- Installable CLI with stable subcommands: `run`, `workbench`, `init`, `doctor`, `version`, `update`, `inspect`
- Stable Python entrypoints: `run_match(...)` and `run_match_async(...)`
- DB-first local control plane for conversation turns, workflow runs, public progress, checkpoints, candidate truth, and recruiter Workbench projections
- Bounded artifact modes: production keeps product state in SQLite; development can emit compact diagnostics; `debug_full_local` is explicit and short-lived
- Explicit text-LLM configuration using `SEEKTALENT_TEXT_LLM_*` plus bare `*_MODEL_ID` values
- Real Liepin integration through the local OpenCLI browser bridge

## Quick Start

### Prerequisites

- Python `3.12+`
- one supported LLM provider credential
- OpenCLI Chrome extension installed and connected
- Liepin already logged in in the local browser profile

### Install as a CLI

Recommended for end users:

```bash
pipx install seektalent==0.7.30
```

If you prefer a plain Python environment:

```bash
pip install seektalent==0.7.30
```

### Domi prepared-machine install

For the current Domi handoff mode, the user machine only needs Domi installed, Chrome already logged in to Liepin, the OpenCLI Chrome extension installed and enabled, and `SEEKTALENT_DOMI_JWT` set in the current terminal. After that, the prepared-machine path is two commands and does not require a source checkout.

Windows PowerShell:

```powershell
Invoke-Expression (Invoke-RestMethod "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.30/scripts/install-seektalent-domi.ps1"); Install-SeekTalentDomi -Version 0.7.30
seektalent workbench
```

macOS shell:

```bash
source <(curl -fsSL "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.30/scripts/install-seektalent-domi.sh") 0.7.30
seektalent workbench
```

The install script uses Domi Python to install the PyPI package into `~/.seektalent/python-prefix/<version>`, generates the `seektalent` command shim under `~/.seektalent/bin`, wires the shim to Domi Python plus Domi Node, and refreshes the root-level `~/.seektalent/seektalent.*` Windows compatibility shims so existing WindowsApps launchers cannot point at stale prefixes. It updates `PATH` only for the current terminal session and does not modify the Domi app/runtime, Chrome, or the OpenCLI Chrome extension.

The current starter env defaults to the canonical text-LLM surface, with `SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible`, the matching `SEEKTALENT_TEXT_LLM_ENDPOINT_*` values, and bare stage `*_MODEL_ID` settings. Dual-protocol support still exists through the same `SEEKTALENT_TEXT_LLM_*` surface.

### Create a starter env file

```bash
seektalent init
```

For installed PyPI users, `seektalent init` writes a minimal `.env` with one required value:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
```

All other runtime, output, cleanup, source, OpenCLI, Liepin, and model settings use product defaults.

### Fill the required value in `.env`

At minimum:

```dotenv
SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
```

Users can also set the same key directly in the current terminal and start immediately:

```bash
export SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
seektalent workbench
```

Active model configuration uses the `SEEKTALENT_TEXT_LLM_*` tuple plus bare `*_MODEL_ID` values. `SEEKTALENT_TEXT_LLM_API_KEY` is the canonical runtime credential.

### Validate the local setup

```bash
seektalent doctor
```

Installed PyPI users start the local Workbench with the packaged frontend:

```bash
seektalent workbench
```

The command starts the backend and serves the built React Workbench from the same loopback origin. It defaults the Workbench to Liepin through OpenCLI in the user's local browser. No extra SeekTalent env configuration is required beyond the LLM API key. SeekTalent downloads and pins its managed Node/OpenCLI runtime under `~/.seektalent/opencli-runtime` on first use when needed. The user still installs and connects the OpenCLI Chrome plugin in their own Chrome profile, and Liepin must already be logged in. If OpenCLI bootstrap, daemon, extension, or Liepin login checks fail, startup exits before launching the server and prints a `reason_code=...` diagnostic. The packaged frontend does not require pnpm, Vite, Node, OpenCLI CLI, or a repository checkout on the user's machine.

For source checkout development, use the repo-local OpenCLI/React launcher:

```bash
scripts/start-dev-workbench.sh
```

The development launcher installs React dependencies with pnpm when needed, points `SEEKTALENT_LIEPIN_OPENCLI_COMMAND` at `apps/web-react/node_modules/.bin/opencli`, exports `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` plus `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`, then starts the backend on `127.0.0.1:8012` and the React Workbench on `127.0.0.1:5178`. The user still installs and connects the OpenCLI Chrome extension in their own Chrome profile. When OpenCLI is selected and ready, Liepin behavior is real local browser behavior, not fixture data.

`doctor`, `inspect --json`, cleanup, and Workbench startup do not upload local databases, provider cookies, browser sessions, raw resumes, or configured secrets. Runtime network calls are limited to the configured LLM provider and the local browser's Liepin session unless an optional provider is explicitly configured. Remote eval logging through W&B/Weave is off by default and requires explicit configuration.

### Recommended black-box workflow

```bash
seektalent --help
seektalent doctor
seektalent run --job-title-file ./job_title.md --jd-file ./jd.md
seektalent inspect --json
seektalent update
```

### Run one workflow

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience"
```

Add `notes` when you want to inject sourcing preferences or exclusions:

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --notes "Shanghai preferred, avoid pure frontend profiles"
```

Canonical output is human-readable. For wrappers and scripts, use machine output:

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --json
```

### Print upgrade instructions

```bash
seektalent update
```

### Inspect the published CLI contract

```bash
seektalent inspect --json
```

## Install Paths

### Terminal users

Recommended:

```bash
pipx install seektalent==0.7.30
```

This gives you the `seektalent` command directly.

### Python integrators

```bash
pip install seektalent==0.7.30
```

Then:

```python
from seektalent import run_match

result = run_match(
    job_title="Python agent engineer",
    jd="Python agent engineer",
)

print(result.final_markdown)
print(result.run_dir)
```

## CLI

The canonical entrypoint is:

```bash
seektalent run --help
```

Available commands:

- `seektalent run`
- `seektalent init`
- `seektalent doctor`
- `seektalent version`
- `seektalent update`
- `seektalent inspect`

Recommended black-box sequence:

- `seektalent --help`
- `seektalent doctor`
- `seektalent run`
- `seektalent inspect --json`
- `seektalent update`

Key options on `run`:

- `--job-title` or `--job-title-file` for the required job title
- `--jd` or `--jd-file` for the required job description
- `--notes` or `--notes-file` for optional sourcing preferences
- `--env-file`
- `--output-dir`
- `--json`

The default output root is `./runs` relative to the current working directory. Override it per run with:

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --output-dir ./outputs
```

Full CLI reference:

- [docs/cli.md](docs/cli.md)

## Wrapping `SeekTalent`

Two supported wrapper patterns are intentionally stable:

### Wrap the CLI

Run:

```bash
seektalent run --job-title "..." --jd "..." --json
```

Then read the single JSON object from stdout.

### Wrap the library

```python
from seektalent import run_match

result = run_match(job_title="...", jd="...", notes="...")
payload = result.final_result.model_dump(mode="json")
```

Pass `notes="..."` when you want to add sourcing preferences; omit it when JD alone is enough.

Use this path when you want to build your own API server, desktop shell, or workflow wrapper around the runtime.

## Configuration

Environment variables are read from `.env` by default. You will usually configure:

- the canonical text-LLM runtime credential `SEEKTALENT_TEXT_LLM_API_KEY`
- text-LLM protocol and endpoint settings under `SEEKTALENT_TEXT_LLM_*`, plus bare stage `*_MODEL_ID` values
- optional CTS settings only when `SEEKTALENT_PROVIDER_NAME=cts` is set explicitly
- runtime settings such as round limits, concurrency, and output directory

Full configuration reference:

- [docs/configuration.md](docs/configuration.md)

Important rules:

- active model variables use bare `*_MODEL_ID` values, not provider-prefixed strings
- the canonical runtime credential is `SEEKTALENT_TEXT_LLM_API_KEY`
- protocol selection and endpoint routing are configured through `SEEKTALENT_TEXT_LLM_*`

## Local Workbench

Installed users start the packaged local Workbench with:

```bash
seektalent workbench
```

Source-checkout development uses the repo-local React/OpenCLI launcher:

```bash
scripts/start-dev-workbench.sh
```

See [docs/development.md](docs/development.md) for lower-level backend/frontend commands and Workbench verification. React Workbench visual acceptance is pinned to the assets under `docs/superpowers/artifacts/react-agent-workbench-design/`.

## Local State And Outputs

Workbench product state is SQLite-first:

- `runtime_control.sqlite3` is the workflow source of truth for runs, commands, public events, checkpoints, stage outputs, candidate truth, and projection state.
- `workbench.sqlite3` is the recruiter-facing projection/read model.
- `conversation_agent.sqlite3` stores thread/turn state and active or historical runtime links.
- `agent_memory.sqlite3` stores advisory memory, usage, jobs, and retention state.

Artifacts are side-channel diagnostics or exports under `artifacts/`, not the production reconciliation path. `prod` avoids full traces by default, `dev` keeps compact bounded diagnostics, and `debug_full_local` must be enabled deliberately.

Output reference:

- [docs/outputs.md](docs/outputs.md)

## Limits

Current boundaries are intentional:

- SeekTalent is local-first, not a hosted multi-tenant recruiting SaaS
- the Workbench is the primary browser UI for local recruiter workflows
- source adapters are scoped to the fields and semantics implemented in this repository
- the runtime is built for auditable deterministic control flow, not open-ended autonomous tool use

## Docs

Start with [docs/README.md](docs/README.md). Active docs are intentionally small; historical plans, old designs, and superseded drafts are not product truth.

## License

This project is licensed under the GNU Affero General Public License v3.0.

See [LICENSE](LICENSE).
