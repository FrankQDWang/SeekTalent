# Liepin Detail-Backed Runtime Lane Design

## Goal

Make Liepin match the Runtime source contract used by CTS: each Runtime logical query returns complete resume candidates only. Liepin may browse search-result cards internally, but card summaries are not Runtime candidates and must not enter merge, scoring, or final Top 10.

## Current Code Facts

- Runtime already freezes a shared logical query bundle with `LogicalQueryDispatch`, including `query_instance_id`, `query_fingerprint`, `lane_type`, `query_role`, and `requested_count`.
- CTS receives the same query bundle and can directly return resume candidates because its provider surface is API-like.
- Liepin currently runs a card lane:
  - `run_liepin_logical_query_bundle(...)` calls `run_liepin_source_lane(... lane_mode="card")`.
  - `LiepinPiWorkerClient.search(...)` calls `PiLiepinExecutor.search_cards(...)`.
  - `seektalent_opencli_search_liepin_cards` returns `seektalent.pi_liepin_cards.v1`.
  - `map_liepin_worker_card(...)` produces `ResumeCandidate` with `score_evidence_source="card_only"`.
- Current tests intentionally assert that card mode does not open details. That is now the wrong product contract.
- Shared resume normalization already exists in `src/seektalent/normalization.py`. It is not CTS-specific. It already consumes full resume fields such as `fullText`, `rawText`, `workExperienceList`, `educationList`, `skills`, `currentTitle`, `currentCompany`, and location fields.

## Product Contract

1. Runtime only accepts complete resume candidates from Liepin.
   - A Liepin card summary may be used inside the Pi task to choose whether to open a detail page.
   - A card summary may be stored as protected/internal trace evidence.
   - A card summary must not become a `ResumeCandidate` in `RuntimeSourceLaneResult.candidate_store_updates`.
   - A card summary must not be normalized, scored, merged, or finalized as a candidate.

2. Each Runtime round keeps the existing 70/30 logical query budget.
   - Exploit query requested count is 7 by default.
   - Explore query requested count is 3 by default.
   - CTS and Liepin receive the same logical query bundle.
   - Liepin must honor `LogicalQueryDispatch.requested_count` as the number of complete resumes requested, not the number of cards to read.

3. Liepin runs one Pi task per logical query.
   - One Pi RPC task handles the exploit query and attempts to return 7 complete resumes.
   - One Pi RPC task handles the explore query and attempts to return 3 complete resumes.
   - These two Pi tasks run concurrently within the Liepin adapter for the same Runtime round.
   - Each Pi task receives exactly one keyword query, the query terms, native filters, and a complete-resume target count.
   - A Pi task lifecycle ends after that one logical query is exhausted, blocked, timed out, or has returned its target count.

4. Pi decides card-to-detail selection inside the task.
   - The task first searches with the supplied keyword query and native filters.
   - The task reads visible card summaries only to estimate likely fit against the supplied must-have and nice-to-have criteria.
   - The task opens detail pages for the most promising cards until it returns the requested count of complete resumes or hits budget/exhaustion.
   - Pi must prefer cards with stronger must-have support, then nice-to-have support, then provider order.
   - Pi must not invent new searches, filters, or requirements outside the Runtime-provided query/filter/request context.

5. Detail opening is still guarded.
   - The task must not click contact, chat, download, phone, email, purchase, payment, or account settings.
   - The task may open resume detail pages only as part of the complete-resume search task.
   - The generic OpenCLI click/state policy must still reject detail URLs and detail click labels outside this task-specific runner.
   - Detail opens are bounded by requested count plus a small card-screening budget.
   - If login, account mismatch, risk control, verification, or backend failure appears, the source result becomes source-scoped blocked/partial coverage.

