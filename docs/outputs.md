# Outputs

## Artifact roots

- Active single runs write to `artifacts/runs/YYYY/MM/DD/run_<ulid>/`
- Maintained benchmark input JSONL files remain in `artifacts/benchmarks/`
- Benchmark execution outputs write to `artifacts/benchmark-executions/YYYY/MM/DD/benchmark_<ulid>/`
- Historical `runs/` content is archived under `artifacts/archive/`, and `runs/` is no longer an active output root

The exact run or benchmark execution path is printed by the CLI on success.

## Single-run layout

Each active run root is partitioned and typed:

```text
artifacts/runs/YYYY/MM/DD/run_<ulid>/
```

Common logical artifacts inside a run root include:

| Path | Purpose |
| --- | --- |
| `runtime/trace.log` | Human-readable event timeline. |
| `runtime/events.jsonl` | Machine-readable event stream. |
| `runtime/run_config.json` | Public Agent configuration for the run. |
| `input/input_snapshot.json` | Short hash and preview summary of input text. |
| `input/input_truth.json` | Structured input truth assembled from the job title, JD, and optional notes. |
| `runtime/requirement_extraction_draft.json` | Draft requirement extraction output. |
| `runtime/requirements_call.json` | Metadata-only LLM call snapshot for the requirement extractor, with artifact refs, hashes, character counts, and short summaries. |
| `runtime/requirement_sheet.json` | Structured requirement sheet used by the Agent runtime. |
| `runtime/scoring_policy.json` | Frozen scoring policy derived from requirements. |
| `runtime/sent_query_history.json` | Cross-round record of sent query metadata. |
| `runtime/search_diagnostics.json` | Cross-round search funnel ledger with query, filter, recall, dedup, scoring, reflection, and LLM schema-pressure signals. |
| `runtime/term_surface_audit.json` | Per-term audit of compiled terms, used query terms, query-containing CTS counts, and candidate surface rules. |
| `runtime/finalizer_context.json` | Slim finalizer context summary with refs to source artifacts and ranked candidate sort-key facts. |
| `runtime/finalizer_call.json` | Metadata-only LLM call snapshot for the finalizer, with artifact refs, hashes, character counts, and short summaries. |
| `output/final_candidates.json` | Final structured shortlist result. |
| `output/final_answer.md` | Final markdown output. |
| `output/judge_packet.json` | Consolidated audit packet for downstream review. |
| `output/run_summary.md` | Human-readable run summary. |
| `evaluation/evaluation.json` | Final evaluation summary for the run when eval is enabled. |

## Prompt assets

Prompt files used for the run are stored under:

```text
assets/prompts/
```

This keeps the exact prompt content used by that run.

## Per-round files

Each round writes subsystem-specific directories like:

```text
rounds/01/<subsystem>/
```

Common per-round files include:

| Path | Purpose |
| --- | --- |
| `rounds/01/controller/controller_context.json` | Slim controller context summary with input refs, budget/stop guidance, query-term state, and top-pool summaries. |
| `rounds/01/controller/controller_call.json` | Metadata-only controller LLM call snapshot. |
| `rounds/01/controller/controller_decision.json` | Structured controller decision. |
| `rounds/01/retrieval/second_lane_decision.json` | Typed second-lane routing decision, including PRF gate outcome, selected lane, and query fingerprint. |
| `rounds/01/retrieval/query_resume_hits.json` | Query-to-resume visibility ledger, enriched after scoring with fit status and score fields. |
| `rounds/01/retrieval/replay_snapshot.json` | Minimal provider request/response snapshot plus version vector for replay and policy comparison. |
| `rounds/01/scoring/scoring_input_refs.jsonl` | Per-resume scoring input refs pointing to normalized scoring inputs, with hashes, character counts, and summaries. |
| `rounds/01/scoring/scoring_calls.jsonl` | Per-resume scoring call metadata snapshots for the round. |
| `rounds/01/scoring/scorecards.jsonl` | Ranked scored candidates for the round. |
| `rounds/01/reflection/reflection_call.json` | Metadata-only reflection LLM call snapshot. |
| `rounds/01/reflection/reflection_advice.json` | Structured reflection output. |
| `rounds/01/reflection/round_review.md` | Human-readable summary of the round. |

Optional recall-rescue files may also appear under a round when the quality gate asks runtime to repair weak search coverage:

| Path | Purpose |
| --- | --- |
| `rounds/01/retrieval/rescue_decision.json` | Runtime-selected rescue lane, skipped lanes, and any forced terms. |
| `rounds/01/retrieval/candidate_feedback_input.json` | Seed, negative, and already-sent resume/query facts used by deterministic candidate feedback extraction. |
| `rounds/01/retrieval/candidate_feedback_terms.json` | Candidate feedback term extraction result. |
| `rounds/01/retrieval/candidate_feedback_decision.json` | Accepted feedback term and forced query terms, or the skip reason. |
| `rounds/01/retrieval/company_discovery_result.json` | Full web company discovery result for the round. |
| `rounds/01/retrieval/company_discovery_input.json` | Requirement-derived input used to discover source companies. |
| `rounds/01/retrieval/company_discovery_plan.json` | Accepted, held, and rejected company plan. |
| `rounds/01/retrieval/company_search_queries.json` | Web search tasks generated for company discovery. |
| `rounds/01/retrieval/company_search_results.json` | Deduplicated web search results. |
| `rounds/01/retrieval/company_search_rerank.json` | Provider rerank results used before page reads. |
| `rounds/01/retrieval/company_page_reads.json` | Fetched page snippets for top reranked results. |
| `rounds/01/retrieval/company_evidence_cards.json` | Evidence-backed company candidates extracted from search/page evidence. |
| `rounds/01/retrieval/query_term_pool_after_company_discovery.json` | Query term pool after accepted company terms are injected. |
| `rounds/01/retrieval/company_discovery_decision.json` | Forced company seed terms, accepted company count, and discovery stop reason. |

Evaluation exports may also include:

| Path | Purpose |
| --- | --- |
| `evaluation/replay_rows.jsonl` | One row per round replay snapshot for experiment comparison and replay tooling. |

## How to use them

- Read `runtime/trace.log` first when debugging a failed or confusing run.
- Use `runtime/events.jsonl` for scripting, indexing, or machine processing. Event payloads are intentionally capped to small metadata.
- Use `rounds/<nn>/reflection/round_review.md` and `output/run_summary.md` for quick human inspection.
- Use `runtime/search_diagnostics.json` when a JD has weak or missing candidates and you need to attribute the issue to query terms, filters, CTS recall, dedup, scoring retention, reflection, or controller response.
- Use `runtime/term_surface_audit.json` when comparing compiled terms against actual query surfaces. Its CTS counts are query-containing aggregates; exact marginal term or surface lift requires a separate surface probe.
- Use `rounds/<nn>/retrieval/rescue_decision.json` with the candidate feedback or company discovery files when a run switches away from the normal controller path to repair low recall.
- Use `output/final_candidates.json` when you need structured downstream consumption.

## Notes

- LLM call snapshots are metadata-only. They do not embed full model input payloads or full structured outputs; follow `input_artifact_refs` and `output_artifact_refs` for the persisted artifacts.
- The Agent tries to keep sensitive output limited to summaries and structured artifacts rather than dumping unrestricted raw text into the trace log.
- The exact set of artifacts may grow as the Agent evolves, but the files above represent the current primary outputs.

## Related docs

- [CLI](cli.md)
- [Architecture](architecture.md)
