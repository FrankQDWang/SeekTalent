# JD Text Baseline

This experiment measures CTS native JD-text search directly.

- It sends exactly one CTS request using only `jd`, `page`, and `pageSize`.
- It does not use an agent, query rewriting, keywords, or native filters.
- `round_01` and `final` are the same top-10 candidate list.
- It writes report-compatible W&B metrics with `config.version = "JD找人"`.
- It intentionally does not log to Weave.

Run:

```bash
python -m experiments.jd_text_baseline.run \
  --job-title "Python Engineer" \
  --jd-file path/to/jd.txt \
  --notes "" \
  --env-file .env
```
