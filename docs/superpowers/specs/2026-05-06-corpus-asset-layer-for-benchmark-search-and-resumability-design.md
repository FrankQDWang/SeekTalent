# Corpus Asset Layer For Benchmark, Search, And Future Resumability Design

## Context

SeekTalent is moving from an experimental local agent toward a production product for recruiters and HR users. Current benchmark inputs are small maintained JD JSONL files under `artifacts/benchmarks/`, and the new flywheel store records query, hit, judge, outcome, term, and query rewriting export assets.

That is not enough for the next phase. Future static benchmarks, a first-party resume search engine, and long-term product learning require a stable corpus layer:

- a JD corpus that can grow from 12 seed JDs to 100+ benchmark-ready tasks;
- a resume corpus that preserves every provider-returned resume snapshot, not only scored or selected candidates;
- provenance that explains which run, query, provider, and stage first surfaced each asset;
- data boundaries that remain compatible with future resumable runs, memory, and cloud multi-tenant isolation.

The user explicitly confirmed:

- ignore old `judge_cache.sqlite3` assets for this rollout;
- default to saving all CTS/provider returned resume snapshots;
- benchmark and query rewriting are separate concepts and must not be coupled;
- resumable execution and personalized memory are not implemented now, but their data boundaries should be anticipated;
- the preferred durable-execution posture is stateless executors with durable DB/artifact ledgers, not a stateful workflow engine as source of truth.

## Goals

1. Add a corpus asset layer for long-term JD and resume accumulation.
2. Save every CTS/provider returned resume snapshot by default.
3. Preserve enough resume text and metadata to build a future first-party search index.
4. Keep corpus, benchmark, and query rewriting flywheel as separate concepts.
5. Add minimal future-facing fields for resumable runs, memory eligibility, sensitivity, tenancy, and provenance.
6. Keep the first implementation small enough to ship before static benchmark and memory features exist.

## Non-Goals

This design does not implement:

- static frozen benchmark pools;
- qrels or benchmark relevance labels;
- a first-party search engine;
- resumable run execution;
- Temporal, LangGraph checkpointing, or another workflow engine;
- personalized memory extraction or full prompt-injection defense beyond corpus trust markers;
- full cloud IAM, KMS, sandbox orchestration, or tenant provisioning;
- migration of old `judge_cache.sqlite3` assets.

## Architecture Position

Use:

```text
stateless executor + durable DB/artifact ledger
```

Do not use:

```text
stateful graph/workflow runtime as the source of truth
```

LLM calls and agent stages remain stateless. Durable state lives in explicit stores and artifacts. A future scheduler such as Temporal, Celery, or a managed job queue may orchestrate stateless stage execution, but it must not become the canonical owner of business data.

Recovery, when implemented later, should use:

```text
run_id -> stage ledger -> artifact refs -> rebuild context -> continue next stage
```

not:

```text
load Python object / graph checkpoint -> continue hidden in-memory state
```

This is better aligned with a production HR product because assets must be auditable, tenant-scoped, searchable, exportable, and inspectable after a worker exits.

## Storage Boundary

Introduce a new corpus boundary separate from `FlywheelStore`.

```text
.seektalent/
  corpus.sqlite3             # local corpus index and metadata
  flywheel.sqlite3           # query rewriting and run learning data

artifacts/
  corpus/                    # portable corpus exports and manifests
  benchmarks/                # maintained benchmark input JSONL
  benchmark-executions/      # benchmark run execution artifacts
  runs/                      # run trajectory artifacts
  exports/                   # dataset exports, including flywheel exports
```

`CorpusStore` owns long-lived JD and resume documents. `FlywheelStore` owns query rewriting trajectories and derived learning rows. They may reference the same run IDs, query IDs, and resume hashes, but neither store should absorb the other's responsibility.

## CorpusStore Scope

Create a focused `CorpusStore` for:

- JD corpus rows;
- resume subject and resume snapshot rows;
- provider-returned resume snapshot provenance;
- corpus collection, membership, and immutable export rows;
- future-facing metadata needed by benchmark, search, resumability, and memory.

The store should use SQLite locally, with the same discipline as `FlywheelStore`:

- explicit schema version;
- `PRAGMA foreign_keys = ON`;
- WAL mode;
- `PRAGMA busy_timeout = 5000` or the project-standard equivalent;
- JSON columns written as canonical JSON and guarded with JSON validity checks where available;
- short transactions;
- no network or artifact file IO inside DB transactions.
- single-writer discipline for corpus writes inside one local process.

All query APIs must require `tenant_id` and `workspace_id`. Content hashes are identifiers inside a tenant/workspace scope, not authorization keys.

## Required Schema

### `jd_documents`

One row per stable JD document.

Fields:

- `jd_doc_id TEXT PRIMARY KEY`
- `tenant_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL`
- `job_title TEXT NOT NULL`
- `jd_text TEXT NOT NULL`
- `notes_text TEXT NOT NULL`
- `jd_sha256 TEXT NOT NULL`
- `notes_sha256 TEXT NOT NULL`
- `task_sha256 TEXT NOT NULL`
- `language TEXT`
- `domain_tags_json TEXT NOT NULL`
- `seniority TEXT`
- `source_kind TEXT NOT NULL`
- `source_ref TEXT`
- `memory_eligible INTEGER NOT NULL`
- `allowed_uses_json TEXT NOT NULL`
- `search_index_eligible INTEGER NOT NULL`
- `benchmark_eligible INTEGER NOT NULL`
- `training_eligible INTEGER NOT NULL`
- `export_eligible INTEGER NOT NULL`
- `llm_ingestion_eligible INTEGER NOT NULL`
- `consent_basis TEXT`
- `source_terms_ref TEXT`
- `pii_classification_version TEXT NOT NULL`
- `redaction_status TEXT NOT NULL`
- `sensitivity_json TEXT NOT NULL`
- `content_trust_level TEXT NOT NULL`
- `contains_prompt_like_text INTEGER NOT NULL`
- `llm_sanitization_version TEXT`
- `llm_ingestion_policy TEXT NOT NULL`
- `retention_policy TEXT NOT NULL`
- `schema_version TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Notes:

- `task_sha256` must include job title, JD text, notes text, and schema version.
- `source_kind` examples: `manual_input`, `benchmark_seed`, `import`, `run_input`.
- `memory_eligible` defaults to `0`.
- `training_eligible`, `export_eligible`, and `llm_ingestion_eligible` default to `0`.
- `allowed_uses_json` records the explicit allowed use set, such as `search`, `benchmark`, `training`, `memory`, `export`, and `llm_ingestion`.
- `content_trust_level` defaults to `untrusted_external`.
- `llm_ingestion_policy` defaults to `quote_as_data_only`.
- `tenant_id` and `workspace_id` are required even in local mode. Local mode may use stable defaults such as `local` / `default`.

### `resume_subjects`

One row per candidate/person-like subject within a tenant/workspace. This is intentionally weaker than a global identity graph. It exists to prevent first-party search and future benchmark pools from counting the same candidate as multiple people just because the provider returned multiple snapshots.

Fields:

- `subject_id TEXT PRIMARY KEY`
- `tenant_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL`
- `provider_name TEXT NOT NULL`
- `provider_candidate_id TEXT`
- `source_resume_id TEXT`
- `dedup_key TEXT`
- `latest_resume_doc_id TEXT`
- `subject_confidence TEXT NOT NULL`
- `subject_binding_reason TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Notes:

- V1 may create weak subjects from provider ID, source resume ID, or existing dedup key.
- `subject_id` may be generated even when confidence is weak.
- `latest_resume_doc_id` points to the newest known usable snapshot when available.
- Subject binding must stay tenant/workspace scoped.

### `resume_documents`

One row per stable resume snapshot. A resume document is a versioned snapshot, not the candidate subject itself.

Fields:

