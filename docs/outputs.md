# Outputs

Every run writes artifacts under the configured runs directory:

```text
runs/<timestamp>_<run_id>/
```

The exact path is printed by the CLI on success.

By default, `runs/` is resolved relative to the current working directory. Override it for one run with:

```bash
deepmatch run --jd "..." --output-dir ./outputs
```

## Top-level files

Common top-level files include:

| File | Purpose |
| --- | --- |
| `trace.log` | Human-readable event timeline. |
| `events.jsonl` | Machine-readable event stream. |
| `run_config.json` | Public Agent configuration for the run. |
| `input_snapshot.json` | Short hash and preview summary of input text. |
| `input_truth.json` | Structured input truth assembled from the JD and optional notes. |
| `requirement_extraction_draft.json` | Draft requirement extraction output. |
| `requirements_call.json` | LLM call snapshot for the requirement extractor. |
| `requirement_sheet.json` | Structured requirement sheet used by the Agent runtime. |
| `scoring_policy.json` | Frozen scoring policy derived from requirements. |
| `sent_query_history.json` | Cross-round record of sent query metadata. |
| `finalizer_context.json` | Finalizer input context. |
| `finalizer_call.json` | LLM call snapshot for the finalizer. |
| `final_candidates.json` | Final structured shortlist result. |
| `final_answer.md` | Final markdown output. |
| `judge_packet.json` | Consolidated audit packet for downstream review. |
| `run_summary.md` | Human-readable run summary. |

## Prompt snapshots

Prompt files used for the run are stored under:

```text
prompt_snapshots/
```

This keeps the exact prompt content used by that run.

## Per-round files

Each round writes a directory like:

```text
rounds/round_01/
```

Common per-round files include:

| File | Purpose |
| --- | --- |
| `controller_context.json` | Input context for the controller. |
| `controller_call.json` | Controller LLM call snapshot. |
| `controller_decision.json` | Structured controller decision. |
| `retrieval_plan.json` | Runtime retrieval plan for the round. |
| `constraint_projection_result.json` | CTS projection result for round constraints. |
| `cts_queries.json` | Serialized CTS queries sent for the round. |
| `sent_query_records.json` | Query metadata recorded for the round. |
| `search_observation.json` | Search outcome summary for the round. |
| `search_attempts.json` | CTS search attempt records. |
| `normalized_resumes.jsonl` | Normalized scoring inputs. |
| `scorecards.jsonl` | Ranked scored candidates for the round. |
| `reflection_context.json` | Reflection input context. |
| `reflection_call.json` | Reflection LLM call snapshot. |
| `reflection_advice.json` | Structured reflection output. |
| `round_review.md` | Human-readable summary of the round. |

## How to use them

- Read `trace.log` first when debugging a failed or confusing run.
- Use `events.jsonl` for scripting, indexing, or machine processing.
- Use `round_review.md` and `run_summary.md` for quick human inspection.
- Use `final_candidates.json` when you need structured downstream consumption.

## Notes

- The Agent tries to keep sensitive output limited to summaries and structured artifacts rather than dumping unrestricted raw text into the trace log.
- The exact set of artifacts may grow as the Agent evolves, but the files above represent the current primary outputs.

## Related docs

- [CLI](cli.md)
- [Architecture](architecture.md)