6. Resume cleaning reuses the shared normalization path.
   - Liepin detail extraction maps the full detail page into `LiepinWorkerCandidateDetail`.
   - `map_liepin_worker_detail(...)` maps that into `ResumeCandidate`.
   - `ResumeCandidate.raw` uses the same field names already consumed by `normalize_resume(...)`: `fullText`, `workExperienceList`, `educationList`, `skills`, `currentTitle`, `currentCompany`, `locations`, and safe source metadata.
   - The Runtime detail payload is a redacted complete-resume payload. Raw provider snapshots stay behind protected artifact refs.
   - Direct contact fields, cookies, storage, authorization strings, raw HTML, and local paths must not appear in the Runtime detail payload.
   - No separate Liepin scoring or cleaning path is introduced.
   - `score_evidence_source` must be `detail_enriched`, never `card_only`, for candidates returned to Runtime.

7. Pi extension tools remain the main extension mechanism.
   - Do not convert the main Liepin workflow to generic MCP browser tools.
   - Use a small provider-specific Pi extension tool with a high-level domain contract.
   - Do not expose broad browser primitives as the main execution surface for this workflow.
   - A new skill is not required. The task-specific contract should live in the generated Pi task prompt plus the high-level tool schema. The existing skill file may be retained only as a safety policy file if the current Pi bootstrap still requires a `--skill` path; it must not contain card-only instructions that conflict with this design.

8. Public payloads stay business-safe.
   - Public Workbench state may show source status, counts, safe reason codes, and complete-resume counts.
   - Public payloads must not expose Pi/OpenCLI terms, selectors, raw provider payloads, cookies, local paths, or raw contact data.
   - Protected action trace artifacts may contain internal browser action detail for audit.

## Non-Goals

- Do not redesign CTS. CTS remains API-backed and directly returns complete candidates.
- Do not build a generic browser-agent framework.
- Do not add manual recruiter approval UI for each Liepin detail open in this slice.
- Do not add salary, industry, active status, gender, or advanced Liepin filters beyond the current native-filter adapter.
- Do not solve all graph node-detail UI completeness issues in this slice; keep that in `TODOS.md`.

## Acceptance Criteria

1. A unit test proves `run_liepin_logical_query_bundle(...)` starts exploit and explore Pi tasks concurrently for a two-query bundle.
2. A unit test proves exploit receives requested count 7 and explore receives requested count 3.
3. A unit test proves Liepin Runtime lane returns only detail-backed candidates with `score_evidence_source="detail_enriched"`.
4. A unit test proves card-only candidates are rejected or ignored at the Runtime boundary.
5. A unit test proves `normalize_resume(...)` can normalize a Liepin detail candidate using the same raw field shape as CTS/full-resume candidates.
6. A unit test proves the Pi prompt contract includes must-have/nice-to-have card-screening guidance and complete-resume-only output guidance.
7. A unit test proves the Pi extension exposes a high-level detail-backed search tool rather than requiring generic low-level browser tools in the main path.
8. A unit test proves generic OpenCLI browser actions still reject resume detail clicks/URLs while `search_liepin_resumes(...)` can open detail pages inside its bounded runner.
9. A unit test proves direct phone/email/cookie/storage/local-path data is rejected from returned Runtime detail payloads.
10. A unit test proves Pi RPC can capture the `seektalent_opencli_search_liepin_resumes` tool event envelope directly, matching the current card-search reliability path.
11. Focused Python tests pass for Liepin worker contracts, executor, runtime lane, normalization, and Pi boundaries.
12. `uv run ruff check` passes for changed Python files.
13. `uv run ty check` passes for changed Python files.
14. `uv build` passes.
15. `cd apps/web-svelte && bun run build` passes.
16. Real Chrome QA uses the complete historical `数据开发专家` input. It must show Liepin filters applied, at least one resume detail page opened when available, complete-resume evidence returned to Runtime, and no card-only Liepin candidates entering final scoring.
17. End-of-test cleanup closes agent-created tabs/windows and runs OpenCLI orphan cleanup.
