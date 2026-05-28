# OpenCLI Liepin Execution Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real OpenCLI Liepin path fail closed on required filter failures, expose precise readiness/errors, and document the BYOK OpenCLI execution contract.

**Architecture:** Keep the current OpenCLI runner and runtime source lane boundaries. Tighten behavior inside `OpenCliBrowserRunner`: required native filters become verified action-state transitions, OpenCLI status/error parsing produces specific safe reason codes, and the internal detail URL probe is made explicit. Worker readiness delegates to the existing retriever/runner status path; docs are corrected to reflect real OpenCLI-by-default local Workbench behavior.

**Tech Stack:** Python 3.12, pytest, dataclasses/Pydantic-adjacent worker contracts, Bash launcher script, Bun-managed Svelte workspace dependency path for OpenCLI.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-27-opencli-liepin-execution-contract-design.md`

It extends but does not replace:

`docs/superpowers/specs/2026-05-27-liepin-observable-source-subworkflow-design.md`

## Execution Notes

- Do not add a live-behavior kill switch for OpenCLI Liepin.
- Do not change CTS query/filter behavior.
- Do not change controller-authored query/filter ownership.
- Do not add system-level tab cleanup or OS/browser process automation.
- Do not reintroduce auto-close of user-owned or workflow-created Liepin detail tabs.
- Do not keep a no-op detail-tab cleanup hook that implies tabs are closed. Source-run-owned detail tabs stay open in this slice; safe closing is a follow-up after the OpenCLI fork.
- Use Bun for frontend workspace commands; do not use npm.
- Keep changes surgical. The current branch is already dirty from prior OpenCLI tab-reuse work, so stage only files touched for each task.

## File Map

- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Required native filter fail-closed behavior.
  - Selected-filter verification after each click.
  - OpenCLI structured error parsing and status reason mapping.
  - Internal fixed read-only detail URL probe boundary.
  - No public detail-tab cleanup action until the OpenCLI fork provides safe lifecycle support.
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
  - Capability wording that separates external arbitrary eval from internal fixed probes.
- Modify: `src/seektalent/runtime/source_lanes.py`
  - Allow and public-map new safe OpenCLI reason codes in runtime source events.
- Modify: `src/seektalent/runtime/public_events.py`
  - Public-map new safe OpenCLI reason codes for runtime event streams.
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
  - Preserve new safe OpenCLI reason codes from worker failures.
- Modify: `src/seektalent_ui/workbench_routes.py`
  - Allow new safe OpenCLI reason codes through Workbench start-probe handling.
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
  - Add a small readiness method on the retriever.
- Modify: `src/seektalent/providers/liepin/opencli_worker_client.py`
  - Make `ensure_ready()` call real OpenCLI readiness and raise `LiepinWorkerModeError` with the precise code.
- Modify: `scripts/start-dev-workbench.sh`
  - Detect `Daemon: stale` and restart OpenCLI daemon.
  - Keep the exit cleanup limited to backend/process and lease-marker cleanup; it must not close Liepin browser tabs.
- Modify: `.env.example`
  - Reword the misleading `SEEKTALENT_LIEPIN_LIVE_ENABLED=false` live-gate comment as a fixture-safety flag.
- Modify: `src/seektalent/default.env`
  - Keep default environment comments aligned with `.env.example`.
- Modify: `README.md`
  - Replace old Pi launcher wording with the current OpenCLI local Workbench path.
- Test: `tests/test_pi_opencli_browser.py`
  - Filter fail-closed, verification failure, structured errors, status reason mapping, internal probe boundary.
- Test: `tests/test_liepin_opencli_worker_client.py`
  - `ensure_ready()` success/failure behavior.
- Test: `tests/test_pi_dokobot_local_setup.py`
  - Launcher stale daemon detection and OpenCLI wording expectations.
- Test: `tests/test_dev_mode_readiness.py`
  - Existing static setup diagnostics must stay green after docs/env alignment.
- Test: `tests/test_liepin_runtime_source_lane.py`
  - New OpenCLI reason codes are preserved from worker failures.
- Test: `tests/test_workbench_api.py`
  - Workbench start-probe handling accepts the new safe reason codes.

---

### Task 1: Make Required Native Filter Failures Block Search

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Test: `tests/test_pi_opencli_browser.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Replace the existing fail-open test with a fail-closed test**

In `tests/test_pi_opencli_browser.py`, replace `test_search_liepin_cards_records_filter_failure_without_blocking_cards` with:

```python
def test_search_liepin_cards_blocks_when_required_native_filter_click_fails(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
                state_city_menu,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(1, ["opencli"]),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
    assert envelope["cards"] == []
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "ok": False,
        "safe_reason_code": "liepin_opencli_filter_unapplied",
    } in trace["events"]
```

