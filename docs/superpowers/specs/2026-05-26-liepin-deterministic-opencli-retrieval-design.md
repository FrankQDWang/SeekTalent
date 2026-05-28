# Liepin Deterministic OpenCLI Retrieval Design

## Summary

Replace the Liepin Pi child-agent resume retrieval path with a deterministic OpenCLI source worker. The runtime should continue to see Liepin as a normal source adapter that returns detail-backed candidates through the existing `SearchResult` and runtime source-lane contracts. The browser execution itself should no longer depend on an LLM choosing tools, producing final JSON, or recovering from malformed agent output.

The new path keeps two artifacts for every captured resume:

- protected raw browser/detail evidence for debugging
- normalized resume text and structured detail payload for runtime scoring and node detail display

When the deterministic path has parity, remove the Liepin-specific Pi executor, Pi worker client, Pi prompt, and Pi readiness/config branches instead of preserving a second production path.

## Current Code Facts

- Runtime source dispatch already treats CTS and Liepin as source adapters through `src/seektalent/runtime/source_round_dispatch.py`.
- Liepin source execution enters `src/seektalent/providers/liepin/runtime_lane.py`, builds a `SearchRequest`, then calls a `LiepinWorkerClient`.
- The active local live Liepin path is currently `liepin_worker_mode="pi_agent"` in `src/seektalent/providers/liepin/client.py`.
- `LiepinPiWorkerClient` in `src/seektalent/providers/liepin/pi_worker_client.py` calls `PiLiepinExecutor.search_resumes()`.
- `PiLiepinExecutor` in `src/seektalent/providers/liepin/pi_executor.py` builds a `liepin.search_resumes` JSON task and asks a Pi RPC child agent to drive browser tools.
- `src/seektalent/providers/pi_agent/opencli_browser.py` already contains deterministic OpenCLI primitives:
  - `status()`
  - `open_liepin_tab()`
  - `state()`
  - `fill()`
  - `click()`
  - `apply_liepin_native_filters()`
  - `extract_visible_liepin_cards()`
  - `open_liepin_detail()`
  - `capture_liepin_detail_resume()`
  - `finalize_liepin_resumes()`
- The repeated production failures came from the Pi orchestration layer, not from the runtime graph contract:
  - browser details opened but no terminal envelope reached runtime
  - `agent_end` / final JSON failures lost already captured artifacts
  - short idle/agent timeouts turned slow browser retrieval into blocked runs
  - readiness depended on a mixed Pi/OpenCLI launch path
  - runtime status and graph nodes received contradictory source states

## Goals

- Add a deterministic `opencli` Liepin worker mode.
- Use OpenCLI browser primitives directly from Python without an LLM tool-selection loop.
- Keep the runtime-owned source adapter contract unchanged for callers:
  - source kind stays `liepin`
  - source lane returns candidates through `RuntimeSourceLaneResult`
  - provider output maps into existing `SearchResult`
  - runtime graph and frontend stay generic renderers
- Keep CTS budget unchanged:
  - total 10
  - exploit 7
  - explore 3
- Keep Liepin budget explicit:
  - first round has exploit only, so Liepin captures 2 resumes
  - second and later rounds capture exploit 2 and explore 1
- Select Liepin cards deterministically:
  - use provider/page rank
  - skip only cards that are structurally unusable or exact duplicates
  - do not make semantic fit decisions in the source retriever
- Capture full detail pages up to the lane budget.
- Persist raw and normalized artifacts for every captured detail page.
- Feed normalized resume text into existing candidate mapping and scoring.
- Replace short retrieval timeouts with long, bounded browser leases plus per-action waits and observable progress.
- Remove Liepin-specific Pi code after deterministic OpenCLI passes tests and live smoke validation.

## Non-Goals

- Do not change CTS retrieval behavior.
- Do not change requirement extraction, query generation semantics, scoring, reflection, or finalizer runtime behavior.
- Do not add LLM calls to Liepin retrieval.
- Do not add a frontend-specific compatibility path.
- Do not change the runtime graph contract.
- Do not change database schema unless an existing test exposes a required source-status field mismatch.
- Do not make OpenCLI run arbitrary browser JavaScript, read cookies, download files, contact candidates, or expose direct contact details.
- Do not use deterministic source retrieval to decide whether a candidate is a good semantic match for the job.

## Target Architecture

```text
Runtime source round
-> SourceRoundDispatchRequest
-> Liepin runtime source lane
-> LiepinOpenCliWorkerClient
-> LiepinOpenCliResumeRetriever
-> OpenCliBrowserRunner
-> SearchResult
-> RuntimeSourceLaneResult
-> merge / scoring / runtime graph
```

