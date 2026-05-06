# Query Rewriting Data Flywheel Store Design

Date: 2026-05-06
Status: Draft for user review
Branch context: `main`

Related reference:

- `/Users/frankqdwang/Documents/工作/seektalent/references/SeekTalent_Query_Rewriting_Data_Flywheel_调研报告.md`

Related current designs:

- `docs/superpowers/specs/2026-04-27-retrieval-flywheel-typed-second-lane-design.md`
- `docs/superpowers/specs/2026-04-28-artifact-taxonomy-and-layout-versioning-design.md`
- `docs/superpowers/specs/2026-05-05-llm-prf-mainline-cleanup-and-validation-design.md`

## Context

The long-term product goal is Query Rewriting Data Flywheel:

1. run many JDs through the retrieval system;
2. preserve query-level and term-level denominators;
3. judge candidates consistently;
4. learn which query terms and rewrites improve qualified candidate discovery;
5. later train or route a cheap query rewriter from grounded outcome data.

The current repository has important prerequisites:

- stable query identity through `query_instance_id` and `query_fingerprint`;
- per-query resume visibility through `query_resume_hits`;
- typed second-lane retrieval with `prf_probe` or `generic_explore`;
- LLM PRF proposal using DeepSeek V4 Flash, followed by deterministic grounding and policy;
- filesystem artifacts under `artifacts/runs/...` and `artifacts/benchmark-executions/...`;
- a SQLite `JudgeCache` that stores JD assets, resume assets, and judge labels.

The missing piece is the flywheel asset layer. Runtime has query outcome classification for internal lane decisions, but the outcome data is not persisted as first-class training assets. There is no active `query_outcomes.jsonl`, `term_outcomes.jsonl`, `query_rewrite_samples.jsonl`, or query/term dataset builder.

The current eval SQLite is also the wrong long-term boundary:

- it is named and shaped as a judge cache, not a flywheel store;
- it only has `jd_assets`, `resume_assets`, and `judge_labels`;
- it does not index runs, queries, query hits, PRF decisions, query outcomes, term outcomes, dataset exports, or artifact refs;
- the current label cache key intentionally ignores judge model and prompt, which is unsafe once judge models and prompts change;
- recent eval runs can produce evaluation artifacts without consistently creating the flywheel-ready exports the research plan needs.

There are no external users yet. This rollout should remove obsolete compatibility surfaces instead of preserving old cache behavior.

## Goal

Replace the active eval cache with a unified local Flywheel Store that supports query rewriting data collection, judge label reuse, query/term outcome analysis, and dataset export.

The target active shape:

```text
artifacts/
  runs/...                         # immutable run and debug artifacts, source of truth
  benchmark-executions/...         # benchmark execution artifacts

.seektalent/
  flywheel.sqlite3                 # unified index, labels, outcomes, and dataset export ledger
```

`artifacts/...` remains the source of truth for full run trajectory. `flywheel.sqlite3` is the queryable index and learning-data store.

## Non-Goals

- Do not train a query rewriter in this rollout.
- Do not add DPO, preference modeling, or online learning.
- Do not add Postgres, a remote database, or a service.
- Do not replace filesystem artifacts with database blobs.
- Do not keep `judge_cache.sqlite3` as an active database.
- Do not migrate old `judge_cache.sqlite3` data.
- Do not preserve old active `JudgeCache` APIs.
- Do not preserve compatibility for old flywheel/eval cache formats.
- Do not add fallback chains for missing old assets.
- Do not change the accepted LLM PRF product decision: DeepSeek V4 Flash proposal plus deterministic grounding and PRF policy stays.
- Do not change the accepted eval cost decision: DeepSeek V4 Pro can remain the default judge/eval model.

## Decisions

1. Add `FlywheelStore` as the only active local database boundary for eval labels and flywheel data.
2. Remove active `JudgeCache` and `.seektalent/judge_cache.sqlite3`.
3. Store full trajectory artifacts on disk; store queryable rows, hashes, metrics, and artifact refs in SQLite.
4. Use strict, versioned schema rows for tasks, resume snapshots, runs, queries, hits, labels, query outcomes, term outcomes, and dataset exports.
5. Judge label reuse must include judge contract hash and label schema version.
6. Query outcome and term outcome must become first-class assets, not transient runtime classifications.
7. Dataset exports must be reproducible from `flywheel.sqlite3` plus artifact refs.
8. No compatibility layer is required for old judge cache data. Old files may remain on disk, but active code must not read them.
9. Remove test behavior that writes `.seektalent/cache-test-*` into the repository root.
10. Separate runtime scoring outcomes from judge-consistent training outcomes.
11. Persist canonical query specs, not only rendered keyword text and term lists.
12. Separate PRF proposal/gate term events from executed-query term outcomes.
13. Store artifact references through a structured `artifact_refs` table, not free-form paths.
14. Add a first-class export artifact kind/root for dataset exports.
15. Keep the implementation small: one store module, focused schema models, focused exporters.