- `resume_doc_id TEXT PRIMARY KEY`
- `tenant_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL`
- `subject_id TEXT`
- `snapshot_sha256 TEXT NOT NULL`
- `source_resume_id TEXT`
- `provider_name TEXT NOT NULL`
- `provider_candidate_id TEXT`
- `dedup_key TEXT`
- `raw_payload_artifact_ref_id TEXT`
- `raw_payload_sha256 TEXT NOT NULL`
- `raw_payload_size_bytes INTEGER NOT NULL`
- `raw_payload_json TEXT`
- `raw_payload_inline_reason TEXT`
- `normalized_text TEXT`
- `normalized_sections_json TEXT NOT NULL`
- `skills_json TEXT NOT NULL`
- `experience_json TEXT NOT NULL`
- `education_json TEXT NOT NULL`
- `locations_json TEXT NOT NULL`
- `current_title TEXT`
- `current_company TEXT`
- `searchable_text_version TEXT NOT NULL`
- `normalization_version TEXT NOT NULL`
- `normalization_status TEXT NOT NULL`
- `normalization_failure_kind TEXT`
- `normalization_warnings_json TEXT NOT NULL`
- `payload_completeness TEXT NOT NULL`
- `has_searchable_text INTEGER NOT NULL`
- `source_kind TEXT NOT NULL`
- `first_seen_run_id TEXT`
- `first_seen_query_instance_id TEXT`
- `first_seen_stage_id TEXT`
- `first_seen_artifact_ref_id TEXT`
- `memory_eligible INTEGER NOT NULL`
- `allowed_uses_json TEXT NOT NULL`
- `search_index_eligible INTEGER NOT NULL`
- `benchmark_eligible INTEGER NOT NULL`
- `training_eligible INTEGER NOT NULL`
- `export_eligible INTEGER NOT NULL`
- `llm_ingestion_eligible INTEGER NOT NULL`
- `consent_basis TEXT`
- `source_terms_ref TEXT`
- `pii_classification_version TEXT NOT NULL`
- `redaction_status TEXT NOT NULL`
- `sensitivity_json TEXT NOT NULL`
- `content_trust_level TEXT NOT NULL`
- `contains_prompt_like_text INTEGER NOT NULL`
- `llm_sanitization_version TEXT`
- `llm_ingestion_policy TEXT NOT NULL`
- `retention_policy TEXT NOT NULL`
- `schema_version TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Uniqueness:

- unique within tenant/workspace by `(tenant_id, workspace_id, snapshot_sha256)`;
- `snapshot_sha256` alone is not a global business key across tenants.

Notes:

- Raw provider payload is artifact-first. DB stores `raw_payload_artifact_ref_id`, `raw_payload_sha256`, and `raw_payload_size_bytes`.
- `raw_payload_json` is nullable and allowed only for tests, tiny fixtures, or explicit local debugging configuration. Normal runtime should not inline full resume payloads into SQLite.
- `normalized_text` is the future search-engine source text. It should remove obvious transport noise while preserving skills, projects, work summaries, titles, industries, and capability evidence.
- `normalized_text` may be `NULL` when `normalization_status = 'failed'`.
- `normalization_status` values: `ok`, `partial`, `failed`.
- `payload_completeness` values: `search_result_summary`, `profile_detail`, `full_resume`, `unknown`.
- `has_searchable_text = 0` when normalization failed or the provider payload has no useful searchable content.
- `current_company`, names, salary, school, and location fields may be useful for product display and filtering, but they must be classified in `sensitivity_json`.
- `content_trust_level` defaults to `untrusted_external`.
- `llm_ingestion_policy` defaults to `quote_as_data_only`.
- `allowed_uses_json` records the explicit allowed use set, such as `search`, `benchmark`, `training`, `memory`, `export`, and `llm_ingestion`.
- `training_eligible`, `memory_eligible`, `export_eligible`, and `llm_ingestion_eligible` default to `0`.
- `pii_classification_version` and `redaction_status` make later export/search/memory decisions auditable instead of implicit.

### `resume_observations`

One row per provider return event. This preserves the denominator for corpus acquisition and future search evaluation.

Fields:

- `observation_id TEXT PRIMARY KEY`
- `tenant_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL`
- `resume_doc_id TEXT NOT NULL`
- `run_id TEXT NOT NULL`
- `round_no INTEGER`
- `stage_id TEXT`
- `query_instance_id TEXT`
- `query_fingerprint TEXT`
- `provider_name TEXT NOT NULL`
- `provider_request_id TEXT`
- `provider_rank INTEGER`
- `provider_page_no INTEGER`
- `provider_fetch_no INTEGER`
- `attempt_no INTEGER NOT NULL`
- `idempotency_key TEXT NOT NULL`
- `was_scored INTEGER NOT NULL`
- `was_judged INTEGER NOT NULL`
- `was_selected_final INTEGER NOT NULL`
- `source_artifact_ref_id TEXT`
- `created_at TEXT NOT NULL`

Notes:

- Save an observation for every CTS/provider returned resume snapshot by default.
- Scoring and selection flags are metadata on the observation, not a condition for corpus inclusion.
- The same resume may have many observations across runs and queries.
- Provider page retry, worker crash recovery, or explicit rerun of the same stage must not duplicate observations.
- `idempotency_key` is a deterministic hash over tenant, workspace, run, stage, query, provider request/page/fetch/rank, and resume doc identity.
- Unique constraint: `(tenant_id, workspace_id, idempotency_key)`.

### `run_corpus_links`

One row linking a run to the JD corpus document used as run input.

Fields:

- `run_id TEXT NOT NULL`
- `tenant_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL`
- `jd_doc_id TEXT NOT NULL`
- `input_artifact_ref_id TEXT`
- `created_at TEXT NOT NULL`
- primary key `(run_id, tenant_id, workspace_id)`

Notes:

- This table is required in V1. Do not hide run-to-JD linkage behind "if needed" language.
- `CorpusStore` should not absorb `FlywheelStore`, but this link makes run input provenance queryable without scanning artifacts.

### `corpus_collections`

One row per named mutable corpus collection.

Fields:

- `corpus_collection_id TEXT PRIMARY KEY`
- `tenant_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL`
- `name TEXT NOT NULL`
- `description TEXT`
- `mutable INTEGER NOT NULL`
- `builder_version TEXT NOT NULL`
- `builder_config_json TEXT NOT NULL`
- `row_count INTEGER NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Notes:

- A growing corpus is a collection, not a version.
- V1 can create one mutable default collection, for example `local-default-resume-corpus`.
- A collection may grow as new provider snapshots arrive.

### `corpus_memberships`

One row per resume document included in a corpus collection.

Fields:

- `corpus_collection_id TEXT NOT NULL`
- `resume_doc_id TEXT NOT NULL`
- `added_by_observation_id TEXT`
- `inclusion_reason TEXT NOT NULL`
- `included_at TEXT NOT NULL`
- primary key `(corpus_collection_id, resume_doc_id)`

### `corpus_exports`

One row per immutable corpus export artifact.

Fields:

- `corpus_export_id TEXT PRIMARY KEY`
- `tenant_id TEXT NOT NULL`
- `workspace_id TEXT NOT NULL`
- `corpus_collection_id TEXT NOT NULL`
- `artifact_ref_id TEXT NOT NULL`
- `builder_version TEXT NOT NULL`
- `builder_config_hash TEXT NOT NULL`
- `builder_config_json TEXT NOT NULL`
- `source_query TEXT NOT NULL`
- `source_run_ids_json TEXT NOT NULL`
- `row_count INTEGER NOT NULL`
- `sha256 TEXT NOT NULL`
- `created_at TEXT NOT NULL`

Notes:

- A corpus export is immutable.
- Do not use `corpus_exports` to represent a growing collection.
- Future frozen benchmark pools may reference export IDs, but benchmark pool/qrel tables are not implemented in this rollout.

## Foreign Keys And Indexes

Required foreign keys:

- `resume_documents.subject_id -> resume_subjects.subject_id`
- `resume_observations.resume_doc_id -> resume_documents.resume_doc_id`
- `run_corpus_links.jd_doc_id -> jd_documents.jd_doc_id`
- `corpus_memberships.corpus_collection_id -> corpus_collections.corpus_collection_id`
- `corpus_memberships.resume_doc_id -> resume_documents.resume_doc_id`
- `corpus_memberships.added_by_observation_id -> resume_observations.observation_id`
- `corpus_exports.corpus_collection_id -> corpus_collections.corpus_collection_id`

Required indexes:

- `idx_jd_documents_task` on `(tenant_id, workspace_id, task_sha256)`
- `idx_resume_subjects_provider` on `(tenant_id, workspace_id, provider_name, provider_candidate_id)`
- `idx_resume_subjects_dedup` on `(tenant_id, workspace_id, dedup_key)`
- `idx_resume_documents_snapshot` on `(tenant_id, workspace_id, snapshot_sha256)`
- `idx_resume_documents_subject` on `(tenant_id, workspace_id, subject_id)`
- `idx_resume_observations_query` on `(tenant_id, workspace_id, run_id, query_instance_id)`
- `idx_resume_observations_doc` on `(tenant_id, workspace_id, resume_doc_id)`
- `idx_resume_observations_idempotency` unique on `(tenant_id, workspace_id, idempotency_key)`
- `idx_run_corpus_links_run` on `(tenant_id, workspace_id, run_id)`

## Artifact Format

Use `ArtifactStore` logical artifacts for portable corpus materialization.

New artifact kind:

```text
ArtifactKind.CORPUS
collection root: artifacts/corpus/YYYY/MM/DD/corpus_<ulid>/
manifest: artifacts/corpus/YYYY/MM/DD/corpus_<ulid>/manifests/corpus_manifest.json
```

Logical artifacts:

- `corpus.jd_documents`
- `corpus.resume_subjects`
- `corpus.resume_documents`
- `corpus.resume_observations`
- `corpus.run_corpus_links`
- `corpus.corpus_collections`
- `corpus.corpus_memberships`
- `corpus.corpus_exports`
- `corpus.export_manifest`

Suggested paths:

```text
corpus/jd_documents.jsonl
corpus/resume_subjects.jsonl
corpus/resume_documents.jsonl
corpus/resume_observations.jsonl
corpus/run_corpus_links.jsonl
corpus/corpus_collections.jsonl
corpus/corpus_memberships.jsonl
corpus/corpus_exports.jsonl
corpus/export_manifest.json
```

SQLite is the mutable local queryable index. Corpus artifacts are immutable snapshots or ingest containers. Do not treat `artifacts/corpus/` itself as a mutable loose folder.

Raw provider payloads should be written into registered corpus artifact/blob refs, for example under an immutable corpus ingest artifact:

```text
artifacts/corpus/YYYY/MM/DD/corpus_<ulid>/
  manifests/corpus_manifest.json
  raw_payloads/<snapshot_sha256>.json
```

The DB stores the artifact ref, content hash, and size. Consumers must resolve raw payloads through artifact refs rather than hand-built paths.

## Runtime Write Path

When retrieval receives provider results:

1. Canonicalize each provider candidate payload.
2. Compute `snapshot_sha256`.
3. Write the raw payload to a corpus artifact/blob and record hash, size, and artifact ref.
4. Build normalized searchable text and structured sections. If normalization fails, record failure metadata and keep the raw payload ref.
5. Upsert or weak-bind a `resume_subjects` row.
6. Upsert `resume_documents`.
7. Insert an idempotent `resume_observations` row for every provider-returned candidate.
8. Continue existing run logic for dedup, scoring, PRF, top pool, and final selection.

This must happen before scoring filters candidates down. Corpus accumulation should not inherit current scoring/model bias.

When a run starts:

1. Upsert the input JD as a `jd_documents` row.
2. Insert `run_corpus_links` for `run_id -> jd_doc_id`.

The first implementation does not need to populate benchmark pools or qrels.

## Benchmark Boundary

Benchmark and query rewriting are separate products of the asset system.

Benchmark:

- uses JD corpus and future frozen resume pools;
- evaluates product versions;
- eventually owns benchmark sets, pool membership, and qrels.

Query rewriting flywheel:

- uses run/query/hit/outcome/term trajectories;
- exports query rewriting samples;
- does not own benchmark pools or qrels.

Allowed cross-reference:

- `runs.benchmark_id`
- `runs.benchmark_case_id`
- `jd_doc_id`
- `resume_doc_id`
- `snapshot_sha256`
- `query_instance_id`
- `artifact_ref_id`

Forbidden coupling:

- `flywheel-export` must not export benchmark qrels or benchmark reports.
- Corpus accumulation must not depend on eval being enabled.
- Static benchmark rows must not be stored as query rewriting samples.
- Benchmark pool construction must not depend on PRF or generic-explore internals.

## Future Static Benchmark Preparation

Do not implement now, but keep the corpus compatible with future static benchmark tables:

