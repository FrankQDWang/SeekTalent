# CLI

[简体中文](cli.zh-CN.md)

The canonical entrypoint is:

```bash
seektalent --help
```

When attached to a TTY, `seektalent` with no arguments launches a one-shot chat-first Textual session. `seektalent --help` remains the canonical protocol reference for humans and agents.

## Current phase

This CLI is a `v0.3.3 active` surface.

- `doctor`, `init`, `version`, `update`, `inspect`, and `run` work

## Commands

### `seektalent`

When attached to a TTY, the bare command launches the one-shot chat-first Textual session:

```bash
seektalent
```

The session provides:

- one transcript as the only main area
- a first prompt for `Job Description`
- a second prompt for optional `Hiring Notes`
- `Shift+Enter` to submit and `Enter` to insert a newline
- a live working transcript that streams progress and the final result in the same conversation
- one run per launch; re-run `seektalent` to start over

### `seektalent init`

Write the repo env template:

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

This writes the bundled starter template that ships with the package.

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

`doctor` now validates the per-callpoint LLM configuration matrix. `inspect --json` now includes the interactive entry, the chat session flow, the non-interactive request contract, the progress contract, and the final result pointer.

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

Inline `--jd` and `--notes` flags no longer exist. Use a request file, request stdin, or the Textual session.

## Related docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
