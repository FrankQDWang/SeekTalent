# Runtime Multi-Source Sourcing Contract Design

## Summary

SeekTalent needs a recruiter-grade multi-source sourcing runtime. The first target is CTS and Liepin running as parallel source lanes, feeding one shared candidate pool, merging likely duplicate people, selecting the freshest usable resume for each person, and returning a final Top 10.

Liepin differs from CTS because it has a two-stage provider flow:

1. Search returns ranked profile cards.
2. Runtime decides which cards are worth recommending for detail open within a budget.
3. A later approved detail lease allows the detail resume to be fetched.

The first implementation must keep the human-in-loop boundary ready, but does not build the manual card-review UI yet. Workbench can display recommendations and state; approval UI and manual card selection remain deferred platform work.

## Product Contract

Runtime owns:

- source planning for selected source kinds
- parallel full-run source lane scheduling
- source-lane terminal barriers
- source budget policy
- Liepin card filtering and detail recommendation ranking
- deterministic candidate identity merge
- canonical resume selection per identity
- source evidence preservation
- final scoring and Top 10 selection
- safe public source events and payloads

Workbench owns:

- source-run display state
- persisted source-run rows
- approved detail request, lease, budget, and audit state
- graph and notes rendering

Provider adapters and PI Agent own:

- bounded provider execution only
- card search
- detail fetch only when Runtime passes an approved detail lease
- raw provider artifact creation behind protected artifact refs

Provider adapters and PI Agent must not choose sources, approve detail opens, change budgets, finalize ranking, or expose raw resumes in public payloads.

## Current Code Facts

The current working tree already contains an initial source-lane implementation:

- `src/seektalent/runtime/source_lanes.py` defines source-lane plan/result/event/detail recommendation contracts and merge helpers.
- `src/seektalent/runtime/orchestrator.py` can run full source lanes and Workbench single source lanes.
- `src/seektalent/providers/liepin/runtime_lane.py` adapts Liepin search results into runtime lane results and detail recommendations.
- `src/seektalent_ui/runtime_bridge.py` routes Workbench Liepin card source runs through Runtime.
- `src/seektalent/evaluation.py` defines final shortlist size as `TOP_K = 10`.

The current implementation is not sufficient for this product contract:

- full-run source lanes are currently executed in source-plan order, not as true parallel lanes
- merge is still primarily keyed by `resume_id`, so CTS and Liepin records for the same person can remain split or overwrite the wrong surface
- source evidence is preserved, but not yet centered on a stable candidate identity
- canonical resume selection does not yet prefer the freshest and most complete resume across sources
- Liepin detail recommendation uses a simple matched-term score instead of provider-rank-first card policy plus detail-open budget allocation
- CTS multi-source lane behavior must be capped to the product budget of one page with page size 10

## Non-Goals

This spec does not build:

- a card-review or manual approval UI
- manual card selection before detail open
- automatic source strategy optimization
- lane health, cost, or quality dashboards
- a generic plugin system
- A2A transport
- DokoBot action execution without a trusted action manifest, capability probe, conformance tests, and audit trail

Those items should be documented as deferred follow-ups, not implemented inside this feature.

## Source Budget Policy

Runtime must create an explicit source budget policy for each run.

The first version uses these defaults:

- CTS: one page, page size 10
- Liepin cards: one search page with a configured card page size
- Liepin details: a configured max detail-open recommendation count per run
- final shortlist: Top 10

The budget policy is runtime-owned. Workbench may display budget state and persist approved detail leases, but it must not silently change the runtime budget for a lane.

Public budget payloads may include counts and reason codes only. They must not include provider credentials, approval secrets, cookies, raw profile payloads, or raw resumes.

## Parallel Source Lanes

A full Runtime run with `source_kinds=("cts", "liepin")` must start CTS and Liepin as independent lane-local executions. Each lane returns a `RuntimeSourceLaneResult` delta. Runtime waits for selected lanes to reach a terminal state before scoring and finalization.

Terminal lane statuses are:

- `completed`
- `partial`
- `blocked`
- `failed`
- `cancelled`

Blocked or failed lanes do not prevent finalization when at least one selected source produced candidates. Runtime must mark the finalization scope as degraded and record missing or blocked sources.

Workbench single-lane source runs remain non-finalizing. They may execute one lane and persist lane state, but they must not produce the full final Top 10 by themselves.

## CTS Lane Contract

CTS is the baseline source. In the multi-source runtime contract, CTS contributes one source lane:

- one page
- page size 10
- lane-local state
- no provider-specific detail approval stage
- source evidence for every returned candidate

CTS-only CLI behavior must remain compatible with existing product behavior unless explicitly invoked through the multi-source runtime contract.

## Liepin Card Policy

Liepin search returns provider-ranked cards. Runtime should use provider ranking as the primary ordering signal because the provider search engine is already applying relevance, recency, and marketplace signals that are not fully visible in the card.

Runtime may filter out cards that are clearly not worth opening:

- hard location mismatch when the job has a hard location constraint
- obviously wrong current title, target role, or function
- materially insufficient required years of experience when stated as a hard constraint
- materially insufficient required education when stated as a hard constraint
- excluded company, school, industry, or keyword
- stale or irrelevant work history that does not match the search intent

