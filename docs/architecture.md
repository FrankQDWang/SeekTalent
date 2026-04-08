# Architecture

`HEAD` is no longer the old `v0.2` agent runtime. It is a `v0.3 phase 1` skeleton.

## What the codebase contains now

### Contracts

- `src/seektalent/models.py`
- Stable phase 1 payloads such as `SearchInputTruth`, `RequirementSheet`, `SearchExecutionPlan_t`, `RetrievedCandidate_t`, `ScoringCandidate_t`, `SearchExecutionResult_t`, and `SearchRunResult`

### Deterministic requirement normalization

- `src/seektalent/requirements/normalization.py`
- Builds `SearchInputTruth`
- Normalizes a `RequirementExtractionDraft` into a flat `RequirementSheet`

### CTS bridge

- `src/seektalent/retrieval/filter_projection.py`
- Maps `SearchExecutionPlan_t` into CTS-safe native filters
- Keeps unsupported fields outside native CTS payloads

### CTS clients

- `src/seektalent/clients/cts_client.py`
- Real CTS client and local mock CTS client
- Both return `RetrievedCandidate_t`

### Candidate projection

- `src/seektalent/retrieval/candidate_projection.py`
- Builds the fixed `raw_candidates -> deduplicated_candidates -> scoring_candidates` sequence

### Gated runtime surface

- `src/seektalent/runtime/orchestrator.py`
- `run` and `run_async` now raise a phase gate instead of pretending a full runtime exists

## What the codebase does not contain anymore

- controller loop
- reflection loop
- finalizer
- old scoring orchestration
- prompt registry and LLM model wiring
- web UI and UI API

## Spec ownership

- `docs/v-0.3/` is the only active spec
- `docs/v-0.2/` and `docs/v-0.1/` are archival only

## Related docs

- [Configuration](configuration.md)
- [CLI](cli.md)
- [docs/v-0.3/implementation-checklist.md](v-0.3/implementation-checklist.md)
