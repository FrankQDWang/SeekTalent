# SeekTalent

SeekTalent coordinates recruiting work across local application state and external data-source browser sessions.

## Candidate assessment

**Hard conflict**:
Resume-grounded evidence that directly contradicts an explicit hard constraint or exclusion policy. Missing information, weak evidence, and ordinary capability gaps are not hard conflicts.
_Avoid_: Low score, missing must-have, general risk

**Fit**:
A candidate assessment with no evidenced hard conflict. Fit is an eligibility verdict and does not mean the candidate reaches recommendation quality.
_Avoid_: Recommended, qualified, high score

**Not fit**:
A candidate assessment supported by at least one explicit hard conflict. A candidate cannot be not fit solely because evidence is incomplete or the match score is low.
_Avoid_: Low fit, weak candidate, below threshold

**Raw match score**:
The authoritative weighted role-match score derived from the applicable assessment dimensions. It determines score-based quality decisions and ordering.
_Avoid_: Display score, fit bucket

**Recommendation threshold**:
The raw-match-score boundary for treating an otherwise fit candidate as recommendation-quality. It is distinct from eligibility and visibility.
_Avoid_: Fit threshold, display floor

**Display score**:
A presentation-only projection of the raw match score. It never changes candidate ordering, eligibility, recommendation quality, or stored assessment truth.
_Avoid_: Raw score, final score

**Scoring semantics version**:
The persisted contract version that makes a scorecard interpretable. A recovered scorecard without the current version must be re-scored as a complete candidate set; an old `not_fit` value can never be converted into hard-conflict evidence.
_Avoid_: Cache version, checkpoint schema version, inferred conflict

**Candidate identity**:
The person-level continuity record that groups known resume observations and resolves historical aliases to one current canonical identity. It does not choose or combine resume content.
_Avoid_: Resume version, candidate card, provider candidate ID

**Resume content version**:
One internally coherent, source-observed body of candidate information. Its normalized resume is derived from that body; explicitly conflicting versions remain separate.
_Avoid_: Candidate identity, merged profile, scoring summary

**Verified source reference**:
A validated external locator attached to retained source evidence. It may supplement other verified references but is independent of resume content selection and scoring.
_Avoid_: Raw source URL, guessed link, resume content

**Candidate detail**:
A presentation of one eligible detail or final resume content version for a candidate identity. A scoring summary alone is not candidate detail, and conflicting content is never mixed into the projection.
_Avoid_: Match explanation, candidate identity, combined resume

## Requirement execution

**Approved requirement revision**:
The sole durable requirement truth accepted for a run. A supplemental user requirement creates the next revision in the same chain; draft UI state, chat text, and RunState fields are not parallel requirement authorities.
_Avoid_: Requirement form snapshot, runtime notes, scoring policy

**Requirement amendment**:
A structured request to derive a later approved requirement revision at a specific unlocked round boundary. It reserves that boundary while extraction or review is pending and never acts as an independent executable requirement.
_Avoid_: Requirement queue, free-form runtime instruction, scoring override

**Requirement execution projection**:
The RunState requirement sheet, scoring policy, and requirement-owned query terms derived together from one approved requirement revision. Runtime reflection and candidate-feedback terms remain dynamic inputs, but none of these projections can become a second requirement truth.
_Avoid_: Approved requirement revision, independent query pool, independent scoring policy

**Round input lock**:
The durable event that closes one round's requirement input before its Controller runs. An amendment reserved first blocks that boundary until it resolves; an amendment arriving after the lock targets the next unlocked round.
_Avoid_: Controller decision, amendment status, checkpoint

## Browser lifecycle

**Source run**:
A single attempt to collect candidates from one data source for a runtime operation.
_Avoid_: Browser session, task

**Browser control scope**:
A single continuous browser-control attempt that may create and command owned tabs. A retry or later expansion always receives a new scope, even when it belongs to the same source run.
_Avoid_: Source run, browser session, task

**Browser control fence**:
The browser-side authority formed by the controller-only control key and its activation fence token. The browser boundary validates both on every command; the scope ID only correlates evidence and never grants control.
_Avoid_: Browser control scope ID, runtime attempt fence, profile binding generation

**Source control lane**:
The single current authority allowed to issue browser commands for one data source, browser profile, and provider account. Tabs awaiting reclamation do not occupy the lane.
_Avoid_: Tab lock, source run

**Owned tab**:
A browser tab that SeekTalent created inside a host window for one browser control scope and may therefore close. An existing user tab can never become an owned tab.
_Avoid_: Managed tab, automation tab

**Page navigation readiness**:
The browser boundary condition reached when an owned tab reports a concrete HTTP(S) URL before its monotonic deadline. Tab allocation never implies navigation readiness; the provider validates the returned URL against its own allowed surface before issuing page actions.
_Avoid_: Tab created, page loaded

