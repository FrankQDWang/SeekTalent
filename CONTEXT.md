# SeekTalent

SeekTalent coordinates recruiting work across local application state and external data-source browser sessions.

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
The instant 60 seconds after the last completed browser command for an owned tab. Each completed command moves the deadline forward; source-run completion immediately requests background reclamation and never waits for the deadline or close result.
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

**Needs attention**:
A non-terminal product outcome that waits for one concrete user action before the same logical run may resume with a new attempt. Infrastructure exhaustion without an actionable user step is not needs attention.
_Avoid_: Retryable failure, degraded result, generic blocked

**Runtime attempt fence**:
The storage authority held by one executor attempt to commit run state, checkpoints, candidates, and completion. It does not by itself authorize browser commands.
_Avoid_: Profile binding generation, browser control scope

**Failure Envelope**:
A versioned, privacy-safe evidence record that preserves typed failure cause, affected operation, component identity, and available user or support action. It reports facts and never grants retry permission by itself.
_Avoid_: Error string, retry policy, support bundle
