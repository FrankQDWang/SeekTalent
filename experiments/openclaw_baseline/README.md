# OpenClaw Baseline

This experiment is a report-only baseline.

- OpenClaw writes judge artifacts, W&B metrics, W&B tables, and the evaluation artifact.
- OpenClaw refreshes the shared W&B report after each successful upload.
- OpenClaw intentionally does not log to Weave.

The main SeekTalent evaluation path stays unchanged:

- Main project success requires both Weave and W&B/report logging.
- `evaluate_run(...)` still logs `Weave -> W&B -> report`.
- If main-project Weave fails, the run fails and W&B is not written.

OpenClaw success is narrower:

- OpenClaw success requires W&B/report logging only.
- OpenClaw is expected to appear in the shared report as `config.version = "openclaw"`.

Round accounting:

- One OpenClaw round means one accepted `search_candidates` CTS tool call.
- `rounds_executed` is the cumulative CTS call count, capped at 10.
- `round_01` is frozen from the first successful CTS result, not from the first OpenClaw message snapshot.
