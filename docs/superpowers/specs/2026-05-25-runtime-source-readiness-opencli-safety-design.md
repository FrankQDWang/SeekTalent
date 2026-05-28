# Runtime Source Readiness And OpenCLI Safety Design

## Summary

The previous Liepin PI source-adapter slice moved the main retrieval path toward the unified CLI Runtime contract. This follow-up slice hardens the boundary before scoring:

```text
RequirementSheet + logical queries
-> CTS adapter + Liepin PI adapter
-> merge source updates once and compute source coverage
-> strict selected-source readiness gate
-> scoring
```

The current code waits for both selected sources to return a terminal result, but it still allows degraded continuation when Liepin is blocked, failed, partial, or empty. That is not the desired product contract for the current dual-source workflow. If the user selected CTS + Liepin, both sources must complete successfully before scoring begins.

This slice also moves several useful Workbench-era OpenCLI protections into the Runtime-owned PI child-agent path: randomized pacing, detail-tab idempotency, cleanup, and a structured card-read tool that lets PI judge card fit against the full `RequirementSheet` without brittle free-text parsing.

## Current Code Facts

- `src/seektalent/runtime/source_round_dispatch.py` uses `asyncio.TaskGroup`, so CTS and Liepin are already awaited before dispatch returns.
- `src/seektalent/runtime/orchestrator.py` currently marks source coverage as `degraded` when one selected source is blocked/failed/partial/empty, then still proceeds into scoring with available candidates.
- `src/seektalent_ui/workbench_routes.py` can mark a Liepin source run blocked during start probing, but still starts a Runtime sourcing job.
- `src/seektalent_ui/workbench_store.py` includes all source run kinds in `runtime_sourcing_jobs.source_kinds_json`, including source runs already marked blocked.
- `src/seektalent_ui/runtime_bridge.py` passes `context.job.source_kinds` into `runtime.run(...)`.
- `src/seektalent/providers/liepin/client.py` already injects OpenCLI env and declares low-level OpenCLI tools for PI when `liepin_browser_action_backend="opencli"`.
- `src/seektalent/providers/pi_agent/pi_external.py` already lists the `liepin.search_resumes` OpenCLI tools, including `seektalent_opencli_apply_liepin_filters`.
- `src/seektalent/providers/pi_agent/opencli_browser.py` currently has fixed waits such as 1s, 2s, and 3s. There is no randomized pacing config.
- `open_liepin_detail(...)` records an `open_detail` event before the detail page is definitely opened or captured. `_detail_ref_was_opened(...)` treats that event as idempotency proof, so a failed open can be misread as a reusable success.
- OpenCLI exposes useful primitives through the current helper path: `state`, `find`, `eval`, `tab list`, `tab new`, `tab select`, `get url`, `fill`, `click`, `scroll`, and `wait`. These primitives can be reused. OpenCLI does not provide SeekTalent/Liepin-specific candidate-card ranking or `RequirementSheet` screening semantics; those remain our helper/PI/runtime responsibility.

## Goals

- Enforce a strict selected-source readiness gate before scoring.
- If selected sources are `("cts", "liepin")`, do not score CTS-only results when Liepin is blocked, failed, partial, empty, or missing.
- Preserve single-source CTS runs for CLI/test scenarios where only CTS is selected.
- Make Workbench start behavior consistent with Runtime strict source readiness:
  - If a selected source is blocked before runtime start, do not start the runtime sourcing job.
  - Return a safe blocked-source response to the UI instead of creating a degraded runtime job.
- Add OpenCLI pacing at the helper/tool boundary so PI cannot accidentally run with fixed, bot-like timing.
- Keep pacing deterministic in tests while allowing random jitter in real runs.
- Fix Liepin detail-open idempotency:
  - Track pending, succeeded, failed, and captured states separately.
  - A failed or timed-out detail open must not be treated as a reusable success.
  - A successful opened-but-not-captured detail may be reused only if the owned detail tab is still present.
  - A captured resume may be reused by rank/source-run artifact.
- Ensure every PI `liepin.search_resumes` lifecycle closes source-run-owned detail tabs at terminal success, blocked, failed, partial, timeout, or repair failure.
- Add a structured, read-only OpenCLI tool for visible Liepin card extraction.
  - The tool returns provider rank, OpenCLI ref, visible card text, visible title/company/location/education/experience fragments when extractable, and safe reason codes.
  - The tool does not decide fit, does not click, does not open details, and does not inspect cookies/storage.
  - PI uses this card list plus full `RequirementSheet` to decide which cards deserve detail opens.
