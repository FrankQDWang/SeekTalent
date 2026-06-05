# Source Decoupling Correction Goal Progress

## Run Identity

- Goal pack: `seektalent_source_decoupling_correction_goal_pack`
- Started at: `2026-06-05T10:33:09+0800`
- Branch: `codex/source-decoupling-correction`
- HEAD at start: `d3f6aa5e06f0a8f46fdeb35eee887e9d41c2f08d`
- Origin main at start: `d3f6aa5e06f0a8f46fdeb35eee887e9d41c2f08d`
- Merge-base with origin/main: `d3f6aa5e06f0a8f46fdeb35eee887e9d41c2f08d`
- Worktree path: `/Users/frankqdwang/Agents/SeekTalent-0.2.4`
- Dirty state at start:
  - `M seektalent_setup_prototype.html` (unrelated; do not touch)
  - `?? .DS_Store` (unrelated; do not touch)
  - `?? docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md`
  - `?? docs/governance/agent-goals/source-decoupling-correction-2026-06.json`
  - `?? seektalent_source_decoupling_correction_goal_pack/*`
  - `?? seektalent_codex_goal_pack/*` (audit input)
  - `?? local-codex-intake-harness/*` (unrelated; do not touch)
- Stashes observed:
  - `stash@{0}: On main: pre-runtime-followup-main-doc-edits`
  - `stash@{1}: On main: pre-merge safety stash before liepin browser session probe`
  - `stash@{2}: On main: backup-runtime-multi-source-plan-docs-moved-to-worktree`
- Pack/checker preflight:
  - `correction goal pack present`
  - `tools/check_source_boundaries.py present`
  - `scripts/verify-source-decoupling.sh present`

## Current Phase

- Phase: Final verification complete
- Status: complete
- Latest successful command: `scripts/verify-source-decoupling.sh` (`172 passed in 2.09s`)
- Latest failed command: `uv run pytest` initially failed after the first full-suite run with 46 stale direct-runtime/source-specific test expectations; those expectations and the missing neutral default registry path in `_run_rounds` were corrected, then full pytest passed.
- Current blocker: none

## Phase Evidence