**Owned tab record**:
A short-lived ownership claim linking an owned tab to its browser control scope, OpenCLI session, and exact page identity. It is supporting evidence and never authorizes closing a tab without matching browser-side ownership.
_Avoid_: Marker, lease file

**User tab**:
A browser tab that existed independently of the source run. SeekTalent may use the surrounding Chrome login state but never navigates, repurposes, or closes a user tab.
_Avoid_: Borrowed tab, reusable tab

**Host tab**:
An existing `h.liepin.com` user tab used only to identify a host window. It remains a user tab and never becomes owned.
_Avoid_: Selected tab, borrowed tab

**Host window**:
An existing user Chrome window in which SeekTalent may place owned tabs. SeekTalent does not own the host window and must never close it.
_Avoid_: Owned window, automation window

**Liepin browser session**:
The user's existing authenticated `h.liepin.com` login state in Chrome. A source run requires this state and does not perform or recover login on the user's behalf.
_Avoid_: SeekTalent login, managed login

**Tab reclamation**:
The best-effort release of an owned tab after it is no longer needed. Reclamation never delays or blocks later source work; failure is observable cleanup information, not a failure of completed business work.
_Avoid_: Tab reset, tab blanking

**Cleanup fault isolation**:
The rule that countdown UI, ownership-record persistence, background reclamation, and close failures remain outside the business-result path. A cleanup fault may produce diagnostics but cannot delay, cancel, or rewrite a run.
_Avoid_: Cleanup fallback, silent failure

**Controlled tab lock**:
A visual and interaction layer shown only inside an owned tab while SeekTalent controls it. It dims the page, blocks human page input, and shows the remaining idle time; it never prevents the user from closing the tab through Chrome itself.
_Avoid_: Loading mask, disabled page

**Idle deadline**:
The instant 60 seconds after the last completed browser command for an owned tab. Extension-owned idle expiry is the only normal reclamation path; scope completion releases business references and authority without requesting immediate closure, while maintenance may reconcile exceptional orphan tabs.
_Avoid_: Tab lifetime, hard timeout

**Orphan tab**:
An inert owned tab whose browser control scope or controlling connection has ended without successful reclamation. It remains locked and awaiting automatic cleanup, but never blocks a later browser control scope.
_Avoid_: Stale tab, leaked tab

**Profile binding generation**:
The version of one explicit Chrome profile, production extension instance, and provider-account binding. Changing any member creates a new generation and invalidates the previous generation's authority to issue browser commands.
_Avoid_: Runtime attempt, browser control scope, profile fallback

## Reliable execution

**Product outcome**:
The durable business result of one logical run after source coverage and committed candidate truth are known. It is distinct from process lifecycle state and from the disposition of one source operation.
_Avoid_: Sidecar status, operation status, success flag

**Source operation disposition**:
The typed fact returned by one source operation: completed, partial, user action required, incompatible, failed, cancelled, or reconciliation unknown. Readiness is an operation-specific fact, not a generic completion disposition. The main application interprets the disposition under the run contract; it is never itself a product outcome.
_Avoid_: Product outcome, sidecar lifecycle state

**Failure cause**:
A fine-grained causal fact observed by a source or component. It may be provider-specific and is preserved for diagnostics, but is neither a product problem nor user-facing text.
_Avoid_: Public problem, error message

**Public problem**:
A stable, privacy-safe product classification whose members differ by handling, outcome, responsibility, user action, or user-facing explanation. It is derived from a failure cause and is not the cause itself.
_Avoid_: Failure cause, raw reason code

**Failure interpretation**:
The deterministic translation of a failure cause and operation context into a public problem, source operation disposition, and optional user action. It reports meaning but grants neither recovery nor retry authority.
_Avoid_: Retry policy, recovery workflow

**User action**:
One concrete step a user can perform to let the same logical run continue through a new attempt. It is distinct from support work, automatic recovery, and a product outcome.
_Avoid_: Retry, support action, generic remediation

**Needs attention**:
A non-terminal product outcome that waits for one concrete user action before the same logical run may resume with a new attempt. Infrastructure exhaustion without an actionable user step is not needs attention.
_Avoid_: Retryable failure, degraded result, generic blocked

**Runtime attempt fence**:
The storage authority held by one executor attempt to commit run state, checkpoints, candidates, and completion. It does not by itself authorize browser commands.
_Avoid_: Profile binding generation, browser control scope

**Failure Envelope**:
A versioned, privacy-safe evidence record that preserves typed failure cause, affected operation, component identity, and available user or support action. It reports facts and never grants retry permission by itself.
_Avoid_: Error string, retry policy, support bundle
