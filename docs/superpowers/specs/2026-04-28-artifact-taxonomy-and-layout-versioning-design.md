# Artifact Taxonomy And Layout Versioning Design

Date: 2026-04-28

## Context

SeekTalent currently writes ordinary single-run artifacts and several other container types into the same `runs/` root. The actual single-run naming is mostly consistent because `RunTracer` creates directories as `YYYYMMDD_HHMMSS_<id>`, but `runs/` has also accumulated benchmark containers, replay folders, debug directories, ad hoc imports, and other one-off names.

This creates four real problems:

- top-level `runs/` is no longer human-reviewable
- time ordering is broken because different container types share one root
- path strings have spread through runtime, evaluation, and diagnostics code
- future cloud storage, database indexing, and training export work would inherit a weak artifact identity model

There is one additional naming conflict already present in the repository:

- `artifacts/benchmarks/` is already used for maintained benchmark input JSONL files

That existing input path must remain distinct from benchmark execution output roots.

The immediate goal is not database work and not training export. The immediate goal is to stop artifact sprawl at the filesystem boundary and replace it with a stable artifact taxonomy that supports later object storage and database indexing.

This design intentionally treats artifact layout as a product boundary, not as an internal implementation detail.

## Goals

- Make single runs, benchmarks, replays, debug outputs, and imports first-class artifact kinds instead of mixed directories.
- Move all future write paths onto one explicit artifact taxonomy.
- Make top-level artifact roots time-sortable and machine-stable.
- Archive historical clutter without deleting evidence.
- Introduce manifest-based artifact addressing so future layout changes do not require repository-wide path rewrites.
- Keep old artifacts readable while making the new layout the only active write target.
- Restore human reviewability immediately.

## Non-Goals

- No database schema work in this change.
- No training dataset export or SFT/DPO pipeline work in this change.
- No rewrite of historical artifact contents.
- No long-term dual-write support between old and new layouts.
- No attempt to normalize every historical directory into the new internal structure.
- No change to evaluation semantics, retrieval semantics, or controller/scoring/reflection behavior.

## Decision Summary

This change is intentionally "one step" at the artifact boundary:

1. Introduce artifact boundary primitives before any physical layout switch.
2. Freeze legacy `runs/` as historical input only.
3. Move historical clutter into archive roots.
4. Make a new `artifacts/` tree the only active write target.
5. Enforce a strict artifact taxonomy with one root per artifact kind.
6. Replace free-form root naming with time-sortable IDs.
7. Require a manifest for every newly written artifact root.
8. Make code depend on logical artifact names, not hard-coded relative paths.

This is "one step" in architecture and write behavior. It is not "one step" in the sense of rewriting every historical file into the new internal layout.

## Artifact Taxonomy

New active artifact roots:

```text
artifacts/
  runs/
  benchmarks/              # maintained benchmark input JSONL, not execution outputs
  benchmark-executions/
  replays/
  debug/
  imports/
  archive/
```

Rules:

- `artifacts/runs/` contains only ordinary single workflow runs.
- `artifacts/benchmarks/` remains the maintained benchmark input directory and is not an execution-output root.
- `artifacts/benchmark-executions/` contains only benchmark execution containers.
- `artifacts/replays/` contains only replay containers.
- `artifacts/debug/` contains only debug containers.
- `artifacts/imports/` contains only import containers.
- `artifacts/archive/` contains historical material and is read-only after migration except for explicit archival operations.

The old `runs/` root stops being an active write target after this migration.

Collection-root naming is explicit, not derived by naive pluralization:

- artifact kind `run` -> collection root `runs/`
- artifact kind `benchmark` -> collection root `benchmark-executions/`
- artifact kind `replay` -> collection root `replays/`
- artifact kind `debug` -> collection root `debug/`
- artifact kind `import` -> collection root `imports/`

## Time-Sortable Root Naming

Every newly created root directory must use:

- `run_<ulid>`
- `benchmark_<ulid>`
- `replay_<ulid>`
- `debug_<ulid>`
- `import_<ulid>`

Human-readable labels must not be embedded in directory names.

Examples:

- `run_01JV1W4P9Q6ZP3Q1Q6Q6WQ5N8B`
- `benchmark_01JV1W4XX2R98R5J0ME9S1D0QK`

The old pattern `YYYYMMDD_HHMMSS_<uuid8>` is no longer used for new writes. Free-form names such as `phase_2_3b_*`, `global_benchmark_*`, `debug_openclaw`, or `jd_text` are forbidden as active artifact root names.

## Date Partitioning

Each runtime artifact collection root is date-partitioned:

```text
artifacts/<collection-root>/YYYY/MM/DD/<artifact-id>/
```

