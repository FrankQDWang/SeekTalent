# Liepin OpenCLI Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Liepin/OpenCLI behavior deterministic across dev and shipped prod by using OpenCLI 1.8.6 everywhere, replacing fake readiness with real browser preflight, fixing Liepin search-surface classification, removing legacy compatibility paths, and locking local OpenCLI state updates.

**Architecture:** The OpenCLI CLI process remains the only browser-action backend for live Liepin. Liepin page semantics stay in `providers/liepin`, generic OpenCLI command execution stays in `opencli_browser`, and Workbench only consumes structured readiness/status contracts. Cleanup-tab behavior is removed from this module: SeekTalent may create/reuse owned tab records, but it will not try to auto-close tabs.

**Tech Stack:** Python 3.12, Pydantic, pytest, Bash, pnpm, `@jackwener/opencli@1.8.6`, OpenCLI browser daemon.

---

## Requirements

- Pin every project-controlled OpenCLI version to `1.8.6`.
- Dev and prod must use the same OpenCLI launcher/preflight core. Dev can differ in process startup and local source layout only.
- Do not preserve old compatibility behavior with fallback paths.
- Remove cleanup-tab automation from this module. User-owned manual tab closing is the expected behavior.
- Do not classify allowed recruiter search/result pages as terminal just because the URL path contains `resume`.
- `session_status()` must perform real OpenCLI/Liepin preflight and return raw `liepin_opencli_*` reason codes.
- Local lease, owned-marker, agent-event, and collected-resume JSON updates must use cross-process file locks around the full read-modify-write transaction.

## File Structure

- Modify `src/seektalent/opencli_launcher.py`: single managed OpenCLI version constant.
- Modify `apps/web-react/package.json` and `apps/web-react/pnpm-lock.yaml`: repo-local package pin to 1.8.6.
- Modify `scripts/start-dev-workbench.sh`: use the managed Python OpenCLI launcher and remove cleanup calls.
- Modify `src/seektalent/opencli_browser/automation.py`: add explicit daemon restart, remove current-tab/blank-window cleanup dependencies from Liepin critical path.
- Modify `src/seektalent/opencli_browser/runtime.py`: delete unused AppleScript tab/cleanup helpers after callers are removed.
- Modify `src/seektalent/providers/liepin/liepin_opencli_policy.py`: define one Liepin OpenCLI policy source for hosts/search surfaces/reuse fragments.
- Modify `src/seektalent/providers/liepin/liepin_site_parsing.py`: classify DOM before forbidden `resume` path for search/result pages.
- Modify `src/seektalent/providers/liepin/liepin_site_adapter.py`: use search-surface helpers, remove cleanup worker/actions, add readiness probe, lock local JSON writes.
- Create `src/seektalent/providers/liepin/opencli_local_state.py`: small file-lock helpers for JSON update operations.
- Modify `src/seektalent/providers/liepin/opencli_retriever.py`: expose session-status probing through the runner it owns.
- Modify `src/seektalent/providers/liepin/opencli_worker_client.py`: delegate `session_status()` to the real retriever probe.
- Modify `src/seektalent/providers/liepin/worker_contracts.py`: extend `SessionStatus` with raw OpenCLI readiness fields.
- Modify `src/seektalent/providers/liepin/client.py`: remove `managed_local` compatibility mapping and build OpenCLI config from one policy.
- Modify `src/seektalent/config.py`, `src/seektalent_ui/server.py`, `src/seektalent/cli.py`, and `src/seektalent/liepin_smoke_cli.py`: remove `managed_local` from user-facing mode choices and keep preflight reason propagation raw.
- Modify `src/seektalent/dev_mode.py` and `src/seektalent_ui/workbench_liepin_start_probe.py`: use real OpenCLI readiness and raw reason codes.
- Update focused tests under `tests/test_opencli_launcher.py`, `tests/test_liepin_opencli_browser.py`, `tests/test_liepin_opencli_worker_client.py`, `tests/test_liepin_config.py`, `tests/test_liepin_opencli_local_setup.py`, `tests/test_liepin_boundaries.py`, `tests/test_liepin_worker_client.py`, `tests/test_liepin_provider_adapter.py`, `tests/test_workbench_liepin_browser_session_probe.py`, and `tests/test_liepin_runtime_source_lane.py`.

---

### Task 0: Lock The Corrected Invariants Before Implementation

**Files:**
- Modify: this plan only during planning review.
- Modify: `docs/superpowers/specs/2026-07-05-liepin-opencli-runtime-hardening-design.md`

- [ ] **Step 1: Enforce these invariants before editing product code**

These are build blockers, not implementation suggestions:

- `session_status()` must prepare the canonical recruiter search surface with `open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)` before deciding readiness. It must not block just because the active Chrome tab was initially GitHub, Baidu, blank, or another non-Liepin page.
- Ready `SessionStatus` must not echo the caller's requested `provider_account_hash`. Until a reliable Liepin account DOM probe exists, OpenCLI local mode reports a stable local browser-profile subject and Workbench treats it as browser-profile binding, not actual Liepin account identity proof.
- Known recruiter search surface URLs are non-terminal after login/risk/identity checks, even without result DOM. DOM evidence improves readiness, but lack of `resultList` must not turn `/resume/search` into `unknown_modal`.
- `state()` classifies against `_state_url(output) or current_url`, not only the earlier `get_url()` result.
- `agent-events.json` keeps the dict schema `{"schema_version": "seektalent.opencli_agent_events.v1", "events": [...]}`.
- Locks cover the full read-modify-write transaction for lease, owned-page markers, agent events, and collected resumes.
- Workbench start-probe raw reason validation imports the authoritative Liepin worker reason set instead of maintaining a partial duplicate set.
- `liepin_opencli_bootstrap_failed` is implemented end-to-end, including Python launcher stderr from browser commands, or it must be removed from error handling and acceptance. This plan keeps it and implements it.

---

### Task 1: Pin OpenCLI 1.8.6 Everywhere

**Files:**
- Modify: `src/seektalent/opencli_launcher.py`
- Modify: `apps/web-react/package.json`
- Modify: `apps/web-react/pnpm-lock.yaml`
- Test: `tests/test_opencli_launcher.py`
- Test: `tests/test_liepin_opencli_local_setup.py`

- [ ] **Step 1: Add failing tests for the pin and dev launcher source**

Append these tests:

```python
# tests/test_opencli_launcher.py
def test_managed_opencli_version_is_pinned_to_1_8_6() -> None:
    from seektalent import opencli_launcher

    assert opencli_launcher.OPENCLI_PACKAGE == "@jackwener/opencli"
    assert opencli_launcher.OPENCLI_VERSION == "1.8.6"
```

```python
# tests/test_liepin_opencli_local_setup.py
def test_dev_launcher_uses_managed_opencli_launcher_instead_of_node_modules_binary() -> None:
    script = Path("scripts/start-dev-workbench.sh").read_text(encoding="utf-8")

    assert "python -m seektalent.opencli_launcher" in script
    assert "apps/web-react/node_modules/.bin/opencli" not in script
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
pytest tests/test_opencli_launcher.py::test_managed_opencli_version_is_pinned_to_1_8_6 \
  tests/test_liepin_opencli_local_setup.py::test_dev_launcher_uses_managed_opencli_launcher_instead_of_node_modules_binary -q
```

Expected: FAIL because `OPENCLI_VERSION` is `1.8.0` and the dev script still uses `apps/web-react/node_modules/.bin/opencli`.

- [ ] **Step 3: Update the Python managed launcher pin**

