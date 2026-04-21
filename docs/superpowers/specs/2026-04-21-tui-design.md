# SeekTalent TUI Design

Date: 2026-04-21

## Goal

Add a terminal-first interactive entry for SeekTalent that matches the existing terminal experience in `/Users/frankqdwang/Agents/SeekTalent/src/seektalent/tui.py`, while using the current `SeekTalent-0.2.4` runtime and artifacts.

The TUI is for non-technical business users watching a search run. It should make the search process readable: what keywords were selected, what happened to the candidate pool, and how reflection changes the next round.

## Confirmed Decisions

- `seektalent` with no arguments opens the interactive TUI when attached to a TTY.
- Direct CLI workflows move under `seektalent exec`, for example `seektalent exec run ...`, `seektalent exec doctor`, and `seektalent exec benchmark ...`.
- The TUI input flow is three steps: `Job Title`, `Job Description`, then optional `Notes`.
- Submitted input remains in the transcript exactly once. The TUI must not show only the last three lines and then print a separate full echo below it.
- Each round shows a business summary, not every candidate row.
- Technical runtime actions are visible but visually secondary, using dim text.
- Controller and reflection thinking states show a Codex-like shimmer status line. When no LLM is thinking, the latest business trace status gets the same moving highlight.

## User Experience

The session starts with the same simple terminal framing as the old SeekTalent TUI: a small intro block, then a bottom composer. The composer uses:

- `Enter` to submit
- `Ctrl+J` to insert a newline
- `Ctrl+C` to quit

The transcript shape is:

```text
Job Title
AI Agent 工程师

Job Description
<full pasted JD>

Notes
<full pasted notes, or skipped>
```

After the input step, the composer disappears and runtime trace output continues below the submitted text.

## Business Trace Format

Each search round renders one block:

```text
第 1 轮 · 关键词：AI agent、LLM

本轮候选人情况
搜到 10 人，新增 10 人，10 人进入评分；fit 5 人，not fit 5 人。
Top pool 当前 10 人，本轮新增 10 人，无候选人被挤出。
代表候选人：
- 算法工程师 · 深圳 · 5 年：有 Agent/LLM 项目，但框架经验待确认
- Python 后端 · 深圳 · 4 年：工程能力可用，但 Agent 深度不足
- 产品经理 · 深圳 · 5 年：背景不匹配，已作为低优先级

反思和下一轮变更
这一轮覆盖面足够，但人群混杂。下一轮保留 AI agent、LLM，加入 LangChain/RAG 等更明确的框架词，提高相关度。
```

Technical lines appear in dim text between business blocks:

```text
已运行 controller-r01：选择首轮搜索词
已运行 search_cts：1 页，10 条原始候选
已运行 scoring：10 人评分完成
已运行 reflection-r01：生成下一轮建议
```

The business block is the primary display. Artifact paths, hashes, adapter internals, and schema pressure fields stay out of the main TUI. They remain available in run artifacts for debugging.

## Data Sources

The current runtime already writes the needed data:

- `events.jsonl` for stage timing and small real-time events
- `search_diagnostics.json` for cross-round query, search, scoring, and reflection summaries
- `rounds/round_xx/round_review.md` for human-readable round detail
- `final_candidates.json` and `final_answer.md` for final output

For real-time TUI rendering, the runtime should expose a small optional `progress_callback`. The callback emits structured progress events near the existing `RunTracer.emit(...)` calls. The TUI consumes these events directly instead of polling files.

The final business trace can be assembled from runtime state as each round completes:

- Round keyword selection: `RoundRetrievalPlan.query_terms`
- Candidate situation: `SearchObservation`, scoring counts, fit/not-fit counts, top pool changes
- Representative candidates: 3-5 short summaries from new candidates plus scoring summaries where available
- Reflection and next change: `ReflectionAdvice.reflection_summary`, suggested activate/drop terms, and the next-step text

## Architecture

Add a small TUI layer and keep runtime behavior unchanged.

### `src/seektalent/tui.py`

Responsibilities:

- Render the intro and three-step composer.
- Preserve submitted user input in the transcript once.
- Run `run_match_async(...)`.
- Render dim technical events.
- Render round business trace blocks.
- Render final shortlist.
- Render one bottom status line with shimmer.