Example:

```text
artifacts/runs/2026/04/28/run_01JV1W4P9Q6ZP3Q1Q6Q6WQ5N8B/
```

Why both date partitioning and ULID:

- date partitions keep storage browsing manageable
- ULID provides stable, time-sortable identity
- lexicographic ordering remains meaningful within each partition

Partition dates are computed in UTC, not local wall-clock time.

## Single-Run Internal Layout

New single runs use this fixed structure:

```text
run_<ulid>/
  manifests/
    run_manifest.json
  input/
    input_truth.json
    input_snapshot.json
    requirement_extraction_draft.json
    requirements_call.json
    requirement_sheet.json
    scoring_policy.json
  runtime/
    run_config.json
    trace.log
    events.jsonl
    sent_query_history.json
    search_diagnostics.json
    term_surface_audit.json
  rounds/
    01/
      controller/
      retrieval/
      scoring/
      reflection/
      rescue/
    02/
      ...
  output/
    finalizer_context.json
    finalizer_call.json
    final_candidates.json
    final_answer.md
    run_summary.md
    judge_packet.json
  evaluation/
    evaluation.json
    replay_rows.jsonl
    round_01_judge_tasks.jsonl
    final_judge_tasks.jsonl
  assets/
    prompts/
    resumes/
    raw_resumes/
```

Important rule:

- round-local artifacts stop being flat files directly under `rounds/<round>/`
- they are grouped by producing subsystem

Example:

- `rounds/01/controller/controller_decision.json`
- `rounds/01/retrieval/query_resume_hits.json`
- `rounds/01/scoring/scorecards.jsonl`
- `rounds/01/reflection/reflection_advice.json`
- `rounds/01/rescue/candidate_feedback_decision.json`

## Logical Artifact Naming And Initial Mapping

Logical artifact names are stable identifiers. They must survive internal layout changes.

Naming convention:

- top-level single file: `input.input_truth`
- top-level collection: `assets.raw_resumes`
- round file: `round.01.retrieval.query_resume_hits`
- round collection: `round.01.scoring.input_refs`

Initial retrieval-flywheel-aligned mappings must be explicit. At minimum:

```json
{
  "runtime.sent_query_history": {
    "path": "runtime/sent_query_history.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "runtime.search_diagnostics": {
    "path": "runtime/search_diagnostics.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "runtime.term_surface_audit": {
    "path": "runtime/term_surface_audit.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "round.01.retrieval.second_lane_decision": {
    "path": "rounds/01/retrieval/second_lane_decision.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "round.01.retrieval.prf_policy_decision": {
    "path": "rounds/01/retrieval/prf_policy_decision.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "round.01.retrieval.query_resume_hits": {
    "path": "rounds/01/retrieval/query_resume_hits.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "round.01.retrieval.replay_snapshot": {
    "path": "rounds/01/retrieval/replay_snapshot.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "round.01.scoring.scorecards": {
    "path": "rounds/01/scoring/scorecards.jsonl",
    "content_type": "application/jsonl",
    "schema_version": "v1"
  },
  "round.01.reflection.advice": {
    "path": "rounds/01/reflection/reflection_advice.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "output.final_candidates": {
    "path": "output/final_candidates.json",
    "content_type": "application/json",
    "schema_version": "v1"
  },
  "evaluation.evaluation": {
    "path": "evaluation/evaluation.json",
    "content_type": "application/json",
    "schema_version": "v1"
  }
}
```

This mapping is not exhaustive, but the real implementation must define a complete logical-name registry for all active artifacts written by runtime, evaluation, diagnostics, and rescue flows.

## Manifest Requirement

Every new artifact root must contain a manifest:

- single runs: `manifests/run_manifest.json`
- benchmark containers: `manifests/benchmark_manifest.json`
- replay containers: `manifests/replay_manifest.json`
- debug containers: `manifests/debug_manifest.json`
- import containers: `manifests/import_manifest.json`

The manifest is required, not optional.

Minimum manifest fields:

- `manifest_schema_version`
- `artifact_kind`
- `artifact_id`
- `layout_version`
- `created_at`
- `updated_at`
- `completed_at`
- `display_name`
- `producer`
- `producer_version`
- `git_sha`
- `status`
- `logical_artifacts`

`logical_artifacts` maps stable logical names to actual relative paths plus metadata.

Manifest timestamps must use UTC `Z` format.

Example:

```json
{
  "manifest_schema_version": "v1",
  "artifact_kind": "run",
  "artifact_id": "run_01JV1W4P9Q6ZP3Q1Q6Q6WQ5N8B",
  "layout_version": "v1",
  "created_at": "2026-04-28T10:30:12Z",
  "updated_at": "2026-04-28T10:35:44Z",
  "completed_at": "2026-04-28T10:36:01Z",
  "display_name": "seek talent workflow run",
  "producer": "WorkflowRuntime",
  "producer_version": "0.6.1",
  "git_sha": "abcd1234",
  "status": "completed",
  "logical_artifacts": {
    "input.input_truth": {
      "path": "input/input_truth.json",
      "content_type": "application/json",
      "schema_version": "v1"
    },
    "round.01.retrieval.query_resume_hits": {
      "path": "rounds/01/retrieval/query_resume_hits.json",
      "content_type": "application/json",
      "schema_version": "v1"
    },
    "assets.raw_resumes": {
      "path": "assets/raw_resumes/",
      "content_type": "inode/directory",
      "collection": true
    },
    "output.final_candidates": {
      "path": "output/final_candidates.json",
      "content_type": "application/json",
      "schema_version": "v1"
    }
  }
}
```

### Manifest Lifecycle

The manifest is a live runtime file, not just a final summary.

Rules:

1. Artifact root creation immediately writes a manifest with `status = "running"`.
2. Every successful logical artifact write updates `logical_artifacts` and `updated_at`.
3. Successful completion sets `status = "completed"` and `completed_at`.
4. Failed completion sets `status = "failed"` and records a short failure summary when available.
5. File writes and manifest replacement should be atomic where practical.

## Artifact Resolver

New code must not locate artifacts by manually stitching relative path strings together.

Instead, artifact access must go through an artifact resolver that accepts:

- artifact root
- layout version
- logical artifact name

Examples of allowed lookups:

- `resolve("input.input_truth")`
- `resolve("output.final_candidates")`
- `resolve("round.01.retrieval.query_resume_hits")`
- `resolve_optional("evaluation.evaluation")`
- `resolve_many("round.*.retrieval.query_resume_hits")`

Examples of disallowed future patterns:

- `run_dir / "rounds" / "round_01" / "query_resume_hits.json"`
- `run_dir / "evaluation" / "evaluation.json"`
- `run_dir / "prompt_snapshots" / "judge.md"`

This resolver is the compatibility boundary that makes future directory moves cheap.

Resolver behavior must support both file descriptors and collection descriptors.

## Path Safety Rules

Manifest-provided paths are constrained:

- path must be relative to artifact root
- path must not be absolute
- path must not contain `..`
- resolver must not follow symlinks outside artifact root

This is part of the product boundary, not optional hardening.

## Legacy Archive Policy

Historical directories are not deleted by this project. They are archived by type.

Archive roots:

```text
artifacts/archive/
  legacy-runs/
  legacy-benchmarks/
  legacy-replays/
  legacy-debug/
  legacy-imports/
```

Classification rules:

- directories matching ordinary historical single-run naming move to `legacy-runs/`
- benchmark containers move to `legacy-benchmarks/`
- replay containers move to `legacy-replays/`
- debug-only directories move to `legacy-debug/`
- loose imports and uncategorized one-off inputs move to `legacy-imports/`

The archive operation is a move, not a rewrite.

Migration must be explicit and replayable:

1. dry-run classify existing top-level legacy directories
2. write `artifacts/archive/archive_migration_plan.json`
3. execute move only after the plan exists
4. write `artifacts/archive/archive_migration_result.json`
5. include `source_path`, `destination_path`, `artifact_kind`, `reason`, `moved_at`
6. do not overwrite existing archive destinations; collisions must fail or require an explicit suffix policy
7. migration must be idempotent

Historical material is preserved exactly as-is inside archive roots. The system does not attempt to convert legacy runs into the new internal layout in this phase.

## Read And Write Rules

After this project:

- new writes go only to `artifacts/...`
- historical top-level `runs/` entries are migrated into `artifacts/archive/...`
- the old `runs/` root is decommissioned as an active product root
- new benchmarks do not write under `runs/`
- new replays do not write under `runs/`
- new debug outputs do not write under `runs/`
- new imports do not write under `runs/`

There is no long-term dual-write mode. New code writes only the new layout.

Legacy reading is allowed through:

- explicit archive-aware readers
- manifest-aware readers for new artifacts

The repository may keep an empty `runs/` directory temporarily for local compatibility during migration, but runtime must not treat it as a write target after this project lands.

The legacy `runs/` root should contain explicit decommissioning markers:

- `runs/README.md`
- `runs/.decommissioned`

If runtime or benchmark code is pointed at legacy `runs/` as an active write root after migration, it must fail fast.

## Benchmark And Replay Relationships

Benchmark and replay containers are not parents of nested single-run directories.