Change:

```python
# src/seektalent/opencli_launcher.py
OPENCLI_VERSION = "1.8.6"
```

- [ ] **Step 4: Update the repo-local package pin and lockfile**

Change:

```json
// apps/web-react/package.json
"@jackwener/opencli": "1.8.6"
```

Verify that the package exists before changing the lockfile:

```bash
corepack pnpm --dir apps/web-react view @jackwener/opencli@1.8.6 version
```

Expected: prints `1.8.6`.

Run:

```bash
corepack pnpm --dir apps/web-react install --lockfile-only
```

Expected: `apps/web-react/pnpm-lock.yaml` records `@jackwener/opencli` specifier and resolved version as `1.8.6`.

- [ ] **Step 5: Change dev startup to use the managed launcher without unsafe shell parsing**

In `scripts/start-dev-workbench.sh`, replace the `OPENCLI_BIN` variable and direct calls with a command array. The default path is a fixed Bash array, not a free-text command parse:

```bash
OPENCLI_COMMAND_TEXT="${SEEKTALENT_LIEPIN_OPENCLI_COMMAND:-}"
if [[ -z "$OPENCLI_COMMAND_TEXT" ]]; then
  OPENCLI_CMD=(uv run python -m seektalent.opencli_launcher)
  OPENCLI_COMMAND_TEXT="uv run python -m seektalent.opencli_launcher"
else
  mapfile -d '' -t OPENCLI_CMD < <(
    uv run python - "$OPENCLI_COMMAND_TEXT" <<'PY'
import shlex
import sys

for part in shlex.split(sys.argv[1]):
    print(part, end="\0")
PY
  )
fi

opencli_cmd() {
  "${OPENCLI_CMD[@]}" "$@"
}
```

Use it for daemon operations:

```bash
opencli_cmd daemon status
opencli_cmd daemon restart
```

Pass the same command text into the backend:

```bash
SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$OPENCLI_COMMAND_TEXT"
```

Remove the dependency on `apps/web-react/node_modules/.bin/opencli` from the OpenCLI checks.

- [ ] **Step 6: Verify the pin**

Run:

```bash
pytest tests/test_opencli_launcher.py tests/test_liepin_opencli_local_setup.py -q
corepack pnpm --dir apps/web-react install --frozen-lockfile
rg -n "1\\.8\\.0|1\\.8\\.3|@jackwener/opencli" src apps/web-react tests scripts -g '!apps/web-react/node_modules/**'
```

Expected: pytest PASS. `rg` finds only `@jackwener/opencli` references, `1.8.6`, and synthetic update-notice test strings if those tests still intentionally exercise notice stripping.

Commit:

```bash
git add src/seektalent/opencli_launcher.py apps/web-react/package.json apps/web-react/pnpm-lock.yaml scripts/start-dev-workbench.sh tests/test_opencli_launcher.py tests/test_liepin_opencli_local_setup.py
git commit -m "fix: pin OpenCLI runtime to 1.8.6"
```

---

### Task 2: Remove Tab Cleanup Automation From The Liepin Module

**Files:**
- Modify: `scripts/start-dev-workbench.sh`
- Modify: `src/seektalent/providers/liepin/opencli_browser_cli.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/opencli_browser/automation.py`
- Modify: `src/seektalent/opencli_browser/runtime.py`
- Test: `tests/test_liepin_opencli_local_setup.py`
- Test: `tests/test_liepin_opencli_browser.py`
- Test: `tests/test_liepin_provider_source_composition.py`

- [ ] **Step 1: Add failing tests that cleanup actions are gone**

Update `tests/test_liepin_opencli_local_setup.py`:

```python
def test_dev_launcher_does_not_try_to_cleanup_liepin_tabs() -> None:
    script = Path("scripts/start-dev-workbench.sh").read_text(encoding="utf-8")

    assert "cleanup_orphaned_tabs" not in script
    assert "watch_idle_lease" not in script
    assert "cleanup_idle_lease" not in script
```

Add to `tests/test_liepin_opencli_browser.py`:

```python
def test_cli_rejects_removed_cleanup_actions(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "cleanup_orphaned_tabs"])
    monkeypatch.setattr("sys.stdin", io.StringIO('{"force": true}'))

    rc = opencli_browser_cli.main()
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["action"] == "cleanup_orphaned_tabs"
    assert payload["safeReasonCode"] == "liepin_opencli_forbidden_command"
```

- [ ] **Step 2: Run cleanup tests and verify they fail**

Run:

```bash
pytest tests/test_liepin_opencli_local_setup.py::test_dev_launcher_does_not_try_to_cleanup_liepin_tabs \
  tests/test_liepin_opencli_browser.py::test_cli_rejects_removed_cleanup_actions -q
```

Expected: FAIL because cleanup actions are still present.

- [ ] **Step 3: Remove dev-script cleanup invocation**

Replace the `cleanup()` function body with process cleanup only:

```bash
cleanup() {
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
  fi
}
```

- [ ] **Step 4: Remove cleanup actions from the OpenCLI browser CLI**

Delete these branches from `_run_action()` in `src/seektalent/providers/liepin/opencli_browser_cli.py`:

```python
if action == "cleanup_idle_lease":
    return runner.cleanup_idle_lease(force=bool(payload.get("force") or False))
if action == "cleanup_orphaned_tabs":
    return runner.cleanup_orphaned_tabs(force=bool(payload.get("force") or False))
if action == "watch_idle_lease":
    return runner.watch_idle_lease()
```

After deletion, unknown cleanup actions fall through to:

```python
raise OpenCliBrowserError("liepin_opencli_forbidden_command")
```

- [ ] **Step 5: Delete cleanup methods and config from the site adapter**

Remove from `LiepinOpenCliSiteConfig`:

```python
idle_close_seconds: int = 120
close_blank_window: bool = False
cleanup_worker_enabled: bool = True
```

Delete these methods from `src/seektalent/providers/liepin/liepin_site_adapter.py`:

```python
cleanup_idle_lease()
watch_idle_lease()
cleanup_orphaned_tabs()
_forget_orphaned_owned_page_markers()
_lease_remaining_seconds()
_close_blank_window_if_enabled()
_launch_idle_cleanup_worker()
```

Keep `_delete_lease()` for active stale-lease replacement inside `open_liepin_tab()`.

- [ ] **Step 6: Remove AppleScript cleanup helpers from generic runtime after callers are gone**

Delete unused protocols/classes from `src/seektalent/opencli_browser/runtime.py`:

```python
ChromeWindowCounter
BlankChromeWindowCloser
SubprocessChromeWindowCounter
SubprocessBlankChromeWindowCloser
```

Update `OpenCliBrowserAutomation.__init__()` so it no longer accepts or stores `window_counter` and `blank_window_closer`.

- [ ] **Step 7: Update tests that asserted cleanup behavior**

Delete cleanup-specific tests from `tests/test_liepin_opencli_browser.py`:

```text
test_cleanup_idle_lease_releases_lease_without_closing_tabs
test_cleanup_idle_lease_preserves_owned_search_tab
test_cleanup_idle_lease_skips_close_when_owned_tab_cannot_be_reverified
test_cleanup_idle_lease_does_not_unbind_or_retry_when_close_fails
test_cleanup_idle_lease_keeps_owned_page_marker_for_user_managed_tabs
test_cleanup_idle_lease_does_not_close_without_owned_marker
test_cleanup_orphaned_tabs_without_lease_never_closes_chrome_tabs
test_cleanup_idle_lease_keeps_active_lease
test_cli_exposes_cleanup_orphaned_tabs
```

