# Outputs

`SeekTalent v0.3.1 phase 6 offline artifacts active` returns a structured `SearchRunBundle` and persists run artifacts.

`seektalent run` writes:

- human mode: `run_dir`, `stop_reason`, comma-joined shortlist ids, `run_summary`
- `--json` mode: `SearchRunBundle.model_dump(mode="json")`

Python API returns the same bundle as `run_match(...)`.

## What currently writes files

### `seektalent run`

Writes `runs/<run_id>/bundle.json`, `final_result.json`, and `eval.json`.

### `seektalent init`

Writes one env file, `.env` by default.

### `seektalent doctor`

Ensures the configured `runs` directory exists and validates the active runtime manifest.

## What exists on disk now

- `runs/<run_id>/bundle.json`
- `runs/<run_id>/final_result.json`
- `runs/<run_id>/eval.json`
- `artifacts/runtime/active.json`
- `artifacts/runtime/policies/*.json`
- `artifacts/runtime/cases/<case_id>/...`
- `artifacts/runtime/evals/e5-matrix.json`

## What remains intentionally absent

- `trace.log`
- `events.jsonl`
- UI payload artifacts

## Related docs

- [CLI](cli.md)
- [Configuration](configuration.md)
- [docs/v-0.3.1/implementation-checklist.md](v-0.3.1/implementation-checklist.md)
