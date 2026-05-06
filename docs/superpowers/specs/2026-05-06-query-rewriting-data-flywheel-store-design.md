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
5. Judge label reuse must include judge model, prompt hash, and label schema version.
6. Query outcome and term outcome must become first-class assets, not transient runtime classifications.
7. Dataset exports must be reproducible from `flywheel.sqlite3` plus artifact refs.
8. No compatibility layer is required for old judge cache data. Old files may remain on disk, but active code must not read them.
9. Remove test behavior that writes `.seektalent/cache-test-*` into the repository root.
10. Keep the implementation small: one store module, focused schema models, focused exporters.

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
- term outcome rows;
- dataset export ledger rows;
- compact artifact refs and hashes.

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

## Core Tables

### `tasks`

One row per JD plus notes task.

Columns:

- `task_id TEXT PRIMARY KEY`
- `task_sha256 TEXT NOT NULL UNIQUE`
- `jd_sha256 TEXT NOT NULL`
- `notes_sha256 TEXT NOT NULL`
- `job_title TEXT`
- `jd_text TEXT NOT NULL`
- `notes_text TEXT NOT NULL`
- `created_at TEXT NOT NULL`

`task_id` should be the task hash unless a future reason appears to separate logical ID from content hash.

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

### `runs`

One row per workflow run.

Columns:

- `run_id TEXT PRIMARY KEY`
- `task_id TEXT NOT NULL REFERENCES tasks(task_id)`
- `version TEXT`
- `git_sha TEXT`
- `artifact_root TEXT NOT NULL`
- `config_hash TEXT NOT NULL`
- `config_json TEXT NOT NULL`
- `status TEXT NOT NULL`
- `eval_enabled INTEGER NOT NULL`
- `benchmark_id TEXT`
- `benchmark_case_id TEXT`
- `started_at TEXT NOT NULL`
- `completed_at TEXT`
- `failure_summary TEXT`

`artifact_root` is a relative or absolute path to the run artifact root. The artifact manifest remains the source of truth for file layout.

### `run_queries`

One row per sent query instance.

Columns:

- `run_id TEXT NOT NULL REFERENCES runs(run_id)`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `query_role TEXT`
- `keyword_query TEXT NOT NULL`
- `query_terms_json TEXT NOT NULL`
- `filters_json TEXT NOT NULL`
- `location_key TEXT`
- `batch_no INTEGER`
- `selected_prf_expression TEXT`
- `accepted_prf_term_family_id TEXT`
- `fallback_reason TEXT`
- `artifact_ref TEXT`
- `created_at TEXT NOT NULL`
- `PRIMARY KEY (run_id, query_instance_id)`

`query_fingerprint` is stable across comparable query specs. `query_instance_id` is run-local and unique.

### `query_resume_hits`

One row per provider-returned resume per query.

Columns:

- `run_id TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `snapshot_sha256 TEXT`
- `resume_id TEXT NOT NULL`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT NOT NULL`
- `rank_in_query INTEGER NOT NULL`
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
- `artifact_ref TEXT`
- `PRIMARY KEY (run_id, query_instance_id, resume_id, rank_in_query)`

This table is the denominator that the original research plan identified as missing.

### `judge_labels`

One row per judge result for a task/resume snapshot under a specific judge contract.

Columns:

- `task_id TEXT NOT NULL REFERENCES tasks(task_id)`
- `snapshot_sha256 TEXT NOT NULL REFERENCES resume_snapshots(snapshot_sha256)`
- `judge_model_id TEXT NOT NULL`
- `judge_prompt_hash TEXT NOT NULL`
- `label_schema_version TEXT NOT NULL`
- `score INTEGER NOT NULL`
- `rationale TEXT NOT NULL`
- `judge_prompt_text TEXT`
- `latency_ms INTEGER`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `PRIMARY KEY (task_id, snapshot_sha256, judge_model_id, judge_prompt_hash, label_schema_version)`

This intentionally removes the old behavior where labels could be reused across judge model or prompt changes.

### `query_outcomes`

One row per query instance outcome.

Columns:

- `run_id TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT NOT NULL`
- `provider_returned_count INTEGER NOT NULL`
- `new_unique_resume_count INTEGER NOT NULL`
- `duplicate_count INTEGER NOT NULL`
- `new_fit_count INTEGER NOT NULL`
- `new_near_fit_count INTEGER NOT NULL DEFAULT 0`
- `fit_rate REAL NOT NULL`
- `must_have_match_avg REAL NOT NULL`
- `risk_score_avg REAL`
- `off_intent_reason_count INTEGER NOT NULL`
- `primary_label TEXT NOT NULL`
- `labels_json TEXT NOT NULL`
- `reasons_json TEXT NOT NULL`
- `latency_ms INTEGER`
- `cost_estimate_usd REAL`
- `artifact_ref TEXT`
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