Update `tests/test_liepin_provider_source_composition.py` so public-method assertions no longer include:

```text
cleanup_idle_lease
cleanup_orphaned_tabs
watch_idle_lease
```

- [ ] **Step 8: Verify cleanup removal**

Run:

```bash
pytest tests/test_liepin_opencli_local_setup.py tests/test_liepin_opencli_browser.py::test_cli_rejects_removed_cleanup_actions tests/test_liepin_provider_source_composition.py -q
rg -n "cleanup_orphaned_tabs|cleanup_idle_lease|watch_idle_lease|close_blank_window|cleanup_worker_enabled|BlankChromeWindow|ChromeWindowCounter" src tests scripts
```

Expected: pytest PASS. `rg` has no live references except this plan file if included in the search.

Commit:

```bash
git add scripts/start-dev-workbench.sh src/seektalent/providers/liepin/opencli_browser_cli.py src/seektalent/providers/liepin/liepin_site_adapter.py src/seektalent/opencli_browser/automation.py src/seektalent/opencli_browser/runtime.py tests/test_liepin_opencli_local_setup.py tests/test_liepin_opencli_browser.py tests/test_liepin_provider_source_composition.py
git commit -m "refactor: remove Liepin OpenCLI tab cleanup automation"
```

---

### Task 3: Define Recruiter Search Surface Family And Fix URL/DOM Classification

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_opencli_policy.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_parsing.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent/config.py`
- Test: `tests/test_liepin_opencli_browser.py`
- Test: `tests/test_liepin_config.py`

- [ ] **Step 1: Add failing classification tests**

Add to `tests/test_liepin_opencli_browser.py`:

```python
def test_classifier_allows_recruiter_resume_search_surface_with_result_dom() -> None:
    state_text = (
        "URL: https://h.liepin.com/resume/search?keyword=Python\n"
        "<div id=resultList></div>\n"
        "<div class=detail-resume-card-wrap>候选人</div>\n"
        "共 30 位人选"
    )

    assert classify_liepin_state(url="https://h.liepin.com/resume/search?keyword=Python", text=state_text) is None


def test_state_reads_dom_before_classifying_resume_search_url() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/resume/search?keyword=Python",
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/search?keyword=Python\n"
                "<div id=resultList></div>\n"
                "共 30 位人选"
            ),
        }
    )

    result = _runner(commands).state()

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "state") in commands.calls


def test_classifier_allows_recruiter_search_surface_initial_state_without_result_dom() -> None:
    state_text = "URL: https://h.liepin.com/resume/search\n页面加载中"

    assert classify_liepin_state(url="https://h.liepin.com/resume/search", text=state_text) is None


def test_state_classifies_against_url_reported_by_state_output() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/resume/detail?id=old",
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/search\n"
                "页面加载中"
            ),
        }
    )

    result = _runner(commands).state()

    assert result.ok is True