- [ ] **Step 2: Run the focused failing cards test**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_search_liepin_cards_blocks_when_required_native_filter_click_fails
```

Expected: the test fails because the current code returns `status == "succeeded"` after recording the filter failure.

- [ ] **Step 3: Add a filter-specific exception helper**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add this small helper near the native filter helpers:

```python
def _filter_unapplied_error() -> OpenCliBrowserError:
    return OpenCliBrowserError("liepin_opencli_filter_unapplied")
```

- [ ] **Step 4: Change `_apply_liepin_native_filters()` to fail closed**

Update the `except OpenCliBrowserError` block in `_apply_liepin_native_filters()` so it records the failed filter and returns a failed result instead of continuing:

```python
            except OpenCliBrowserError as exc:
                reason = (
                    exc.safe_reason_code
                    if exc.safe_reason_code == "liepin_opencli_filter_unapplied"
                    else "liepin_opencli_filter_unapplied"
                )
                events.append(
                    {
                        "action_kind": "apply_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "ok": False,
                        "safe_reason_code": reason,
                    }
                )
                return OpenCliBrowserResult(
                    ok=False,
                    action="apply_liepin_filters",
                    safe_reason_code=reason,
                    observation={"filter": filter_name, "section": section},
                )
```

Keep the `skip_native_filter` loop after successful required filters only; skipped unknown keys should still be recorded when all required filters succeeded.

- [ ] **Step 5: Register `liepin_opencli_filter_unapplied` as a safe runtime reason**

In `src/seektalent/providers/pi_agent/pi_external.py`, add this code to `_OPENCLI_SAFE_TOOL_REASON_CODES`:

```python
        "liepin_opencli_filter_unapplied",
```

In `src/seektalent/providers/liepin/runtime_lane.py`, add the same code to `OPENCLI_SAFE_REASON_CODES`.

In `src/seektalent_ui/workbench_routes.py`, add the same code to `RUNTIME_SOURCE_REASON_CODES`.

In `src/seektalent/runtime/source_lanes.py`, add it to `_SAFE_REASON_CODES` and map it publicly:

```python
    "liepin_opencli_filter_unapplied",
```

```python
    "liepin_opencli_filter_unapplied": "source_filter_unavailable",
```

Also add `"source_filter_unavailable"` to `_PUBLIC_SOURCE_REASON_CODES`.

In `src/seektalent/runtime/public_events.py`, add:

```python
    "liepin_opencli_filter_unapplied": "source_filter_unavailable",
```

- [ ] **Step 6: Extend runtime reason-code preservation test**

In `tests/test_liepin_runtime_source_lane.py`, extend `test_pi_failure_codes_preserve_opencli_safe_reason_codes`:

```python
    assert (
        runtime_safe_reason_code_from_worker_failure_code("liepin_opencli_filter_unapplied")
        == "liepin_opencli_filter_unapplied"
    )
```

- [ ] **Step 7: Add Workbench start-probe preservation test**

In `tests/test_workbench_api.py`, add this import:

```python
from seektalent_ui.workbench_routes import _liepin_start_probe_error_reason
```

Append this test:

```python
def test_liepin_start_probe_preserves_opencli_filter_failure_reason() -> None:
    assert (
        _liepin_start_probe_error_reason(
            LiepinWorkerModeError("filter not applied", code="liepin_opencli_filter_unapplied")
        )
        == "liepin_opencli_filter_unapplied"
    )
```

- [ ] **Step 8: Run the focused test**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_search_liepin_cards_blocks_when_required_native_filter_click_fails
```

Expected: `1 passed`.

- [ ] **Step 9: Run the native filter and reason-code regression cluster**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_search_liepin_cards_applies_native_filters_before_reading_cards \
  tests/test_pi_opencli_browser.py::test_search_liepin_cards_clicks_filters_in_named_sections \
  tests/test_pi_opencli_browser.py::test_search_liepin_cards_retries_transient_native_filter_status \
  tests/test_pi_opencli_browser.py::test_search_liepin_cards_blocks_when_required_native_filter_click_fails \
  tests/test_liepin_runtime_source_lane.py::test_pi_failure_codes_preserve_opencli_safe_reason_codes \
  tests/test_workbench_api.py::test_liepin_start_probe_preserves_opencli_filter_failure_reason
```

Expected: all selected tests pass.

- [ ] **Step 10: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/runtime/source_lanes.py src/seektalent/runtime/public_events.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent_ui/workbench_routes.py src/seektalent/providers/pi_agent/pi_external.py tests/test_pi_opencli_browser.py tests/test_liepin_runtime_source_lane.py tests/test_workbench_api.py
git commit -m "fix: block Liepin search when required filters fail"
```

---

### Task 2: Verify Filter Selection Before Committing State

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Test: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Add a failing verification test**

Append this test to `tests/test_pi_opencli_browser.py` near the native filter tests:

```python
def test_search_liepin_cards_blocks_when_filter_click_does_not_apply_selection(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_bad_click = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 北京"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_after_bad_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_search_liepin_cards_blocks_when_filter_click_does_not_apply_selection
```