### `term_outcomes`

One row per term or phrase family observed in a query or PRF proposal.

Columns:

- `run_id TEXT NOT NULL`
- `query_instance_id TEXT NOT NULL`
- `query_fingerprint TEXT NOT NULL`
- `term_surface TEXT NOT NULL`
- `term_family_id TEXT NOT NULL`
- `term_role TEXT NOT NULL`
- `source TEXT NOT NULL`
- `round_no INTEGER NOT NULL`
- `lane_type TEXT NOT NULL`
- `supporting_resume_ids_json TEXT NOT NULL`
- `negative_resume_ids_json TEXT NOT NULL`
- `accepted_by_prf_gate INTEGER`
- `prf_reject_reasons_json TEXT NOT NULL`
- `appeared_in_keyword_query INTEGER NOT NULL`
- `new_fit_count INTEGER NOT NULL`
- `new_unique_resume_count INTEGER NOT NULL`
- `duplicate_count INTEGER NOT NULL`
- `noise_count INTEGER NOT NULL`
- `primary_query_outcome_label TEXT NOT NULL`
- `artifact_ref TEXT`
- `created_at TEXT NOT NULL`
- `PRIMARY KEY (run_id, query_instance_id, term_family_id, term_role, source)`

`source` examples:

- `controller_query`
- `llm_prf_candidate`
- `accepted_prf_expression`
- `generic_explore`

Do not use broad semantic familying in this rollout. Use the same conservative family logic that protects `Java` vs `JavaScript`, `React` vs `React Native`, and CJK/ASCII wrapper phrases.

### `query_rewrite_samples`

One row per generated training sample.

Columns:

- `sample_id TEXT PRIMARY KEY`
- `task_id TEXT NOT NULL`
- `run_id TEXT NOT NULL`
- `source_query_instance_ids_json TEXT NOT NULL`
- `input_json TEXT NOT NULL`
- `target_json TEXT NOT NULL`
- `reward_json TEXT NOT NULL`
- `schema_version TEXT NOT NULL`
- `dataset_version TEXT NOT NULL`
- `created_at TEXT NOT NULL`

This table is derived. It should be rebuildable from tasks, runs, queries, hits, labels, query outcomes, term outcomes, and artifacts.

### `dataset_exports`

One row per materialized dataset export.

Columns:

- `export_id TEXT PRIMARY KEY`
- `dataset_name TEXT NOT NULL`
- `dataset_version TEXT NOT NULL`
- `schema_version TEXT NOT NULL`
- `artifact_root TEXT NOT NULL`
- `output_path TEXT NOT NULL`
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
- `record_term_outcomes(...)`
- `record_dataset_export(...)`
- `get_cached_judge_label(...)`

Do not create repository-wide manager classes. Do not create generic ORM layers. Use `sqlite3` directly with explicit SQL.

## Runtime Data Flow

At run start:

```text
ArtifactStore.create_root(...)
FlywheelStore.upsert_task(...)
FlywheelStore.start_run(...)
```

For each retrieval round:

```text
write round artifacts
record sent query metadata into run_queries
record query_resume_hits into flywheel store
```

After scoring:

```text
enrich query_resume_hits
build and persist query_outcomes
build and persist term_outcomes
write matching JSONL artifacts
```

After eval:

```text
upsert resume_snapshots
lookup judge_labels by task + snapshot + model + prompt + schema
judge missing rows
record judge_labels
write evaluation artifacts
register evaluation artifacts in manifest
```

For dataset build:

```text
read flywheel.sqlite3
follow artifact refs when richer context is needed
write query_outcomes.jsonl
write term_outcomes.jsonl
write query_rewrite_samples.jsonl
record dataset_exports
```

## Artifact Contract

Add active logical artifacts:

- `flywheel.query_outcomes`
- `flywheel.term_outcomes`
- `flywheel.query_rewrite_samples`
- `flywheel.dataset_export_manifest`

Paths:

```text
flywheel/query_outcomes.jsonl
flywheel/term_outcomes.jsonl
flywheel/query_rewrite_samples.jsonl
flywheel/dataset_export_manifest.json
```

Per-run JSONL artifacts should mirror the rows inserted into SQLite. SQLite is the query index; JSONL is the portable artifact and training handoff format.

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
- cache key includes task, resume snapshot, judge model, judge prompt hash, and label schema version;
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
4. Term outcomes must be persisted as rows and JSONL.
5. Judge label cache hit metrics must come from `FlywheelStore`.
6. Tests must not create `.seektalent/cache-test-*` under the repository root.
7. New benchmark/eval runs must populate `flywheel.sqlite3`.

## Data Quality Rules

### Stable IDs

