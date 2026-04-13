# CLI

[简体中文](cli.zh-CN.md)

The canonical entrypoint is:

```bash
seektalent --help
```

When attached to a TTY, `seektalent` with no arguments launches the Textual UI. `seektalent --help` remains the canonical protocol reference for humans and agents.

## Current phase

This CLI is a `v0.3.3 active` surface.

- `doctor`, `init`, `version`, `update`, `inspect`, and `run` work

## Commands

### `seektalent`

When attached to a TTY, the bare command launches the Textual UI:

```bash
seektalent
```

The UI provides:

- multiline `Job Description` and `Hiring Notes` inputs
- editable `Top K`, `Round Budget`, and `Env File` settings
- a live trace panel driven by the runtime progress event stream
- a final top-candidate table plus a details panel for the selected candidate

### `seektalent init`

Write the repo env template:

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

This reads the repo-root `.env.example` directly and is intended for source checkouts.

### `seektalent doctor`

Validate the local runtime surface without making network calls:

```bash
seektalent doctor
seektalent doctor --json
seektalent doctor --env-file ./local.env --json
```

### `seektalent version`

Print the installed version:

```bash
seektalent version
```

### `seektalent update`

Print upgrade instructions:

```bash
seektalent update
```

### `seektalent inspect`

Describe the current CLI contract:

```bash
seektalent inspect
seektalent inspect --json
seektalent inspect --env-file ./local.env --json
```

`doctor` now validates the per-callpoint LLM configuration matrix. `inspect --json` now includes the interactive entry, the non-interactive request contract, the progress contract, and the final result pointer.

### `seektalent run`

This is the non-interactive protocol surface.

Preferred inputs:

- `--request-file <path>`
- `--request-stdin`
- `--jd-file <path>` with optional `--notes-file <path>`

Other flags:

- `--round-budget`
- `--progress text|jsonl|off`
- `--env-file`
- `--json`

Example:

```bash
seektalent run --request-file ./request.json
seektalent run --request-file ./request.json --json --progress jsonl
cat request.json | seektalent run --request-stdin --json --progress jsonl
seektalent run --jd-file ./jd.md --notes-file ./notes.md
```

Current behavior:

- runs the full runtime loop and writes run artifacts
- `--round-budget` overrides the request payload value and `SEEKTALENT_ROUND_BUDGET`
- human mode writes progress to `stderr` and prints a compact summary to `stdout`
- `--progress jsonl` writes stable progress events to `stderr`
- prints `SearchRunBundle.model_dump(mode="json")` to stdout in `--json` mode
- final product results live at `final_result.final_candidate_cards`

Inline `--jd` and `--notes` flags no longer exist. Use a request file, request stdin, or the Textual UI.

## Related docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
