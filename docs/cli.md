# CLI

[简体中文](cli.zh-CN.md)

`SeekTalent` has two terminal surfaces:

- `seektalent` with no arguments opens the interactive terminal UI when stdin/stdout are interactive.
- Direct commands can be run as `seektalent <command>` or `seektalent exec <command>`.

`seektalent --help` shows the top-level interactive shell. `seektalent exec --help` shows the full direct command list.

Recommended black-box sequence:

```bash
seektalent doctor
seektalent run --job-title-file ./job_title.md --jd-file ./jd.md
seektalent inspect --json
seektalent update
```

## Local Product Entrypoints

The local-first product has these entrypoints:

- `seektalent` for CLI and terminal workflows.
- `seektalent workbench` for installed PyPI users; it starts the backend and serves the packaged Workbench frontend.
- `seektalent-domi` for prepared-machine Domi Workbench launches.
- `seektalent-ui-api` for lower-level development and diagnostics.

## Commands

| Command | Purpose |
| --- | --- |
| `seektalent run` | Run one resume-matching workflow. |
| `seektalent benchmark` | Run benchmark JD rows from a JSONL file. |
| `seektalent init` | Write a starter env file. |
| `seektalent workbench` | Start the local Workbench with the packaged frontend. |
| `seektalent-domi` | Start the Workbench with Domi LLM and Domi Node on a prepared machine. |
| `seektalent doctor` | Check local configuration without network calls. |
| `seektalent version` | Print the installed version. |
| `seektalent update` | Print upgrade instructions. |
| `seektalent inspect` | Print the machine-readable CLI contract when used with `--json`. |

Every `seektalent <command>` row above can also be invoked under `seektalent exec`, for example `seektalent exec run ...`.

## `seektalent run`

Each run requires:

- exactly one job title source: `--job-title` or `--job-title-file`
- exactly one JD source: `--jd` or `--jd-file`
- optional notes from at most one source: `--notes` or `--notes-file`

Examples:

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience"
```

```bash
seektalent run \
  --job-title-file ./job_title.md \
  --jd-file ./jd.md \
  --notes-file ./notes.md
```

Useful options:

| Option | Purpose |
| --- | --- |
| `--env-file ./local.env` | Load a specific env file. |
| `--output-dir ./outputs` | Write run artifacts under a custom root. |
| `--json` | Emit one JSON object on stdout on success. |
| `--max-rounds N` / `--min-rounds N` | Override retrieval round limits. |
| `--scoring-max-concurrency N` | Override scoring fan-out. |
| `--search-max-pages-per-round N` | Override per-round CTS page budget. |
| `--search-max-attempts-per-round N` | Override per-round CTS attempt budget. |
| `--search-no-progress-limit N` | Override repeated no-progress threshold. |
| `--enable-eval` / `--disable-eval` | Override judge + eval for this run. |
| `--enable-reflection` / `--disable-reflection` | Override reflection for this run. |

Default success output is human-readable final markdown plus `run_id`, `run_directory`, and `trace_log`. With `--json`, stdout contains exactly one JSON object on success and stderr contains exactly one JSON object on failure.

## `seektalent benchmark`

Run benchmark rows from the maintained benchmark directory:

```bash
seektalent benchmark \
  --benchmarks-dir ./artifacts/benchmarks \
  --output-dir ./artifacts/benchmark-executions/manual \
  --benchmark-max-concurrency 6 \
  --enable-eval
```

Run an explicit JSONL file:

```bash
seektalent benchmark \
  --jds-file ./artifacts/benchmarks/agent_jds.jsonl \
  --output-dir ./artifacts/benchmark-executions/manual