## Storage Boundary

Use filesystem artifacts for:

- full LLM call metadata snapshots;
- full prompt snapshots;
- per-round controller, retrieval, scoring, reflection, PRF, and finalization artifacts;
- raw run summaries and human-readable logs;
- benchmark execution manifests and child run refs;
- replay/debug files that are large or rarely queried.

Use `flywheel.sqlite3` for:

- stable task identity;
- stable resume snapshot identity;
- run and benchmark index rows;
- query identity and query specs;
- query-to-resume hit rows;
- judge labels and cache lookup;
- query outcome rows;
- judge-consistent query outcome rows;
- term event rows;
- term outcome rows;
- dataset export ledger rows;
- structured artifact refs and hashes.

The database may store compact raw JSON for task and resume snapshots when needed for judge reuse. It must not become a dump of every artifact file.

## Database Path

Default path:

```text
.seektalent/flywheel.sqlite3
```

Add a setting:

```text
SEEKTALENT_FLYWHEEL_DB_PATH=.seektalent/flywheel.sqlite3
```

The path is resolved from `project_root` unless absolute.

Remove active use of:

```text
.seektalent/judge_cache.sqlite3
```

Do not silently fall back to the old path. If old judge cache settings or commands remain, delete them or fail with a clear migration error during development.

## SQLite Discipline

`FlywheelStore` must configure each connection explicitly:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

Do not rely on SQLite defaults for foreign key enforcement. Benchmark and eval runs may have concurrent readers and queued writers; WAL is required for reader/writer concurrency, while the implementation must still assume only one writer can commit at a time.

Prefer `STRICT` tables where supported by the repository's SQLite runtime. If a local SQLite build does not support `STRICT`, keep explicit Python validation and SQL `CHECK` constraints.

Every JSON text column must be canonical JSON written by the store and guarded with `CHECK(json_valid(column_name))` where SQLite JSON functions are available. Invalid JSON should fail loudly rather than entering the flywheel database.

## Core Tables

### `tasks`

One row per JD plus notes task.

Columns:

- `task_id TEXT PRIMARY KEY`
- `task_sha256 TEXT NOT NULL UNIQUE`
- `task_schema_version TEXT NOT NULL`
- `jd_sha256 TEXT NOT NULL`
- `notes_sha256 TEXT NOT NULL`
- `job_title TEXT NOT NULL`
- `jd_text TEXT NOT NULL`
- `notes_text TEXT NOT NULL`
- `created_at TEXT NOT NULL`

`task_sha256` and `task_id` must be derived from canonical JSON containing `task_schema_version`, `job_title`, `jd_text`, and `notes_text`. `job_title` is semantic input, not display-only metadata, because it can affect retrieval and judge framing.

### `resume_snapshots`

One row per stable resume snapshot.

Columns:

- `snapshot_sha256 TEXT PRIMARY KEY`
- `source_resume_id TEXT`
- `dedup_key TEXT`
- `raw_json TEXT NOT NULL`
- `normalized_preview_json TEXT`
- `created_at TEXT NOT NULL`

The snapshot hash must come from the same canonical snapshot logic used by scoring/eval. If that logic is currently unstable, fixing it is part of this rollout.

`raw_json` and `normalized_preview_json` must be guarded by JSON validity checks.

### `artifact_refs`

One row per structured reference from SQLite data to an artifact manifest entry.

Columns:

- `artifact_ref_id TEXT PRIMARY KEY`
- `artifact_kind TEXT NOT NULL`
- `artifact_id TEXT NOT NULL`
- `logical_name TEXT NOT NULL`
- `content_sha256 TEXT`
- `schema_version TEXT`
- `created_at TEXT NOT NULL`
- `UNIQUE (artifact_kind, artifact_id, logical_name, content_sha256)`

Do not store free-form path strings in learning tables. Runtime and exporters must resolve artifacts through `ArtifactResolver` and store logical names here. If a file is rewritten with different content, it gets a distinct `content_sha256`.