The old path is removed after parity:

```text
Runtime source round
-> LiepinPiWorkerClient
-> PiLiepinExecutor
-> PiRpcAgentClient
-> LLM child agent
-> OpenCLI tools
```

## Deterministic Retrieval Flow

For each Liepin logical query lane:

```text
status check
-> open/reuse owned Liepin search tab
-> wait for search page readiness
-> fill keyword query
-> submit search
-> apply native filters when provided
-> extract visible candidate cards with detail refs
-> choose the first N provider-ranked usable cards
-> open each detail ref
-> wait until resume detail markers are visible
-> capture raw detail state
-> normalize detail text
-> write raw + normalized artifacts
-> finalize a deterministic resume envelope
-> map to LiepinResumeSearchResponse
-> map to SearchResult
```

Terminal blocked states remain explicit and public-safe:

- `liepin_opencli_login_required`
- `liepin_opencli_identity_intercept`
- `liepin_opencli_risk_page`
- `liepin_opencli_extension_disconnected`
- `liepin_opencli_status_unavailable`
- `liepin_opencli_timeout`
- `liepin_opencli_detail_not_opened`
- `liepin_opencli_malformed_state`

## Raw And Normalized Resume Artifacts

Every opened detail page writes both raw and normalized artifacts.

Protected raw artifact:

```json
{
  "schema_version": "seektalent.liepin_opencli_detail_raw.v1",
  "source_run_id": "plan-liepin:round:1:lane:1",
  "provider_rank": 1,
  "captured_at": "2026-05-26T00:00:00+08:00",
  "page_text": "bounded raw visible browser text",
  "page_url_hash": "sha256:..."
}
```

Protected normalized artifact:

```json
{
  "schema_version": "seektalent.liepin_opencli_detail_normalized.v1",
  "source_run_id": "plan-liepin:round:1:lane:1",
  "provider_rank": 1,
  "full_text": "resume-only text after deterministic noise removal",
  "current_title": "Data Engineer",
  "current_company": "Example Inc",
  "work_experience_list": [],
  "education_list": [],
  "skills": [],
  "locations": []
}
```

The candidate public fields and runtime graph node details use the normalized resume text. The raw artifact is for debugging and must stay protected.

## Noise Removal Policy

The normalized parser may remove page chrome and non-resume text:

- navigation labels
- search controls
- filter labels
- recommendation panels
- job ads
- buttons
- login/risk banners
- contact/download/payment prompts
- duplicated card text
- duplicated section headings

The normalized parser must not remove resume-relevant content merely because it looks weak for the job. Source retrieval is allowed to decide that a block is not resume text; it is not allowed to decide that a person is a poor match.

## Timeout And Progress Policy

Do not use a short global agent timeout for Liepin retrieval. The browser flow must remain bounded, but the bound belongs to the deterministic browser layer:

- keep a long OpenCLI task timeout, default 900 seconds
- keep a bounded detail-open timeout, default 90 seconds
- keep per-action waits small and explicit
- emit source-lane events as each stage progresses
- rely on runtime cancellation or session stop for user interruption

If a detail page stalls after one or more resumes were captured, return `partial` with the captured resumes instead of discarding the lane.

## Cleanup Requirements

After the deterministic OpenCLI path is wired and verified:

- remove `liepin_worker_mode="pi_agent"` from active live worker modes
- remove `LiepinPiWorkerClient`
- remove `PiLiepinExecutor`
- remove the Liepin Pi prompt file
- remove Liepin Pi config fields and frontend labels that can no longer occur
- keep generic OpenCLI browser code only if it is still used by the deterministic worker
- do not keep both Pi and deterministic OpenCLI as production alternatives

## Acceptance Criteria

- Creating a workbench session does not require a Pi child agent for Liepin retrieval.
- Liepin first round captures at most 2 detail-backed resumes from provider-ranked cards.
- Liepin second and later rounds capture at most 2 exploit resumes and 1 explore resume.
- If OpenCLI opens detail pages and captures text, runtime receives candidates without needing an LLM final JSON.
- Runtime graph source nodes show candidate counts that match the backend source lane result.
- Node details can display captured Liepin candidates from normalized resume text.
- Slow Liepin detail pages do not force CTS-only premature advancement while Liepin is still running.
- Login/risk/identity states are recoverable blocked states, not malformed output failures.
- No active source retrieval code references `PiLiepin`, `LiepinPi`, `liepin_pi`, or `seektalent.pi_liepin`.
- Tests and cleanup scans pass.
