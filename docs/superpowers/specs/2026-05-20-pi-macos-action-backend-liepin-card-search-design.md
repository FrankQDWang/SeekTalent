# Pi OpenCLI Browser Backend For Liepin Card Search Design

## Summary

SeekTalent's Liepin live path needs a fast, local browser read/action spike that reuses the user's already logged-in Chrome session. The previous macOS `cliclick`/CGEvent direction is no longer the product default because it can interfere with the user's real mouse, keyboard, focus, and windows. This design replaces that direction with an OpenCLI Browser Bridge backend that is used only inside Pi.

The new slice wires a constrained OpenCLI backend into the existing Pi-first Liepin path:

```text
Workbench
  -> Runtime source lane
    -> LiepinPiWorkerClient
      -> Pi RPC
        -> repo-owned Liepin skill
        -> repo-owned SeekTalent OpenCLI Pi extension
          -> SeekTalent OpenCLI helper
            -> local @jackwener/opencli CLI
              -> user-installed OpenCLI Chrome extension
                -> user's current Chrome profile and a SeekTalent-owned tab
```

Runtime and Workbench still do not control the browser. Runtime sends bounded source tasks and validates the final strict JSON envelope. Pi owns the observe-act loop through a small set of SeekTalent tools. OpenCLI is a local browser bridge dependency for this spike, not a raw capability surface exposed to the model.

Installation policy:

- SeekTalent should install the OpenCLI CLI package as an explicit project dependency so users do not need a global `opencli` install.
- Users still install and authorize the OpenCLI Chrome extension themselves. The product must show a safe business-facing blocked state when the extension is missing or disconnected.
- The default session uses one named OpenCLI browser session and a tab, not repeated standalone Chrome windows.

## Current Evidence

Repo evidence:

- `PiRpcAgentClient` already runs Pi in RPC mode, loads a repo-owned skill, records observed tool events, and parses strict JSON final output.
- `PiLiepinExecutor` already validates Liepin card envelopes, provider-rank budgets, protected artifact refs, safe public payloads, and card-mode trace refs.
- `LiepinPiWorkerClient` already sits behind the existing `LiepinWorkerClient` boundary, so Runtime and Workbench can keep the same source-lane pipeline.
- `apps/web-svelte/package.json` already pins Pi-related Node dependencies and is the right explicit location for the OpenCLI CLI package dependency.
- The previous DokoBot MCP path blocks live Liepin because the local DokoBot action/read tool names were not proven. DokoBot can remain as a separate optional read backend later, but it is not required for this OpenCLI spike.

OpenCLI experiment evidence from this workspace:

- `npx -y @jackwener/opencli doctor` reported OpenCLI v1.8.0, daemon running on port `19825`, and extension connected.
- OpenCLI has no built-in `liepin` or `猎聘` adapter in the current public adapter registry/source tree.
- OpenCLI generic browser commands can read Liepin public pages with `browser <session> state`.
- OpenCLI generic browser commands can click and fill on Liepin public pages:
  - `fill` verified the `搜索职位、公司` input with `数据开发专家`;
  - clicking `搜索` navigated to a Liepin result URL with the query key set.
- Opening the wrong Liepin identity entry (`lpt.liepin.com`) triggered a Liepin identity intercept for this user's account. This is a policy lesson: the backend must not freely navigate to broad Liepin entrypoints. It must use source-policy start URLs and stop on account/identity/login states.

## Goals

- Use OpenCLI for both read and action in the Liepin spike.
- Keep all OpenCLI access inside Pi via repo-owned tools.
- Automatically install the OpenCLI CLI package with the project dependency set.
- Require the user to install/authorize the OpenCLI Chrome extension manually.
- Reuse the user's current Chrome profile/login state through OpenCLI Browser Bridge without copying cookies, storage, or session state.
- Use a single named OpenCLI session, and create/reuse a tab rather than standalone Chrome windows.
- Limit the first live path to Liepin card search only.
- Keep Runtime, Workbench, CTS, scoring, evidence merge, and final Top 10 unchanged.
- Preserve account safety:
  - allowlisted hosts and source-specific start URLs;
  - low-volume budgets;
  - non-uniform pacing;
  - stop on login, captcha, risk, account mismatch, identity intercept, payment/contact/chat/download prompts, and unknown modals;
  - no detail open without an approved detail lease;
  - no direct provider API replay.
