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
| `runtime/prf_sidecar_dependency_manifest.json` | Sidecar dependency manifest summary written when PRF v1.5 talks to the HTTP sidecar, including manifest hash, image digest, and pinned model revisions observed by the run. |
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
| `rounds/01/retrieval/prf_span_candidates.json` | Exact-offset PRF v1.5 candidate spans emitted before gate evaluation. |
| `rounds/01/retrieval/prf_expression_families.json` | Guarded PRF v1.5 phrase families built from candidate spans before policy evaluation. |
| `rounds/01/retrieval/prf_policy_decision.json` | Final PRF policy decision artifact for the round; in shadow mode this is diagnostic-only and does not change selected queries. |
| `rounds/01/retrieval/llm_prf_input.json` | Seed, negative, and query-state payload sent to the LLM PRF phrase proposal extractor. |
| `rounds/01/retrieval/llm_prf_call.json` | Redacted LLM PRF phrase proposal call snapshot, including model metadata, structured-output mode, retry budget, status, latency, and failure kind when applicable. |
| `rounds/01/retrieval/llm_prf_candidates.json` | Raw structured phrase proposals returned by the LLM extractor before deterministic grounding and PRF policy gates. |
| `rounds/01/retrieval/llm_prf_grounding.json` | Deterministic grounding records that map LLM proposals back to exact source text spans or reject them. |
| `rounds/01/retrieval/query_resume_hits.json` | Query-to-resume visibility ledger, enriched after scoring with fit status and score fields. |
| `rounds/01/retrieval/replay_snapshot.json` | Minimal provider request/response snapshot plus PRF proposal version fields and artifact refs for replay and policy comparison. |
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
| `rounds/01/retrieval/candidate_feedback_expression_evidence.json` | Shared evidence spans extracted from seed and negative resumes before feedback-term selection. |
| `rounds/01/retrieval/candidate_feedback_terms.json` | Candidate feedback term extraction result. |
| `rounds/01/retrieval/candidate_feedback_decision.json` | Accepted feedback term and forced query terms, or the skip reason. |

Evaluation exports may also include:

| Path | Purpose |
| --- | --- |
| `evaluation/replay_rows.jsonl` | One row per round replay snapshot for experiment comparison and replay tooling. |

## PRF sidecar replay fields

When `prf_model_backend=http_sidecar`, the PRF replay snapshot and replay rows may include:

- `prf_model_backend`
- `prf_sidecar_endpoint_contract_version`
- `prf_sidecar_dependency_manifest_hash`
- `prf_sidecar_image_digest`
- `prf_span_model_name`
- `prf_span_model_revision`
- `prf_span_tokenizer_revision`
- `prf_span_schema_version`
- `prf_embedding_model_name`
- `prf_embedding_model_revision`
- `prf_embedding_dimension`
- `prf_embedding_normalized`
- `prf_embedding_dtype`
- `prf_embedding_pooling`
- `prf_embedding_truncation`
- `prf_candidate_span_artifact_ref`
- `prf_expression_family_artifact_ref`
- `prf_policy_decision_artifact_ref`
- `prf_fallback_reason`

These fields are diagnostic and replay-facing metadata. The sidecar still does not decide lane routing directly; the main runtime continues to enforce exact-offset validation, familying guardrails, and deterministic PRF acceptance.

## LLM PRF replay fields

When `prf_probe_proposal_backend=llm_deepseek_v4_flash`, the PRF replay snapshot and replay rows may include:

- `prf_probe_proposal_backend`
- `llm_prf_extractor_version`
- `llm_prf_grounding_validator_version`
- `llm_prf_familying_version`
- `llm_prf_model_id`
- `llm_prf_protocol_family`
- `llm_prf_endpoint_kind`
- `llm_prf_endpoint_region`
- `llm_prf_structured_output_mode`
- `llm_prf_prompt_hash`
- `llm_prf_output_retry_count`
- `llm_prf_failure_kind`
- `llm_prf_input_artifact_ref`
- `llm_prf_call_artifact_ref`
- `llm_prf_candidates_artifact_ref`
- `llm_prf_grounding_artifact_ref`

The LLM PRF extractor only proposes phrases. Runtime grounding, phrase-family checks, and the deterministic PRF policy gate remain authoritative before a `prf_probe` second lane can run.

## Legacy archive-only artifacts

Historical archived runs may still contain `company_discovery_*`, `target_company`, or `company_rescue` fields and artifacts. Those are legacy read-only records for archive/replay tolerance only and are not part of the active output contract for new runs.

## How to use them

- Read `runtime/trace.log` first when debugging a failed or confusing run.
- Use `runtime/events.jsonl` for scripting, indexing, or machine processing. Event payloads are intentionally capped to small metadata.
- Use `rounds/<nn>/reflection/round_review.md` and `output/run_summary.md` for quick human inspection.
- Use `runtime/search_diagnostics.json` when a JD has weak or missing candidates and you need to attribute the issue to query terms, filters, CTS recall, dedup, scoring retention, reflection, or controller response.
- Use `runtime/term_surface_audit.json` when comparing compiled terms against actual query surfaces. Its CTS counts are query-containing aggregates; exact marginal term or surface lift requires a separate surface probe.
- Use `rounds/<nn>/retrieval/prf_span_candidates.json`, `rounds/<nn>/retrieval/prf_expression_families.json`, and `rounds/<nn>/retrieval/prf_policy_decision.json` together when debugging PRF v1.5 proposal quality. In `shadow` mode they are diagnostic artifacts only; only `mainline` mode allows them to change the executed second-lane query.
- Use `rounds/<nn>/retrieval/llm_prf_input.json`, `rounds/<nn>/retrieval/llm_prf_call.json`, `rounds/<nn>/retrieval/llm_prf_candidates.json`, and `rounds/<nn>/retrieval/llm_prf_grounding.json` together when debugging the LLM PRF proposal path. `prf_policy_decision.json` shows whether grounded proposals were accepted.
- Use `rounds/<nn>/retrieval/rescue_decision.json` with the candidate feedback files when a run switches away from the normal controller path to repair low recall.
- Use `output/final_candidates.json` when you need structured downstream consumption.

## Notes

- LLM call snapshots are metadata-only. They do not embed full model input payloads or full structured outputs; follow `input_artifact_refs` and `output_artifact_refs` for the persisted artifacts.
- The Agent tries to keep sensitive output limited to summaries and structured artifacts rather than dumping unrestricted raw text into the trace log.
- The exact set of artifacts may grow as the Agent evolves, but the files above represent the current primary outputs.

## Related docs

- [CLI](cli.md)
- [Architecture](architecture.md)
