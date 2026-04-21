# SeekTalent Full-Screen TUI Follow Mode Design

Date: 2026-04-21

## Goal

Refine the SeekTalent TUI experience after the first interactive release. The main goals are:

- Stop live status refreshes from pulling the user's terminal scrollback back to the bottom.
- Let users scroll up through run history without losing their place.
- Restore automatic following when the user returns near the bottom.
- Remove duplicate Job Title and Notes display.
- Speed up the shimmer status animation.
- Add a short LLM-generated resume-quality comment after the top representative candidates and before reflection.

This is a TUI-only refinement. It must not change controller, scoring, reflection, finalizer, CTS retrieval, or ranking decisions.

## Confirmed Decisions

- Use a true full-screen `prompt-toolkit` application instead of streaming `rich.console.Console.print(...)` plus `rich.live.Live`.
- The application owns the transcript viewport, scroll offset, follow mode, input area, and shimmer status line.
- User scrolls up: follow mode turns off and the visible transcript stays fixed while new events continue appending.
- User scrolls back near the bottom: follow mode turns on again and new events auto-follow.
- Keep the shimmer effect, but make it faster and render it in the TUI status area, not via stdout.
- Remove duplicate submitted input lines. Job Title, JD, and Notes each appear once in the transcript.
- Add a short resume-quality comment after the representative top candidates and before reflection.
- Generate the quality comment with `deepseek-chat`.
- If quality-comment generation fails, do not fail or stop the search. Show a dim technical message and continue to reflection.

## Current Problems

### Terminal Scrollback Jumps

The current TUI is a stream-style terminal program:

- `prompt-toolkit` collects each pasted input.
- Rich prints submitted input and progress lines to stdout.
- `rich.live.Live` refreshes the bottom shimmer line repeatedly.

Because Live keeps writing terminal control sequences, many terminals treat the process as actively writing at the bottom and snap the scrollback back down. A normal stdout program cannot reliably know where the user's terminal scrollbar is. Therefore precise "pause follow when user scrolls up, resume follow near bottom" behavior needs an application-owned viewport.

### Duplicate Title And Notes

Prompt submission leaves the prompt line visible, then the TUI prints the submitted block again. This is most visible for short values such as Job Title and Notes:

```text
› AI agent开发

Job Title
AI agent开发
```

JD feels less duplicated because it is long, but the behavior is the same. The full-screen TUI should clear the input buffer on submit and append only the normalized transcript block.

### Shimmer Feels Slow

The current shimmer refreshes around 10 frames per second and moves roughly 10 characters per second. It should feel closer to Codex: faster and smoother, without disturbing transcript scroll.

### Resume Quality Needs A Short Human Comment

The current round block already shows useful top representative candidates. The new comment should summarize the quality of the current round's resumes in plain Chinese text, no structure, no markdown, and under 80 Chinese characters.

## Full-Screen TUI Architecture

Create a small stateful full-screen TUI in `src/seektalent/tui.py`.

Core state:

- `transcript_lines: list[str]`
- `scroll_offset: int`
- `follow: bool`
- `status_text: str`
- `status_phase: str | None`
- `input_step: Literal["job_title", "jd", "notes", "running", "done"]`
- current input buffer

Layout:

- Main transcript window: scrollable, wraps long lines.
- Input window: visible during Job Title, JD, and Notes collection.
- Status window: one-line shimmer/status display at the bottom.

This keeps the implementation local and avoids introducing a larger TUI framework.

## Scroll And Follow Behavior

Default behavior:

- `follow=True`.
- When transcript lines are appended, the viewport stays pinned to the bottom.

When the user scrolls upward:

- Mouse wheel up, PageUp, or Up sets `follow=False`.
- New lines append to `transcript_lines`, but `scroll_offset` is preserved.
- The visible transcript stays fixed.

When the user scrolls downward:

- Mouse wheel down, PageDown, Down, or End moves the viewport down.
- If the distance from the viewport bottom to the transcript bottom is `<= 4` lines, set `follow=True`.
- End always jumps to bottom and sets `follow=True`.

The exact threshold is `4` lines. This gives the "一定范围又会自动跟随" behavior without requiring a complex scroll model.

## Input Transcript Behavior