This module should stay presentation-only. It should not decide search strategy, scoring, reflection behavior, or artifact schema.

### `src/seektalent/progress.py`

Defines a small `ProgressEvent` dataclass or Pydantic model with:

- `type`
- `message`
- `timestamp`
- `round_no`
- `payload`

The model should be direct and close to usage. It should not become a generic event framework.

### `src/seektalent/runtime/orchestrator.py`

Add an optional `progress_callback` to `WorkflowRuntime.run(...)` and `WorkflowRuntime.run_async(...)`.

Emit progress events around existing runtime milestones:

- requirements started/completed
- controller started/completed
- CTS search completed
- scoring completed
- reflection started/completed
- finalizer started/completed
- run completed/failed

Do not change retrieval, scoring, reflection, finalizer, or evaluation decisions.

### `src/seektalent/api.py`

Pass `progress_callback` through `run_match(...)` and `run_match_async(...)`.

### `src/seektalent/cli.py`

Routing:

- No args + TTY: launch TUI.
- No args + non-TTY: print help and exit without hanging.
- `exec`: delegate to the existing CLI command parser.
- Other top-level command words should guide users to `seektalent exec <command>`.

### `pyproject.toml`

Add the dependencies used by the old TUI:

- `prompt-toolkit`
- `rich`

## Shimmer Status Line

The TUI maintains one status line at the bottom.

State mapping:

- `controller_started`: `controller 正在思考`
- `reflection_started`: `reflection 正在思考`
- `requirements_started`: `正在分析岗位需求`
- `finalizer_started`: `正在整理最终推荐`
- scoring events: `正在评分候选人`
- no active LLM stage while running: latest short business trace status
- completed or failed: stop animation

The shimmer is a terminal-friendly approximation of the Codex moving highlight: a light band moves left to right across the status text. Use a lightweight refresh mechanism such as `rich.live.Live` or prompt-toolkit refresh. Do not redraw the full transcript on each tick.

If the terminal cannot support dynamic refresh, the TUI falls back to a dim static status line.

## Error Handling

Keep failure behavior direct:

- `Ctrl+C` exits with code `130` and prints an interrupted message.
- Empty `Job Title` or `Job Description` asks the user to paste a non-empty value.
- Runtime exceptions print a concise failure line and leave any run artifacts already written.
- Missing credentials or invalid settings should use the existing CLI/runtime error messages.

No retry chains, fallback model routing, or hidden recovery behavior are part of this TUI work.

## Tests

Add focused tests:

- CLI routing:
  - no args in TTY launches TUI
  - no args outside TTY prints help
  - `seektalent exec run ...` reaches the existing run command behavior
  - legacy top-level command words point to `seektalent exec <command>`
- TUI transcript:
  - fake prompt inputs for job title, JD, and notes render exactly once
  - empty notes can be skipped
  - JD and notes are not truncated into a separate full echo
- Business trace rendering:
  - fake progress events render round keywords, search counts, scoring counts, fit/not-fit counts, top pool changes, representative candidates, and reflection next-step text
  - technical events render dim/secondary text
- Runtime progress:
  - existing stub runtime fixtures emit controller/search/scoring/reflection/finalizer progress events with enough payload to render the TUI
- Regression:
  - existing CLI, API, and runtime audit tests still pass for non-TUI workflows

## Out Of Scope

- No browser UI changes.
- No Textual/curses full-screen app.
- No changes to controller, reflection, scoring, finalizer, CTS retrieval, or prompts.
- No changes to run artifact schemas beyond optional progress events.
- No full historical run browser.
- No candidate-by-candidate live stream in the main trace.

## Acceptance Criteria

- Running `seektalent` in a TTY opens the TUI.
- Running `seektalent exec run ...` preserves the existing CLI workflow.
- The three submitted inputs stay visible in the transcript exactly once.
- Each completed round renders the Excel-style business trace: keywords, candidate situation, and reflection/next change.
- Technical actions are visible but visually secondary.
- Controller/reflection thinking and idle running states show a moving shimmer when the terminal supports it.
- Final shortlist is printed after completion.
- Existing non-TUI tests remain green.
