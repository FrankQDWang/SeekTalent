# SeekTalent

`SeekTalent` is a local-first recruiter workbench with a stable CLI and a local browser UI. It turns a required job title, a job description, and optional sourcing notes into a deterministic multi-round shortlist using requirement extraction, controlled CTS retrieval, per-resume scoring, reflection, and finalization.

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
- Real CTS integration with explicit credential requirements

## Quick Start

### Prerequisites

- Python `3.12+`
- one supported LLM provider credential
- CTS credentials for real CTS mode

### Install as a CLI

Recommended for end users:

```bash
pipx install seektalent==0.6.7
```

If you prefer a plain Python environment:

```bash
pip install seektalent==0.6.7
```

The current starter env defaults to the canonical text-LLM surface, with `SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible`, the matching `SEEKTALENT_TEXT_LLM_ENDPOINT_*` values, and bare stage `*_MODEL_ID` settings. Dual-protocol support still exists through the same `SEEKTALENT_TEXT_LLM_*` surface.

### Create a starter env file

```bash
seektalent init
```

For installed PyPI users, `seektalent init` writes a minimal `.env` with only three required values:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
```

All other runtime, output, cleanup, and model settings use product defaults. Source checkout developers should use `.env.example` for the full development configuration surface.

### Fill the required values in `.env`

At minimum:

```dotenv
SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

Users can also set the same three keys directly in the current terminal and start immediately:

```bash
export SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
export SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
export SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
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

The command starts the backend and serves the built React Workbench from the same loopback origin. It defaults the Workbench to CTS + Liepin, with Liepin using OpenCLI through the user's local browser. No extra SeekTalent env configuration is required beyond the three keys above. SeekTalent downloads and pins its managed Node/OpenCLI runtime under `~/.seektalent/opencli-runtime` on first use when needed. The user still installs and connects the OpenCLI Chrome plugin in their own Chrome profile. The packaged frontend does not require pnpm, Vite, or a repository checkout on the user's machine.

For source checkout development, use the repo-local OpenCLI/React launcher:

```bash
scripts/start-dev-workbench.sh
```

The development launcher installs React dependencies with pnpm when needed, points `SEEKTALENT_LIEPIN_OPENCLI_COMMAND` at `apps/web-react/node_modules/.bin/opencli`, exports `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` plus `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`, then starts the backend on `127.0.0.1:8012` and the React Workbench on `127.0.0.1:5178`. The user still installs and connects the OpenCLI Chrome extension in their own Chrome profile. When OpenCLI is selected and ready, Liepin behavior is real local browser behavior, not fixture data.

`doctor`, `inspect --json`, cleanup, and Workbench startup do not upload local databases, provider cookies, browser sessions, raw resumes, or configured secrets. Runtime network calls are limited to the configured LLM provider and CTS provider. Remote eval logging through W&B/Weave is off by default and requires explicit configuration.

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
pipx install seektalent==0.6.7
```

This gives you the `seektalent` command directly.

### Python integrators

```bash
pip install seektalent==0.6.7
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
- CTS settings such as `SEEKTALENT_CTS_BASE_URL`, `SEEKTALENT_CTS_TENANT_KEY`, and `SEEKTALENT_CTS_TENANT_SECRET`
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
- the CTS adapter is scoped to the fields and semantics implemented in this repository
- the runtime is built for auditable deterministic control flow, not open-ended autonomous tool use

## Docs

Start with [docs/README.md](docs/README.md). Active docs are intentionally small; historical plans, old designs, and superseded drafts are not product truth.

## License

This project is licensed under the GNU Affero General Public License v3.0.

See [LICENSE](LICENSE).