- Update PI prompt/tool contract so the search loop is explicit:
  - open/search page
  - apply Runtime-provided native filters
  - search keyword
  - read structured visible cards
  - preserve provider rank
  - exclude only clear mismatches
  - open details within target/budget
  - capture full detail resumes
  - finalize strict v2 envelope

## Non-Goals

- Do not redesign scoring, reflection, or finalization.
- Do not change requirement extraction fields.
- Do not build a generic browser automation framework.
- Do not add a compatibility fallback for old Workbench-specific source flows.
- Do not delete the old UI in this slice.
- Do not run live Liepin/OpenCLI automation as part of automated tests.
- Do not make PI own source readiness policy. Runtime owns readiness; PI owns page-level execution under a bounded task.

## OpenCLI Capability Boundary

Use OpenCLI primitives where they already exist:

- `state` for current page text and terminal state classification.
- `find --css` for locating card containers and stable result structures.
- `eval` for extracting safe DOM fields that OpenCLI state does not expose cleanly.
- `tab list`, `tab new`, `tab select`, and `tab close` for owned-tab lifecycle.
- `wait time` for browser waits, wrapped by our helper pacing policy.
- `fill`, `click`, and `scroll` for constrained page actions.

Implement SeekTalent-specific behavior in our helper, not in PI free-form instructions:

- Liepin visible-card extraction to JSON.
- Runtime-native filter application semantics.
- Detail-open idempotency state.
- Source-run-owned detail cleanup.
- Safe public/protected artifact shaping.

Do not rely on a hypothetical OpenCLI built-in "read candidates" or "screen candidates" command. The current codebase uses OpenCLI as a browser primitive provider; Liepin semantics are our adapter layer.

## Target Data Flow Before Scoring

```text
Runtime source plan
-> SourceRoundDispatchRequest(selected_sources, RequirementSheet, source query intents)
-> CTS source task
-> Liepin source task
   -> exploit PI child-agent
   -> explore PI child-agent
   -> each child-agent uses OpenCLI low-level tools
-> structured visible-card read
-> detail opens/captures/finalize
-> dispatch returns source results
-> merge source updates once and compute RuntimeSourceCoverageSummary
-> strict readiness check
   -> all selected sources completed with candidates: continue
   -> any selected source blocked/failed/partial/empty/missing: stop before scoring
-> scoring
```

## Acceptance Criteria

1. A focused runtime test proves CTS finishing first does not trigger scoring while Liepin is still running.
2. A focused runtime test proves selected CTS + Liepin with Liepin `blocked` stops before scoring.
3. A focused runtime test proves selected CTS + Liepin with Liepin `partial`, `failed`, `empty`, or `missing` stops before scoring.
4. A focused runtime test proves CTS-only selected runs still score CTS candidates.
5. Workbench tests prove a pre-start blocked Liepin source prevents runtime job creation when Liepin is selected.
6. Workbench tests prove source status and blocked reason stay visible to the UI.
7. OpenCLI helper tests prove randomized pacing is called around mutating actions and fixed waits are no longer the only timing behavior.
8. OpenCLI helper tests can disable randomness or inject deterministic jitter for stable unit tests.
9. OpenCLI helper tests prove failed detail opens do not make later calls return `reused=1`.
10. OpenCLI helper tests prove successfully captured detail resumes can be reused without opening duplicate tabs.
11. OpenCLI helper tests prove terminal cleanup closes only source-run-owned Liepin detail tabs.
12. PI external tests prove cleanup runs once on success, failure, timeout, and repair failure.
13. PI external/prompt tests prove `liepin.search_resumes` lists the structured visible-card extraction tool and instructs PI to use it before opening details.
14. TS extension tests prove the new card extraction tool is declared and is read-only.
15. Boundary tests prove public payloads do not expose raw browser output, cookies, storage, contact data, or local paths.
16. Focused tests pass:
    - `uv run pytest tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_state_flow.py tests/test_workbench_runtime_owned_execution.py tests/test_workbench_api.py tests/test_pi_opencli_browser.py tests/test_pi_external_agent.py tests/test_liepin_pi_executor.py tests/test_liepin_config.py tests/test_liepin_boundaries.py -q`
17. Static checks pass for changed Python files with `uv run ruff check <changed files>`.