- Keep main recruiter UI business-facing. It must not mention OpenCLI, CDP, MCP, DokoBot, debugger, anti-detection, or risk-control internals.

## Non-Goals

- Do not integrate DokoBot as a required read path in this slice.
- Do not implement a custom SeekTalent Chrome extension in this slice.
- Do not use macOS CGEvent, `cliclick`, `pynput`, Accessibility, AppleScript, or system-level keyboard/mouse automation.
- Do not expose raw OpenCLI commands to Pi.
- Do not use OpenCLI site adapters such as `boss`, `51job`, `maimai`, or `linkedin`.
- Do not use OpenCLI `browser eval`, `browser network`, `browser upload`, download waits, plugin/adapter authoring, cookie/storage access, or arbitrary JS.
- Do not automate Boss in this slice. Boss remains read-only/human-in-loop until a separate approved plan.
- Do not automatically contact candidates, send messages, open payment/contact workflows, download resumes, change account settings, or bypass risk states.
- Do not claim this path is undetectable. It is an account-safety-constrained spike, not a risk-control bypass.

## Product Contract

### Read And Action Through One Restricted OpenCLI Backend

The OpenCLI backend is the only read/action surface for this spike. It may use these OpenCLI browser primitives through a SeekTalent wrapper:

- `browser <session> state`
- `browser <session> get url`
- `browser <session> find`
- `browser <session> tab list`
- `browser <session> tab new`
- `browser <session> tab select`
- `browser <session> click`
- `browser <session> fill`
- `browser <session> type`
- `browser <session> scroll`
- `browser <session> wait text`
- `browser <session> wait selector`
- `browser <session> wait time`

The wrapper must reject every other command.

For this first slice, first navigation must use the tab-scoped path:

- create or select the SeekTalent Liepin tab with `browser <session> tab new <url>` / `tab select`;
- do not use generic `browser <session> open <url>` for the start URL unless OpenCLI later proves that command is tab-scoped in the already selected SeekTalent tab;
- if tab creation/selection cannot be proven without opening a standalone Chrome window, block with `liepin_opencli_window_policy_blocked`.

The Pi loop is:

```text
receive Runtime Liepin card-search task
  -> check OpenCLI backend status
  -> create or select the SeekTalent Liepin tab
  -> open an allowlisted Liepin card-search start URL
  -> read page state with OpenCLI
  -> decide the next safe action
  -> run one bounded OpenCLI action
  -> wait/read again
  -> repeat until page/card/action/time budget is terminal
  -> return one strict JSON envelope to Runtime
```

Runtime does not send low-level click refs, selectors, or URLs beyond bounded source strategy inputs. Pi may choose OpenCLI refs/selectors inside the source policy. The final Runtime-visible output remains the existing Liepin card envelope.

### Pi Observation Payload Versus Public Payload

OpenCLI page state is necessary for Pi's observe-act loop, but it is not a public Runtime/Workbench payload.

The helper must therefore expose two projections:

- **Pi tool observation payload:** returned only to the Pi tool call, bounded and sanitized, and allowed to include current URL, title-like page text, OpenCLI refs, visible labels, and truncated rendered state needed for the next action.
- **Public payload:** returned to Runtime events, Workbench, logs, notes, and diagnostics. This contains only `ok`, action names, counts, and safe reason codes.

Rules:

- `state`, `find`, and `get_url` may return bounded observations to Pi.
- Observations must be length-limited, rendered text only, and must strip or reject cookies, storage, authorization headers, local paths, raw HTML/script, raw resumes, and provider secrets.
- Observations must not be persisted in Workbench events, notes, public diagnostics, or final Runtime lane results.
- Tests must prove `to_public_payload()` excludes page text while the Pi tool payload still includes enough sanitized observation content for Pi to decide the next action.

### OpenCLI Installation And Extension Ownership

SeekTalent owns the CLI dependency but not the browser extension installation:

- Add `@jackwener/opencli` to `apps/web-svelte/package.json`.
- Resolve the OpenCLI binary explicitly from `apps/web-svelte/node_modules/.bin/opencli`.
- Do not rely on a global `opencli` binary unless the user explicitly configures it.
- `scripts/start-dev-workbench.sh` may run the repo's normal frontend dependency install path when `node_modules` is missing.
- If the OpenCLI CLI is missing, the whole app still starts; Liepin is blocked with a safe reason.
- If the OpenCLI Chrome extension is missing or disconnected, the whole app still starts; Liepin is blocked with a safe reason.
- The user installs the OpenCLI Chrome extension manually. SeekTalent docs explain the step; main Workbench UI only says the browser channel is not ready.

Distribution policy:

- This spike guarantees automatic OpenCLI CLI installation for source/dev workspaces through the repo Svelte dependency install path.
- A future packaged/PyPI distribution must either bundle the built frontend dependency tree or run an explicit first-run dependency bootstrap before enabling `opencli` mode.
- Until that packaging contract exists, packaged installations must fail closed with `liepin_opencli_command_missing` rather than silently relying on a global binary.

### Tab And Window Contract

The backend may create one tab for the named session. It must not repeatedly open standalone Chrome windows.

Rules:

- Use a stable session name, default `seektalent-liepin`.
- Prefer `browser <session> tab new <url>` or an equivalent tab-scoped operation.
- Reuse the selected tab for the life of a source run.
- Do not bind or mutate arbitrary user tabs unless the user explicitly starts from a bound tab in developer diagnostics.
- Do not close unrelated tabs.
- Do not log tab IDs, local paths, raw page content, cookies, or storage in public payloads.
- If OpenCLI cannot create/select a tab without opening a standalone window, block with `liepin_opencli_window_policy_blocked`.

### Source Browser Policy

Each task carries a source kind. The first implemented policy is `liepin`.

Initial policy:

| Source | This slice | Allowed | Blocked |
|---|---|---|---|
| `liepin` | Card search spike | Open/search card-list pages, fill search keywords, click search/list controls, scroll, read card text, read current URL. | Wrong identity entrypoints, detail open without lease, contact/chat/download/payment, account settings, captcha/risk bypass, unknown modal continuation. |
| `boss` | Deferred | Read-only or human-in-loop in future plan. | All automated action, chat, first contact, message sending, bulk outreach, account/company settings, identity verification bypass. |

Liepin allowed hosts:

- `www.liepin.com`
- `h.liepin.com`
- `c.liepin.com`
- `lpt.liepin.com`

Allowed start URLs must be narrower than the host list. The first slice should use a configured card-search start URL. Broad identity entrypoints such as `https://lpt.liepin.com/` are not safe defaults because account identity can differ.

Manual handoff states:

- login required;
- identity intercept or account mismatch;
- captcha or risk check;
- account binding/verification;
- unknown modal;
- payment/contact/chat/download prompt;
- page no longer on an allowlisted host;
- search result path cannot be identified within budget.

### Safe Reason Codes

New safe reason codes:

- `liepin_opencli_backend_disabled`
- `liepin_opencli_command_missing`
- `liepin_opencli_extension_disconnected`
- `liepin_opencli_status_unavailable`
- `liepin_opencli_forbidden_command`
- `liepin_opencli_host_blocked`
- `liepin_opencli_start_url_blocked`
- `liepin_opencli_window_policy_blocked`
- `liepin_opencli_budget_exhausted`
- `liepin_opencli_timeout`
- `liepin_opencli_login_required`
- `liepin_opencli_identity_intercept`
- `liepin_opencli_risk_page`
- `liepin_opencli_unknown_modal`
- `liepin_opencli_source_policy_missing`
- `liepin_opencli_malformed_state`

Main Workbench copy maps these to generic browser-channel states. Developer diagnostics may show the safe reason code but must not show local paths, raw OpenCLI output, cookies, storage, or full page text.

### Security Boundary

Allowed subprocess:

- only the resolved OpenCLI binary;
- only whitelisted `browser` subcommands;
- only one configured session;
- only source-policy hosts and start URLs;
- only bounded text fields, limited to generated search keywords/query strings.

Forbidden:

- `opencli boss ...` or any site adapter;
- `opencli browser eval`;
- `opencli browser network`;
- `opencli browser upload`;
- `opencli browser wait download`;
- `opencli plugin`, `adapter`, `external`, `daemon stop/restart` from Pi tools;
- raw shell commands;
- cookies/localStorage/sessionStorage;
- raw page HTML in public payloads;
- raw resumes in OpenCLI traces;
- JD/notes/raw resume text in subprocess argv.