Expected: the test fails because the current implementation accepts the fresh state without checking selected evidence.

- [ ] **Step 3: Add selected-filter evidence helpers**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add helpers near `_native_filter_option_visible_in_section()`:

```python
def _native_filter_selection_applied(state_text: str, *, section: str, label: str) -> bool:
    normalized_label = _normalize_liepin_filter_text(label)
    if not normalized_label:
        return False
    normalized_section = _normalize_liepin_filter_text(section)
    for raw_line in state_text.splitlines():
        line = _normalize_liepin_filter_text(raw_line)
        if not line:
            continue
        if line.startswith(("已选", "当前条件", "筛选条件")) and normalized_label in line:
            return True
        if normalized_section and normalized_section in line and "已选" in line and normalized_label in line:
            return True
    return False


def _normalize_liepin_filter_text(value: str) -> str:
    return " ".join(value.replace("：", " ").replace(":", " ").split()).casefold()
```

If the file already has a better text normalization helper, reuse it instead of adding this exact function.

- [ ] **Step 4: Verify after click in `_select_liepin_native_filter()`**

After the post-click `state = self.state()` and `if not state.ok` check, verify the label before returning:

```python
                state_text = _opencli_result_text(state)
                if not _native_filter_selection_applied(state_text, section=section, label=label):
                    events.append(
                        {
                            "action_kind": "verify_native_filter",
                            "filter": filter_name,
                            "section": section,
                            "value": label,
                            "ok": False,
                            "safe_reason_code": "liepin_opencli_filter_unapplied",
                        }
                    )
                    raise _filter_unapplied_error()
                events.append(
                    {
                        "action_kind": "verify_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "ok": True,
                    }
                )
                return state
```

- [ ] **Step 5: Add the workflow mapper event**

In `src/seektalent/providers/liepin/opencli_workflow.py`, add:

```python
    "verify_native_filter": ("apply_filters", "source_workflow_step_completed", "completed"),
```

to `_ACTION_TO_STEP_EVENT`. A failed event with `ok: False` should already become `source_workflow_step_failed`.

- [ ] **Step 6: Run the verification and workflow tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_search_liepin_cards_blocks_when_filter_click_does_not_apply_selection \
  tests/test_pi_opencli_browser.py::test_search_liepin_cards_applies_native_filters_before_reading_cards \
  tests/test_pi_opencli_browser.py::test_search_liepin_cards_clicks_filters_in_named_sections \
  tests/test_liepin_opencli_workflow.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/liepin/opencli_workflow.py tests/test_pi_opencli_browser.py tests/test_liepin_opencli_workflow.py
git commit -m "fix: verify Liepin native filters before continuing"
```

---

### Task 3: Prevent Detail Capture After Filter Failure

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Test: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Add a failing resume-level test**

Append this test near the `search_liepin_resumes` tests in `tests/test_pi_opencli_browser.py`:

```python
def test_search_liepin_resumes_does_not_open_details_after_filter_failure(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(1, ["opencli"]),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        target_resumes=2,
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
    assert envelope["resumes"] == []
    assert all("showresumedetail" not in " ".join(call) for call in commands.calls)
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_search_liepin_resumes_does_not_open_details_after_filter_failure
```

Expected after Tasks 1-2: `1 passed`. Before Task 1 this test should fail because the search can still proceed after the filter failure.

- [ ] **Step 3: Ensure blocked card envelopes short-circuit resume detail flow**

In `search_liepin_resumes()`, keep the code path after `cards_envelope = self.search_liepin_cards(...)` returning immediately for `status in {"blocked", "failed"}` with no detail opens.

The blocked branch should use this shape:

```python
        if cards_envelope.get("status") in {"blocked", "failed"}:
            return self._blocked_resumes_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_reason_code=str(cards_envelope.get("safe_reason_code") or "failed_provider_error"),
                cards_seen=int(cards_envelope.get("cards_seen") or 0),
            )
```

Do not call `_resumes_envelope()` for this path; that helper computes partial/succeeded detail-search status and is not the blocked-path helper.

- [ ] **Step 4: Run the resume-level test**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_search_liepin_resumes_does_not_open_details_after_filter_failure
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py tests/test_pi_opencli_browser.py
git commit -m "test: prevent detail capture after Liepin filter failure"
```

---

### Task 4: Make Internal Detail URL Probing Explicit

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Test: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Add boundary tests**

Append these tests near the detail URL tests in `tests/test_pi_opencli_browser.py`:

```python
def test_external_opencli_eval_command_remains_forbidden(tmp_path: Path) -> None:
    commands = FakeCommands()
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as error:
        runner._run_browser_command("eval", ("document.title",))

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_internal_detail_url_probe_rejects_sensitive_output(tmp_path: Path) -> None:
    commands = EvalCommands(eval_output="cookie=secret", outputs={})
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as error:
        runner._liepin_detail_url_for_ref("70")

    assert error.value.safe_reason_code == "liepin_opencli_malformed_state"


def test_internal_detail_url_probe_rejects_unknown_probe_name(tmp_path: Path) -> None:
    commands = FakeCommands()
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as error:
        runner._run_fixed_readonly_eval_probe(probe_name="arbitrary", ref="70")

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []
```

