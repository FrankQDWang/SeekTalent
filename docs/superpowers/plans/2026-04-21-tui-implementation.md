# SeekTalent TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the default interactive terminal TUI for `seektalent`, with `seektalent exec` preserving existing CLI workflows and live business-readable round trace.

**Architecture:** Add a small progress event contract, emit optional progress events from the runtime without changing matching behavior, and render those events in a prompt-toolkit/rich TUI adapted from the old SeekTalent implementation. Keep all business decisions in the existing runtime; the TUI only reads inputs, renders trace, and prints final results.

**Tech Stack:** Python 3.12, prompt-toolkit, rich, pytest, existing `WorkflowRuntime` / `run_match_async`.

---

## File Map

- Create `src/seektalent/progress.py`: tiny `ProgressEvent` and `ProgressCallback` contract.
- Create `src/seektalent/tui.py`: prompt-toolkit composer, rich transcript rendering, trace block rendering, status text helpers.
- Modify `src/seektalent/api.py`: pass optional `progress_callback` into `WorkflowRuntime`.
- Modify `src/seektalent/runtime/orchestrator.py`: accept optional callback and emit progress events around existing milestones.
- Modify `src/seektalent/cli.py`: make no-arg TTY launch TUI, move current parser behind `exec`, and keep non-TTY help behavior.
- Modify `pyproject.toml` and `uv.lock`: add `prompt-toolkit` and `rich` as direct dependencies.
- Modify tests: `tests/test_api.py`, `tests/test_cli.py`, `tests/test_runtime_audit.py`.
- Create `tests/test_tui.py`: focused renderer and transcript tests.

## Task 1: Progress Contract And API Passthrough

**Files:**
- Create: `src/seektalent/progress.py`
- Modify: `src/seektalent/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API passthrough tests**

Add tests showing both sync and async API calls pass the callback into runtime:

```python
from seektalent.progress import ProgressEvent


def test_run_match_passes_progress_callback(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class FakeRuntime:
        def __init__(self, settings: AppSettings) -> None:
            del settings

        def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            captured["progress_callback"] = progress_callback
            progress_callback(ProgressEvent(type="run_started", message="started"))
            return _artifacts(tmp_path)

    events: list[ProgressEvent] = []
    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    run_match(
        job_title="Python Engineer",
        jd="JD",
        settings=make_settings(mock_cts=True),
        env_file=None,
        progress_callback=events.append,
    )

    assert captured["progress_callback"] is events.append
    assert [event.type for event in events] == ["run_started"]
```

- [ ] **Step 2: Run the new API test and verify it fails**

Run: `uv run pytest tests/test_api.py::test_run_match_passes_progress_callback -q`

Expected: FAIL because `run_match()` does not accept `progress_callback`.

- [ ] **Step 3: Implement the minimal progress contract and API pass-through**

Create `src/seektalent/progress.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ProgressEvent:
    type: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    round_no: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]