| Phase | Status | Files changed | Tests/checks | Evidence |
| --- | --- | --- | --- | --- |
| Run setup | complete | `docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md` | preflight commands; baseline `uv run python tools/check_source_boundaries.py`; baseline `uv run python tools/check_tach_baseline.py`; baseline `scripts/verify-source-decoupling.sh`; fresh `rg -n` inventory; `uv run tach check` | Current gates all pass despite concrete runtime/source coupling. Source-boundary checker exit 0; Tach baseline exit 0 with `Tach baseline ok: 0 current accepted failures`; verify-source-decoupling exit 0 with `164 passed`; Tach check exit 0 with `All modules validated!`. |
| Gate hardening | complete | `tools/check_source_boundaries.py`, `tools/check_tach_baseline.py`, `tests/test_source_boundaries.py`, `tests/test_tach_baseline.py` | Required red tests first failed; then `uv run pytest tests/test_source_boundaries.py -q` passed; `uv run ruff check tools/check_source_boundaries.py tests/test_source_boundaries.py`; `uv run ty check tools/check_source_boundaries.py tests/test_source_boundaries.py`; post-hardening red commands `uv run python tools/check_source_boundaries.py`, `uv run python tools/check_tach_baseline.py`, `scripts/verify-source-decoupling.sh` | Checker now catches concrete `seektalent.sources.cts/liepin` imports, `seektalent.source_adapters` imports, concrete source comparisons, membership tests, match/case, dispatch dicts, `.get`/indexing by concrete source id, source-specific budget/detail/default/function leakage. Tach baseline now fails on missing `seektalent.source_contracts`, runtime depending on `seektalent.sources`, and runtime/sources/providers cycles. |
| Fixture full-runtime proof | complete | `tests/test_source_registry_contract.py`, `src/seektalent/source_contracts/*`, `src/seektalent/sources/contracts.py`, `src/seektalent/sources/registry.py`, `src/seektalent/runtime/orchestrator.py`, `tach.toml` | Red: focused fixture runtime test failed because `WorkflowRuntime` lacked `source_registry`. Green: `uv run pytest tests/test_source_registry_contract.py::test_fixture_source_executes_through_workflow_runtime_without_runtime_source_branch -q` -> 1 passed; focused `uv run ruff check ...` -> pass. | `WorkflowRuntime(settings, source_registry=registry)` now executes a registered fixture source through neutral plan/request/result merge without runtime knowing that source id. |
| Registry injection | complete | `src/seektalent/source_contracts/*`, `src/seektalent/runtime/orchestrator.py`, `tach.toml` | Focused fixture runtime test passed; final full pytest passed. | Runtime consumes a neutral registry for registered sources. Direct `_run_rounds(...)` now derives the default source plan from `SourceRegistry`, matching `run_async(...)`, without concrete source branches. |
| CTS/Liepin migration | complete | `src/seektalent/source_adapters.py`, `src/seektalent/sources/cts/*`, `src/seektalent/sources/liepin/*`, `src/seektalent/liepin_smoke_cli.py`, `src/seektalent_ui/*` | `uv run pytest`, `scripts/verify-source-decoupling.sh`, source-boundary checker | Concrete CTS/Liepin budget/query/reason/provider rules moved to adapter/source/UI boundary code. Runtime depends on neutral contracts only. |
| Tach boundary repair | complete | `tach.toml`, `tools/check_tach_baseline.py`, `tests/test_tach_baseline.py` | `uv run python tools/check_tach_baseline.py` -> `Tach baseline ok: 0 current accepted failures` | Tach now models `source_contracts` as the thin neutral package and `source_adapters` as the concrete composition layer; runtime no longer depends on sources/providers/source_adapters. |
| Behavior regression | complete | runtime/source/provider/UI tests | `uv run pytest` -> `1855 passed in 71.78s`; focused failed-file rerun -> `204 passed in 10.27s` | Existing CTS/Liepin behavior coverage passes after updating tests to use neutral artifact/action names where runtime owns the output. |
| Full verification | complete | all touched production/test/gate files | final command table below | Acceptance commands are recorded and green. |

## Fresh Red Inventory

### Runtime imports concrete source implementations

Command:

```bash
rg -n "from seektalent\.sources\.(cts|liepin)|import seektalent\.sources\.(cts|liepin)" src/seektalent/runtime -S
```

Output:

```text
src/seektalent/runtime/source_lanes.py:26:from seektalent.sources.liepin.reason_codes import (
src/seektalent/runtime/orchestrator.py:114:from seektalent.sources.liepin.runtime_lane import (
src/seektalent/runtime/orchestrator.py:120:from seektalent.sources.cts.filter_projection import (
src/seektalent/runtime/public_events.py:6:from seektalent.sources.liepin.reason_codes import LIEPIN_PUBLIC_EVENT_REASON_MAP
```

### Runtime contains concrete source branch/dispatch forms

Command:

```bash
rg -n "source not in \{\"cts\", \"liepin\"\}|source != \"liepin\"|provider_name != \"liepin\"|_SOURCE_LANE_REQUEST_RUNNERS|source_plan_by_source\[\"cts\"\]|source_plan_by_source\[\"liepin\"\]|\(\"cts\", \"liepin\"\)" src/seektalent/runtime -S
```

Output:

```text
src/seektalent/runtime/source_lanes.py:348:        if self.source != "liepin":
src/seektalent/runtime/source_lanes.py:513:        if source not in {"cts", "liepin"}:
src/seektalent/runtime/orchestrator.py:366:_SOURCE_LANE_REQUEST_RUNNERS: Mapping[str, RuntimeSourceLaneRequestRunner] = {
src/seektalent/runtime/orchestrator.py:511:        runner = _SOURCE_LANE_REQUEST_RUNNERS.get(request.source)
src/seektalent/runtime/orchestrator.py:530:        if detail_lane_request.source != "liepin" or detail_lane_request.lane_mode != "detail":
src/seektalent/runtime/orchestrator.py:537:        selected_sources = base_run_artifacts.finalization_revision.selected_source_kinds or ("cts", "liepin")
src/seektalent/runtime/orchestrator.py:1622:                source_plan=source_plan_by_source["cts"],
src/seektalent/runtime/orchestrator.py:1627:                source_plan=source_plan_by_source["liepin"],
src/seektalent/runtime/orchestrator.py:2602:        if result.source != "liepin" or not result.candidate_store_updates or not result.provider_snapshots:
src/seektalent/runtime/retrieval_runtime.py:111:    if provider_name != "liepin":
```

### Runtime has source-specific budget/detail/reason/name leakage

Command:

```bash
rg -n "cts_|liepin_|opencli|RuntimeApprovedDetailLease" src/seektalent/runtime -S
```

Relevant output:

```text
src/seektalent/runtime/source_lanes.py:156:    max_cts_pages: int = 1
src/seektalent/runtime/source_lanes.py:157:    cts_page_size: int = 10
src/seektalent/runtime/source_lanes.py:158:    liepin_exploit_resume_target: int = 2
src/seektalent/runtime/source_lanes.py:159:    liepin_explore_resume_target: int = 1
src/seektalent/runtime/source_lanes.py:160:    liepin_card_page_size: int = 30
src/seektalent/runtime/source_lanes.py:161:    liepin_max_cards: int = 30
src/seektalent/runtime/source_lanes.py:162:    liepin_max_detail_recommendations: int = 6
src/seektalent/runtime/source_lanes.py:163:    liepin_max_detail_opens_per_run: int = 4
src/seektalent/runtime/source_lanes.py:315:class RuntimeApprovedDetailLease:
src/seektalent/runtime/source_lanes.py:349:            raise ValueError("RuntimeApprovedDetailLease currently supports only liepin.")
src/seektalent/runtime/source_lanes.py:544:def _build_cts_runtime_source_plan(
src/seektalent/runtime/source_lanes.py:563:def _build_liepin_runtime_source_plan(
src/seektalent/runtime/source_lanes.py:587:    "cts": _build_cts_runtime_source_plan,
src/seektalent/runtime/source_lanes.py:588:    "liepin": _build_liepin_runtime_source_plan,
src/seektalent/runtime/orchestrator.py:358:async def _run_liepin_source_lane_request(
src/seektalent/runtime/orchestrator.py:1125:        async def run_cts_lane(lane: RuntimeSourceLanePlan) -> RuntimeSourceLaneResult:
src/seektalent/runtime/orchestrator.py:1133:        async def run_liepin_lane(lane: RuntimeSourceLanePlan) -> RuntimeSourceLaneResult:
src/seektalent/runtime/orchestrator.py:1262:    async def _run_cts_source_lane(
src/seektalent/runtime/orchestrator.py:1415:    async def _run_liepin_source_lane_request(
src/seektalent/runtime/orchestrator.py:1669:    async def _execute_cts_source_round_adapter(
src/seektalent/runtime/orchestrator.py:1732:    async def _execute_liepin_source_round_adapter(
src/seektalent/runtime/orchestrator.py:2989:    def _executed_query_summaries(self, cts_queries: list[CTSQuery]) -> list[dict[str, object]]:
src/seektalent/runtime/retrieval_runtime.py:38:from seektalent.retrieval.query_builder import CTSQueryBuildInput, build_cts_query
src/seektalent/runtime/retrieval_runtime.py:328:        return "cts_exhausted"
src/seektalent/runtime/retrieval_runtime.py:933:                exhausted_reason = "cts_exhausted"
```

## Red-Green Evidence

