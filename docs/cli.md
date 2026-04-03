# CLI

[简体中文](cli.zh-CN.md)

The canonical CLI entrypoint is:

```bash
deepmatch run --help
```

For one compatibility cycle, the legacy alias still works:

```bash
deepmatch --jd "Python agent engineer" --notes "Shanghai preferred" --mock-cts
```

## Commands

### `deepmatch init`

Write a starter env file in the current directory:

```bash
deepmatch init
```

Write to a custom path:

```bash
deepmatch init --env-file ./local.env
```

Overwrite an existing file:

```bash
deepmatch init --force
```

### `deepmatch doctor`

Run local checks without network calls:

```bash
deepmatch doctor
```

Machine-readable output:

```bash
deepmatch doctor --json
```

### `deepmatch version`

Print the installed package version:

```bash
deepmatch version
```

## `deepmatch run`

Each run requires one required input and one optional supplement:

- a job description
- optional sourcing notes / sourcing preferences

You must provide the job description with exactly one source:

- `--jd` or `--jd-file`

If you want to add sourcing preferences, provide them with exactly one source:

- `--notes` or `--notes-file`

### Run with only a JD

```bash
deepmatch run \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --real-cts
```

### Run from inline text

```bash
deepmatch run \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --notes "Shanghai preferred, avoid pure frontend profiles" \
  --real-cts
```

### Run from files

```bash
deepmatch run \
  --jd-file ./jd.md \
  --notes-file ./notes.md \
  --real-cts
```

### Override output location

```bash
deepmatch run \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --mock-cts \
  --output-dir ./outputs
```

### Use a custom env file

```bash
deepmatch run \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --mock-cts \
  --env-file ./local.env
```

### Machine-readable output

```bash
deepmatch run \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --mock-cts \
  --json
```

In `--json` mode, stdout contains exactly one JSON object on success. On failure, stderr contains exactly one JSON object.

## Success output

Default success output is human-readable:

- final markdown answer
- `run_id`
- `run_directory`
- `trace_log`

When `--output-dir` is omitted, artifacts go under `./runs` relative to the current working directory.

## Failure behavior

The CLI fails fast when:

- the job description is missing
- both inline and file input are supplied for the same field
- model configuration is invalid
- provider credentials are missing
- real CTS credentials are missing in `--real-cts` mode
- any runtime stage raises an exception

## Related docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
