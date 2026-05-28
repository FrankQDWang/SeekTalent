# SeekTalent

<p>
  <a href="#english"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="./README.zh-CN.md"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## English

`SeekTalent` is a local-first recruiter workbench with a stable CLI and a local browser UI. It turns a required job title, a job description, and optional sourcing notes into a deterministic multi-round shortlist using requirement extraction, controlled CTS retrieval, per-resume scoring, reflection, and finalization.

The current product shape is local-first:

- the CLI remains the stable terminal entrypoint;
- the local recruiter workbench is the primary browser UI for business workflows;
- business data, workbench state, run artifacts, provider snapshots, and backups stay local by default;
- account entitlement may use a minimal remote control plane, but SeekTalent is not a hosted recruiting SaaS.

## Highlights

- Installable CLI with stable subcommands: `run`, `init`, `doctor`, `version`, `update`, `inspect`
- Stable Python entrypoints: `run_match(...)` and `run_match_async(...)`
- Structured run artifacts written under `runs/` by default
- Explicit text-LLM configuration using `SEEKTALENT_TEXT_LLM_*` plus bare `*_MODEL_ID` values
- Real CTS integration with explicit credential requirements

## Quick Start

### Prerequisites

- Python `3.12+`
- one supported LLM provider credential
- CTS credentials for real CTS mode

### Install as a CLI

From a local checkout:

```bash
uv build
pipx install dist/seektalent-0.5.11-py3-none-any.whl
```

If you prefer a plain Python environment:

```bash
pip install dist/seektalent-0.5.11-py3-none-any.whl
```

The current starter env defaults to the canonical text-LLM surface, with `SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible`, the matching `SEEKTALENT_TEXT_LLM_ENDPOINT_*` values, and bare stage `*_MODEL_ID` settings. Dual-protocol support still exists through the same `SEEKTALENT_TEXT_LLM_*` surface.

### Create a starter env file

```bash
seektalent init
```

In a source checkout, `.env.example` is the single editable env template. The packaged mirror stays in `src/seektalent/default.env` so installed wheels can still run `seektalent init`.

### Fill the required values in `.env`

At minimum:

```dotenv
SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

Active model configuration uses the `SEEKTALENT_TEXT_LLM_*` tuple plus bare `*_MODEL_ID` values. `SEEKTALENT_TEXT_LLM_API_KEY` is the canonical runtime credential.

### Validate the local setup

```bash
seektalent doctor
```

For the local Workbench, the product launcher starts the backend and Svelte frontend with the repo-local OpenCLI browser dependency:

```bash
scripts/start-dev-workbench.sh
```

The launcher installs Svelte dependencies with Bun when needed, points `SEEKTALENT_LIEPIN_OPENCLI_COMMAND` at `apps/web-svelte/node_modules/.bin/opencli`, exports `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` plus `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`, then starts the backend on `127.0.0.1:8012` and the Svelte Workbench on `127.0.0.1:5178`. The user still installs and connects the OpenCLI Chrome extension in their own Chrome profile. When OpenCLI is selected and ready, Liepin behavior is real local browser behavior, not fixture data. Python-only or PyPI-style installs do not yet bundle the Node dependency tree; in those installs OpenCLI mode fails closed with `liepin_opencli_command_missing` until a packaged installer or first-run dependency bootstrap exists.

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
pipx install dist/seektalent-0.5.11-py3-none-any.whl
```

This gives you the `seektalent` command directly.

### Python integrators

```bash
pip install dist/seektalent-0.5.11-py3-none-any.whl
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

The repository includes the source-checkout local workbench:

- backend API: `seektalent-ui-api`
- frontend app: `apps/web`
- default backend port: `8011`
- default frontend port: `5176`

Start the backend:

```bash
uv run seektalent-ui-api
```

Start the frontend in another terminal:

```bash
cd apps/web
bun install
bun run dev
```

Then open:

```text
http://127.0.0.1:5176
```

## Outputs

Each run creates a timestamped directory under `runs/` by default, including files such as:

- `trace.log`
- `events.jsonl`
- `run_config.json`
- `final_candidates.json`
- `final_answer.md`
- per-round controller / retrieval / reflection / scoring artifacts

Output reference:

- [docs/outputs.md](docs/outputs.md)

## Limits

Current boundaries are intentional:

- this is an experimental local engine, not a hosted multi-tenant product
- the web UI is a thin local shim, not a full recruiting platform
- the CTS adapter is scoped to the fields and semantics implemented in this repository
- the runtime is built for auditable deterministic control flow, not open-ended autonomous tool use

## Docs

Start with:

- [Architecture](docs/architecture.md) for the component map, architecture diagram, and runtime sequence.
- [CLI](docs/cli.md) for the command contract.
- [Configuration](docs/configuration.md) for environment variables and model settings.
- [Outputs](docs/outputs.md) for run artifacts and diagnostics.
- [UI](docs/ui.md) for the local web shell.
- [Development](docs/development.md) for local checks and repository conventions.

Historical versioned design notes remain under `docs/v-*`.

## License

This project is licensed under the GNU Affero General Public License v3.0.

See [LICENSE](LICENSE).
