# SeekTalent

<p>
  <a href="#english"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="./README.zh-CN.md"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## English

`SeekTalent` is an experimental local-first resume matching engine. It turns a required job title, a job description, and optional sourcing notes into a deterministic multi-round shortlist using requirement extraction, controlled CTS retrieval, per-resume scoring, reflection, and finalization.

The current product shape is intentionally narrow:

- the primary product is a local CLI
- the same runtime can also be imported as a Python dependency
- a minimal local web UI still exists, but it is secondary

## Highlights

- Installable CLI with stable subcommands: `run`, `init`, `doctor`, `version`, `update`, `inspect`
- Stable Python entrypoints: `run_match(...)` and `run_match_async(...)`
- Structured run artifacts written under `runs/` by default
- Explicit model configuration using `provider:model`
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
pipx install dist/seektalent-0.4.2-py3-none-any.whl
```

If you prefer a plain Python environment:

```bash
pip install dist/seektalent-0.4.2-py3-none-any.whl
```

The default package install is OpenAI-only. It includes `pydantic-ai-slim[openai]`, so `openai:*`, `openai-chat:*`, and `openai-responses:*` model IDs work out of the box, including OpenAI-compatible `OPENAI_BASE_URL` endpoints.

### Create a starter env file

```bash
seektalent init
```

In a source checkout, `.env.example` is the single editable env template. The packaged mirror stays in `src/seektalent/default.env` so installed wheels can still run `seektalent init`.

### Fill the required values in `.env`

At minimum:

```dotenv
OPENAI_API_KEY=your-openai-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

If you keep the default `openai-responses:*` models, `OPENAI_API_KEY` is the only provider key you need.

If you want to run `anthropic:*` or `google-gla:*` models later, extend the install to include the matching `pydantic-ai-slim[...]` extras first.

### Validate the local setup

```bash
seektalent doctor
```

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
pipx install dist/seektalent-0.4.2-py3-none-any.whl
```

This gives you the `seektalent` command directly.

### Python integrators

```bash
pip install dist/seektalent-0.4.2-py3-none-any.whl
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

- provider credentials such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY`
- CTS settings such as `SEEKTALENT_CTS_BASE_URL`, `SEEKTALENT_CTS_TENANT_KEY`, and `SEEKTALENT_CTS_TENANT_SECRET`
- runtime settings such as round limits, concurrency, and output directory

Full configuration reference:

- [docs/configuration.md](docs/configuration.md)

Important rules:

- model variables must use the `provider:model` format
- OpenAI-family models require `OPENAI_API_KEY`
- `anthropic:*` requires `ANTHROPIC_API_KEY`
- `google-gla:*` requires `GOOGLE_API_KEY`

## Web UI

The repository still includes a minimal local web UI:

- backend API: `seektalent-ui-api`
- frontend app: `apps/web-user-lite`
- default backend port: `8011`
- default frontend port: `5176`

Start the backend:

```bash
uv run seektalent-ui-api
```

Start the frontend in another terminal:

```bash
cd apps/web-user-lite
pnpm install
pnpm dev
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

- [Configuration](docs/configuration.md)
- [CLI](docs/cli.md)
- [UI](docs/ui.md)
- [Outputs](docs/outputs.md)
- [Architecture](docs/architecture.md)
- [Development](docs/development.md)

Historical versioned design notes remain under `docs/v-*`.

## License

This project is licensed under the GNU Affero General Public License v3.0.

See [LICENSE](LICENSE).
