# Outputs

`SeekTalent v0.3 phase 1` does not produce run artifacts yet.

`seektalent run` is intentionally phase-gated, so there is no `runs/<id>/` tree, no round artifacts, and no final shortlist output at this stage.

## What currently writes files

### `seektalent init`

Writes one env file, `.env` by default.

### `seektalent doctor`

Ensures the configured `runs` directory exists so the phase 1 surface can validate path settings.

That is the only filesystem side effect kept in the CLI besides `init`.

## What is intentionally absent

- `trace.log`
- `events.jsonl`
- `input_truth.json`
- `requirement_sheet.json`
- any round directory
- any controller / reflection / scoring / finalizer artifact
- any UI payload artifact

These outputs are expected to come back only when phase 2+ runtime work lands.

## Related docs

- [CLI](cli.md)
- [Configuration](configuration.md)
- [docs/v-0.3/implementation-checklist.md](v-0.3/implementation-checklist.md)
