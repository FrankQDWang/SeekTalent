# Claude Code Baseline

This experiment is a report-only baseline.

- Claude Code writes judge artifacts, W&B metrics, W&B tables, and the evaluation artifact.
- Claude Code refreshes the shared W&B report after each successful upload.
- Claude Code intentionally does not log to Weave.

The main SeekTalent evaluation path stays unchanged:

- Main project success requires both Weave and W&B/report logging.
- `evaluate_run(...)` still logs `Weave -> W&B -> report`.
- If main-project Weave fails, the run fails and W&B is not written.

Isolation:

- This baseline does not use `cc-switch`.
- Each run creates an isolated `HOME` under the run directory.
- CCR config is written to that isolated home, not to real `~/.claude-code-router/config.json`.
- Claude Code settings are written inside the run directory, not to real `~/.claude/settings.json`.

Round accounting:

- One Claude Code round means one accepted `search_candidates` CTS MCP tool call.
- `rounds_executed` is the cumulative CTS call count, capped at 10.
- `round_01` is frozen from the first successful CTS result.
- Failed runs still write zero-score W&B metrics with `config.version = "claude_code"`.

Run:

```bash
python -m experiments.claude_code_baseline.run \
  --job-title "Python Engineer" \
  --jd-file path/to/jd.txt \
  --notes "" \
  --env-file .env
```