```

Each row must include `job_title` and `job_description`. Extra fields are allowed.

Useful options:

| Option | Purpose |
| --- | --- |
| `--jds-file PATH` | Optional input JSONL file. When omitted, `--benchmarks-dir` is scanned. |
| `--benchmarks-dir PATH` | Directory of maintained benchmark JSONL files. Defaults to `artifacts/benchmarks`. |
| `--benchmark-max-concurrency N` | Run up to N benchmark rows in parallel. Defaults to `1`. |
| `--benchmark-run-retries N` | Retry each failed benchmark row N times. Defaults to `1`. |
| `--benchmark-upload-retries N` | Retry each failed remote eval upload N times. Defaults to `1`. |
| `--env-file PATH` | Load a specific env file. |
| `--output-dir PATH` | Write benchmark run artifacts under a custom root. |
| `--json` | Emit one JSON object on stdout. |
| `--enable-eval` / `--disable-eval` | Override judge + eval. |
| `--enable-reflection` / `--disable-reflection` | Override reflection. |

Default directory mode skips generated or temporary JSONL files such as `phase_*.jsonl`, `*.tmp.jsonl`, `*.only.jsonl`, and `*.subset.jsonl`. When eval is enabled, local runs may execute in parallel, judge requests share one process-level limit, and Weave/W&B uploads are serialized after local eval artifacts are written.

The command writes `benchmark_summary_*.json` under the configured runs directory.

## Setup Commands

Write a starter env file:

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

Run local checks without network calls:

```bash
seektalent doctor
seektalent doctor --json
```

Print version or upgrade instructions:

```bash
seektalent version
seektalent update
```

Inspect the published CLI contract:

```bash
seektalent inspect --json
```

## `seektalent workbench`

Installed PyPI users run:

```bash
seektalent init
seektalent workbench
```

The command starts the FastAPI backend and serves the packaged React Workbench from the same loopback origin. It does not require pnpm, Node, Vite, or a repository checkout on the user's machine.

On first use, `seektalent workbench` uses Domi Node to install the pinned OpenCLI CLI package under `~/.seektalent/opencli-runtime` when needed, then probes that CLI before the server launches. It does not download a replacement Node runtime. Users still need the OpenCLI Chrome extension installed and Liepin already logged in in their local Chrome profile. If the LLM key, Domi Node, or OpenCLI bootstrap fails, startup exits before launching the server and prints a `reason_code=...` diagnostic on stderr.

## `seektalent-domi`

`seektalent-domi` is the prepared-machine Domi launcher. It requires `SEEKTALENT_DOMI_JWT` and `SEEKTALENT_DOMI_NODE`, where `SEEKTALENT_DOMI_NODE` points to the Domi node executable or node bin directory.

The launcher sets the Domi LLM provider and normalized Domi Node path, then delegates to `seektalent workbench` with the same arguments.

## `seektalent-domi-bootstrap`

`seektalent-domi-bootstrap` writes the prepared-machine `seektalent` command shim. It is normally invoked by the platform install scripts after they use Domi Python to install the PyPI package into `~/.seektalent/python-prefix/<version>`. Target machines do not need a source checkout; the scripts can be loaded from the release tag.

Windows:

```powershell
Invoke-Expression (Invoke-RestMethod "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.22/scripts/install-seektalent-domi.ps1"); Install-SeekTalentDomi -Version 0.7.22
seektalent workbench
```

macOS:

```bash
source <(curl -fsSL "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.22/scripts/install-seektalent-domi.sh") 0.7.22
seektalent workbench
```

The bootstrap path writes only under `~/.seektalent`, updates `PATH` only for the current terminal session, uses Domi Python and Domi Node, and leaves the Domi app/runtime, Chrome, and the OpenCLI Chrome extension untouched.

## Failure Behavior

The CLI fails fast when:

- required input text is missing
- mutually exclusive input flags are used together
- settings validation fails
- required provider credentials are missing
- `seektalent workbench` cannot bootstrap OpenCLI, connect the OpenCLI Chrome extension, or verify Liepin login
- CTS credentials are missing in real CTS mode
- mock CTS is requested through the published CLI path
- any runtime stage raises an exception

## Related Docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