### `runs`

One row per workflow run.

Columns:

- `run_id TEXT PRIMARY KEY`
- `task_id TEXT NOT NULL REFERENCES tasks(task_id)`
- `version TEXT`
- `git_sha TEXT`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `config_hash TEXT NOT NULL`
- `config_json TEXT NOT NULL`
- `status TEXT NOT NULL`
- `eval_enabled INTEGER NOT NULL`
- `benchmark_id TEXT`
- `benchmark_case_id TEXT`
- `started_at TEXT NOT NULL`
- `completed_at TEXT`
- `failure_summary TEXT`

`artifact_ref_id` must point to the run manifest. The artifact manifest remains the source of truth for file layout.

### `run_queries`

One row per sent query instance.

Columns:

- `run_id TEXT NOT NULL REFERENCES runs(run_id)`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `query_role TEXT`
- `canonical_query_spec_json TEXT NOT NULL`
- `query_spec_schema_version TEXT NOT NULL`
- `query_policy_version TEXT NOT NULL`
- `job_intent_fingerprint TEXT NOT NULL`
- `provider_name TEXT NOT NULL`
- `rendered_provider_query TEXT NOT NULL`
- `keyword_query TEXT NOT NULL`
- `query_terms_json TEXT NOT NULL`
- `filters_json TEXT NOT NULL`
- `location_key TEXT`
- `batch_no INTEGER`
- `source_plan_version TEXT`
- `selected_prf_expression TEXT`
- `accepted_prf_term_family_id TEXT`
- `fallback_reason TEXT`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `created_at TEXT NOT NULL`
- `PRIMARY KEY (run_id, query_instance_id)`

`query_fingerprint` is stable across comparable canonical query specs. `query_instance_id` is run-local and unique. The canonical spec must include the lane, role, terms, filters, location plan inputs, provider rendering inputs, PRF selection if any, and the policy versions that can affect query meaning.

### `query_resume_hits`

One row per provider-returned resume per query.

Columns:

- `run_id TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `hit_sequence_no INTEGER NOT NULL`
- `snapshot_sha256 TEXT REFERENCES resume_snapshots(snapshot_sha256)`
- `snapshot_missing_reason TEXT`
- `resume_id TEXT NOT NULL`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT NOT NULL`
- `rank_in_query INTEGER NOT NULL`
- `rank_global_in_query INTEGER`
- `provider_name TEXT NOT NULL`
- `provider_page_no INTEGER`
- `provider_fetch_no INTEGER`
- `provider_score_if_any REAL`
- `dedup_key TEXT`
- `was_new_to_pool INTEGER NOT NULL`
- `was_duplicate INTEGER NOT NULL`
- `fit_bucket TEXT`
- `overall_score INTEGER`
- `must_have_match_score INTEGER`
- `risk_score INTEGER`
- `off_intent_reason_count INTEGER NOT NULL DEFAULT 0`
- `final_candidate_status TEXT`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `PRIMARY KEY (run_id, query_instance_id, hit_sequence_no)`

This table is the denominator that the original research plan identified as missing.

Normal provider-returned hits must have `snapshot_sha256`. A nullable snapshot is only allowed for explicitly recorded provider edge cases, and `snapshot_missing_reason` must explain why. Rows without `snapshot_sha256` must be excluded from judge-consistent training outcomes.

### `judge_labels`

One row per judge result for a task/resume snapshot under a specific judge contract.

Columns:

