# SeekTalent Product Register

## Register

Product.

SeekTalent is a local-first recruiter workbench for turning a job requirement into an auditable candidate shortlist. The primary user is a recruiter, founder, or hiring partner who needs to confirm the requirement, supervise the agent run, inspect search strategy, review candidate evidence, approve detail fetches, and export a final shortlist without leaking raw provider data into the UI.

## Product Promise

The product should feel precise, calm, operational, and trustworthy. It is not a marketing site, a generic AI chat demo, or a SaaS dashboard skin. The first screen is the actual workbench.

## Core Workflow

1. The user states or edits the hiring requirement.
2. The BFF projects requirement confirmation, pending actions, source status, transcript groups, strategy graph, thinking process, candidates, detail approvals, and final review into stable UI DTOs.
3. The React workbench renders the active agent run with smooth streaming, durable replay, and clear separation between model text and tool/runtime facts.
4. The user reviews candidates and evidence, approves sensitive or costly steps, and gets a final shortlist.

## Brand Behavior

- Focused: dense information, restrained controls, and clear scan paths.
- Exacting: lifecycle status, source evidence, and approval state are explicit.
- Calm: streaming updates should feel continuous without visual jitter.
- Local-first: do not suggest hosted SaaS assumptions or cloud-only affordances.
- Auditable: every important agent/runtime action must be replayable or explicitly marked live-only.

## Non-Goals

- No backwards compatibility with the retired legacy UI.
- No old UI docs as design source material.
- No decorative AI landing page, hero marketing page, gradient-orb theme, or generic assistant shell.
- No raw runtime, provider, shell, source, or resume payload exposure in React.
- No UI-only display fields added to core runtime models for convenience.

## Architecture Product Principle

The BFF is the volatility boundary. Frontend product changes may change BFF projection and React rendering, but they must not make the core conversation agent, runtime, provider adapters, or source layer depend on React UI concepts.

## Accessibility And Responsiveness

The workbench must be usable at 375, 768, 1440, and wide desktop widths. It must support keyboard operation, visible focus, WCAG AA contrast for text and controls, accessible names for icon controls, and reduced-motion behavior for stream updates.
