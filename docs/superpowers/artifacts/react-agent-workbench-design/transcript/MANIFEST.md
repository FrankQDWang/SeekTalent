# Codex Transcript Visual Regression References

These screenshots are stable local references for the React Agent Workbench transcript implementation. They are intentionally separate from the WTS product screenshots because the transcript follows the Codex interaction model, not the WTS thinking-process panel.

## Assets

- `codex-transcript-01-full-collapsed.png`
  - Full Codex window reference with a compact processed row.
  - Covers conversation layout, side context, composer, and collapsed transcript rhythm.
- `codex-transcript-02-full-expanded.png`
  - Full Codex window reference with an expanded processed section.
  - Covers expanded assistant work blocks, tool rows, context compression, and attachment thumbnail placement.
- `codex-transcript-03-toolread-detail.png`
  - Tight detail reference for an expanded `Loaded a toolread 2 files` row.
  - Covers action label plus child file/resource lines.
- `codex-transcript-04-web-search-running.png`
  - Running transcript state with web search activity and file read in progress.
  - Covers muted running tool row style.
- `codex-transcript-05-file-search-complete.png`
  - Completed file/search activity followed by thinking state.
  - Covers completed tool row language and inline code chips.
- `codex-transcript-06-file-read-running.png`
  - Running file read state while conversation context continues.
  - Covers repeated tool rows and pending activity spacing.
- `codex-transcript-07-guided-followup.png`
  - Guided follow-up / handoff state with multiple tool rows and user prompt bubble.
  - Covers guided prompt placement, continued thinking state, and lower composer boundary.

## Implementation Notes

- React transcript components should treat these screenshots as visual and interaction references, not pixel-perfect clones of the Codex app chrome.
- Required states: collapsed run group, expanded run group, running tool, completed tool, failed tool, web/page-load activity, file-read activity, shell-command activity, attachment, context-compression divider, and guided follow-up.
- The BFF should emit semantic transcript/tool events. React should not parse raw shell output, raw runtime logs, provider payloads, or display-oriented tool strings.