| Violation class | Red command/result | Fix | Green command/result |
| --- | --- | --- | --- |
| runtime import of concrete `seektalent.sources.cts/liepin` | Baseline checker incorrectly passed: `uv run python tools/check_source_boundaries.py` exit 0 while `rg` shows concrete imports in `source_lanes.py`, `orchestrator.py`, and `public_events.py`. |  |  |
| runtime import of concrete `seektalent.source_adapters` | Not yet applicable; package split not introduced. Checker must forbid it before adapters are added. |  |  |
| runtime CTS/Liepin source whitelist or branch | Baseline checker incorrectly passed: `uv run python tools/check_source_boundaries.py` exit 0 while `rg` shows `source not in {"cts", "liepin"}`, `source != "liepin"`, `provider_name != "liepin"`, and concrete source defaults. |  |  |
| fixture source bypasses full `WorkflowRuntime` path | Existing registry tests are not full runtime proof; red full-runtime fixture test not added yet. |  |  |
| Tach permits runtime/sources/providers cycle | Baseline `uv run tach check` exit 0 with `All modules validated!`; `uv run python tools/check_tach_baseline.py` exit 0 with `Tach baseline ok: 0 current accepted failures`. |  |  |
| source-specific runtime budget/detail/reason leakage | Baseline checker incorrectly passed while `rg` shows `RuntimeSourceBudgetPolicy` CTS/Liepin fields, `RuntimeApprovedDetailLease currently supports only liepin`, and Liepin reason-code imports. |  |  |
| runtime concrete source dispatch maps or source-plan indexing | Baseline checker incorrectly passed while `rg` shows `_SOURCE_LANE_REQUEST_RUNNERS`, `{"cts": ...}`, `{"liepin": ...}`, and `source_plan_by_source["cts"/"liepin"]`. |  |  |
| gate hardening unit tests | Red commands failed before checker implementation: `test_runtime_concrete_source_import_is_reported`, `test_runtime_source_membership_whitelist_is_reported`, `test_runtime_concrete_source_dispatch_map_is_reported`, and `test_tach_config_has_no_runtime_source_provider_cycle`. Added additional red tests for source adapter imports, match/get, and budget/detail/default leakage. | Implemented AST/text scanner in `tools/check_source_boundaries.py`; added graph traversal checks to `tools/check_tach_baseline.py`; added source-boundary cycle test. | `uv run pytest tests/test_source_boundaries.py -q` -> 11 passed; focused ruff/ty passed. `test_tach_config_has_no_runtime_source_provider_cycle` remains expected-red against current product Tach graph until architecture migration. |
| post-hardening source-boundary command |  | Hardened checker implemented. | `uv run python tools/check_source_boundaries.py` -> expected exit 1 before product migration, reporting concrete source imports, concrete source comparisons, concrete source id indexing, and source-specific budget/detail/reason leakage. |
| post-hardening Tach command |  | Tach baseline graph traversal implemented. | `uv run python tools/check_tach_baseline.py` -> expected exit 1 before product migration, reporting missing `seektalent.source_contracts`, runtime depending on `seektalent.sources`, and runtime/sources/providers cycles. |
| post-hardening verify script |  | `scripts/verify-source-decoupling.sh` already runs source-boundary checker first. | `scripts/verify-source-decoupling.sh` -> expected exit 1 before product migration because hardened source-boundary checker fails. |
| full `WorkflowRuntime` fixture source proof | `uv run pytest tests/test_source_registry_contract.py::test_fixture_source_executes_through_workflow_runtime_without_runtime_source_branch -q` -> expected exit 1, `WorkflowRuntime.__init__() got an unexpected keyword argument 'source_registry'`. | Added neutral `seektalent.source_contracts` package, kept old `seektalent.sources.contracts/registry` as compatibility re-exports, added `source_registry` injection to `WorkflowRuntime`, and added a generic registered-source round executor that builds plans, creates neutral lane requests, converts lane results, and merges into `RunState`. | `uv run pytest tests/test_source_registry_contract.py::test_fixture_source_executes_through_workflow_runtime_without_runtime_source_branch -q` -> 1 passed; focused `uv run ruff check src/seektalent/source_contracts src/seektalent/runtime/orchestrator.py src/seektalent/runtime/source_lanes.py src/seektalent/runtime/source_filters.py src/seektalent/sources/provider_card_lane.py src/seektalent/providers/liepin/source_compiler.py tests/test_source_registry_contract.py` -> pass. |

