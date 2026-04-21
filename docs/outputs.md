# Outputs

Every run writes artifacts under the configured runs directory:

```text
runs/<timestamp>_<run_id>/
```

The exact path is printed by the CLI on success.

By default, `runs/` is resolved relative to the current working directory. Override it for one run with:

```bash
seektalent run --job-title "..." --jd "..." --output-dir ./outputs
```

## Top-level files

Common top-level files include:

| File | Purpose |
| --- | --- |
| `trace.log` | Human-readable event timeline. |
| `events.jsonl` | Machine-readable event stream. |
| `run_config.json` | Public Agent configuration for the run. |
| `input_snapshot.json` | Short hash and preview summary of input text. |
| `input_truth.json` | Structured input truth assembled from the job title, JD, and optional notes. |
| `requirement_extraction_draft.json` | Draft requirement extraction output. |
| `requirements_call.json` | Metadata-only LLM call snapshot for the requirement extractor, with artifact refs, hashes, character counts, and short summaries. |
| `requirement_sheet.json` | Structured requirement sheet used by the Agent runtime. |
| `scoring_policy.json` | Frozen scoring policy derived from requirements. |
| `sent_query_history.json` | Cross-round record of sent query metadata. |
| `search_diagnostics.json` | Cross-round search funnel ledger with query, filter, recall, dedup, scoring, reflection, and LLM schema-pressure signals. |
| `term_surface_audit.json` | Per-term audit of compiled terms, used query terms, query-containing CTS counts, and candidate surface rules. |
| `finalizer_context.json` | Slim finalizer context summary with refs to source artifacts and ranked candidate sort-key facts. |
| `finalizer_call.json` | Metadata-only LLM call snapshot for the finalizer, with artifact refs, hashes, character counts, and short summaries. |
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
| `controller_context.json` | Slim controller context summary with input refs, budget/stop guidance, query-term state, and top-pool summaries. |
| `controller_call.json` | Metadata-only controller LLM call snapshot. |
| `controller_decision.json` | Structured controller decision. |
| `retrieval_plan.json` | Runtime retrieval plan for the round. |
| `constraint_projection_result.json` | CTS projection result for round constraints. |
| `cts_queries.json` | Serialized CTS queries sent for the round. |
| `sent_query_records.json` | Query metadata recorded for the round. |
| `search_observation.json` | Search outcome summary for the round. |
| `search_attempts.json` | CTS search attempt records. |
| `scoring_input_refs.jsonl` | Per-resume scoring input refs pointing to `resumes/{resume_id}.json`, with hashes, character counts, and summaries. |
| `scorecards.jsonl` | Ranked scored candidates for the round. |
| `top_pool_snapshot.json` | Slim global top-pool snapshot with resume ids, ranks, sort-key facts, and short scoring signals. |
| `reflection_context.json` | Slim reflection context summary with retrieval/search facts, scored-candidate summaries, and refs to source artifacts. |
| `reflection_call.json` | Metadata-only reflection LLM call snapshot. |
| `reflection_advice.json` | Structured reflection output. |
| `round_review.md` | Human-readable summary of the round. |

## How to use them

- Read `trace.log` first when debugging a failed or confusing run.
- Use `events.jsonl` for scripting, indexing, or machine processing. Event payloads are intentionally capped to small metadata.
- Use `round_review.md` and `run_summary.md` for quick human inspection.
- Use `search_diagnostics.json` when a JD has weak or missing candidates and you need to attribute the issue to query terms, filters, CTS recall, dedup, scoring retention, reflection, or controller response.
- Use `term_surface_audit.json` when comparing compiled terms against actual query surfaces. Its CTS counts are query-containing aggregates; exact marginal term or surface lift requires a separate surface probe.
- Use `final_candidates.json` when you need structured downstream consumption.

## Notes

- LLM call snapshots are metadata-only. They do not embed full model input payloads or full structured outputs; follow `input_artifact_refs` and `output_artifact_refs` for the persisted artifacts.
- The Agent tries to keep sensitive output limited to summaries and structured artifacts rather than dumping unrestricted raw text into the trace log.
- The exact set of artifacts may grow as the Agent evolves, but the files above represent the current primary outputs.

## Related docs

- [CLI](cli.md)
- [Architecture](architecture.md)