- [ ] **Step 2: Run the boundary tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_external_opencli_eval_command_remains_forbidden \
  tests/test_pi_opencli_browser.py::test_internal_detail_url_probe_rejects_sensitive_output \
  tests/test_pi_opencli_browser.py::test_internal_detail_url_probe_rejects_unknown_probe_name
```

Expected: the external eval and sensitive-output tests may already pass; the unknown-probe test fails because `_run_fixed_readonly_eval_probe` does not exist yet.

- [ ] **Step 3: Rename and constrain the internal helper**

Replace `_run_browser_eval(self, script: str)` with:

```python
def _run_fixed_readonly_eval_probe(self, *, probe_name: str, ref: str) -> str:
    if probe_name != "liepin_detail_url_for_card":
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    if not _is_safe_page_id(ref):
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    script = _liepin_detail_url_probe_script(ref)
    argv = tuple(self._config.command) + ("browser", self._config.session, "eval", script)
    output = self._run(argv)
    if _looks_sensitive(output):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    self._touch_lease()
    return output


def _liepin_detail_url_probe_script(ref: str) -> str:
    return (
        "(() => {"
        f"const card = document.querySelector('[data-opencli-ref=\"{ref}\"]');"
        "const input = card && card.querySelector('input[name=\"res_id_encode\"]');"
        "const value = input && (input.getAttribute('value') || input.value || '');"
        "if (!/^[A-Za-z0-9]+$/.test(value || '')) return null;"
        "const cards = Array.from(document.querySelectorAll('.detail-resume-card-wrap'));"
        "const index = Math.max(0, cards.indexOf(card));"
        "return 'https://h.liepin.com/resume/showresumedetail/?res_id_encode='"
        "+ encodeURIComponent(value)"
        "+ '&index=' + index"
        "+ '&position=' + index"
        "+ '&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal';"
        "})()"
    )
```

Then update `_liepin_detail_url_for_ref()`:

```python
        output = self._run_fixed_readonly_eval_probe(
            probe_name="liepin_detail_url_for_card",
            ref=ref,
        ).strip()
```

Remove the local `script = (...)` block from `_liepin_detail_url_for_ref()` so the only JavaScript template lives in `_liepin_detail_url_probe_script()`.

- [ ] **Step 4: Update capability wording**

In `src/seektalent/providers/pi_agent/pi_external.py`, keep external `eval` forbidden. Add this sentence to the OpenCLI capability/prompt text that describes forbidden browser commands:

```python
"External arbitrary OpenCLI eval/network/upload/download/storage/cookie actions are forbidden; the Python runner may use fixed read-only internal DOM probes for Liepin detail URL extraction."
```

Do not expose a new public eval tool.

- [ ] **Step 5: Run boundary tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_external_opencli_eval_command_remains_forbidden \
  tests/test_pi_opencli_browser.py::test_internal_detail_url_probe_rejects_sensitive_output \
  tests/test_pi_opencli_browser.py::test_internal_detail_url_probe_rejects_unknown_probe_name
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/pi_external.py tests/test_pi_opencli_browser.py
git commit -m "refactor: make Liepin detail URL probe explicit"
```

---

### Task 5: Parse OpenCLI Structured Errors

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Test: `tests/test_pi_opencli_browser.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Add structured error tests**

Append these tests to `tests/test_pi_opencli_browser.py`:

```python
def test_run_maps_opencli_structured_stale_ref_error(tmp_path: Path) -> None:
    error = subprocess.CalledProcessError(
        1,
        ["opencli"],
        stdout='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
        stderr="",
    )
    commands = FakeCommands(outputs={("opencli", "browser", "seektalent-liepin", "click", "44"): error})
    runner = _runner(commands, lease_dir=tmp_path, allowed_click_refs=("44",))

    with pytest.raises(OpenCliBrowserError) as raised:
        runner.click(target="44")

    assert raised.value.safe_reason_code == "liepin_opencli_stale_ref"