## Final Correction Evidence

| Command | Purpose | Result |
| --- | --- | --- |
| `uv run pytest tests/test_api.py tests/test_cli.py tests/test_liepin_corpus_integration.py tests/test_runtime_audit.py tests/test_runtime_public_event_contract.py tests/test_runtime_state_flow.py -q` | Rerun the files that failed in the first full-suite attempt after neutral runtime wiring | pass: `204 passed in 10.27s` |
| `uv run pytest` | Full backend regression | pass: `1855 passed in 71.78s` |
| `uv run ruff check src tests tools` | Python lint/quality | pass: `All checks passed!` |
| `uv run python tools/check_source_boundaries.py` | Hardened runtime/source boundary gate | pass: exit 0 |
| `uv run python tools/check_tach_baseline.py` | Tach architecture gate | pass: `Tach baseline ok: 0 current accepted failures` |
| `scripts/verify-source-decoupling.sh` | Source-decoupling aggregate gate | pass: `172 passed in 2.09s` |
| `rg -n "\b(cts\|liepin)\b\|CTS\|Liepin\|search_cts\|cts_" src/seektalent/runtime` | Final runtime concrete-source text audit | pass by inspection: matches are false positives in `_conflicts_by_id`, `runtime_note_facts_from_events`, and `artifacts_path`; no concrete CTS/Liepin runtime implementation knowledge remains. |

## Final Architecture Notes

- `seektalent.source_contracts` remains a thin contract package: DTO/dataclass/protocol/registry/safe serialization only.
- Source-specific CTS/Liepin budget, query, provider, and reason-code behavior lives outside runtime in adapter/source/UI boundary code, primarily `src/seektalent/source_adapters.py` and source-specific modules.
- Runtime no longer imports `seektalent.sources.*`, `seektalent.providers.*`, or `seektalent.source_adapters`.
- Runtime-owned artifacts and actions now use neutral names such as `executed_queries`, `provider_exhausted`, and `source_search`; legacy test/controller payloads may still contain `search_cts` where they intentionally model old external inputs.
- A read-only subagent audit was run during execution; it confirmed the remaining risks were provider/retrieval/source cycles and runtime CTS/Liepin semantics, both addressed before final verification.

## PR CI Remediation Evidence

| Command/check | Finding | Fix | Current evidence |
| --- | --- | --- | --- |
| GitHub Actions `quality-python` / `uv run --group dev ty check src tests tools` | Ty rejected `Awaitable` source runners passed to `asyncio.TaskGroup.create_task`, broad `**runtime_kwargs`, and Liepin context `object` forwarding. | Narrowed `SourceLaneRunner` to coroutine shape, made `build_source_enabled_runtime` parameters explicit, and validated Liepin context inside the Liepin adapter. | `uv run ty check src tests tools` -> `All checks passed!` |
| GitHub Actions `pr-governance` | Base gate rejected the corrective manifest schema and reported new/changed oversized files. | Converted the corrective goal JSON to the base-recognized major-refactor schema, listed red files/deletion targets/layers, split safe serialization into `source_contracts/safe_serialization.py`, and kept `source_adapters.py` under the 600-line new-file limit. | Simulated clean PR governance evaluation returned `OK=True`; `runtime_lanes.py` is now 407 lines, `safe_serialization.py` is 223 lines, and `source_adapters.py` is 599 lines. |
| `scripts/verify-red-zone.sh` | Red-zone bad-smell gate flagged new `Any`/`cast` usage in adapter/runtime typing. | Replaced casts with alias simplification, runtime-checkable worker protocol narrowing, provider snapshot type filtering, and explicit Liepin context mapping validation. | `uv run ruff check src tests tools` -> pass; `uv run ty check src tests tools` -> pass; targeted source/runtime tests -> `103 passed in 2.03s`. |