Runtime should not overfit the card text. When a card passes hard filters, provider rank remains the primary ordering signal. Soft card value only breaks ties or pushes down weak matches; it should not reorder a provider rank 1 card below a provider rank 8 card unless the higher-ranked card has a clear hard-negative reason.

Each detail recommendation must include structured, public-safe fields:

- stable recommendation id
- source evidence id
- source candidate resume id
- provider candidate key hash
- provider rank
- card policy rank
- hard-filter status
- safe reason codes
- budget reason code
- safe card summary ref when available

The card lane must not fetch detail resumes directly. It only emits detail recommendations.

## Detail-Open Boundary

Detail open is a two-stage boundary:

1. Runtime emits detail recommendations from card evidence.
2. Workbench store owns approval, lease, budget, and audit.

Runtime may execute a Liepin detail lane only from an approved detail lease. The detail lane must reject missing, expired, over-budget, wrong-source, or wrong-candidate leases. The Liepin provider adapter remains the final enforcement layer.

The first implementation must keep this boundary in contracts and tests, while leaving the actual human approval UI for later.

## Candidate Identity Merge

Runtime must merge source outputs by likely person identity, not just by resume id.

The merge model must preserve all evidence while choosing one display/canonical resume for scoring and final output.

Required concepts:

- `RuntimeCandidateIdentity`: stable identity record for a likely person
- `RuntimeSourceEvidence`: source evidence attached to an identity
- `RuntimeCanonicalResumeSelection`: deterministic choice of the best resume for the identity

Identity matching should use conservative rules:

- exact provider candidate key within the same provider is strong evidence
- exact protected contact hashes may be strong evidence when available
- exact name plus current company plus current title is medium evidence
- name plus school plus overlapping work chronology is medium evidence
- name-only or broad keyword overlap is weak evidence and must not auto-merge

Ambiguous matches must remain separate identities and record a safe conflict reason. Runtime should prefer false negatives over false positive merges because merging two different people corrupts recruiter output.

## Canonical Resume Selection

For each candidate identity, Runtime must select a canonical resume deterministically:

1. detail evidence beats card-only evidence
2. newer resume update timestamp beats older timestamp
3. current work marked as ongoing beats stale current-job data
4. more complete normalized resume beats sparse resume
5. source trust and provider rank break remaining ties

Canonical selection must not delete or overwrite source evidence. CTS evidence, Liepin card evidence, and Liepin detail evidence for the same identity must remain available to scoring, notes, graph rendering, and audit.

## Unified Scoring And Final Top 10

After all selected full-run lanes reach terminal state, Runtime merges lane deltas into identity records, selects canonical resumes, scores identities once, and returns Top 10.

The scoring context must be multi-source aware:

- a candidate may have evidence from CTS, Liepin card, and Liepin detail
- detail-enriched resumes should improve available context
- card-only candidates may still rank when they are strong enough
- missing or blocked source lanes must be reflected as coverage gaps, not silently hidden

Final output remains 10 candidates unless fewer candidates are available.

## Public Events And Payload Safety

All public payloads must use allowlisted serializers. Public paths include CLI JSON, Workbench graph state, Workbench notes, source-run rows, logs, and events.

Forbidden in public payloads:

- provider API keys
- provider tokens
- browser cookies
- approval secrets
- raw resumes
- raw HTML
- raw provider responses
- unredacted exception messages
- protected artifact contents

Runtime source events must include stable correlation fields:

- schema version
- runtime run id
- source plan id
- source lane run id
- source kind
- attempt
- event sequence
- event type
- safe counts
- safe reason codes
- safe artifact refs

Events may arrive out of order in Workbench. Workbench persistence must upsert by stable ids and avoid graph state moving backward.

## Acceptance Criteria

- CTS-only default behavior remains available.
- A full run with CTS and Liepin starts both selected source lanes in parallel.
- Full-run finalization waits for selected lanes to reach terminal state.
- If one selected source blocks and another returns candidates, Runtime finalizes with degraded source coverage.
- CTS multi-source lane uses one page with page size 10.
- Liepin card lane emits detail recommendations but does not fetch detail resumes.
- Liepin card recommendation ranking is provider-rank-first after hard filters.
- Liepin detail recommendations respect the per-run detail-open budget.
- Runtime can consume an approved Liepin detail lease through a separate detail lane.
- CTS and Liepin records for the same person merge into one identity when conservative identity evidence is strong enough.
- Ambiguous possible duplicates stay separate and record safe conflict reasons.
- Canonical resume selection prefers detail, freshness, completeness, and source trust deterministically.
- Source evidence is never collapsed or overwritten by canonical resume selection.
- Final shortlist returns Top 10 across all selected sources.
- Run notes and graph context include multi-source evidence context.
- Public serializers do not leak provider credentials, session secrets, cookies, approval secrets, raw resumes, raw HTML, or raw provider payloads.

## Deferred Follow-Ups

Record these outside the current implementation scope:

- human card-review UI
- manual detail-open approval UI
- manual source budget editing UI
- lane health, cost, and marginal quality metrics
- automatic source strategy optimization
- broader source capability descriptor
- trusted DokoBot action manifest and conformance suite
- future A2A bridge if PI Agent becomes out-of-process with independent lifecycle and identity