Because OpenCLI's CLI takes typed text as an argument, the first slice must pass only short generated keyword strings, never full JD, notes, raw resumes, secrets, or provider payload. If a future implementation needs to pass sensitive text, it must move to a lower-level library or stdin-safe local bridge.

### Runtime Output

The final Pi response remains `seektalent.pi_liepin_cards.v1`:

- status;
- source run id;
- query;
- cards seen/returned;
- pages visited;
- safe summary refs;
- protected snapshot refs;
- card summaries;
- safe stop reason.

OpenCLI state text is an input to Pi reasoning, not a public Runtime payload. Public events contain counts and safe reason codes, not page text.

## Acceptance Criteria

- OpenCLI CLI package is an explicit project dependency and can be resolved from the repo dependency tree.
- User-installed OpenCLI Chrome extension is required and diagnosed safely.
- Pi command includes the repo-owned OpenCLI extension when `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`.
- Pi capability probe requires the SeekTalent OpenCLI status tool when OpenCLI mode is enabled.
- DokoBot is not required for the OpenCLI mode.
- Runtime and Workbench do not import, execute, or shell out to OpenCLI.
- Pi tools expose only restricted OpenCLI read/action commands.
- Pi observation tools return bounded sanitized page observations to Pi, while public payloads contain only safe counts/reasons.
- Forbidden OpenCLI commands are rejected by tests.
- Liepin card search skill describes OpenCLI read/action loop and stop states.
- The backend uses tab-scoped `tab new` / `tab select` for the source tab and does not intentionally create standalone Chrome windows.
- Wrong identity/login/risk states block only Liepin; CTS remains independent.
- Main Workbench UI contains no OpenCLI/CDP/debugger/MCP/DokoBot/risk-control internal copy.
- Safe reason codes flow from Pi/Liepin runtime to Workbench source state.
- No public payload includes cookies, storage, raw OpenCLI output, raw HTML, raw resume, local filesystem paths, or provider secrets.

## Verification

Required automated verification:

```bash
uv run pytest tests/test_liepin_config.py tests/test_pi_opencli_browser.py tests/test_liepin_pi_executor.py tests/test_liepin_pi_worker_client.py tests/test_dev_mode_readiness.py tests/test_pi_agent_boundaries.py -q
uv run ruff check src/seektalent/config.py src/seektalent/providers/pi_agent src/seektalent/providers/liepin tests/test_liepin_config.py tests/test_pi_opencli_browser.py tests/test_liepin_pi_executor.py tests/test_liepin_pi_worker_client.py tests/test_dev_mode_readiness.py tests/test_pi_agent_boundaries.py
cd apps/web-svelte && bun install --frozen-lockfile && bun run check && bun run test
git diff --check docs/superpowers/specs/2026-05-20-pi-macos-action-backend-liepin-card-search-design.md docs/superpowers/plans/2026-05-20-pi-macos-action-backend-liepin-card-search.md
```

Optional manual spike verification:

```bash
apps/web-svelte/node_modules/.bin/opencli doctor
apps/web-svelte/node_modules/.bin/opencli browser seektalent-liepin tab new https://www.liepin.com/zhaopin/
apps/web-svelte/node_modules/.bin/opencli browser seektalent-liepin state
apps/web-svelte/node_modules/.bin/opencli browser seektalent-liepin find --css 'input[placeholder="搜索职位、公司"]'
```

Manual spike rules:

- Use a harmless keyword.
- Do not open detail pages.
- Do not use `eval`, `network`, cookie/storage, downloads, or site adapters.
- Stop immediately on login, identity intercept, captcha, risk page, payment/contact/chat/download prompt, or unknown modal.

## Deferred Work

- Self-owned SeekTalent Chrome extension that replaces OpenCLI after the spike proves the Liepin product path.
- DokoBot read-only optional fallback if OpenCLI state quality is insufficient.
- Boss read-only/human-in-loop source plan.
- Approved-detail lease execution through a separate detail lane.
- Native stdin-safe browser bridge if keyword argv exposure becomes unacceptable.
