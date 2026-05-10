# Design

## Theme

Light, warm, focused desktop workbench. The primary scene is a recruiter working during the day on a Mac or office monitor, watching a long-running agent process while making judgment calls.

## Visual Baseline

The visual baseline is `/Users/frankqdwang/Documents/工作/seektalent/references/Recruiter Agent _Standalone_.html`.

The page must read as the same product family as that reference:

- warm off-white app background and slightly lighter fixed panels;
- thin beige-gray borders, not heavy shadows;
- dense, compact typography using system sans plus mono labels for counters/status;
- restrained green action color and muted source-state dots;
- small tinted pills for location, level, salary, and bonus requirements;
- white source/candidate cards with 1px borders and tight internal rhythm;
- fixed top status bar and fixed bottom timeline;
- the center is a strategy canvas/state machine, not a dashboard card stack.

## Layout Contract

Desktop workbench layout:

```text
session rail | JD/source panel | strategy canvas | right rail
fixed top status bar
fixed bottom playback timeline
```

The supplied HTML covers the JD/source panel, strategy canvas, right rail, and bottom timeline. SeekTalent adds a far-left collapsible session rail without changing the reference page's core proportions.

Desktop target proportions:

- top bar: about 52px high;
- session rail: about 248-280px when expanded, icon-only when collapsed;
- JD/source panel: about 304px;
- strategy canvas: flexible center lane;
- right rail: about 360px;
- bottom timeline: about 48px high.

## Components

- Session rail: compact search, recent JD sessions, status labels, collapse control.
- JD/source panel: session/JD summary, requirement triage, source cards, source counters, source-specific action buttons.
- Strategy canvas: empty/ready state, running graph nodes, source filter, merged event timeline, and later graph expansion.
- Right rail: top run log, bottom candidate shortlist/review queue.
- Bottom timeline: play/pause control, reset/clock controls, stage labels, progress line, elapsed/total time.
- Top bar: product/project identity, run status, elapsed time, user identity.

## Interaction And Motion

The reference is a multi-frame interactive design. Visual QA must cover:

- initial/ready frame;
- running frame after pressing the lower-left play/start control;
- paused frame;
- mid-run frame with strategy nodes/log entries visible;
- later completed or candidate-filled frame when available.

Motion should be subtle and state-driven: timeline progress, active stage indicators, source status changes, and graph node reveal. Do not add decorative choreography.

## Color And Type

Use OKLCH or locally equivalent CSS tokens when practical. Keep the strategy restrained:

- warm neutral background;
- lighter panel surfaces;
- muted border color;
- deep green primary action and active state;
- soft teal, lavender, amber, and blue tints only for semantic pills/source categories.

Use a system UI font stack for product text and a mono stack for status counters, event tags, and technical identifiers. Avoid fluid type and oversized dashboard headings.

## Visual QA

Use Playwright screenshots and `odiff-bin` visual comparisons against reference screenshots. The first UI-architecture gate is structural similarity, not pixel-perfect matching: topology, proportions, fixed bars, panel density, color temperature, and run-state frames must align before Liepin UI work continues.
