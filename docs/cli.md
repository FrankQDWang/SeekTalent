# CLI

[简体中文](cli.zh-CN.md)

The canonical CLI entrypoint is:

```bash
seektalent --help
```

Recommended black-box sequence:

```bash
seektalent --help
seektalent doctor
seektalent run --job-title-file ./job_title.md --jd-file ./jd.md
seektalent inspect --json
seektalent update
```

## Commands

### `seektalent init`

Write a starter env file in the current directory:

```bash
seektalent init
```

Write to a custom path:

```bash
seektalent init --env-file ./local.env
```

Overwrite an existing file:

```bash
seektalent init --force
```

### `seektalent doctor`

Run local checks without network calls:

```bash
seektalent doctor
```

Machine-readable output:

```bash
seektalent doctor --json
```

### `seektalent version`

Print the installed package version:

```bash
seektalent version
```

### `seektalent update`

Print upgrade instructions for pip and pipx installs:

```bash
seektalent update
```

### `seektalent inspect`

Describe the published CLI for wrappers, agents, and automation:

```bash
seektalent inspect
seektalent inspect --json
```

## `seektalent run`

Each run requires two required inputs and one optional supplement:

- a job title
- a job description
- optional sourcing notes / sourcing preferences

You must provide the job title with exactly one source:

- `--job-title` or `--job-title-file`

You must provide the job description with exactly one source:

- `--jd` or `--jd-file`

If you want to add sourcing preferences, provide them with exactly one source:

- `--notes` or `--notes-file`

### Run with a job title and JD

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience"
```

### Run from inline text

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --notes "Shanghai preferred, avoid pure frontend profiles"
```

### Run from files

```bash
seektalent run \
  --job-title-file ./job_title.md \
  --jd-file ./jd.md \
  --notes-file ./notes.md
```

### Override output location

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --output-dir ./outputs
```

### Use a custom env file

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --env-file ./local.env
```

### Machine-readable output

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
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
- the job title is missing
- both inline and file input are supplied for the same field
- model configuration is invalid
- provider credentials are missing
- CTS credentials are missing
- mock CTS is requested through configuration
- any runtime stage raises an exception

## Related docs

- [Configuration](configuration.md)
- [Outputs](outputs.md)