### Post-Commit Final Verification

| Command | Result |
| --- | --- |
| `uv run pytest` | pass: `1855 passed in 81.42s` |
| `scripts/verify-source-decoupling.sh` | pass: `172 passed in 2.31s` |
| `scripts/verify-red-zone.sh` | pass: `288 passed`, Tach baseline ok, source-decoupling tests passed, Liepin worker checks passed |
| `uv run ty check src tests tools` | pass: `All checks passed!` |
| `uv run ruff check src tests tools` | pass: `All checks passed!` |
| `uv run python tools/check_tach_baseline.py` | pass: `Tach baseline ok: 0 current accepted failures` |
| `uv run python tools/check_source_boundaries.py` | pass: exit 0 |
| `scripts/verify-dev-workbench.sh` | pass: backend/workbench contract tests, Svelte checks, Vitest, build, and Playwright parity passed; mutable smoke skipped because `127.0.0.1:8012` was already owned by another process |
| `cd apps/web-svelte && bun run test` | pass: `31 passed`, `115 passed` |
| `cd apps/liepin-worker && bun test` | pass: `73 pass`, `0 fail` |

## Decisions

| Time | Decision | Reason | Files affected |
| --- | --- | --- | --- |
| `2026-06-05` | Use a new corrective goal pack instead of editing the old source-decoupling pack. | The old pack and progress ledger are audit evidence from the previous run; this correction needs a clean objective and harder acceptance gates. | `seektalent_source_decoupling_correction_goal_pack/*`, this ledger |
| `2026-06-05T10:33:09+0800` | Create branch `codex/source-decoupling-correction` from `main` before implementation. | `fw-build`/Superpowers execution rules prohibit starting implementation work on `main`; the user permitted subagents for execution but did not request working directly on `main`. | git branch only |
| `2026-06-05T10:33:09+0800` | Treat existing green source/Tach gates as red evidence of weak gates, not as completion evidence. | The correction pack says current gates are expected to pass despite real violations; fresh `rg` output confirms runtime concrete imports and source-specific branches. | `tools/check_source_boundaries.py`, `tools/check_tach_baseline.py`, `scripts/verify-source-decoupling.sh` |
| `2026-06-05T10:49:00+0800` | Introduce `seektalent.source_contracts` before moving concrete adapters. | Runtime needs a neutral registry contract target before CTS/Liepin can be moved out. Keeping old `seektalent.sources.contracts/registry` as re-exports avoids a broad call-site break while runtime starts importing the neutral module. | `src/seektalent/source_contracts/*`, `src/seektalent/sources/contracts.py`, `src/seektalent/sources/registry.py`, `tach.toml` |

## Known Risks

| Risk | Status | Mitigation |
| --- | --- | --- |
| Existing gates currently pass despite real runtime/source coupling. | confirmed | Phase 1 must add red tests/checks before product fixes. |
| Existing fixture source test is not a full runtime proof. | open | Phase 2 must add a `WorkflowRuntime` path test. |
| Tach currently models dependencies permissively enough to validate cycles. | confirmed | Tach repair must be part of acceptance, not a cleanup afterthought. |
| The corrective architecture now requires a `source_contracts` / `source_adapters` split. | open | Escalate if that split cannot satisfy runtime, provider, and Tach constraints together. |
| Checker false negatives caused the prior failure. | confirmed | Phase 1 red output must include concrete import, branch/dispatch, budget/detail/reason, and Tach-cycle violations before product migration. |
| Stale line numbers can mislead final evidence. | mitigated for setup | Phase 0 pasted fresh `rg -n` output into this ledger. |
| Tach baseline could hide the cycle again. | open | Do not add source/runtime/provider cycles to `tools/tach_baseline.json`; record them only as red evidence. |
