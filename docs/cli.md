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

The local-first product has two current source-checkout entrypoints:

- `seektalent` for CLI and terminal workflows.
- `seektalent-ui-api` for the local workbench backend used with `apps/web`.

The target packaged launcher is `seektalent workbench`. That command is the intended future one-step local startup for non-developer users; it is a named contract for packaging work, not an implemented command today.

## Commands

| Command | Purpose |
| --- | --- |
| `seektalent run` | Run one resume-matching workflow. |
| `seektalent benchmark` | Run benchmark JD rows from a JSONL file. |
| `seektalent flywheel-export` | Export query rewriting flywheel dataset artifacts. |
| `seektalent init` | Write a starter env file. |
| `seektalent doctor` | Check local configuration without network calls. |
| `seektalent version` | Print the installed version. |
| `seektalent update` | Print upgrade instructions. |
| `seektalent inspect` | Print the machine-readable CLI contract when used with `--json`. |

Every command above can also be invoked under `seektalent exec`, for example `seektalent exec run ...`.

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
  --output-dir ./runs/benchmark \
  --benchmark-max-concurrency 6 \
  --enable-eval
```

Run an explicit JSONL file:

```bash
seektalent benchmark \
  --jds-file ./artifacts/benchmarks/agent_jds.jsonl \
  --output-dir ./runs/benchmark
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

## `seektalent flywheel-export`

Export query rewriting samples and source flywheel rows from `.seektalent/flywheel.sqlite3`:

```bash
seektalent flywheel-export \
  --dataset-version 0.6.2 \
  --run-id run_01H... \
  --output-dir ./artifacts
```

Use `--run-id` more than once to include multiple runs. The command writes an export artifact under `artifacts/exports/` and registers `flywheel.*` logical artifacts in the export manifest. Use `--json` for a machine-readable export summary.

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

## Failure Behavior

The CLI fails fast when:

- required input text is missing
- mutually exclusive input flags are used together
- settings validation fails
- required provider credentials are missing
- CTS credentials are missing in real CTS mode
- mock CTS is requested through the published CLI path
- any runtime stage raises an exception

## Related Docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