- `task_id TEXT NOT NULL REFERENCES tasks(task_id)`
- `snapshot_sha256 TEXT NOT NULL REFERENCES resume_snapshots(snapshot_sha256)`
- `judge_model_id TEXT NOT NULL`
- `judge_prompt_hash TEXT NOT NULL`
- `judge_contract_hash TEXT NOT NULL`
- `judge_protocol_family TEXT`
- `judge_provider_label TEXT`
- `judge_policy_version TEXT NOT NULL`
- `label_schema_version TEXT NOT NULL`
- `score INTEGER NOT NULL`
- `rationale TEXT NOT NULL`
- `label_json TEXT NOT NULL`
- `judge_prompt_text TEXT`
- `judge_output_schema_json TEXT`
- `latency_ms INTEGER`
- `judge_call_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `PRIMARY KEY (task_id, snapshot_sha256, judge_contract_hash, label_schema_version)`

This intentionally removes the old behavior where labels could be reused across judge model or prompt changes.

`score` and `rationale` are indexed convenience fields. `label_json` is the complete judge result under the judge contract and is the source for future label schema evolution.

### `query_outcomes`

One row per query instance outcome.

Columns:

- `run_id TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `outcome_schema_version TEXT NOT NULL`
- `outcome_policy_version TEXT NOT NULL`
- `outcome_thresholds_hash TEXT NOT NULL`
- `outcome_thresholds_json TEXT NOT NULL`
- `scoring_policy_version TEXT`
- `dedupe_version TEXT`
- `outcome_basis TEXT NOT NULL DEFAULT 'runtime_score'`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT NOT NULL`
- `provider_returned_count INTEGER NOT NULL`
- `new_unique_resume_count INTEGER NOT NULL`
- `duplicate_count INTEGER NOT NULL`
- `scored_resume_count INTEGER NOT NULL`
- `new_fit_count INTEGER NOT NULL`
- `new_near_fit_count INTEGER NOT NULL DEFAULT 0`
- `fit_rate_denominator TEXT`
- `fit_rate REAL`
- `must_have_match_avg REAL`
- `risk_score_avg REAL`
- `off_intent_reason_count INTEGER NOT NULL`
- `primary_label TEXT NOT NULL`
- `labels_json TEXT NOT NULL`
- `reasons_json TEXT NOT NULL`
- `latency_ms INTEGER`
- `cost_estimate_usd REAL`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `created_at TEXT NOT NULL`
- `PRIMARY KEY (run_id, query_instance_id)`

Expected labels include:

- `zero_recall`
- `duplicate_only`
- `marginal_gain`
- `broad_noise`
- `drift_suspected`
- `low_recall_high_precision`

The label vocabulary can grow, but it must be versioned.

`query_outcomes` is the online/runtime outcome table. It may use runtime scorecards. Precision-like fields must be `NULL` when there is no scored denominator, such as zero recall or duplicate-only outcomes.

### `query_judge_outcomes`

One row per query instance outcome after eval labels are available.

Columns:

- `run_id TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `task_id TEXT NOT NULL REFERENCES tasks(task_id)`
- `judge_contract_hash TEXT NOT NULL`
- `judge_model_id TEXT NOT NULL`
- `judge_prompt_hash TEXT NOT NULL`
- `label_schema_version TEXT NOT NULL`
- `outcome_schema_version TEXT NOT NULL`
- `outcome_policy_version TEXT NOT NULL`
- `outcome_thresholds_hash TEXT NOT NULL`
- `outcome_thresholds_json TEXT NOT NULL`
- `provider_returned_count INTEGER NOT NULL`
- `new_unique_resume_count INTEGER NOT NULL`
- `judged_resume_count INTEGER NOT NULL`
- `new_judge_positive_count INTEGER NOT NULL`
- `new_judge_near_positive_count INTEGER NOT NULL`
- `judge_positive_rate REAL`
- `duplicate_count INTEGER NOT NULL`
- `primary_label TEXT NOT NULL`
- `labels_json TEXT NOT NULL`
- `reasons_json TEXT NOT NULL`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `created_at TEXT NOT NULL`
- `PRIMARY KEY (run_id, query_instance_id, judge_contract_hash, label_schema_version)`

This is the default training source for `query_rewrite_samples`. If eval is disabled or judge labels are missing, dataset rows must either exclude that query or mark it as weak/runtime-derived; they must not silently treat runtime score as judge truth.

### `term_events`

One row per term event before learning outcomes are derived.

Columns:

- `run_id TEXT NOT NULL`
- `term_event_id TEXT NOT NULL`
- `proposal_id TEXT`
- `prf_decision_id TEXT`
- `candidate_query_fingerprint TEXT`
- `executed_query_instance_id TEXT`
- `selected_query_instance_id TEXT`
- `term_surface TEXT NOT NULL`
- `term_family_id TEXT NOT NULL`
- `term_role TEXT NOT NULL`
- `source TEXT NOT NULL`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT`
- `accepted_by_prf_gate INTEGER`
- `prf_reject_reasons_json TEXT NOT NULL`
- `supporting_resume_ids_json TEXT NOT NULL`
- `negative_resume_ids_json TEXT NOT NULL`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `created_at TEXT NOT NULL`
- `PRIMARY KEY (run_id, term_event_id)`

Rejected PRF proposals must live here without being bound to a generic fallback query. `executed_query_instance_id` is only set when the term actually appeared in an executed query.

### `term_outcomes`

One row per term or phrase family after joining term events to query outcomes.

Columns:

- `run_id TEXT NOT NULL`
- `term_event_id TEXT NOT NULL`
- `executed_query_instance_id TEXT`
- `executed_query_fingerprint TEXT`
- `term_outcome_schema_version TEXT NOT NULL`
- `term_familying_version TEXT NOT NULL`
- `prf_gate_version TEXT`
- `prf_policy_version TEXT`
- `term_surface TEXT NOT NULL`
- `term_family_id TEXT NOT NULL`
- `term_role TEXT NOT NULL`
- `source TEXT NOT NULL`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT`
- `execution_status TEXT NOT NULL`
- `supporting_resume_ids_json TEXT NOT NULL`
- `negative_resume_ids_json TEXT NOT NULL`
- `accepted_by_prf_gate INTEGER`
- `prf_reject_reasons_json TEXT NOT NULL`
- `appeared_in_keyword_query INTEGER NOT NULL`
- `new_fit_count INTEGER NOT NULL`
- `new_judge_positive_count INTEGER`
- `new_unique_resume_count INTEGER NOT NULL`
- `duplicate_count INTEGER NOT NULL`
- `noise_count INTEGER NOT NULL`
- `primary_query_outcome_label TEXT`
- `primary_judge_outcome_label TEXT`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `created_at TEXT NOT NULL`
- `PRIMARY KEY (run_id, term_event_id, term_family_id, term_role, source)`

`source` examples:

- `controller_query`
- `llm_prf_candidate`
- `accepted_prf_expression`
- `generic_explore`

Do not use broad semantic familying in this rollout. Use the same conservative family logic that protects `Java` vs `JavaScript`, `React` vs `React Native`, and CJK/ASCII wrapper phrases.

For rejected or not-selected proposal events, `execution_status` must indicate that no provider query executed the term. In those rows, query outcome labels may be `NULL`; do not attach them to a generic fallback query.

### `query_rewrite_samples`

One row per generated training sample.

Columns:

- `sample_id TEXT PRIMARY KEY`
- `task_id TEXT NOT NULL`
- `run_id TEXT NOT NULL`
- `source_query_instance_ids_json TEXT NOT NULL`
- `sample_basis TEXT NOT NULL`
- `input_json TEXT NOT NULL`
- `target_json TEXT NOT NULL`
- `reward_json TEXT NOT NULL`
- `schema_version TEXT NOT NULL`
- `dataset_version TEXT NOT NULL`
- `builder_version TEXT NOT NULL`
- `created_at TEXT NOT NULL`

This table is derived. It should be rebuildable from tasks, runs, queries, hits, labels, runtime query outcomes, judge query outcomes, term events, term outcomes, and artifacts.

`sample_id` must be deterministic, based on canonical JSON containing `task_id`, `source_query_instance_ids`, `dataset_version`, `schema_version`, `builder_version`, and `sample_basis`.

### `dataset_exports`

One row per materialized dataset export.

Columns:

- `export_id TEXT PRIMARY KEY`
- `dataset_name TEXT NOT NULL`
- `dataset_version TEXT NOT NULL`
- `schema_version TEXT NOT NULL`
- `builder_version TEXT NOT NULL`
- `builder_config_hash TEXT NOT NULL`
- `builder_config_json TEXT NOT NULL`
- `source_db_sha256 TEXT`
- `source_run_ids_json TEXT NOT NULL`
- `source_query TEXT NOT NULL`
- `source_artifact_refs_json TEXT NOT NULL`
- `git_sha TEXT`
- `artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id)`
- `row_count INTEGER NOT NULL`
- `sha256 TEXT NOT NULL`
- `created_at TEXT NOT NULL`

Exports should also be registered as artifacts.

## Active API

Create one small active module:

```text
src/seektalent/flywheel/store.py
```

Public API should stay direct:

- `FlywheelStore(path: Path)`
- `upsert_task(...)`
- `upsert_resume_snapshot(...)`
- `start_run(...)`
- `complete_run(...)`
- `record_run_queries(...)`
- `record_query_resume_hits(...)`
- `record_judge_labels(...)`
- `record_query_outcomes(...)`
- `record_query_judge_outcomes(...)`
- `record_term_events(...)`
- `record_term_outcomes(...)`
- `record_dataset_export(...)`
- `record_artifact_ref(...)`
- `get_cached_judge_label(...)`