Instead:

- each ordinary workflow execution remains a first-class `run` artifact under `artifacts/runs/...`
- benchmark containers reference child run artifact IDs
- replay containers reference source run or benchmark artifact IDs

Example benchmark manifest fragment:

```json
{
  "artifact_kind": "benchmark",
  "artifact_id": "benchmark_01JV1W4XX2R98R5J0ME9S1D0QK",
  "child_artifacts": [
    {
      "artifact_kind": "run",
      "artifact_id": "run_01JV1W4P9Q6ZP3Q1Q6Q6WQ5N8B",
      "role": "case_run",
      "case_id": "agent_jd_001"
    }
  ]
}
```

This keeps single runs independently diagnosable, replayable, and indexable.

## Partition Review Index

To keep ULID-based roots human-reviewable, each date partition maintains a small index file:

- `artifacts/runs/YYYY/MM/DD/_index.jsonl`
- `artifacts/benchmark-executions/YYYY/MM/DD/_index.jsonl`
- `artifacts/replays/YYYY/MM/DD/_index.jsonl`

Each row should include:

- `artifact_id`
- `created_at`
- `status`
- `display_name`
- `producer`
- `summary_logical_artifact`

This index is for fast human scanning. It does not replace manifests.

## Migration Strategy

This design is intentionally strict but bounded.

### Phase 0: Artifact Boundary Primitives

- define `ArtifactKind`
- define ULID-based artifact ID generation
- introduce `ArtifactStore`
- introduce `ArtifactResolver`
- define manifest schema and logical artifact naming registry
- make new writer code route through `ArtifactStore` instead of stitching paths directly

### Phase 1: New Active Layout

- create the new `artifacts/` root
- switch active single-run writes to `artifacts/runs/YYYY/MM/DD/run_<ulid>/`
- switch benchmark execution writes to `artifacts/benchmark-executions/...`
- switch replay, debug, and import writes to their new roots
- write running/completed/failed manifests
- write partition `_index.jsonl` files
- migrate active codepaths to logical artifact names and resolver-backed access

### Phase 2: Archive Migration

- dry-run classify old `runs/`
- emit `archive_migration_plan.json`
- move legacy material into `artifacts/archive/...`
- leave decommissioning sentinels in legacy `runs/`
- keep a narrow legacy archive reader path

### Phase 3: Cleanup And Enforcement

- add fail-fast protections against writes to legacy `runs/`
- tighten tests around migrated writer/reader modules
- update docs and reference material to the new roots and logical names

### Explicitly not part of this migration

- rewriting every historical artifact into the new internal structure
- changing historical JSON payload contents
- building the cloud database schema
- implementing training export datasets

## Consequences

Positive outcomes:

- top-level artifact browsing becomes human-reviewable again
- artifact identity becomes stable enough for later database indexing
- cloud object storage layout can mirror local artifact layout cleanly
- future internal file moves stop causing repository-wide path churn

Accepted cost:

- this change touches many path-producing and path-consuming codepaths
- manifest and resolver machinery add a small amount of explicit structure
- historical layout remains heterogeneous inside archive roots

That heterogeneity is acceptable because archive roots are read-only history, not the active write path.

## Testing Expectations

This project should prove the following:

1. New single runs write only under `artifacts/runs/YYYY/MM/DD/run_<ulid>/`.
2. Benchmark execution, replay, debug, and import containers each write only under their own roots.
3. New roots always contain a live manifest with lifecycle fields and logical artifact mappings.
4. Legacy archive migration classifies and moves historical top-level `runs/` directories without deleting them.
5. Migration produces plan and result files and is idempotent.
6. Active readers resolve required artifacts through manifest-based logical names instead of direct path stitching.
7. Runtime refuses to create new active artifacts under legacy `runs/`.
8. Migrated writer and reader modules do not introduce new direct `rounds/round_XX/...` path stitching outside artifact-boundary helpers.

## What This Project Does Not Do

To keep "one step" honest, this project deliberately does not do the following:

- It does not design or implement the long-term Postgres schema.
- It does not build SFT or DPO exports.
- It does not redesign evaluation logic.
- It does not rewrite old archived runs into new-format runs.
- It does not support arbitrary human-chosen root names.
- It does not allow `runs/` to remain an active mixed-purpose root.

## Recommendation

Proceed with this as the first concrete storage-architecture project before the cloud pilot work.

Reason:

- multi-provider retrieval can land on the current provider seam
- cloud service persistence can wait a little
- but artifact taxonomy chaos will actively slow every later step unless fixed now

The repository should leave this change with a clean rule:

active artifacts are typed, time-sortable, manifest-addressed, and written only under `artifacts/`.