Each submitted input is appended once:

```text
Job Title
AI agent开发

JD
<full pasted JD>

Notes
<full pasted notes>
```

For empty Notes, append a dim skipped line or omit the Notes block. Prefer omitting empty Notes to reduce noise.

Prompt labels remain visible only in the input area while editing. After submit, the input area clears and the transcript receives the normalized block.

## Shimmer Status

The shimmer should be rendered by prompt-toolkit invalidation, not by writing stdout.

Parameters:

- Refresh rate: 20 frames per second.
- Highlight movement: about 24 characters per second.
- Highlight width: 5 or 6 characters.

Status mapping stays the same:

- requirements started: `requirements 正在思考：岗位需求解析`
- controller started: `controller 正在思考：第 N 轮策略判断`
- reflection started: `reflection 正在思考：第 N 轮复盘判断`
- finalizer started: `finalizer 正在思考：整理最终名单`
- idle running: latest short business trace status

Status refresh must not change `scroll_offset`.

## Resume Quality Comment

Add a new optional progress payload field:

- `resume_quality_comment: str | None`
- `resume_quality_comment_error: str | None`

Render order in a completed round:

```text
代表候选人：
- ...
- ...

本轮简历质量：整体相关度较高，工程背景扎实，但 Agent 深度分化明显，少数候选人偏通用后端。

本轮反思：...
```

If generation fails:

```text
· 本轮简历质量短评生成失败，已继续 reflection。

本轮反思：...
```

The failure line is dim technical output and must not interrupt the run.

## Quality Comment Generation

Generation happens after scoring and representative candidate selection, before reflection output is rendered.

Model:

- Default: `openai-chat:deepseek-chat`
- Config: `SEEKTALENT_TUI_SUMMARY_MODEL`

Input should be compact:

- round number
- query terms
- up to five representative candidates
- candidate score
- fit bucket
- compact resume summary
- scoring reasoning summary
- first few risk flags or weaknesses

Prompt intent:

```text
请用中文用 80 字以内总结本轮简历质量，只评论候选人整体质量、匹配度和主要风险。不要编号，不要 Markdown。
```

Output handling:

- Strip whitespace.
- Collapse newlines to spaces.
- Truncate to 80 Chinese characters if the model exceeds the limit.
- If the call raises or returns empty text, set `resume_quality_comment_error` and continue.

This is display-only. The comment must not feed controller, scoring, reflection, finalizer, or evaluation.

## Error Handling

- Runtime search errors keep existing behavior.
- Ctrl+C exits with code 130.
- Quality-comment failure is non-fatal and displayed as a dim line.
- Full-screen TUI should restore the terminal on exit.
- If the terminal cannot support full-screen prompt-toolkit cleanly, the failure should be direct and visible. Do not add fallback UI chains.

## Tests

Add focused tests for:

- Submitted Job Title, JD, and Notes appear exactly once.
- Empty Notes are skipped or displayed only once according to final implementation.
- Up-scroll disables follow mode.
- Appending new transcript lines while follow is disabled does not move the viewport.
- Scrolling within four lines of the bottom restores follow mode.
- End restores follow mode and jumps to bottom.
- Shimmer status parameters use the faster rate and movement.
- Round rendering places resume-quality comment after representative candidates and before reflection.
- Quality-comment failure renders a dim failure line and still renders reflection.
- Runtime emits the new optional quality-comment payload without changing existing round progress fields.

Existing CLI/API/runtime tests must stay green.

## Out Of Scope

- No browser UI.
- No Textual/curses migration.
- No change to matching decisions or artifact ranking.
- No candidate-by-candidate interactive review.
- No persistent run-history browser.
- No full LLM-generated structured round summaries.
- No retry/fallback chain for the quality-comment model.

## Acceptance Criteria

- Running `seektalent` opens a full-screen TUI.
- User can scroll up and stay on history while new events arrive.
- User regains auto-follow when scrolling near the bottom.
- Shimmer is visibly faster and does not pull the transcript to the bottom.
- Job Title, JD, and Notes are not duplicated.
- Completed rounds show representative candidates, then a short resume-quality comment, then reflection.
- If the quality-comment LLM call fails, the run continues and shows a dim failure note.
- CI-equivalent checks pass.