def test_run_maps_opencli_structured_selector_error(tmp_path: Path) -> None:
    error = subprocess.CalledProcessError(
        1,
        ["opencli"],
        stdout="",
        stderr='{"error":{"code":"selector_not_found","message":"not found"}}',
    )
    commands = FakeCommands(outputs={("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): error})
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as raised:
        runner.click(target="城市")

    assert raised.value.safe_reason_code == "liepin_opencli_selector_not_found"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_run_maps_opencli_structured_stale_ref_error \
  tests/test_pi_opencli_browser.py::test_run_maps_opencli_structured_selector_error
```

Expected: both fail because `_run()` maps them to `liepin_opencli_status_unavailable`.

- [ ] **Step 3: Add JSON error parsing helpers**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add:

```python
_OPENCLI_ERROR_CODE_TO_REASON = {
    "stale_ref": "liepin_opencli_stale_ref",
    "selector_not_found": "liepin_opencli_selector_not_found",
    "not_found": "liepin_opencli_selector_not_found",
    "selector_ambiguous": "liepin_opencli_selector_ambiguous",
    "target_not_found": "liepin_opencli_target_not_found",
}


def _safe_reason_from_opencli_error_output(output: str) -> str | None:
    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        error = payload.get("error")
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        if isinstance(code, str):
            reason = _OPENCLI_ERROR_CODE_TO_REASON.get(code.strip().casefold())
            if reason is not None:
                return reason
    return None
```

- [ ] **Step 4: Use structured parsing in `_run()`**

Update the `subprocess.CalledProcessError` block:

```python
        except subprocess.CalledProcessError as exc:
            output = f"{exc.stdout or ''}\n{exc.stderr or ''}"
            if "Extension" in output and ("not connected" in output or "disconnected" in output):
                raise OpenCliBrowserError("liepin_opencli_extension_disconnected") from exc
            structured_reason = _safe_reason_from_opencli_error_output(output)
            if structured_reason is not None:
                raise OpenCliBrowserError(structured_reason) from exc
            raise OpenCliBrowserError("liepin_opencli_status_unavailable") from exc
```

- [ ] **Step 5: Mirror reason codes in external allowlists**

In the hard-coded safe reason list in `src/seektalent/providers/pi_agent/pi_external.py`, add:

```python
"liepin_opencli_stale_ref",
"liepin_opencli_selector_not_found",
"liepin_opencli_selector_ambiguous",
"liepin_opencli_target_not_found",
```

- [ ] **Step 6: Register structured error reason codes in runtime/public allowlists**

In `src/seektalent/providers/liepin/runtime_lane.py`, `src/seektalent_ui/workbench_routes.py`, and `src/seektalent/runtime/source_lanes.py`, add:

```python
    "liepin_opencli_stale_ref",
    "liepin_opencli_selector_not_found",
    "liepin_opencli_selector_ambiguous",
    "liepin_opencli_target_not_found",
```

In `src/seektalent/runtime/source_lanes.py`, map each to public browser backend unavailability:

```python
    "liepin_opencli_stale_ref": "source_browser_backend_unavailable",
    "liepin_opencli_selector_not_found": "source_browser_backend_unavailable",
    "liepin_opencli_selector_ambiguous": "source_browser_backend_unavailable",
    "liepin_opencli_target_not_found": "source_browser_backend_unavailable",
```

In `src/seektalent/runtime/public_events.py`, add the same public mappings.

- [ ] **Step 7: Extend runtime reason-code preservation test**

In `tests/test_liepin_runtime_source_lane.py`, extend `test_pi_failure_codes_preserve_opencli_safe_reason_codes`:

```python
    for reason in (
        "liepin_opencli_stale_ref",
        "liepin_opencli_selector_not_found",
        "liepin_opencli_selector_ambiguous",
        "liepin_opencli_target_not_found",
    ):
        assert runtime_safe_reason_code_from_worker_failure_code(reason) == reason
```

- [ ] **Step 8: Add Workbench start-probe structured reason test**

Append this test to `tests/test_workbench_api.py`:

```python
def test_liepin_start_probe_preserves_opencli_structured_error_reasons() -> None:
    for reason in (
        "liepin_opencli_stale_ref",
        "liepin_opencli_selector_not_found",
        "liepin_opencli_selector_ambiguous",
        "liepin_opencli_target_not_found",
    ):
        assert _liepin_start_probe_error_reason(LiepinWorkerModeError("opencli error", code=reason)) == reason
```

- [ ] **Step 9: Run structured error tests and OpenCLI browser suite**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_run_maps_opencli_structured_stale_ref_error \
  tests/test_pi_opencli_browser.py::test_run_maps_opencli_structured_selector_error \
  tests/test_liepin_runtime_source_lane.py::test_pi_failure_codes_preserve_opencli_safe_reason_codes \
  tests/test_workbench_api.py::test_liepin_start_probe_preserves_opencli_structured_error_reasons \
  tests/test_pi_opencli_browser.py
```

Expected: all selected tests pass.

- [ ] **Step 10: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/runtime/source_lanes.py src/seektalent/runtime/public_events.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent_ui/workbench_routes.py tests/test_pi_opencli_browser.py tests/test_liepin_runtime_source_lane.py tests/test_workbench_api.py
git commit -m "fix: map structured OpenCLI browser errors"
```

---

### Task 6: Split OpenCLI Status Reason Codes

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Test: `tests/test_pi_opencli_browser.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Update existing status tests**

In `tests/test_pi_opencli_browser.py`, change `test_status_does_not_call_doctor_or_start_browser_probe` expected code:

```python
    assert result.safe_reason_code == "liepin_opencli_daemon_not_running"
```

Add this new test:

```python
def test_status_blocks_when_daemon_is_stale() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "Daemon: stale\nExtension: connected\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_daemon_stale"


def test_status_reports_unavailable_for_malformed_daemon_output() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "unexpected status text\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_status_unavailable"
```

- [ ] **Step 2: Run the failing status tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_status_does_not_call_doctor_or_start_browser_probe \
  tests/test_pi_opencli_browser.py::test_status_blocks_when_daemon_is_stale \
  tests/test_pi_opencli_browser.py::test_status_reports_unavailable_for_malformed_daemon_output \
  tests/test_pi_opencli_browser.py::test_status_blocks_when_extension_is_disconnected
```

Expected: daemon-not-running and stale fail with old `liepin_opencli_extension_disconnected`.

- [ ] **Step 3: Add status output classifier**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add:

```python
def _opencli_status_reason(output: str) -> str | None:
    normalized = output.casefold()
    if "daemon:" not in normalized:
        return "liepin_opencli_status_unavailable"
    if "daemon: stale" in normalized:
        return "liepin_opencli_daemon_stale"
    if "daemon: not running" in normalized or "daemon: stopped" in normalized:
        return "liepin_opencli_daemon_not_running"
    if "daemon: running" not in normalized:
        return "liepin_opencli_status_unavailable"
    if "extension: connected" not in normalized:
        return "liepin_opencli_extension_disconnected"
    return None
```

- [ ] **Step 4: Use the classifier in `status()`**

Update `OpenCliBrowserRunner.status()`:

```python
        reason = _opencli_status_reason(output)
        if reason is not None:
            return OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code=reason,
                private_output=output,
            )
        return OpenCliBrowserResult(ok=True, action="status")
```

- [ ] **Step 5: Add reason codes to external safe list**

In the `pi_external.py` safe reason allowlist, add:

```python
"liepin_opencli_daemon_not_running",
"liepin_opencli_daemon_stale",
```

- [ ] **Step 6: Register daemon status reason codes in runtime/public allowlists**

In `src/seektalent/providers/liepin/runtime_lane.py`, `src/seektalent_ui/workbench_routes.py`, and `src/seektalent/runtime/source_lanes.py`, add:

```python
    "liepin_opencli_daemon_not_running",
    "liepin_opencli_daemon_stale",
```

In `src/seektalent/runtime/source_lanes.py`, map them publicly:

```python
    "liepin_opencli_daemon_not_running": "source_browser_backend_unavailable",
    "liepin_opencli_daemon_stale": "source_browser_backend_unavailable",
```

In `src/seektalent/runtime/public_events.py`, add the same mappings.

In `tests/test_liepin_runtime_source_lane.py`, extend `test_pi_failure_codes_preserve_opencli_safe_reason_codes`:

```python
    assert (
        runtime_safe_reason_code_from_worker_failure_code("liepin_opencli_daemon_not_running")
        == "liepin_opencli_daemon_not_running"
    )
    assert (
        runtime_safe_reason_code_from_worker_failure_code("liepin_opencli_daemon_stale")
        == "liepin_opencli_daemon_stale"
    )
```

- [ ] **Step 7: Add Workbench start-probe daemon reason test**

Append this test to `tests/test_workbench_api.py`:

```python
def test_liepin_start_probe_preserves_opencli_daemon_status_reasons() -> None:
    for reason in ("liepin_opencli_daemon_not_running", "liepin_opencli_daemon_stale"):
        assert _liepin_start_probe_error_reason(LiepinWorkerModeError("opencli not ready", code=reason)) == reason
```

- [ ] **Step 8: Run status tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py::test_status_maps_opencli_doctor_success \
  tests/test_pi_opencli_browser.py::test_status_does_not_call_doctor_or_start_browser_probe \
  tests/test_pi_opencli_browser.py::test_status_blocks_when_daemon_is_stale \
  tests/test_pi_opencli_browser.py::test_status_reports_unavailable_for_malformed_daemon_output \
  tests/test_pi_opencli_browser.py::test_status_blocks_when_extension_is_disconnected \
  tests/test_liepin_runtime_source_lane.py::test_pi_failure_codes_preserve_opencli_safe_reason_codes \
  tests/test_workbench_api.py::test_liepin_start_probe_preserves_opencli_daemon_status_reasons
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/runtime/source_lanes.py src/seektalent/runtime/public_events.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent_ui/workbench_routes.py tests/test_pi_opencli_browser.py tests/test_liepin_runtime_source_lane.py tests/test_workbench_api.py
git commit -m "fix: report precise OpenCLI readiness status"
```

---

### Task 7: Make Worker `ensure_ready()` Use OpenCLI Status

**Files:**
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/liepin/opencli_worker_client.py`
- Test: `tests/test_liepin_opencli_worker_client.py`

- [ ] **Step 1: Add worker readiness tests**

Append these tests to `tests/test_liepin_opencli_worker_client.py`:

```python
class StatusRunner:
    def __init__(self, *, ok: bool, safe_reason_code: str = "configured") -> None:
        self.ok = ok
        self.safe_reason_code = safe_reason_code
        self.status_calls = 0

    def status(self):
        self.status_calls += 1
        return self

    def search_liepin_resumes(self, **kwargs):
        raise AssertionError("ensure_ready must not search or open Liepin pages")


def test_opencli_worker_ensure_ready_checks_runner_status() -> None:
    runner = StatusRunner(ok=True)
    client = LiepinOpenCliWorkerClient(
        retriever=LiepinOpenCliResumeRetriever(runner=runner),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    asyncio.run(client.ensure_ready())

    assert runner.status_calls == 1


def test_opencli_worker_ensure_ready_raises_specific_status_code() -> None:
    runner = StatusRunner(ok=False, safe_reason_code="liepin_opencli_daemon_stale")
    client = LiepinOpenCliWorkerClient(
        retriever=LiepinOpenCliResumeRetriever(runner=runner),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(client.ensure_ready())

    assert error.value.code == "liepin_opencli_daemon_stale"
```

Ensure these imports exist at the top:

```python
import pytest
from seektalent.providers.liepin.opencli_retriever import LiepinOpenCliResumeRetriever
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_liepin_opencli_worker_client.py::test_opencli_worker_ensure_ready_checks_runner_status \
  tests/test_liepin_opencli_worker_client.py::test_opencli_worker_ensure_ready_raises_specific_status_code
```

Expected: the first test fails because `ensure_ready()` is a no-op.

- [ ] **Step 3: Add retriever readiness method**

In `src/seektalent/providers/liepin/opencli_retriever.py`, keep the protocol status method:

```python
class OpenCliResumeRunner(Protocol):
    def status(self): ...
```

Then add:

```python
    def ensure_ready(self) -> None:
        status = self._runner.status()
        if not status.ok:
            raise RuntimeError(str(status.safe_reason_code or "liepin_opencli_status_unavailable"))
```

- [ ] **Step 4: Implement worker readiness**

In `src/seektalent/providers/liepin/opencli_worker_client.py`, replace the no-op:

```python
    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event
        try:
            await asyncio.to_thread(self._retriever.ensure_ready)
        except RuntimeError as exc:
            code = str(exc) or "liepin_opencli_status_unavailable"
            raise LiepinWorkerModeError("Liepin OpenCLI worker is not ready.", code=code) from exc
```

- [ ] **Step 5: Run worker readiness tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_liepin_opencli_worker_client.py::test_opencli_worker_ensure_ready_checks_runner_status \
  tests/test_liepin_opencli_worker_client.py::test_opencli_worker_ensure_ready_raises_specific_status_code \
  tests/test_liepin_opencli_worker_client.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/providers/liepin/opencli_retriever.py src/seektalent/providers/liepin/opencli_worker_client.py tests/test_liepin_opencli_worker_client.py
git commit -m "fix: check OpenCLI readiness before Liepin worker use"
```

---

### Task 8: Restart Stale OpenCLI Daemon in Dev Launcher

**Files:**
- Modify: `scripts/start-dev-workbench.sh`
- Test: `tests/test_pi_dokobot_local_setup.py`
- Test: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Add launcher test expectations**

Add this focused test to `tests/test_pi_dokobot_local_setup.py`:

```python
def test_dev_launcher_detects_stale_opencli_daemon() -> None:
    script = Path("scripts/start-dev-workbench.sh").read_text(encoding="utf-8")

    assert "opencli_daemon_stale" in script
    assert "Daemon: stale" in script
    assert '"$OPENCLI_BIN" daemon restart' in script
```

- [ ] **Step 2: Run the failing launcher test**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_dokobot_local_setup.py::test_dev_launcher_detects_stale_opencli_daemon
```

Expected: the test fails because the script does not have stale daemon detection yet.

- [ ] **Step 3: Add stale daemon helper**

In `scripts/start-dev-workbench.sh`, add:

```bash
opencli_daemon_stale() {
  "$OPENCLI_BIN" daemon status 2>/dev/null | grep -q "Daemon: stale"
}
```

- [ ] **Step 4: Restart stale daemon before extension check**

Update the daemon branch:

```bash
elif opencli_daemon_stale; then
  echo "OpenCLI browser bridge daemon is stale; restarting daemon and waiting..." >&2
  if ! "$OPENCLI_BIN" daemon restart >&2 || ! wait_for_opencli_extension; then
    echo "OpenCLI browser bridge extension is not connected; Liepin OpenCLI source will fail closed." >&2
  fi
elif ! "$OPENCLI_BIN" daemon status >/dev/null 2>&1; then
```

Keep the existing extension-disconnected restart path.

- [ ] **Step 5: Lock cleanup behavior to marker cleanup only**

Keep `cleanup_orphaned_tabs` only if the Python implementation does not close browser tabs. Add this regression test to `tests/test_pi_opencli_browser.py`:

```python
def test_cleanup_orphaned_tabs_force_forgets_markers_without_closing_tabs(tmp_path: Path) -> None:
    commands = FakeCommands()
    runner = _runner(commands, lease_dir=tmp_path)

    result = runner.cleanup_orphaned_tabs(force=True)

    assert result.ok is True
    assert result.counts["closedTabs"] == 0
    assert all("close" not in call for call in commands.calls)
```

If this test fails, change `cleanup_orphaned_tabs(force=True)` so it only deletes stale lease/owned-page marker files and returns `closedTabs: 0`. Do not remove the backend process cleanup trap.

- [ ] **Step 6: Run launcher tests**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_dokobot_local_setup.py::test_dev_launcher_detects_stale_opencli_daemon tests/test_pi_dokobot_local_setup.py::test_dev_launcher_uses_opencli_without_legacy_mcp_adapter
```

Also run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_cleanup_orphaned_tabs_force_forgets_markers_without_closing_tabs
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/start-dev-workbench.sh tests/test_pi_dokobot_local_setup.py tests/test_pi_opencli_browser.py
git commit -m "fix: restart stale OpenCLI daemon in dev launcher"
```

---

### Task 9: Align Environment and README with Real OpenCLI Behavior

**Files:**
- Modify: `.env.example`
- Modify: `src/seektalent/default.env`
- Modify: `README.md`
- Test: `tests/test_dev_mode_readiness.py`
- Test: `tests/test_pi_dokobot_local_setup.py`

- [ ] **Step 1: Update `.env.example` wording**

Replace:

```text
# Live gate：默认禁止真实 Liepin 行为，后续 live connector 任务必须显式打开。
SEEKTALENT_LIEPIN_LIVE_ENABLED=false
```

with this backward-compatible wording:

```text
# Fixture safety flag only：OpenCLI local Workbench ignores this and uses real Liepin behavior when configured.
# When true, fake_fixture is rejected so fixture data is not mistaken for a live search.
SEEKTALENT_LIEPIN_LIVE_ENABLED=false
```

- [ ] **Step 2: Apply the same wording to bundled defaults**

Make the same comment replacement in `src/seektalent/default.env` and keep `SEEKTALENT_LIEPIN_LIVE_ENABLED=false`.

- [ ] **Step 3: Update README launcher section**

In `README.md`, replace the old paragraph that says the launcher exports `SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent` and points to `apps/web-svelte/node_modules/.bin/pi` with:

```markdown
The launcher installs Svelte dependencies with Bun when needed, points `SEEKTALENT_LIEPIN_OPENCLI_COMMAND` at `apps/web-svelte/node_modules/.bin/opencli`, exports `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` plus `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`, then starts the backend on `127.0.0.1:8012` and the Svelte Workbench on `127.0.0.1:5178`. The user still installs and connects the OpenCLI Chrome extension in their own Chrome profile. When OpenCLI is selected and ready, Liepin behavior is real local browser behavior, not fixture data.
```

Keep the existing note that Python-only installs do not bundle Node dependencies if it is still accurate.

- [ ] **Step 4: Search for stale docs**

Run:

```bash
rg -n "node_modules/.bin/pi|Live gate|默认禁止真实 Liepin|5176|apps/web/" README.md .env.example src/seektalent/default.env
```

Expected: no stale OpenCLI Workbench instructions remain in `README.md`, `.env.example`, or `src/seektalent/default.env`.

- [ ] **Step 5: Run docs/setup tests**

Run:

```bash
.venv/bin/pytest -q tests/test_dev_mode_readiness.py tests/test_pi_dokobot_local_setup.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add .env.example src/seektalent/default.env README.md tests/test_dev_mode_readiness.py tests/test_pi_dokobot_local_setup.py
git commit -m "docs: align OpenCLI local Workbench setup"
```

---

### Task 10: Final Verification

**Files:**
- No new implementation files unless earlier tasks reveal a missing test fixture.

- [ ] **Step 1: Run the focused OpenCLI/Liepin suites**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_pi_opencli_browser.py \
  tests/test_liepin_opencli_workflow.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_dev_mode_readiness.py \
  tests/test_pi_dokobot_local_setup.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full backend tests**

Run:

```bash
.venv/bin/pytest -q
```

Expected: full backend suite passes.

- [ ] **Step 3: Run diff hygiene**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: only files intentionally modified by this plan are present.

- [ ] **Step 5: Prepare handoff**

Summarize:

```text
- Required Liepin native filters now fail closed when unverified.
- OpenCLI readiness and structured errors produce specific safe reason codes.
- External eval remains forbidden; any internal detail URL probe is fixed-template/read-only.
- Local Workbench docs now describe real OpenCLI-by-default behavior.
- Verification: focused suites passed; full pytest passed; git diff --check passed.
```