```

Replace the old forbidden-before-reading test with:

```python
def test_state_reads_dom_then_blocks_non_search_resume_detail_url() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://www.liepin.com/resume/detail/123",
            ("opencli", "browser", "seektalent-liepin", "state"): "候选人详情",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_unknown_modal"
    assert ("opencli", "browser", "seektalent-liepin", "state") in commands.calls
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py -k "resume_search_surface or reads_dom" -q
```

Expected: FAIL because `resume/search` is classified as `liepin_opencli_unknown_modal`, `state()` returns before reading DOM, or `state()` classifies against the stale `get_url()` result instead of the URL reported by `state`.

- [ ] **Step 3: Add shared search-surface policy**

In `src/seektalent/providers/liepin/liepin_opencli_policy.py`, add:

```python
LIEPIN_OPENCLI_ALLOWED_HOSTS = ("www.liepin.com", "h.liepin.com", "c.liepin.com", "lpt.liepin.com")
LIEPIN_RECRUITER_SEARCH_SURFACE_PATHS = ("/search/getConditionItem", "/resume/search")
LIEPIN_RECRUITER_SEARCH_URLS = (
    "https://h.liepin.com/search/getConditionItem#session",
    "https://h.liepin.com/resume/search",
)
LIEPIN_RECRUITER_SEARCH_TAB_REUSE_FRAGMENTS = (
    "h.liepin.com/search/getConditionItem",
    "h.liepin.com/resume/search",
)
```

Keep `LIEPIN_RECRUITER_SEARCH_URL` as the canonical URL:

```python
LIEPIN_RECRUITER_SEARCH_URL = LIEPIN_RECRUITER_SEARCH_URLS[0]
```

- [ ] **Step 4: Add parser helpers**

In `src/seektalent/providers/liepin/liepin_site_parsing.py`, add:

```python
def _is_liepin_recruiter_search_surface(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in {"h.liepin.com", "c.liepin.com"}:
        return False
    path = (parsed.path or "/").rstrip("/")
    return path in {"/search/getConditionItem", "/resume/search"}


def _looks_like_liepin_search_result_surface(text: str) -> bool:
    return _looks_like_liepin_search_result_page(text) or extract_liepin_search_input_ref(text) is not None
```

Export or import these helpers where `liepin_site_adapter.py` needs them.

- [ ] **Step 5: Reorder classifier logic**

Replace `classify_liepin_state()` with:

```python
def classify_liepin_state(*, url: str, text: str) -> str | None:
    host = urlparse(url).hostname or ""
    lowered = text.lower()
    if host in LIEPIN_RISK_HOSTS:
        return "liepin_opencli_risk_page"
    if host not in LIEPIN_ALLOWED_HOSTS:
        return "liepin_opencli_host_blocked"
    if host == "lpt.liepin.com" and ("身份" in text or "请选择" in text):
        return "liepin_opencli_identity_intercept"
    if _looks_like_login_required(text):
        return "liepin_opencli_login_required"
    if "验证码" in text or "安全验证" in text or "风险提示" in text or re.search(r"\bcaptcha\b", lowered):
        return "liepin_opencli_risk_page"
    if _is_liepin_recruiter_search_surface(url):
        return None
    if _is_forbidden_liepin_url(url) and not _is_allowed_liepin_resume_detail_url(url):
        return "liepin_opencli_unknown_modal"
    return None
```

- [ ] **Step 6: Remove the URL-only terminal gate from `state()`**

Replace the start of `LiepinSiteAdapter.state()` with:

```python
def state(self) -> OpenCliBrowserResult:
    current_url = self._current_url()
    output = self._run_browser_command("state", ())
    observation = build_observation(output)
    observed_url = _state_url(output) or current_url
    terminal_reason = classify_liepin_state(url=observed_url, text=output)
    observation["terminal"] = terminal_reason is not None
```

Do not call `classify_liepin_state(url=current_url, text="")` for allowed Liepin hosts. Use the state-output URL when OpenCLI provides one, because `get_url()` and `state()` can observe different pages during Liepin redirects.

- [ ] **Step 7: Update search URL readiness**

Replace `_search_url_ready()` with:

```python
def _search_url_ready(snapshot: LiepinStateSnapshot) -> bool:
    return snapshot.url is not None and _is_liepin_recruiter_search_surface(snapshot.url)
```

Update `_validate_start_url()` so configured start URLs and known recruiter search surfaces are both accepted:

```python
def _validate_start_url(self, url: str) -> None:
    host = urlparse(url).hostname or ""
    if host not in self._site_config.allowed_hosts:
        raise OpenCliBrowserError("liepin_opencli_host_blocked")
    if url in self._site_config.allowed_start_urls or _is_liepin_recruiter_search_surface(url):
        return
    raise OpenCliBrowserError("liepin_opencli_start_url_blocked")
```

- [ ] **Step 8: Update config defaults**

Change:

```python
liepin_opencli_allowed_start_urls_json: str = (
    '["https://h.liepin.com/search/getConditionItem#session","https://h.liepin.com/resume/search"]'
)
```

Update `tests/test_liepin_config.py` expected defaults to include both URLs.

- [ ] **Step 9: Verify classifier and config behavior**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py -k "state_classifier or reads_dom or resume_search_surface or search_url or state_output" -q
pytest tests/test_liepin_config.py -q
```

Expected: PASS. Known recruiter search surfaces are non-terminal even without result DOM after login/risk/identity checks. Existing detail URL tests should still block `www.liepin.com/resume/detail/...` after DOM is read.

Commit:

```bash
git add src/seektalent/providers/liepin/liepin_opencli_policy.py src/seektalent/providers/liepin/liepin_site_parsing.py src/seektalent/providers/liepin/liepin_site_adapter.py src/seektalent/config.py tests/test_liepin_opencli_browser.py tests/test_liepin_config.py
git commit -m "fix: classify Liepin recruiter search surfaces by DOM"
```

---

### Task 4: Make `session_status()` A Real OpenCLI/Liepin Preflight

**Files:**
- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/liepin/opencli_worker_client.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Modify: `src/seektalent_ui/workbench_liepin_start_probe.py`
- Test: `tests/test_liepin_opencli_browser.py`
- Test: `tests/test_liepin_opencli_worker_client.py`
- Test: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Add failing worker-client tests**

Replace the two fake-ready tests in `tests/test_liepin_opencli_worker_client.py` with:

```python
@dataclass
class FakeRetriever:
    calls: list[object]
    session_status_value: object | None = None

    def ensure_ready(self) -> None:
        self.calls.append("ensure_ready")

    def session_status(self, *, connection_id: str, provider_account_hash: str | None):
        self.calls.append(("session_status", connection_id, provider_account_hash))
        return self.session_status_value

    def search_resumes(self, request):
        self.calls.append(request)
        return LiepinResumeSearchResponse(
            resumes=[],
            exhausted=True,
            requestPayload={"backend": "opencli"},
            rawCandidateCount=3,
        )


def test_opencli_worker_session_status_delegates_to_retriever_probe() -> None:
    expected = SessionStatus(
        connectionId="liepin-opencli",
        status="login_required",
        providerAccountHash=None,
        safeReasonCode="liepin_opencli_login_required",
        currentUrl="https://www.liepin.com/",
    )
    retriever = FakeRetriever(calls=[], session_status_value=expected)
    client = LiepinOpenCliWorkerClient(
        retriever=retriever,
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    status = asyncio.run(client.session_status(connection_id="liepin-opencli"))

    assert status == expected
    assert retriever.calls == [("session_status", "liepin-opencli", None)]


def test_opencli_worker_session_status_does_not_echo_requested_provider_hash() -> None:
    expected = SessionStatus(
        connectionId="liepin-opencli",
        status="ready",
        providerAccountHash="liepin-opencli-local-browser-profile",
        safeReasonCode="configured",
        currentUrl="https://h.liepin.com/search/getConditionItem#session",
        searchSurfaceReady=True,
    )
    retriever = FakeRetriever(calls=[], session_status_value=expected)
    client = LiepinOpenCliWorkerClient(
        retriever=retriever,
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    status = asyncio.run(
        client.session_status(
            connection_id="liepin-opencli",
            provider_account_hash="workbench-bound-real-account-hash",
        )
    )

    assert status.provider_account_hash == "liepin-opencli-local-browser-profile"
    assert retriever.calls == [("session_status", "liepin-opencli", "workbench-bound-real-account-hash")]
```

Import `SessionStatus` in the test file.

Add to `tests/test_liepin_opencli_browser.py`:

```python
def test_session_status_probe_prepares_search_surface_from_non_liepin_active_tab() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: running\nExtension: connected\n",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://github.com/",
                LIEPIN_SEARCH_URL,
                LIEPIN_SEARCH_URL,
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                f"URL: {LIEPIN_SEARCH_URL}\n"
                "<span>包含全部关键词</span>\n"
                "[27]<input type=search autocomplete=off role=combobox id=rc_select_1 />"
            ),
        }
    )

    status = _runner(commands).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash="caller-hash-that-must-not-be-echoed",
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "liepin-opencli-local-browser-profile"
    assert status.current_url == LIEPIN_SEARCH_URL
    assert status.search_surface_ready is True
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL) in commands.calls
```

- [ ] **Step 2: Extend `SessionStatus` contract**

In `src/seektalent/providers/liepin/worker_contracts.py`, replace the model with:

```python
class SessionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    status: Literal["missing", "login_required", "ready", "revoked"]
    provider_account_hash: str | None = Field(default=None, alias="providerAccountHash")
    fixture_only: bool = Field(default=False, alias="fixtureOnly")
    safe_reason_code: str | None = Field(default=None, alias="safeReasonCode")
    current_url: str | None = Field(default=None, alias="currentUrl")
    search_surface_ready: bool = Field(default=False, alias="searchSurfaceReady")
    result_surface_ready: bool = Field(default=False, alias="resultSurfaceReady")
```

- [ ] **Step 3: Add a site-level readiness probe**

Add to `LiepinSiteAdapter`:

```python
OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT = "liepin-opencli-local-browser-profile"


def session_status_probe(self, *, connection_id: str, provider_account_hash: str | None) -> SessionStatus:
    del provider_account_hash
    status = self.status()
    if not status.ok:
        return SessionStatus(
            connectionId=connection_id,
            status="missing",
            providerAccountHash=None,
            safeReasonCode=status.safe_reason_code,
        )

    try:
        opened = self.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)
    except OpenCliBrowserError as exc:
        return SessionStatus(
            connectionId=connection_id,
            status=_session_status_for_liepin_reason(exc.safe_reason_code),
            providerAccountHash=None,
            safeReasonCode=exc.safe_reason_code,
        )
    if not opened.ok:
        return SessionStatus(
            connectionId=connection_id,
            status=_session_status_for_liepin_reason(opened.safe_reason_code),
            providerAccountHash=None,
            safeReasonCode=opened.safe_reason_code,
        )

    current_url = ""
    try:
        state = self.state()
    except OpenCliBrowserError as exc:
        return SessionStatus(
            connectionId=connection_id,
            status=_session_status_for_liepin_reason(exc.safe_reason_code),
            providerAccountHash=None,
            safeReasonCode=exc.safe_reason_code,
            currentUrl=current_url or None,
        )
    state_text = state.private_output or str(state.observation.get("text") or "")
    current_url = _state_url(state_text) or self._current_url()
    if not state.ok:
        return SessionStatus(
            connectionId=connection_id,
            status=_session_status_for_liepin_reason(state.safe_reason_code),
            providerAccountHash=None,
            safeReasonCode=state.safe_reason_code,
            currentUrl=current_url or None,
        )
    search_ready = _is_liepin_recruiter_search_surface(current_url)
    result_ready = _looks_like_liepin_search_result_surface(state_text)
    if not search_ready:
        return SessionStatus(
            connectionId=connection_id,
            status="missing",
            providerAccountHash=None,
            safeReasonCode="liepin_opencli_search_not_ready",
            currentUrl=current_url or None,
            searchSurfaceReady=False,
            resultSurfaceReady=result_ready,
        )
    return SessionStatus(
        connectionId=connection_id,
        status="ready",
        providerAccountHash=OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT,
        safeReasonCode="configured",
        currentUrl=current_url or None,
        searchSurfaceReady=search_ready,
        resultSurfaceReady=result_ready,
    )
```

This probe intentionally changes browser state by opening or reusing the canonical recruiter search surface. That is acceptable because the Workbench start probe is meant to prove that a Liepin search can begin. It must not reject a user just because their currently active Chrome tab started somewhere else.

`OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT` is a browser-profile binding subject, not a real Liepin account identity. Do not copy `provider_account_hash` from the request into the ready response unless a future DOM probe reads and hashes a real provider account subject.

Add helper:

```python
def _session_status_for_liepin_reason(reason: str | None) -> str:
    if reason == "liepin_opencli_login_required":
        return "login_required"
    if reason in {"liepin_opencli_identity_intercept", "liepin_opencli_risk_page", "liepin_opencli_unknown_modal"}:
        return "login_required"
    return "missing"
```

- [ ] **Step 4: Expose probe through the retriever**

In `src/seektalent/providers/liepin/opencli_retriever.py`, extend the protocol:

```python
class LiepinResumeSearchSite(Protocol):
    def status(self): ...
    def session_status_probe(self, *, connection_id: str, provider_account_hash: str | None): ...
```

Add:

```python
def session_status(self, *, connection_id: str, provider_account_hash: str | None):
    return self._runner.session_status_probe(
        connection_id=connection_id,
        provider_account_hash=provider_account_hash,
    )
```

- [ ] **Step 5: Delegate worker-client `session_status()` to the retriever**

Replace `LiepinOpenCliWorkerClient.session_status()` with:

```python
async def session_status(
    self,
    *,
    connection_id: str,
    tenant: str | None = None,
    workspace: str | None = None,
    provider_account_hash: str | None = None,
) -> SessionStatus:
    del tenant, workspace
    probe = getattr(self._retriever, "session_status")
    return await asyncio.to_thread(
        probe,
        connection_id=connection_id or self._connection_id,
        provider_account_hash=provider_account_hash,
    )
```

- [ ] **Step 6: Preserve raw reason codes in Workbench start probe**

Replace the local hand-maintained reason set with an imported authoritative Liepin set:

```python
from seektalent.sources.liepin.reason_codes import LIEPIN_WORKER_SAFE_REASON_CODES

RUNTIME_SOURCE_REASON_CODES = {
    "blocked_backend_unavailable",
    "failed_provider_error",
    "login_required",
    "partial_timeout",
    "cancelled_by_user",
    "liepin_connection_not_connected",
    "liepin_browser_login_required",
    "liepin_browser_probe_unavailable",
    "liepin_browser_account_mismatch",
    "runtime_failed",
    *LIEPIN_WORKER_SAFE_REASON_CODES,
}
```

Add helper to `src/seektalent_ui/workbench_liepin_start_probe.py`:

```python
def _status_warning_code(status: SessionStatus, default_code: str) -> str:
    code = str(status.safe_reason_code or "").strip()
    if code in RUNTIME_SOURCE_REASON_CODES and code.startswith("liepin_opencli_"):
        return code
    return default_code
```

Use it where `status.status != "ready"`:

```python
warning_code = _status_warning_code(status, LIEPIN_BROWSER_LOGIN_REQUIRED_CODE)
warning_message = liepin_start_probe_warning_message(warning_code)
```

Pass `warning_code` and `warning_message` to `_mark_login_required()`. Return `LiepinStartProbeResult(ready=False, reason_code=warning_code, warning_message=warning_message)`.

When `status.status == "ready"`, Workbench may bind/check `status.provider_account_hash` as the OpenCLI local browser-profile subject. It must not treat that value as a verified real Liepin account subject unless `SessionStatus` later gains an explicit provider-account identity proof field backed by DOM evidence.

- [ ] **Step 7: Verify session preflight**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py -k "session_status_probe" -q
pytest tests/test_liepin_opencli_worker_client.py tests/test_workbench_liepin_browser_session_probe.py -q
```

Expected: PASS. The fake-ready behavior is gone; tests assert raw `liepin_opencli_*` reasons.

Commit:

```bash
git add src/seektalent/providers/liepin/worker_contracts.py src/seektalent/providers/liepin/opencli_retriever.py src/seektalent/providers/liepin/opencli_worker_client.py src/seektalent/providers/liepin/liepin_site_adapter.py src/seektalent_ui/workbench_liepin_start_probe.py tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_worker_client.py tests/test_workbench_liepin_browser_session_probe.py
git commit -m "fix: probe real Liepin OpenCLI session readiness"
```

---

### Task 5: Remove `managed_local` Compatibility And Align Dev/Prod OpenCLI Config

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/sources/liepin/runtime_lane.py`
- Modify: `src/seektalent_ui/server.py`
- Modify: `src/seektalent/cli.py`
- Modify: `src/seektalent/liepin_smoke_cli.py`
- Test: `tests/test_liepin_worker_client.py`
- Test: `tests/test_liepin_provider_adapter.py`
- Test: `tests/test_liepin_boundaries.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_runtime_source_lanes.py`

- [ ] **Step 1: Add failing tests that `managed_local` is rejected**

Add to `tests/test_liepin_config.py`:

```python
def test_managed_local_worker_mode_is_removed() -> None:
    with pytest.raises(ValidationError, match="managed_local"):
        AppSettings(_env_file=None, liepin_worker_mode="managed_local")
```

Replace `tests/test_liepin_boundaries.py::test_managed_local_worker_mode_uses_opencli_compatibility_path` with:

```python
def test_managed_local_worker_mode_is_not_a_live_compatibility_path() -> None:
    with pytest.raises(Exception, match="managed_local"):
        make_settings(liepin_worker_mode="managed_local")
```

- [ ] **Step 2: Run removal tests**

Run:

```bash
pytest tests/test_liepin_config.py::test_managed_local_worker_mode_is_removed \
  tests/test_liepin_boundaries.py::test_managed_local_worker_mode_is_not_a_live_compatibility_path -q
```

Expected: FAIL because `managed_local` is still accepted and mapped to OpenCLI.

- [ ] **Step 3: Remove mode from config and CLI choices**

In `src/seektalent/config.py`, remove `managed_local` from `LiepinWorkerMode`.

In argparse choices in `src/seektalent_ui/server.py` and `src/seektalent/cli.py`, change:

```python
choices=["disabled", "fake_fixture", "external_http", "opencli"]
```

In `src/seektalent/liepin_smoke_cli.py`, replace:

```python
if configured_mode in {"fake_fixture", "managed_local", "external_http", "opencli"}:
```

with:

```python
if configured_mode in {"fake_fixture", "external_http", "opencli"}:
```

- [ ] **Step 4: Remove compatibility construction path**

Delete from `build_liepin_worker_client()`:

```python
if settings.liepin_worker_mode == "managed_local":
    return build_liepin_opencli_worker_client(
        settings.with_overrides(
            liepin_worker_mode="opencli",
            liepin_browser_action_backend="opencli",
        )
    )
```

Update `is_live_liepin_worker_mode()` so only `"opencli"` and `"external_http"` are live if the existing external worker path remains supported.

Update `liepin_backend_posture()` so only `"opencli"` returns OpenCLI:

```python
if worker_mode == "opencli":
    return {"backend_mode": "opencli", "reason": "opencli"}
if worker_mode == "external_http":
    return {"backend_mode": "worker_external_http", "reason": "external_http"}
```

- [ ] **Step 5: Replace test setup modes that are not testing mode selection**

In tests that use `liepin_worker_mode="managed_local"` only to exercise live Liepin behavior, replace with:

```python
liepin_worker_mode="opencli"
```

Do not add a shim that maps `"managed_local"` to `"opencli"`.

- [ ] **Step 6: Align OpenCLI CLI defaults with app settings**

In `opencli_browser_cli._runner_from_env()`, import policy defaults:

```python
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_OPENCLI_ALLOWED_HOSTS, LIEPIN_RECRUITER_SEARCH_URLS
```

Use:

```python
default=LIEPIN_OPENCLI_ALLOWED_HOSTS
default=LIEPIN_RECRUITER_SEARCH_URLS
```

for allowed hosts and start URLs. This removes config drift between Workbench, helper CLI, dev, and prod.

- [ ] **Step 7: Verify mode and config convergence**

Run:

```bash
pytest tests/test_liepin_config.py tests/test_liepin_worker_client.py tests/test_liepin_provider_adapter.py tests/test_liepin_boundaries.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_source_lanes.py -q
rg -n '"managed_local"|managed_local|worker_compat' src tests apps scripts
```

Expected: pytest PASS. `rg` has no live `managed_local` compatibility path except historical docs/plans if searched.

Commit:

```bash
git add src/seektalent/config.py src/seektalent/providers/liepin/client.py src/seektalent/providers/liepin/opencli_browser_cli.py src/seektalent/sources/liepin/runtime_lane.py src/seektalent_ui/server.py src/seektalent/cli.py src/seektalent/liepin_smoke_cli.py tests/test_liepin_config.py tests/test_liepin_worker_client.py tests/test_liepin_provider_adapter.py tests/test_liepin_boundaries.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_source_lanes.py
git commit -m "refactor: remove managed_local Liepin compatibility mode"
```

---

### Task 6: Lock Liepin OpenCLI Local JSON State Updates

**Files:**
- Create: `src/seektalent/providers/liepin/opencli_local_state.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Test: `tests/test_liepin_opencli_local_state.py`
- Test: `tests/test_liepin_opencli_browser.py`

- [ ] **Step 1: Add failing tests for locked JSON updates and preserved schemas**

Create `tests/test_liepin_opencli_local_state.py`:

```python
from __future__ import annotations

import json
import threading
from pathlib import Path

from seektalent.providers.liepin.opencli_local_state import locked_json_update


def test_locked_json_update_preserves_concurrent_appends(tmp_path: Path) -> None:
    path = tmp_path / "agent-events.json"

    def append_event(index: int) -> None:
        def update(value: object) -> dict[str, object]:
            loaded = value if isinstance(value, dict) else {}
            raw_events = loaded.get("events")
            events = raw_events if isinstance(raw_events, list) else []
            return {
                "schema_version": "seektalent.opencli_agent_events.v1",
                "events": [*events, {"index": index}],
            }

        locked_json_update(
            path,
            default={"schema_version": "seektalent.opencli_agent_events.v1", "events": []},
            update=update,
        )

    threads = [threading.Thread(target=append_event, args=(index,)) for index in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "seektalent.opencli_agent_events.v1"
    assert sorted(event["index"] for event in payload["events"]) == list(range(25))


def test_locked_json_update_preserves_dict_schema(tmp_path: Path) -> None:
    path = tmp_path / "agent-events.json"

    def update(value: object) -> dict[str, object]:
        loaded = value if isinstance(value, dict) else {}
        raw_events = loaded.get("events")
        events = raw_events if isinstance(raw_events, list) else []
        return {
            "schema_version": "seektalent.opencli_agent_events.v1",
            "events": [*events, {"action_kind": "observe"}],
        }

    locked_json_update(
        path,
        default={"schema_version": "seektalent.opencli_agent_events.v1", "events": []},
        update=update,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": "seektalent.opencli_agent_events.v1",
        "events": [{"action_kind": "observe"}],
    }
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_liepin_opencli_local_state.py -q
```

Expected: FAIL because `opencli_local_state.py` does not exist.

- [ ] **Step 3: Implement file-lock helper**

Create `src/seektalent/providers/liepin/opencli_local_state.py`:

```python
from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar


T = TypeVar("T")


@contextmanager
def opencli_state_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        _lock_file(lock_file)
        try:
            yield
        finally:
            _unlock_file(lock_file)


def _lock_file(lock_file) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return
    raise RuntimeError("liepin_opencli_file_lock_unsupported")


def _unlock_file(lock_file) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return


def locked_json_update(path: Path, *, default: object, update: Callable[[object], object]) -> object:
    with opencli_state_lock(path):
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            current = default
        next_value = update(current)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(next_value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return next_value
```

- [ ] **Step 4: Use locks around complete adapter read-modify-write transactions**

In `liepin_site_adapter.py`, import:

```python
from seektalent.providers.liepin.opencli_local_state import locked_json_update, opencli_state_lock
```

Wrap lease writes and deletes, but do not rely on write-only locking for read-modify-write operations:

```python
def _write_lease_payload(self, payload: Mapping[str, object]) -> None:
    path = self._lease_path()
    with opencli_state_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(payload), sort_keys=True), encoding="utf-8")
        tmp.replace(path)
```

Wrap lease deletion:

```python
def _delete_lease(self) -> None:
    path = self._lease_path()
    with opencli_state_lock(path):
        path.unlink(missing_ok=True)
```

Replace `_touch_lease()` so the read, mutation, and write happen under one lock:

```python
def _touch_lease(self) -> None:
    path = self._lease_path()
    with opencli_state_lock(path):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_lease_malformed") from exc
        if not isinstance(loaded, dict):
            raise OpenCliBrowserError("liepin_opencli_lease_malformed")
        loaded["last_activity_at"] = time.time()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(loaded, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
```

Replace `_append_agent_event()` body while preserving the existing dict schema:

```python
def _append_agent_event(self, source_run_id: str, event: Mapping[str, object]) -> None:
    safe_run_id = _safe_artifact_segment(source_run_id)
    path = self._pi_artifact_path("protected", f"pi-trace/{safe_run_id}/agent-events.json")

    def update(value: object) -> dict[str, object]:
        loaded = value if isinstance(value, dict) else {}
        raw_events = loaded.get("events")
        events = raw_events if isinstance(raw_events, list) else []
        return {
            "schema_version": "seektalent.opencli_agent_events.v1",
            "events": [*events, dict(event)],
        }

    locked_json_update(
        path,
        default={"schema_version": "seektalent.opencli_agent_events.v1", "events": []},
        update=update,
    )
```

In `_write_owned_page_marker()` and `_forget_owned_page_marker()`, hold `opencli_state_lock(self._owned_pages_path())` across read, mutation, and write.

Replace the collected resume read-append-write in `_capture_liepin_detail_resume()` with a locked upsert helper:

```python
def _upsert_collected_resume(
    self,
    safe_run_id: str,
    *,
    rank: int,
    resume: Mapping[str, object],
) -> list[dict[str, object]]:
    path = self._pi_artifact_path("protected", f"pi-detail/{safe_run_id}/collected-resumes.json")

    def update(value: object) -> dict[str, object]:
        loaded = value if isinstance(value, dict) else {}
        raw_resumes = loaded.get("resumes")
        resumes = [dict(item) for item in raw_resumes if isinstance(item, dict)] if isinstance(raw_resumes, list) else []
        resumes = [item for item in resumes if item.get("provider_rank") != rank]
        resumes.append(dict(resume))
        resumes.sort(key=lambda item: _positive_int_or_none(item.get("provider_rank")) or 0)
        return {
            "schema_version": "seektalent.opencli_collected_resumes.v1",
            "resumes": resumes,
        }

    payload = locked_json_update(
        path,
        default={"schema_version": "seektalent.opencli_collected_resumes.v1", "resumes": []},
        update=update,
    )
    raw_resumes = payload.get("resumes") if isinstance(payload, dict) else None
    if not isinstance(raw_resumes, list):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    return [dict(item) for item in raw_resumes if isinstance(item, dict)]
```

Then replace:

```python
resumes = [item for item in self._read_collected_resumes(safe_run_id) if item.get("provider_rank") != rank]
resumes.append(resume)
resumes.sort(key=lambda item: _positive_int_or_none(item.get("provider_rank")) or 0)
self._write_collected_resumes(safe_run_id, resumes)
```

with:

```python
resumes = self._upsert_collected_resume(safe_run_id, rank=rank, resume=resume)
```

- [ ] **Step 5: Verify local state updates**

Run:

```bash
pytest tests/test_liepin_opencli_local_state.py tests/test_liepin_opencli_browser.py -k "owned_pages or append_agent_event or collected_resumes or lease" -q
```

Expected: PASS. Existing stale lease and owned-marker recovery tests still pass. `agent-events.json` and `collected-resumes.json` remain dict payloads with their existing `schema_version` fields.

Commit:

```bash
git add src/seektalent/providers/liepin/opencli_local_state.py src/seektalent/providers/liepin/liepin_site_adapter.py tests/test_liepin_opencli_local_state.py tests/test_liepin_opencli_browser.py
git commit -m "fix: lock Liepin OpenCLI local state writes"
```

---

### Task 7: Use OpenCLI Daemon Restart For Recovery, Not Local Browser AppleScript State

**Files:**
- Modify: `src/seektalent/opencli_browser/automation.py`
- Modify: `src/seektalent/opencli_browser/runtime.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Test: `tests/test_liepin_opencli_browser.py`
- Test: `tests/test_liepin_opencli_browser_window_policy.py`

- [ ] **Step 1: Add failing recovery test**

Add to `tests/test_liepin_opencli_browser.py`:

```python
def test_recover_connection_restarts_opencli_daemon_without_current_chrome_tab_opener() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): [
                "Daemon: stale\nExtension: disconnected\n",
                "Daemon: running\nExtension: connected\n",
            ],
            ("opencli", "daemon", "restart"): "Daemon: running\nExtension: connected\n",
        }
    )
    opener = FakeCurrentChromeTabOpener(result=False, commands=commands)

    result = _runner(commands, current_tab_opener=opener).recover_connection()

    assert result.ok is True
    assert ("opencli", "daemon", "restart") in commands.calls
    assert opener.calls == []
```

- [ ] **Step 2: Run the failing recovery test**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py::test_recover_connection_restarts_opencli_daemon_without_current_chrome_tab_opener -q
```

Expected: FAIL because `recover_connection()` still uses `current_tab_opener.open_tab()`.

- [ ] **Step 3: Add generic OpenCLI daemon restart**

In `OpenCliBrowserAutomation`, add:

```python
def restart_daemon(self) -> OpenCliBrowserResult:
    try:
        output = self._run(tuple(self.config.command) + ("daemon", "restart"))
    except OpenCliBrowserError as exc:
        return OpenCliBrowserResult(ok=False, action="restart_daemon", safe_reason_code=exc.safe_reason_code)
    reason = _opencli_status_reason(output)
    if reason is not None:
        return OpenCliBrowserResult(ok=False, action="restart_daemon", safe_reason_code=reason, private_output=output)
    return OpenCliBrowserResult(ok=True, action="restart_daemon", private_output=output)
```

- [ ] **Step 4: Replace Liepin recovery behavior**

Replace `LiepinSiteAdapter.recover_connection()` with:

```python
def recover_connection(self) -> OpenCliBrowserResult:
    status = self.status()
    if status.ok:
        return OpenCliBrowserResult(ok=True, action="recover_connection", counts={"already_ready": 1})
    if status.safe_reason_code not in _RECOVERABLE_CONNECTION_REASONS:
        return OpenCliBrowserResult(
            ok=False,
            action="recover_connection",
            safe_reason_code=status.safe_reason_code,
            private_output=status.private_output,
        )
    restarted = liepin_result_from_opencli_result(self._automation.restart_daemon())
    if not restarted.ok:
        return OpenCliBrowserResult(
            ok=False,
            action="recover_connection",
            safe_reason_code=restarted.safe_reason_code,
            private_output=restarted.private_output,
        )
    last_status = restarted
    for _attempt in range(5):
        time.sleep(1)
        last_status = self.status()
        if last_status.ok:
            return OpenCliBrowserResult(ok=True, action="recover_connection", counts={"restarted": 1})
    return OpenCliBrowserResult(
        ok=False,
        action="recover_connection",
        safe_reason_code=last_status.safe_reason_code,
        private_output=last_status.private_output,
    )
```

- [ ] **Step 5: Remove current-tab opener from the critical path**

After the test passes, remove `CurrentChromeTabOpener` from `OpenCliBrowserAutomation.__init__()` and remove `SubprocessCurrentChromeTabOpener` from `runtime.py` if no callers remain.

Update test helpers so `_runner()` no longer accepts `current_tab_opener` once production code no longer does.

- [ ] **Step 6: Verify recovery and window-policy tests**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py -k "recover_connection or open_liepin_tab" -q
pytest tests/test_liepin_opencli_browser_window_policy.py -q
rg -n "CurrentChromeTabOpener|osascript|Google Chrome|current_tab_opener|BlankChromeWindow|ChromeWindowCounter" src tests
```

Expected: pytest PASS. `rg` has no critical-path AppleScript helper references in `src`.

Commit:

```bash
git add src/seektalent/opencli_browser/automation.py src/seektalent/opencli_browser/runtime.py src/seektalent/providers/liepin/liepin_site_adapter.py tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_browser_window_policy.py
git commit -m "fix: recover Liepin OpenCLI via daemon restart"
```

---

### Task 8: End-To-End Reason Propagation And Verification

**Files:**
- Modify: `src/seektalent/opencli_browser/reason_codes.py`
- Modify: `src/seektalent/opencli_browser/automation.py`
- Modify: `src/seektalent/providers/liepin/liepin_opencli_policy.py`
- Modify: `src/seektalent/sources/liepin/reason_codes.py`
- Modify: `src/seektalent/cli.py`
- Modify: `src/seektalent/dev_mode.py`
- Modify: `src/seektalent_ui/workbench_liepin_start_probe.py`
- Test: `tests/test_liepin_cli.py`
- Test: `tests/test_liepin_opencli_local_setup.py`
- Test: `tests/test_workbench_liepin_browser_session_probe.py`
- Test: `tests/test_runtime_public_event_contract.py`

- [ ] **Step 1: Add failing tests for raw preflight reasons**

In `tests/test_workbench_liepin_browser_session_probe.py`, add or update a fake session-status case:

```python
async def test_opencli_start_probe_preserves_raw_session_status_reason(tmp_path: Path) -> None:
    status = SessionStatus(
        connectionId="liepin-opencli",
        status="login_required",
        providerAccountHash=None,
        safeReasonCode="liepin_opencli_identity_intercept",
        currentUrl="https://lpt.liepin.com/",
    )
    result = await _run_opencli_start_probe_with_status(tmp_path, status)

    assert result.ready is False
    assert result.reason_code == "liepin_opencli_identity_intercept"
```

Use the existing test helper in that file for constructing the request/store/user fixture.

- [ ] **Step 2: Run the failing reason test**

Run:

```bash
pytest tests/test_workbench_liepin_browser_session_probe.py -k "raw_session_status_reason or identity_intercept" -q
```

Expected: FAIL if Workbench still maps this to `liepin_browser_login_required` or `source_browser_backend_unavailable`.

- [ ] **Step 3: Keep CLI preflight raw**

In `src/seektalent/cli.py`, keep `_workbench_action_reason()` returning the helper action `safeReasonCode` directly:

```python
def _workbench_action_reason(result: Mapping[str, object]) -> str:
    reason = str(result.get("safeReasonCode") or "").strip()
    if reason.startswith("liepin_opencli_"):
        return reason
    return "liepin_opencli_status_unavailable"
```

Add missing messages for any new raw reasons used by `session_status()`:

```python
"liepin_opencli_search_not_ready": "Liepin recruiter search is not ready in the browser.",
"liepin_opencli_results_not_ready": "Liepin search results are not ready in the browser.",
```

- [ ] **Step 4: Implement managed launcher bootstrap failure as a real reason**

In `src/seektalent/opencli_browser/reason_codes.py`, add:

```python
OPENCLI_BOOTSTRAP_FAILED = "opencli_bootstrap_failed"
```

In `src/seektalent/providers/liepin/liepin_opencli_policy.py`, add the mapping:

```python
OPENCLI_BOOTSTRAP_FAILED: "liepin_opencli_bootstrap_failed",
```

In `src/seektalent/sources/liepin/reason_codes.py`, add `liepin_opencli_bootstrap_failed` to `LIEPIN_WORKER_SAFE_REASON_CODES` and map it to `source_browser_backend_unavailable` in public/source-lane maps.

In `OpenCliBrowserAutomation._run()`, detect the Python launcher failure:

```python
except subprocess.CalledProcessError as exc:
    output = f"{getattr(exc, 'stdout', None) or getattr(exc, 'output', '') or ''}\n{exc.stderr or ''}"
    if exc.returncode == 127 and "SeekTalent OpenCLI bootstrap failed:" in output:
        raise OpenCliBrowserError(OPENCLI_BOOTSTRAP_FAILED) from exc
```

Keep this before generic daemon/status parsing so bootstrap failure does not collapse to `opencli_status_unavailable`.

- [ ] **Step 5: Make dev diagnostics more than command existence**

In `src/seektalent/dev_mode.py`, add a status field that distinguishes command existence from runtime readiness without running browser actions in pure config diagnostics:

```python
return _component(
    "liepin_opencli_browser",
    "Liepin browser channel",
    "configured",
    reason_code="liepin_opencli_preflight_required",
)
```

Use the real preflight path in Workbench start checks, not dev diagnostics, so diagnostics do not falsely claim browser readiness.

- [ ] **Step 6: Verify reason propagation**

Run:

```bash
pytest tests/test_liepin_cli.py tests/test_liepin_opencli_local_setup.py tests/test_workbench_liepin_browser_session_probe.py tests/test_runtime_public_event_contract.py -q
rg -n "RUNTIME_SOURCE_REASON_CODES = \\{" src/seektalent_ui/workbench_liepin_start_probe.py
```

Expected: PASS. Public runtime events may still publish public-safe reason codes, but Workbench preflight and internal Liepin session status preserve raw `liepin_opencli_*` reasons. The `rg` result should show only a set that composes `LIEPIN_WORKER_SAFE_REASON_CODES`, not a manually duplicated partial Liepin list.

Commit:

```bash
git add src/seektalent/opencli_browser/reason_codes.py src/seektalent/opencli_browser/automation.py src/seektalent/providers/liepin/liepin_opencli_policy.py src/seektalent/sources/liepin/reason_codes.py src/seektalent/cli.py src/seektalent/dev_mode.py src/seektalent_ui/workbench_liepin_start_probe.py tests/test_liepin_cli.py tests/test_liepin_opencli_local_setup.py tests/test_workbench_liepin_browser_session_probe.py tests/test_runtime_public_event_contract.py
git commit -m "fix: preserve raw Liepin OpenCLI readiness reasons"
```

---

### Task 9: Full Verification

**Files:**
- No new source files.
- Uses all files touched by prior tasks.

- [ ] **Step 1: Run focused Liepin/OpenCLI suite**

Run:

```bash
pytest tests/test_liepin_opencli_browser.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_local_setup.py \
  tests/test_liepin_config.py \
  tests/test_workbench_liepin_browser_session_probe.py \
  tests/test_liepin_worker_client.py \
  tests/test_liepin_provider_adapter.py \
  tests/test_liepin_runtime_source_lane.py -q
```

Expected: PASS.

- [ ] **Step 2: Run boundary and reason-code contract tests**

Run:

```bash
pytest tests/test_liepin_boundaries.py \
  tests/test_liepin_provider_source_composition.py \
  tests/test_runtime_public_event_contract.py \
  tests/test_source_registry_contract.py \
  tests/test_runtime_source_lanes.py -q
```

Expected: PASS.

- [ ] **Step 3: Run static drift checks**

Run:

```bash
rg -n "1\\.8\\.0|1\\.8\\.3|managed_local|cleanup_orphaned_tabs|cleanup_idle_lease|watch_idle_lease|current_tab_opener|SubprocessCurrentChromeTabOpener|BlankChromeWindow|ChromeWindowCounter" src tests scripts apps/web-react -g '!apps/web-react/node_modules/**'
```

Expected: no live references. If update-notice test strings remain, narrow the command to source/runtime files and confirm they are only fixture text.

- [ ] **Step 4: Run package checks**

Run:

```bash
pytest -q
corepack pnpm --dir apps/web-react install --frozen-lockfile
corepack pnpm --dir apps/web-react check
```

Expected: PASS. If full pytest is too slow, run the failing subset first, then run the full suite before final handoff.

- [ ] **Step 5: Manual installed-runtime smoke on this Mac**

Run:

```bash
uv run python -m seektalent.opencli_launcher daemon status
uv run seektalent workbench --help
```

Expected: the launcher bootstraps OpenCLI 1.8.6 if needed. `daemon status` returns a structured OpenCLI status or a raw `liepin_opencli_*` failure path through the Workbench preflight.

- [ ] **Step 6: Route verification fixes back to the owning task**

Run:

```bash
git status --short
git diff --name-only
```

Expected: no output if prior task commits already covered all changes. If verification required an additional fix, return to the task that owns the touched file and commit it with that task's commit message pattern.

---

## Self-Review

- Spec coverage: OpenCLI version pin, URL/DOM classifier, real session preflight, raw reason propagation, dev/prod convergence, local-state locking, cleanup removal, and compatibility removal are all covered by tasks.
- Placeholder scan: no `TBD`, `TODO`, `implement later`, or unspecified edge-case steps remain.
- Type consistency: `SessionStatus.safe_reason_code`, `current_url`, `search_surface_ready`, and `result_surface_ready` are introduced before use. `session_status_probe()` returns the same Pydantic contract consumed by the worker client and Workbench.
- Scope note: this plan intentionally touches more than eight files because the user explicitly requested one hardening pass with no old-compat fallback. The work should still be executed in task commits so regressions can be isolated.
