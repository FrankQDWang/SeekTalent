# AGENTS.md

## React Agent Workbench Rules

- Load and apply the `impeccable` skill before implementing, reviewing, or polishing React UI components in this app.
- Treat `apps/web-react/DESIGN.md`, the WTS assets, the local Figma export metadata, and the Codex transcript reference screenshots as the active design baseline.
- Do not use retired legacy UI documents or deleted legacy frontend behavior as design source material.
- This app is a breaking pre-1.0 replacement. Do not preserve old frontend compatibility paths.
- React must consume typed BFF DTOs and semantic stream events only. Do not parse raw runtime/provider payloads, raw shell output, source payloads, or localized display strings.
- Production React code must not import `src/test/fixtures/*`.
- Transcript lifecycle, active-cell behavior, tool/web/command rows, and output details must follow the BFF semantic transcript protocol. If semantics are unclear, inspect `.external/codex-reference` before coding. Guessing from screenshots alone is not acceptable.
- The Codex reference checkout is read-only reference material. Do not copy Codex internals or make `.external/codex-reference` a production dependency.
- The right rail thinking-process panel comes from the BFF `thinkingProcess` model. It is not graph-node details and it is not the Codex-style transcript.
