# cv-match

<p>
  <a href="#english"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="./README.zh-CN.md"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## English

`cv-match` is an experimental open-source resume matching Agent for local use. It turns a job description and sourcing notes into a deterministic multi-round shortlist using LLM-based requirement extraction, controlled CTS retrieval, per-resume scoring, reflection, and finalization.

The project is usable today, but it is still intentionally narrow:

- It is optimized for local iteration and auditability, not for hosted multi-tenant deployment.
- It connects to CTS through authenticated search.
- It exposes a CLI and a minimal local web UI.

## Highlights

- Deterministic Python Agent with explicit control over a small number of LLM-backed steps
- Real CTS integration with explicit credential requirements
- Structured run artifacts written to `runs/` for review and debugging
- Minimal web UI for entering JD / sourcing notes and browsing shortlist results
- Explicit model configuration using `provider:model` IDs

## Quick Start

The recommended way to use `cv-match` is the local web UI.

### 1. Open a terminal

- On macOS: press `Command + Space`, type `Terminal`, and open it.
- On Windows: open `Windows Terminal` or `PowerShell` from the Start menu.

### 2. Go to the project folder

```bash
cd path/to/cv-match
```

### 3. Make sure you have the prerequisites

- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/)
- Node.js and `pnpm`
- one supported LLM provider credential
- CTS credentials

### 4. Install dependencies

```bash
uv sync
```

### 5. Copy `.env.example` to `.env`

```bash
cp .env.example .env
```

Windows PowerShell:

```bash
Copy-Item .env.example .env
```

### 6. Fill the required values in `.env`

For most users, the default model names in `.env.example` can stay as they are.

You must fill:

- one LLM provider key matching your configured models
- `CVMATCH_CTS_TENANT_KEY`
- `CVMATCH_CTS_TENANT_SECRET`

Example:

```dotenv
OPENAI_API_KEY=your-openai-key
CVMATCH_CTS_TENANT_KEY=your-cts-tenant-key
CVMATCH_CTS_TENANT_SECRET=your-cts-tenant-secret
```

If you keep the default `openai-responses:*` models, `OPENAI_API_KEY` is the only provider key you need.

### 7. Start the backend

```bash
uv run cv-match-ui-api
```

### 8. Start the frontend in another terminal

```bash
cd path/to/cv-match/apps/web-user-lite
pnpm install
pnpm dev
```

### 9. Open the app in your browser

```text
http://127.0.0.1:5176
```

## Installation

For normal usage:

```bash
uv sync
```

## Configuration

Environment variables are read from `.env` automatically.

You will usually configure three groups of variables:

- LLM provider credentials such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY`
- CTS connection settings such as `CVMATCH_CTS_BASE_URL`, `CVMATCH_CTS_TENANT_KEY`, and `CVMATCH_CTS_TENANT_SECRET`
- Agent behavior such as model IDs, round limits, concurrency, and output directory

Minimum values required to run the Agent:

- one provider key that matches your configured models
- `CVMATCH_CTS_TENANT_KEY`
- `CVMATCH_CTS_TENANT_SECRET`

Full configuration reference:

- [docs/configuration.md](docs/configuration.md)

Important rules:

- Model variables must use the `provider:model` format.
- If you use `openai`, `openai-chat`, or `openai-responses` model IDs, set `OPENAI_API_KEY`.
- If you use `anthropic:*`, set `ANTHROPIC_API_KEY`.
- If you use `google-gla:*`, set `GOOGLE_API_KEY`.

## CLI Usage

Run with files:

```bash
uv run cv-match --jd-file examples/jd.md --notes-file examples/notes.md --real-cts
```

Run with inline text:

```bash
uv run cv-match --jd "Python agent engineer" --notes "Shanghai preferred" --real-cts
```

The CLI prints:

- final markdown output
- `run_id`
- `run_directory`
- `trace_log`

Full CLI reference:

- [docs/cli.md](docs/cli.md)

## Web UI

The repository includes a minimal local web UI:

- backend API: `cv-match-ui-api`
- frontend app: `apps/web-user-lite`
- default backend port: `8011`
- default frontend port: `5176`

Start the backend:

```bash
uv run cv-match-ui-api
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

Full UI reference:

- [docs/ui.md](docs/ui.md)

## Outputs

Each run creates a timestamped directory under `runs/`, including files such as:

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

- This is an experimental local Agent, not a hosted product.
- The web UI is a small local shim, not a full recruiting platform.
- The CTS adapter is scoped to the fields and semantics currently implemented in this repository.
- The Agent is built for auditable deterministic control flow, not open-ended autonomous tool use.

## Docs

- [Configuration](docs/configuration.md)
- [UI](docs/ui.md)
- [CLI](docs/cli.md)
- [Outputs](docs/outputs.md)
- [Architecture](docs/architecture.md)
- [Development](docs/development.md)

Historical versioned design notes remain under `docs/v-*`.

## License

This project is licensed under the GNU Affero General Public License v3.0.

See [LICENSE](LICENSE).