- `benchmark_sets`
- `benchmark_cases`
- `benchmark_case_jds`
- `benchmark_pool_versions`
- `benchmark_pool_members`
- `benchmark_qrels`
- `benchmark_execution_results`

The expected future path:

1. Grow JD corpus to 100+ high-quality cases.
2. Grow resume corpus from all provider returns and later imported/private resume sources.
3. Use multiple retrieval systems to build candidate pools.
4. Materialize immutable corpus exports.
5. Freeze benchmark pool versions from explicit corpus export IDs and qrel contracts.
6. Generate qrels through judge/human review under explicit contracts.
7. Execute product versions against the frozen benchmark.

## Future Search Engine Preparation

The first-party search engine should build indexes from `resume_documents`, not from run artifacts directly.

The corpus must preserve:

- `normalized_text`
- `normalized_sections_json`
- skill/capability surfaces;
- experience/project summaries;
- seniority/title/location fields;
- source and normalization versions;
- sensitivity markers.

Future index builds should be tracked separately:

- `search_index_build_id`
- `corpus_collection_id`
- `corpus_export_id`
- `index_backend`
- `index_config_hash`
- `index_artifact_ref_id`
- `created_at`

Index builds may read from a mutable collection in local development, but production/benchmark-comparable index builds should reference an immutable `corpus_export_id`.

Do not implement index builds in this rollout.

## Future Resumable Run Preparation

This rollout does not implement resumable runs, but corpus rows should carry enough provenance to support a future stage ledger.

The future run-stage ledger should include:

- `run_id`
- `stage_id`
- `stage_type`
- `status`
- `input_hash`
- `output_hash`
- `output_artifact_ref_id`
- `idempotency_key`
- `retry_count`
- `failure_kind`
- `started_at`
- `completed_at`

Corpus fields that align with this:

- `first_seen_run_id`
- `first_seen_query_instance_id`
- `first_seen_stage_id`
- `first_seen_artifact_ref_id`
- `observation_id`
- `source_artifact_ref_id`

If a future run resumes after a crash, it should rebuild context from persisted stage outputs and corpus/flywheel rows. It should not require live process memory.

## Future Memory Preparation

This rollout does not implement personalized memory.

Memory-ready fields are included only to avoid losing provenance:

- `memory_eligible`
- `allowed_uses_json`
- `training_eligible`
- `export_eligible`
- `llm_ingestion_eligible`
- `consent_basis`
- `source_terms_ref`
- `pii_classification_version`
- `redaction_status`
- `sensitivity_json`
- `retention_policy`
- `source_kind`
- `source_ref`
- tenant/workspace identifiers.

Rules:

- default `memory_eligible = 0`;
- default `training_eligible = 0`;
- default `export_eligible = 0`;
- default `llm_ingestion_eligible = 0`;
- user explicit feedback may later produce memory-eligible records;
- runtime inference alone should not become long-term user memory by default;
- names, company names, salaries, locations, schools, and contact-like data must be marked sensitive;
- corpus text is untrusted external content and must later be quoted as data, not treated as model instruction;
- memory eligibility must stay separate from search, benchmark, training, export, and LLM-ingestion eligibility;
- memory extraction must be a separate future design.

## Cloud And Tenant Isolation Preparation

Even local-mode schema must include tenant and workspace scope.

Rules:

- every corpus row includes `tenant_id` and `workspace_id`;
- all query APIs require tenant/workspace filters;
- content hashes such as `snapshot_sha256` are not global authorization keys;
- artifact refs must be tenant-scoped before cloud deployment;
- future sandboxes are execution boundaries, not data ownership boundaries;
- service-managed storage remains the source of truth for recruiter/HR data;
- no cross-tenant corpus, memory, benchmark, or flywheel access is allowed.

First local implementation may use default values:

```text
tenant_id = "local"
workspace_id = "default"
```

but these fields must not be optional.

## Failure Handling

Corpus writes should be best-effort but explicit:

- if corpus write succeeds, continue run normally;
- if corpus write fails because of schema/configuration bugs, fail fast in development/test;
- if provider payload cannot be normalized, save raw payload artifact ref and record `normalization_status = 'failed'`, `normalization_failure_kind`, and `has_searchable_text = 0`;
- do not let a single malformed resume prevent recording other provider results;
- provider page retries and stage retries must use idempotency keys so they do not create duplicate observations;
- raw payload write failures should be counted separately from normalization failures;
- emit corpus write counts and failures into run artifacts.

For production, this may evolve into a stricter policy, but the first version should avoid losing an entire run because one provider candidate is malformed.

## Testing Strategy

Focused tests should cover:

1. Corpus schema creates required tables and enforces JSON validity.
2. JD documents require tenant/workspace and stable hashes.
3. Resume subjects and resume documents are separate rows.
4. Resume documents are unique by tenant/workspace/snapshot hash.
5. The same snapshot can exist in different tenants without authorization leakage.
6. Raw provider payload is not inlined into SQLite by default; DB rows store artifact ref, hash, and size.
7. Normalization failure still records raw payload ref, resume document metadata, and observation with `has_searchable_text = 0`.
8. Retrieval with mock CTS records all provider-returned resumes, not only scored candidates.
9. Resume observations preserve query/run/provider provenance.
10. Retried provider pages do not duplicate observations because of deterministic idempotency keys.
11. Run start writes `run_corpus_links`.
12. Corpus accumulation does not require eval.
13. Corpus export writes immutable JSONL through `ArtifactStore` logical names.
14. Flywheel export still contains no benchmark/qrels/corpus raw-payload output.
15. Static benchmark tables are not implemented in this rollout.
16. Memory, training, export, and LLM-ingestion fields default to not eligible.
17. Sensitivity and untrusted-content metadata is present on JD and resume docs.

## Acceptance Criteria

1. New runs upsert JD input rows into `CorpusStore`.
2. New retrieval results upsert every CTS/provider returned resume snapshot into `CorpusStore`.
3. New retrieval results insert one `resume_observations` row per provider-returned candidate.
4. Corpus rows include tenant/workspace scope.
5. Corpus rows include allowed-use, memory, sensitivity, retention, and untrusted-content metadata.
6. Raw provider payload is artifact-first and not inline in SQLite by default.
7. Provider retry or stage retry is idempotent for observations.
8. Normalization failures preserve raw payload ref and do not drop the provider observation.
9. Corpus assets can be materialized through immutable `ArtifactStore` logical artifacts.
10. Mutable corpus collections are not represented as corpus versions.
11. `FlywheelStore` remains the query rewriting data boundary and is not renamed or expanded into benchmark/corpus ownership.
12. `flywheel-export` remains query rewriting only and does not export corpus raw payloads, benchmark qrels, or benchmark reports.
13. Static benchmark pool/qrels tables are not implemented yet.
14. Resumable run execution is not implemented yet.
15. Personalized memory runtime is not implemented yet.
16. Tests prove corpus accumulation does not require eval.

## Rollout Order

1. Add corpus artifact kind and logical artifact names.
2. Add `CorpusStore` schema and primitive upsert APIs for JD docs, resume subjects, resume documents, observations, run links, collections, memberships, and exports.
3. Add raw payload artifact write helpers and artifact-ref recording.
4. Add resume normalization/searchable-text helper if no existing helper is clean enough.
5. Wire JD input upsert and `run_corpus_links` at run start.
6. Wire all provider-returned resume snapshots and idempotent observations at retrieval time.
7. Add corpus export/materialization as immutable `ArtifactStore` snapshots.
8. Update docs and env defaults.
9. Run focused tests and full suite.

## Open Decisions For Future Specs

These are intentionally not decided here:

- exact static benchmark qrel schema;
- whether qrels are human-only, judge-only, or hybrid;
- search engine backend;
- whether corpus DB remains SQLite in cloud or moves to Postgres plus object storage;
- resumable run stage ledger schema;
- human-in-the-loop intervention flow;
- personalized memory extraction policy.

## Summary

The next implementation should build the corpus asset layer now because it is the shared data foundation for future static benchmark, search, and product learning. It should not implement benchmark pools, qrels, durable execution, or personalized memory yet.

The architectural stance is stateless execution with durable DB/artifact facts. This keeps the system auditable, restartable, and compatible with future cloud tenant isolation without prematurely adopting a stateful workflow engine.
