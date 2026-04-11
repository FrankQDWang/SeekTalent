# CLI

[简体中文](cli.zh-CN.md)

The canonical entrypoint is:

```bash
seektalent --help
```

Explicit subcommands are required. There is no implicit `run` mode.

## Current phase

This CLI is a `v0.3.3 active` surface.

- `doctor`, `init`, `version`, `update`, `inspect`, and `run` work

## Commands

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

`doctor` now validates the per-callpoint LLM configuration matrix. `inspect --json` now includes each callpoint's provider, model, and resolved output mode.

### `seektalent run`

The command accepts:

- `--jd` or `--jd-file`
- `--notes` or `--notes-file`
- `--round-budget`
- `--env-file`
- `--json`

Example:

```bash
seektalent run --jd-file ./jd.md --notes-file ./notes.md
```

Current behavior:

- runs the full runtime loop and writes run artifacts
- `--round-budget` overrides `SEEKTALENT_ROUND_BUDGET`
- prints `run_dir`, `stop_reason`, `reviewer_summary`, and `run_summary` in human mode
- prints `SearchRunBundle.model_dump(mode="json")` to stdout in `--json` mode

Failures still emit one JSON object on stderr.

## Related docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