Do not create repository-wide manager classes. Do not create generic ORM layers. Use `sqlite3` directly with explicit SQL.

## Runtime Data Flow

At run start:

```text
ArtifactStore.create_root(...)
FlywheelStore.upsert_task(...)
FlywheelStore.record_artifact_ref(run manifest)
FlywheelStore.start_run(...)
```

For each retrieval round:

```text
write round artifacts
record sent query metadata into run_queries
canonicalize and upsert resume snapshots for provider hits
record query_resume_hits with snapshot hashes into flywheel store
```

After scoring:

```text
enrich query_resume_hits
build and persist runtime query_outcomes
record PRF proposal/gate/executed term_events
derive and persist runtime term_outcomes
materialize flywheel JSONL artifacts from committed DB rows
```

After eval:

```text
upsert resume_snapshots
lookup judge_labels by task + snapshot + judge contract + schema
judge missing rows
record judge_labels
build query_judge_outcomes from judge labels
derive judge-consistent term_outcomes where possible
write evaluation artifacts
register evaluation artifacts in manifest
```

For dataset build:

```text
read flywheel.sqlite3
follow artifact refs when richer context is needed
write query_outcomes.jsonl
write query_judge_outcomes.jsonl
write term_events.jsonl
write term_outcomes.jsonl
write query_rewrite_samples.jsonl
record dataset_exports
```

## Artifact Contract

Add active logical artifacts:

- `flywheel.query_outcomes`
- `flywheel.query_judge_outcomes`
- `flywheel.term_events`
- `flywheel.term_outcomes`
- `flywheel.query_rewrite_samples`
- `flywheel.dataset_export_manifest`

Paths:

```text
flywheel/query_outcomes.jsonl
flywheel/query_judge_outcomes.jsonl
flywheel/term_events.jsonl
flywheel/term_outcomes.jsonl
flywheel/query_rewrite_samples.jsonl
flywheel/dataset_export_manifest.json
```

Per-run JSONL artifacts are materialized views of committed SQLite rows. SQLite is the row-level source for flywheel indexing; JSONL is the portable artifact and training handoff format.

Materialization order:

1. write or upsert rows in a SQLite transaction;
2. commit the transaction;
3. read committed rows and write JSONL artifacts through `ArtifactSession`;
4. register logical artifacts in the manifest;
5. record/update `artifact_refs` and dataset/export ledger rows.

If steps 3-5 fail, the stage must report an explicit failure instead of silently leaving unregistered or mismatched artifacts.

## Export Artifact Kind

Add a first-class `export` artifact kind to `ArtifactStore`.

Collection root:

```text
artifacts/exports/
```

Manifest:

```text
manifests/export_manifest.json
```

Dataset exports must write under:

```text
artifacts/exports/query-rewriting/YYYY/MM/DD/export_<ulid>/
```

Do not write final dataset exports into loose folders or `artifacts/debug/...`.

## Eval Cache Replacement

Replace `JudgeCache` with `FlywheelStore` judge label methods.

Old behavior to delete:

- `.seektalent/judge_cache.sqlite3`;
- `JudgeCache` class as an active API;
- tests that assert labels are reused without model or prompt in the key;
- CLI text that says eval rebuilds `judge_cache.sqlite3`;
- migration helpers whose only purpose is backfilling old judge cache data.

New behavior:

- judge labels are cached in `flywheel.sqlite3`;
- cache key includes task, resume snapshot, judge contract hash, and label schema version;
- changing judge model or prompt creates a cache miss by design;
- judge cache hit metrics read from the new store.

## No Compatibility Policy

This product has no external users yet. Do not keep compatibility code for old cache formats.

Rules:

- do not read old `.seektalent/judge_cache.sqlite3`;
- do not migrate old DB rows;
- do not keep old `JudgeCache` wrappers around `FlywheelStore`;
- do not dual-write to old and new DBs;
- do not preserve tests that exist only to keep old cache semantics;
- do not add fallback behavior for old flywheel/eval artifacts;
- if old artifacts remain on disk, they are historical files only, not supported active input.

Implementation should use search-based cleanup gates such as:

```text
rg -n "JudgeCache|judge_cache|judge_cache.sqlite3" src tests docs
```

Allowed remaining references after implementation should be limited to:

- the new design/plan docs;
- explicit removed-setting or removed-command tests, if needed.

## Current Bugfix Scope

The implementation plan must include these fixes:

1. Evaluation outputs must always be registered in the run manifest when eval succeeds.
2. `evaluation/replay_rows.jsonl` must be exported when replay snapshots exist.
3. Query outcomes must be persisted as rows and JSONL, not only used for runtime lane refill.
4. Judge-consistent query outcomes must be persisted after eval.
5. Term events and term outcomes must be persisted as rows and JSONL.
6. Judge label cache hit metrics must come from `FlywheelStore`.
7. Tests must not create `.seektalent/cache-test-*` under the repository root.
8. New benchmark/eval runs must populate `flywheel.sqlite3`.

## Data Quality Rules

### Stable IDs

- `task_sha256` must be a single canonical hash over task schema version, job title, JD, and notes.
- Empty notes are represented explicitly in the canonical input; do not preserve old special-case hash compatibility.
- `snapshot_sha256` must be canonical across equivalent resume payloads.
- `query_fingerprint` must remain stable across comparable query specs.
- `query_instance_id` must remain run-local.

### Label Reuse

Reuse judge labels only when all are equal:

- task id;
- snapshot sha256;
- judge contract hash;
- label schema version.

Do not reuse labels across prompt or model changes to save cost.

### Runtime Query Outcome

Runtime query outcome rows must be based on provider returns and scored candidates:

- zero recall comes from provider returned count;
- duplicate-only comes from provider hit rows and dedupe;
- marginal gain comes from new fit or near-fit candidates;
- broad noise comes from fit rate, must-have match, and off-intent signals;
- drift uses comparison against exploit baseline where available.

Runtime outcomes are useful online and for diagnostics, but they are not the default training truth.

### Judge Query Outcome

Judge query outcome rows must be based on judge labels under a recorded judge contract:

- `judged_resume_count` is the denominator for judge precision;
- `judge_positive_rate` is nullable when no judged denominator exists;
- rows missing snapshot hashes are excluded from judge outcome derivation;
- dataset builder should prefer judge outcomes and only use runtime outcomes when explicitly configured for weak/runtime-derived samples.

Do not ask an LLM to label query outcomes.

### Term Event And Term Outcome

Term events must record proposal, gate, and executed-query lineage before outcomes are derived. Term outcomes must be based on term events plus runtime and judge query outcomes.

Rejected PRF candidates must not be bound to generic fallback executed queries. Only terms that actually appear in a provider query may have `executed_query_instance_id`.

Do not maintain domain dictionaries. The implementation should remain domain-general across technical, product, operations, sales, finance, healthcare, and mixed-language resumes.

## Dataset Export

Add a dataset builder command or function that can produce:

```text
query_outcomes.jsonl
query_judge_outcomes.jsonl
term_events.jsonl
term_outcomes.jsonl
query_rewrite_samples.jsonl
```

Export root:

```text
artifacts/exports/query-rewriting/YYYY/MM/DD/export_<ulid>/
```

Use the first-class `export` artifact kind. Do not use `artifacts/debug/...` for final dataset exports.

The exported `query_rewrite_samples.jsonl` is constrained:

- input includes job title, requirement digest, query history, failed terms, successful terms, PRF evidence summaries, and top positive/negative signals;
- target is not free-form query generation;
- target must select, suppress, or rank corpus-supported terms;
- reward includes high-score gain, precision gain, zero-recall recovery, duplicate penalty, broad-noise penalty, and drift penalty.
- sample ids must be deterministic across repeated exports from the same DB and builder config.

## Deletion Scope

Delete or rewrite active code for:

- `JudgeCache`;
- `_cache_path(...)` for judge cache;
- `migrate_judge_assets...` style old-cache migration helpers;
- tests requiring old judge cache schema;
- tests requiring cache reuse without model/prompt;
- CLI help text mentioning `.seektalent/judge_cache.sqlite3`;
- docs that describe judge cache as the active eval DB;
- repo-root `.seektalent/cache-test-*` creation behavior.

Do not delete:

- `runtime/exact_llm_cache.py` if it is still the active exact LLM response cache for requirements/scoring;
- filesystem artifacts under `artifacts/...`;
- benchmark input fixtures under `artifacts/benchmarks/...`;
- LLM PRF runtime artifacts and policy logic.

If exact LLM cache behavior also needs cleanup, keep it separate from flywheel store unless it is directly causing repo-root garbage. This rollout should not merge unrelated cache concepts.

## Testing

Add focused tests:

1. `FlywheelStore` creates all tables and enforces primary keys.
2. Every connection enables `PRAGMA foreign_keys=ON`.
3. JSON columns reject invalid JSON.
4. Task hash changes when job title changes.
5. Task and resume snapshot upserts are stable.
6. Judge label cache misses when judge contract hash changes.
7. Judge label cache hits when task, snapshot, judge contract, and schema match.
8. `run_queries` persists `canonical_query_spec_json`.
9. Normal `query_resume_hits` rows include `snapshot_sha256`.
10. Runtime `query_outcomes` use `NULL` averages for zero-recall or no-scored-denominator cases.
11. Eval writes judge labels and `query_judge_outcomes` into `flywheel.sqlite3`.
12. Rejected PRF candidates create `term_events` without binding to generic fallback query ids.
13. Single-run runtime writes task, run, queries, query hits, query outcomes, term events, and term outcomes.
14. Eval registers `evaluation.evaluation` and `evaluation.replay_rows` when available.
15. Dataset builder writes valid JSONL and records deterministic `dataset_exports`.
16. Re-exporting the same DB with the same builder config produces stable sample ids and output hashes.
17. SQLite rows, JSONL artifacts, and manifest logical artifacts stay consistent.
18. `rg -n "JudgeCache|judge_cache|judge_cache.sqlite3" src tests docs` has only allowed removed-surface references.
19. Search-based guard catches direct flywheel artifact path stitching outside artifact registry/resolver code.
20. No tests create `.seektalent/cache-test-*` under repository root.

Run verification:

```text
uv run pytest tests/test_flywheel_store.py tests/test_evaluation.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py -q
uv run pytest -q
```

## Acceptance Criteria

1. `FlywheelStore` is the only active eval/flywheel SQLite boundary.
2. `.seektalent/flywheel.sqlite3` is created during eval/flywheel-enabled runs.
3. `.seektalent/judge_cache.sqlite3` is not created by active tests or runtime.
4. Canonical query specs are persisted for every sent query.
5. Normal query hit rows join to resume snapshots through `snapshot_sha256`.
6. Runtime query outcomes and judge query outcomes are separate tables.
7. Term events and derived term outcomes are separate tables.
8. Query, judge-query, term-event, and term-outcome rows are persisted both in SQLite and JSONL artifacts.
9. Judge label reuse is prompt/model/schema-safe through `judge_contract_hash`.
10. A single JD with eval enabled can be rerun and shows correct label cache hit behavior.
11. A 12-JD benchmark with eval enabled populates `runs`, `run_queries`, `query_resume_hits`, `query_outcomes`, `query_judge_outcomes`, `term_events`, `term_outcomes`, and `judge_labels`.
12. Dataset export produces `query_outcomes.jsonl`, `query_judge_outcomes.jsonl`, `term_events.jsonl`, `term_outcomes.jsonl`, and `query_rewrite_samples.jsonl`.
13. Export outputs are stored under first-class `artifacts/exports/...` artifacts.
14. Old judge cache compatibility code is deleted.
15. Full test suite passes.

## Open Implementation Notes

- Prefer direct `sqlite3` with explicit SQL over an ORM.
- Keep schema creation in one place.
- Keep row-building functions close to runtime/eval usage.
- Do not introduce a generic database manager.
- Keep JSON columns as canonical JSON strings.
- Guard JSON columns with `CHECK(json_valid(...))` where available.
- Enable foreign keys, WAL, and busy timeout on every connection.
- Prefer `STRICT` tables when supported.
- Add indexes only for queries needed now: task lookup, run lookup, query fingerprint lookup, label cache lookup, and dataset export lookup.
- Use transactions for multi-row writes from a run or eval stage.
- Materialize JSONL artifacts from committed rows rather than interleaving row inserts and file writes.
- Make partial failures explicit; do not silently swallow store write failures.

## Expected Next Step

After user approval, write an implementation plan that executes in this order:

1. add `FlywheelStore` schema and tests;
2. add structured `artifact_refs` and `ArtifactKind.EXPORT`;
3. replace `JudgeCache` in eval;
4. wire runtime task/run/query/query-hit writes with canonical query specs and snapshot hashes;
5. add runtime query outcome construction;
6. add judge query outcome construction after eval;
7. add term event and term outcome construction;
8. add JSONL artifact materialization and manifest registration;
9. add dataset builder/export ledger with deterministic sample ids;
10. delete old judge cache code/tests/docs;
11. clean test cache leakage;
12. run focused tests and full suite.
