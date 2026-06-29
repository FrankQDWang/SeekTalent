# WTS Candidate Data And UI Repair Design

## Context

Workbench v2 can now drive the stable SeekTalent runtime, but the candidate surface still drifts from the WTS design assets. The current failure is not only visual. Runtime scorecards and Liepin detail data exist, but the runtime-control and v2 BFF projection do not expose enough typed fields for the WTS candidate list and detail drawer. The UI then falls back to generic cards, incomplete details, and status panels that do not match the product requirement.

This design covers a narrow vertical repair:

- Candidate list and candidate detail drawer data flow.
- WTS candidate list and drawer rendering.
- Runtime progress projection needed by the strategy graph.
- OpenCLI stale-reference recovery so one failed Liepin action does not incorrectly fail the whole run.

It does not change the general Workbench v2 conversation architecture, transcript schema, memory model, model provider routing, or runtime ranking algorithm.

## Source Of Truth

The visual requirements are the local WTS design assets:

- `WTS/候选人列表页面.png`
- `WTS/简历详情完整内容.png`

The detail image is a full-content design view for a right-side drawer. It is not a centered modal. Candidate avatars use a WTS-style round surname/initial mark with varied colors, not real profile photos.

The product runs locally for recruiters. For this UI slice, data should not be reduced to a safety-redacted projection. The BFF should expose the fields required by the WTS assets. If the BFF does not currently have those fields, fix the runtime/runtime-control projection. Do not fabricate missing fields in the frontend.

## Recommended Approach

Use a complete but narrow vertical slice:

1. Improve runtime/OpenCLI extraction only where needed for WTS candidate fields.
2. Project full WTS-ready candidate data into runtime-control.
3. Expose typed candidate summary and detail views from the Workbench v2 BFF.
4. Render the WTS candidate list, right-side detail drawer, and real runtime strategy progress from those typed views.
5. Add tests at each boundary so the data does not regress silently.

Frontend-only fixes are insufficient because key fields are currently dropped before the UI sees them. Restoring the old final LLM stage is also unnecessary for the match section because current scorecards already contain `reasoning_summary`, `strengths`, and `weaknesses`.

## Data Contract

### Candidate Summary

The v2 BFF should expose a typed candidate summary for each card:

- Stable candidate id.
- Display name or masked Liepin name.
- Avatar label derived from the displayed surname/name.
- Avatar color seed.
- Current title.
- Current company.
- Age.
- City/location.
- Education.
- Work years.
- Source label, such as `猎聘`.
- Match score or fit status when available.
- Detail availability/access state.

The frontend must not render engineering placeholders such as `Candidate abc123`, `unknown`, or raw ids when better backend data exists. If no usable name exists, use a neutral localized label rather than leaking internal ids.

### Candidate Detail

The v2 BFF should expose a typed WTS candidate detail view:

- Header: name, current status, current title, current company, recent activity, gender, age, city, education, work years.
- Match section:
  - `reasoning_summary` -> 推荐理由 / match summary.
  - `strengths` -> 候选人强项.
  - `weaknesses` -> 候选人弱项.
- Job intention: expected role, expected industry, expected city, expected salary.
- Work experience: timeline entries with date range, company, title, and description.
- Project experience: timeline entries with date range, project name, role, and description.
- Education experience: timeline entries with date range, school, major, and degree.
- Skill tags.
- Source link or source action when available.

If a field is genuinely unavailable after backend extraction, the BFF may omit it and the UI should hide that row or section. The frontend should not parse raw text or invent values.

## Runtime And Projection Flow

Runtime remains the owner of candidate facts. Workbench v2 should not read runtime internals directly.

1. Liepin/OpenCLI retrieval stores raw detail data, structured detail fields, and `fullText` where available.
2. Runtime normalization extracts WTS-needed fields from structured detail first.
3. If structured detail is missing but `fullText` contains the information, runtime may apply deterministic parsing for stable fields such as masked name, age, gender, city, degree, work years, current title/company, work history, education, projects, and skills.
4. Runtime scorecards remain the owner of match reasoning:
   - `reasoning_summary`
   - `strengths`
   - `weaknesses`
   - score/fit
5. Runtime-control candidate evidence stores the WTS-needed normalized profile and scorecard fields in its candidate payload.
6. Workbench v2 runtime service reads runtime-control and returns typed BFF models.
7. React renders only the BFF typed models.

This keeps the frontend simple and prevents a repeat of the current field-loss issue.

