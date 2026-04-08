# CLI

[简体中文](cli.zh-CN.md)

The canonical entrypoint is:

```bash
seektalent --help
```

## Current phase

This CLI is a `v0.3 phase 1` skeleton.

- `doctor`, `init`, `version`, `update`, and `inspect` work
- `run` is intentionally gated and always fails fast with `Phase1RuntimeGateError`

## Commands

### `seektalent init`

Write the packaged env template:

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

### `seektalent doctor`

Validate the local phase 1 surface without making network calls:

```bash
seektalent doctor
seektalent doctor --json
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
```

### `seektalent run`

The command still accepts the planned inputs:

- `--jd` or `--jd-file`
- `--notes` or `--notes-file`
- `--env-file`
- `--output-dir`
- `--json`

Example:

```bash
seektalent run --jd-file ./jd.md --notes-file ./notes.md
```

Current behavior:

- validates input wiring
- loads settings
- then fails immediately with the phase 1 runtime gate

In `--json` mode, the failure is emitted as one JSON object on stderr.

## Related docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
