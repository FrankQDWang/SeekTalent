# SeekTalent

`SeekTalent` is a local-first recruiter workbench with a stable CLI and a local browser UI. It turns a required job title, a job description, and optional sourcing notes into a deterministic multi-round shortlist using requirement extraction, local Liepin retrieval through the WTSCLI browser bridge, per-resume scoring, reflection, and finalization.

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
- Real Liepin integration through the local WTSCLI browser bridge

## Quick Start

### Prerequisites

- Python `3.12+`
- one supported LLM provider credential
- WTSCLI Chrome extension installed and connected
- Liepin already logged in in the local browser profile

### Install as a CLI

Recommended for end users:

```bash
pipx install seektalent==0.7.49
```

If you prefer a plain Python environment:

```bash
pip install seektalent==0.7.49
```

### Domi prepared-machine install

For the current Domi handoff mode, the separate Domi host supplies its Python, Node, and `SEEKTALENT_DOMI_JWT`. The exact delivery archive contains the SeekTalent startup script and does not require a source checkout.

Windows PowerShell:

```powershell
Invoke-Expression (Invoke-RestMethod "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.49/scripts/install-seektalent-domi.ps1"); Install-SeekTalentDomi -Version 0.7.49
$env:SEEKTALENT_DOMI_JWT = "<inject at launch; do not commit>"
$env:DOMI_PYTHON = "<Domi-provided Python executable>"
$env:DOMI_NODE = "<Domi-provided Node executable>"
& .\start-seektalent-domi.ps1
```

macOS shell:

```bash
source <(curl -fsSL "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.49/scripts/install-seektalent-domi.sh") 0.7.49
export SEEKTALENT_DOMI_JWT="<inject at launch; do not commit>"
export DOMI_PYTHON="/path/to/Domi-provided/python"
export DOMI_NODE="/path/to/Domi-provided/node"
scripts/start-seektalent-domi.sh
```

The install script uses the explicitly supplied Domi Python and Node to install the exact package and WTSCLI pair under `~/.seektalent`. The delivered startup script validates that install, exports the host JWT at launch, and execs the package; it never discovers a Domi app version or starts `19826` directly.

The current starter env defaults to the canonical text-LLM surface, with `SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible`, the matching `SEEKTALENT_TEXT_LLM_ENDPOINT_*` values, and bare stage `*_MODEL_ID` settings. Dual-protocol support still exists through the same `SEEKTALENT_TEXT_LLM_*` surface.

### Create a starter env file

```bash
seektalent init
```

For installed PyPI users, `seektalent init` writes a minimal `.env` with one required value:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
```

All other runtime, output, cleanup, source, WTSCLI, Liepin, and model settings use product defaults.

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

The command starts the backend and serves the built React Workbench from the same loopback origin. Starting the Workbench, normal conversation, requirement extraction, requirement editing, and requirement confirmation do not depend on browser readiness. After requirements are confirmed, immediately before a real Liepin source starts, SeekTalent checks the paired WTSCLI runtime, Chrome extension, any `https://h.liepin.com/*` host tab, and the current Liepin session. If the source cannot start, the Workbench keeps the normal source status, adds the verified reason and one action, and offers **重新检查并继续** from the same task after the user fixes it. The Workbench may be open in any browser; the real WTSCLI and Liepin session must be ready in Chrome. The packaged frontend does not require pnpm, Vite, Node, a WTSCLI CLI, or a repository checkout on the user's machine.

For source checkout development, use the repo-local WTSCLI/React launcher:

```bash
scripts/start-dev-workbench.sh
```

For production-package staging on macOS, install the published wheel into a fully isolated home:

```bash
scripts/install-seektalent-staging.sh 0.7.49
# In chrome://extensions, load this unpacked extension first:
# ~/.seektalent-staging/home/.seektalent/chrome-extension/wtscli
~/.seektalent-staging/bin/seektalent-staging --check
~/.seektalent-staging/bin/seektalent-staging
```

This path runs the downloaded production wheel, packaged React frontend, production server flags, and the pinned
WTSCLI browser bridge. It uses standalone Python/Node plus `SEEKTALENT_TEXT_LLM_*` configuration, rejects Domi
runtime paths, and keeps all staging databases, browser-bridge state, caches, and generated secrets under
`~/.seektalent-staging`. The WTSCLI Chrome extension is installed under
`~/.seektalent-staging/home/.seektalent/chrome-extension/wtscli`; load that directory as an unpacked Chrome
extension before running `--check` or live Liepin testing. WTSCLI uses its isolated `19826` endpoint; legacy OpenCLI
`19825` remains untouched and can stay running concurrently.

The development launcher installs React dependencies with pnpm when needed, exports `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` plus `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`, then starts the backend on `127.0.0.1:8012` and the React Workbench on `127.0.0.1:5178`. SeekTalent owns one exact-package WTSCLI lifecycle supervisor for the application lifetime; the user still installs and connects the WTSCLI Chrome extension in their own Chrome profile. When the supervisor and browser surface are ready, Liepin behavior is real local browser behavior, not fixture data.

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
pipx install seektalent==0.7.49
```

This gives you the `seektalent` command directly.

### Python integrators

```bash
pip install seektalent==0.7.49
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

Source-checkout development uses the repo-local React/WTSCLI launcher:

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