## OpenCLI Stale Reference Handling

`liepin_opencli_stale_ref` is an automation failure, not a valid “0 results” search outcome. It should be handled at the Liepin/OpenCLI boundary.

When a stale ref occurs:

1. Re-observe the current page.
2. Re-locate the same business target.
3. Retry the current idempotent action once.
4. If the retry succeeds, continue normally.
5. If it still fails, mark the source lane or round as blocked/partial with a clear runtime event.

If previous rounds already produced candidates and scorecards, the whole run should not fail solely because a later Liepin action hit a stale reference. The UI should preserve existing candidates and show the partial failure honestly.

## Runtime Progress And Strategy Graph

The strategy graph must reflect real runtime events and should not display future/final states before the backend emits them.

Each round has three WTS-visible node types:

- Keyword/query strategy.
- Observation after candidate retrieval/scoring has produced evaluable results.
- Reflection after the runtime has reflected on the round.

Nodes appear only when the corresponding backend data exists. Observation is not the same as “search request sent” or “search completed with raw results”. It should summarize evaluated candidates or scoring observations. Reflection should not be fabricated if the runtime did not produce it.

## Frontend Behavior

### Candidate List

The right-side candidate column should be present for runs where candidate data is relevant. It should not be replaced by a generic “运行状态” panel.

The column uses the WTS tabs:

- 候选人
- 思考过程

Candidate cards follow the WTS asset:

- White card with light purple border.
- Round surname/initial avatar with varied colors.
- Name and source badge.
- Title and company line.
- Age/city/education/work-years chips.
- Bottom-right detail button.

Cards are rendered in backend order. The frontend does not invent a “final shortlist” before the runtime produces one.

### Candidate Detail Drawer

Clicking “查看详情” opens a right-side drawer. The drawer follows the `简历详情完整内容.png` section order:

1. Header.
2. 匹配程度.
3. 求职意向.
4. 工作经历.
5. 项目经历.
6. 教育经历.
7. 技能标签.

The drawer is scrollable, uses WTS section headers, and closes without losing the current transcript or runtime state.

## Error Handling

- Backend extraction failure for one optional field hides that field; it does not fail the run.
- Missing detail for one candidate marks that candidate detail as unavailable; it does not break the candidate list.
- OpenCLI stale ref gets one re-observe/retry at the adapter boundary.
- Persistent stale ref becomes a partial source failure event.
- A run may fail only when the runtime cannot produce a valid state from remaining sources/candidates, not merely because one later source action failed after useful results already exist.

## Tests

### Backend

- Runtime-control candidate evidence includes scorecard `reasoning_summary`, `strengths`, and `weaknesses`.
- Workbench v2 candidate summary returns WTS card fields from runtime-control.
- Workbench v2 candidate detail returns WTS drawer fields from runtime-control.
- Deterministic parsing fills stable fields from Liepin `fullText` when structured detail is incomplete.
- `liepin_opencli_stale_ref` re-observes and retries once before producing a partial failure event.
- A later stale ref does not discard existing candidates or scorecards.

### Frontend

- Candidate list renders WTS-style cards with surname avatars and chips.
- Missing optional fields are hidden rather than rendered as placeholders.
- Clicking “查看详情” opens the right-side drawer with WTS sections.
- Match section renders recommendation, strengths, and weaknesses from BFF fields.
- Strategy graph nodes appear only after matching backend events.

### Integration

- Run reaches candidate display with real runtime-control candidates.
- Candidate detail drawer shows Liepin-derived detail fields and scorecard match fields.
- A stale ref in a later round preserves earlier candidates and shows a partial-failure progress note.

## Acceptance Criteria

- Candidate list visually matches `WTS/候选人列表页面.png` for layout, hierarchy, card structure, and right-side placement.
- Candidate detail appears as a right-side drawer matching `WTS/简历详情完整内容.png`, not a centered modal.
- Match section uses current scorecard fields and does not require restoring final LLM.
- BFF exposes typed WTS candidate fields; frontend does not parse runtime raw JSON or raw text.
- OpenCLI stale ref no longer causes a full run failure when useful candidates already exist.
- Runtime progress and strategy graph do not show future states before backend events exist.

## Out Of Scope

- Rewriting Workbench v2 transcript.
- Changing the general conversation agent architecture.
- Replacing OpenCLI.
- Adding sandbox skills or MCP registration.
- Restoring old final LLM markdown generation.
- Migrating historical conversations.
