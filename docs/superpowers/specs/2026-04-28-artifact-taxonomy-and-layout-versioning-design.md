# Artifact Taxonomy And Layout Versioning Design

Date: 2026-04-28

## Context

SeekTalent currently writes ordinary single-run artifacts and several other container types into the same `runs/` root. The actual single-run naming is mostly consistent because `RunTracer` creates directories as `YYYYMMDD_HHMMSS_<id>`, but `runs/` has also accumulated benchmark containers, replay folders, debug directories, ad hoc imports, and other one-off names.

This creates four real problems:

- top-level `runs/` is no longer human-reviewable
- time ordering is broken because different container types share one root
- path strings have spread through runtime, evaluation, and diagnostics code
- future cloud storage, database indexing, and training export work would inherit a weak artifact identity model

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

1. Freeze legacy `runs/` as historical input only.
2. Move historical clutter into archive roots.
3. Make a new `artifacts/` tree the only active write target.
4. Enforce a strict artifact taxonomy with one root per artifact kind.
5. Replace free-form root naming with time-sortable IDs.
6. Require a run manifest for every newly written artifact root.
7. Introduce an artifact resolver so code depends on logical artifact names, not hard-coded relative paths.

This is "one step" in architecture and write behavior. It is not "one step" in the sense of rewriting every historical file into the new internal layout.

## Artifact Taxonomy

New active artifact roots:

```text
artifacts/
  runs/
  benchmarks/
  replays/
  debug/
  imports/
  archive/
```

Rules:

- `artifacts/runs/` contains only ordinary single workflow runs.
- `artifacts/benchmarks/` contains only benchmark containers.
- `artifacts/replays/` contains only replay containers.
- `artifacts/debug/` contains only debug containers.
- `artifacts/imports/` contains only import containers.
- `artifacts/archive/` contains historical material and is read-only after migration except for explicit archival operations.

The old `runs/` root stops being an active write target after this migration.

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

Each artifact kind is date-partitioned:

```text
artifacts/<kind>/YYYY/MM/DD/<kind>_<ulid>/
```

Example:

```text
artifacts/runs/2026/04/28/run_01JV1W4P9Q6ZP3Q1Q6Q6WQ5N8B/
```

Why both date partitioning and ULID:

- date partitions keep storage browsing manageable
- ULID provides stable, time-sortable identity
- lexicographic ordering remains meaningful within each partition

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

## Manifest Requirement

Every new artifact root must contain a manifest:

- single runs: `manifests/run_manifest.json`
- benchmark containers: `manifests/benchmark_manifest.json`
- replay containers: `manifests/replay_manifest.json`
- debug containers: `manifests/debug_manifest.json`
- import containers: `manifests/import_manifest.json`

The manifest is required, not optional.

Minimum manifest fields:

- `artifact_kind`
- `artifact_id`
- `layout_version`
- `created_at`
- `display_name`
- `producer`
- `status`
- `logical_artifacts`

`logical_artifacts` maps stable logical names to actual relative paths plus metadata.

Example:

```json
{
  "artifact_kind": "run",
  "artifact_id": "run_01JV1W4P9Q6ZP3Q1Q6Q6WQ5N8B",
  "layout_version": "v1",
  "created_at": "2026-04-28T18:30:12+08:00",
  "display_name": "seek talent workflow run",
  "producer": "WorkflowRuntime",
  "status": "completed",
  "logical_artifacts": {
    "input_truth": {
      "path": "input/input_truth.json",
      "content_type": "application/json"
    },
    "query_resume_hits.round_01": {
      "path": "rounds/01/retrieval/query_resume_hits.json",
      "content_type": "application/json"
    },
    "final_candidates": {
      "path": "output/final_candidates.json",
      "content_type": "application/json"
    }
  }
}
```

## Artifact Resolver

New code must not locate artifacts by manually stitching relative path strings together.

Instead, artifact access must go through an artifact resolver that accepts:

- artifact root
- layout version
- logical artifact name

Examples of allowed lookups:

- `resolve("input_truth")`
- `resolve("final_candidates")`
- `resolve("query_resume_hits.round_01")`

Examples of disallowed future patterns:

- `run_dir / "rounds" / "round_01" / "query_resume_hits.json"`
- `run_dir / "evaluation" / "evaluation.json"`
- `run_dir / "prompt_snapshots" / "judge.md"`

This resolver is the compatibility boundary that makes future directory moves cheap.

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

## Migration Strategy

This design is intentionally strict but bounded.

### Phase 1

- create the new `artifacts/` root
- archive historical `runs/` content by type
- switch active write roots to `artifacts/`
- switch new root naming to ULID-based names
- write manifests for all new artifact roots

### Phase 2

- introduce artifact resolver helpers
- migrate active codepaths away from hard-coded relative artifact strings
- keep a narrow legacy reader path for archived historical material

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

This project should prove five things:

1. New single runs write only under `artifacts/runs/YYYY/MM/DD/run_<ulid>/`.
2. Benchmark, replay, debug, and import containers each write only under their own roots.
3. New roots always contain a manifest with `layout_version` and logical artifact mappings.
4. Legacy archive migration classifies and moves historical top-level `runs/` directories without deleting them.
5. Active readers can resolve required artifacts through manifest-based logical names instead of direct path stitching.

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