- `task_sha256` must be a single canonical hash over JD and notes.
- Empty notes are represented explicitly in the canonical input; do not preserve old special-case hash compatibility.
- `snapshot_sha256` must be canonical across equivalent resume payloads.
- `query_fingerprint` must remain stable across comparable query specs.
- `query_instance_id` must remain run-local.

### Label Reuse

Reuse judge labels only when all are equal:

- task id;
- snapshot sha256;
- judge model id;
- judge prompt hash;
- label schema version.

Do not reuse labels across prompt or model changes to save cost.

### Query Outcome

Query outcome rows must be based on provider returns and scored candidates:

- zero recall comes from provider returned count;
- duplicate-only comes from provider hit rows and dedupe;
- marginal gain comes from new fit or near-fit candidates;
- broad noise comes from fit rate, must-have match, and off-intent signals;
- drift uses comparison against exploit baseline where available.

Do not ask an LLM to label query outcomes.

### Term Outcome

Term outcome rows must be based on query terms, PRF proposals, PRF gate decisions, and resulting query outcomes.

Do not maintain domain dictionaries. The implementation should remain domain-general across technical, product, operations, sales, finance, healthcare, and mixed-language resumes.

## Dataset Export

Add a dataset builder command or function that can produce:

```text
query_outcomes.jsonl
term_outcomes.jsonl
query_rewrite_samples.jsonl
```

Recommended export root:

```text
artifacts/exports/query-rewriting/YYYY/MM/DD/export_<ulid>/
```

Add a first-class export artifact kind or equivalent explicit export root in `ArtifactStore`. Do not use `artifacts/debug/...` for final dataset exports.

The exported `query_rewrite_samples.jsonl` should be constrained:

- input includes job title, requirement digest, query history, failed terms, successful terms, PRF evidence summaries, and top positive/negative signals;
- target is not free-form query generation;
- target must select, suppress, or rank corpus-supported terms;
- reward includes high-score gain, precision gain, zero-recall recovery, duplicate penalty, broad-noise penalty, and drift penalty.

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
2. Task and resume snapshot upserts are stable.
3. Judge label cache misses when judge model changes.
4. Judge label cache misses when judge prompt hash changes.
5. Judge label cache hits when task, snapshot, model, prompt, and schema match.
6. Single-run runtime writes task, run, queries, query hits, query outcomes, and term outcomes.
7. Eval writes judge labels into `flywheel.sqlite3`.
8. Eval registers `evaluation.evaluation` and `evaluation.replay_rows` when available.
9. Dataset builder writes valid JSONL and records `dataset_exports`.
10. `rg -n "JudgeCache|judge_cache|judge_cache.sqlite3" src tests docs` has only allowed removed-surface references.
11. No tests create `.seektalent/cache-test-*` under repository root.

Run verification:

```text
uv run pytest tests/test_flywheel_store.py tests/test_evaluation.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py -q
uv run pytest -q
```

## Acceptance Criteria

1. `FlywheelStore` is the only active eval/flywheel SQLite boundary.
2. `.seektalent/flywheel.sqlite3` is created during eval/flywheel-enabled runs.
3. `.seektalent/judge_cache.sqlite3` is not created by active tests or runtime.
4. Query and term outcomes are persisted both in SQLite and JSONL artifacts.
5. Judge label reuse is prompt/model/schema-safe.
6. A single JD with eval enabled can be rerun and shows correct label cache hit behavior.
7. A 12-JD benchmark with eval enabled populates `runs`, `run_queries`, `query_resume_hits`, `query_outcomes`, `term_outcomes`, and `judge_labels`.
8. Dataset export produces `query_outcomes.jsonl`, `term_outcomes.jsonl`, and `query_rewrite_samples.jsonl`.
9. Old judge cache compatibility code is deleted.
10. Full test suite passes.

## Open Implementation Notes

- Prefer direct `sqlite3` with explicit SQL over an ORM.
- Keep schema creation in one place.
- Keep row-building functions close to runtime/eval usage.
- Do not introduce a generic database manager.
- Keep JSON columns as canonical JSON strings.
- Add indexes only for queries needed now: task lookup, run lookup, query fingerprint lookup, label cache lookup, and dataset export lookup.
- Use transactions for multi-row writes from a run or eval stage.
- Make partial failures explicit; do not silently swallow store write failures.

## Expected Next Step

After user approval, write an implementation plan that executes in this order:

1. add `FlywheelStore` schema and tests;
2. replace `JudgeCache` in eval;
3. wire runtime query/query-hit/outcome writes;
4. add term outcome construction;
5. add JSONL artifact exports and manifest registration;
6. add dataset builder/export ledger;
7. delete old judge cache code/tests/docs;
8. clean test cache leakage;
9. run focused tests and full suite.