```

Update `run_match(...)` and `run_match_async(...)` to accept `progress_callback: ProgressCallback | None = None` and pass it into `runtime.run(...)` / `runtime.run_async(...)`.

- [ ] **Step 4: Run API tests**

Run: `uv run pytest tests/test_api.py -q`

Expected: PASS.

## Task 2: CLI Routing For TUI And `exec`

**Files:**
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI routing tests**

Add tests for no-arg TTY, no-arg non-TTY, and `exec run`:

```python
def test_no_args_tty_launches_tui(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {}
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("seektalent.cli._launch_tui", lambda: called.setdefault("launched", True) or 0)

    assert main([]) == 0

    assert called == {"launched": True}


def test_no_args_non_tty_prints_help(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    assert main([]) == 0

    assert "seektalent exec run" in capsys.readouterr().out


def test_exec_run_uses_existing_run_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    captured = {}
    monkeypatch.setattr("seektalent.cli.run_match", lambda **kwargs: captured.setdefault("kwargs", kwargs) or _result(tmp_path))

    assert main(["exec", "run", "--job-title", "Python Engineer", "--jd", "JD"]) == 0

    assert captured["kwargs"]["job_title"] == "Python Engineer"
    assert "run_id: run-1" in capsys.readouterr().out
```

- [ ] **Step 2: Run the CLI routing tests and verify they fail**

Run: `uv run pytest tests/test_cli.py::test_no_args_tty_launches_tui tests/test_cli.py::test_no_args_non_tty_prints_help tests/test_cli.py::test_exec_run_uses_existing_run_command -q`

Expected: FAIL because `_launch_tui` and `exec` routing do not exist.

- [ ] **Step 3: Implement routing**

Add:

```python
KNOWN_COMMANDS = {"run", "benchmark", "migrate-judge-assets", "init", "doctor", "version", "update", "inspect"}


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _launch_tui() -> int:
    from seektalent.tui import run_chat_session

    return run_chat_session()
```

Split current parser builder into `build_exec_parser()`, add a root parser with an `exec` subcommand, and make `main([])` launch TUI only in a TTY. Route `main(["exec", *args])` through the current parser logic.

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -q`

Expected: PASS.

## Task 3: TUI Renderer And Transcript

**Files:**
- Create: `src/seektalent/tui.py`
- Create: `tests/test_tui.py`

- [ ] **Step 1: Write failing renderer tests**

Create tests for submitted input, trace blocks, and final output:

```python
from rich.console import Console

from seektalent.progress import ProgressEvent
from seektalent.tui import _render_progress_lines, _submitted_message


def test_submitted_message_keeps_full_text_once() -> None:
    text = "line1\nline2\nline3\nline4"

    rendered = _submitted_message("Job Description", text)

    assert rendered.count("line1") == 1
    assert rendered.count("line4") == 1
    assert "last 3" not in rendered


def test_round_completed_renders_business_trace() -> None:
    event = ProgressEvent(
        type="round_completed",
        message="第 1 轮完成",
        round_no=1,
        payload={
            "query_terms": ["AI agent", "LLM"],
            "raw_candidate_count": 10,
            "unique_new_count": 10,
            "newly_scored_count": 10,
            "fit_count": 5,
            "not_fit_count": 5,
            "top_pool_count": 10,
            "selected_count": 10,
            "dropped_count": 0,
            "representative_candidates": ["算法工程师 · 深圳 · 5 年：Agent 项目"],
            "reflection_summary": "下一轮加入 LangChain。",
        },
    )

    lines = _render_progress_lines(event)
    text = "\n".join(lines)

    assert "第 1 轮 · 关键词：AI agent、LLM" in text
    assert "本轮候选人情况" in text
    assert "fit 5 人，not fit 5 人" in text
    assert "反思和下一轮变更" in text
```

- [ ] **Step 2: Run TUI tests and verify they fail**

Run: `uv run pytest tests/test_tui.py -q`

Expected: FAIL because `seektalent.tui` does not exist.

- [ ] **Step 3: Implement minimal TUI renderer and prompt flow**

Port the old TUI composer from `/Users/frankqdwang/Agents/SeekTalent/src/seektalent/tui.py`, adjusted for three inputs. Implement `_render_progress_lines(...)`, `_submitted_message(...)`, and `_result_message(...)` as small pure helpers so tests can cover trace text without a live TTY.

- [ ] **Step 4: Run TUI tests**

Run: `uv run pytest tests/test_tui.py -q`

Expected: PASS.

## Task 4: Runtime Progress Events

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Test: `tests/test_runtime_audit.py`

- [ ] **Step 1: Write failing runtime progress test**

Add a test beside existing runtime audit tests:

```python
def test_runtime_emits_tui_progress_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    events = []

    runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes", progress_callback=events.append)

    event_types = [event.type for event in events]
    assert "requirements_started" in event_types
    assert "controller_started" in event_types
    assert "search_completed" in event_types
    assert "scoring_completed" in event_types
    assert "reflection_completed" in event_types
    assert "round_completed" in event_types
    round_event = next(event for event in events if event.type == "round_completed")
    assert round_event.payload["query_terms"]
    assert "fit_count" in round_event.payload
    assert round_event.payload["representative_candidates"]
```

- [ ] **Step 2: Run the runtime progress test and verify it fails**

Run: `uv run pytest tests/test_runtime_audit.py::test_runtime_emits_tui_progress_events -q`

Expected: FAIL because `WorkflowRuntime.run()` does not accept `progress_callback`.

- [ ] **Step 3: Implement runtime progress emission**

Add a small `_emit_progress(...)` helper to `WorkflowRuntime`. Emit progress events immediately beside existing `RunTracer.emit(...)` calls and after each round completes. Build the `round_completed` payload from existing round data; do not change runtime decisions.

- [ ] **Step 4: Run focused runtime tests**

Run: `uv run pytest tests/test_runtime_audit.py::test_runtime_emits_tui_progress_events tests/test_runtime_audit.py::test_runtime_writes_v02_audit_outputs -q`

Expected: PASS.

## Task 5: Dependencies And Verification

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add direct TUI dependencies**

Add to `[project].dependencies`:

```toml
"prompt-toolkit>=3.0.52",
"rich>=14.2.0",
```

- [ ] **Step 2: Refresh lockfile**

Run: `uv lock`

Expected: exit 0.

- [ ] **Step 3: Run focused validation**

Run:

```bash
uv run pytest tests/test_api.py tests/test_cli.py tests/test_tui.py tests/test_runtime_audit.py -q
```

Expected: PASS.

- [ ] **Step 4: Run lint/type smoke**

Run:

```bash
uv run ruff check src/seektalent/progress.py src/seektalent/tui.py src/seektalent/api.py src/seektalent/cli.py src/seektalent/runtime/orchestrator.py tests/test_tui.py
```

Expected: PASS.

- [ ] **Step 5: Inspect git diff**

Run: `git diff --stat`

Expected: only TUI/progress/API/CLI/runtime/test/dependency/plan files changed, plus pre-existing unrelated dirty files left unstaged if they are still present.
