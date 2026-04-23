# Runtime Lifecycle And Latency Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dev`/`prod` runtime lifecycle defaults, startup cleanup, provider usage snapshots, and generous controller/reflection rationale length controls without changing retrieval, scoring, stop, or ranking behavior.

**Architecture:** Keep this as runtime plumbing, not a strategy rewrite. `AppSettings` resolves mode-specific paths, a small lifecycle module performs safe startup cleanup, `LLMCallSnapshot` stores provider usage, and existing model call wrappers pass usage metadata to snapshots. Controller/reflection behavior remains the same except visible explanation fields get generous max-length constraints and prompt wording.

**Tech Stack:** Python 3.12, Pydantic v2, pydantic-settings, Pydantic AI `AgentRunResult.usage()`, SQLite exact cache, pytest.

---

## Scope Check

The spec covers one coherent runtime/observability slice. It touches multiple files, but the changes are coupled by one execution path: load settings, clean runtime artifacts, run LLM stages, write snapshots. This is suitable for one implementation plan.

## File Structure

- Modify `src/seektalent/config.py`: add `RuntimeMode`, mode-specific path defaults, packaged-prod forcing, and public run config coverage.
- Modify `src/seektalent/resources.py`: expand `~` in configured paths.
- Modify `src/seektalent/runtime/exact_llm_cache.py`: add a focused cache-clear function.
- Create `src/seektalent/runtime/lifecycle.py`: safe startup cleanup for cache and run artifacts.
- Modify `src/seektalent/cli.py`: call startup cleanup from `run` and `benchmark`.
- Modify `src/seektalent/tracing.py`: add `ProviderUsageSnapshot` and `provider_usage_from_result`, then include usage in `LLMCallSnapshot`.
- Modify `src/seektalent/requirements/extractor.py`: capture provider usage for requirements calls.
- Modify `src/seektalent/controller/react_controller.py`: capture provider usage for controller calls.
- Modify `src/seektalent/reflection/critic.py`: capture provider usage for reflection calls.
- Modify `src/seektalent/finalize/finalizer.py`: capture provider usage for finalizer calls.
- Modify `src/seektalent/scoring/scorer.py`: return per-call provider usage from parallel scoring calls.
- Modify `src/seektalent/runtime/orchestrator.py`: write provider usage into snapshots and schema-pressure audit output.
- Modify `src/seektalent/models.py`: add generous max-length constraints to visible rationale fields.
- Modify `src/seektalent/prompts/controller.md`: state that few-shot terms are examples only and rationale should be an audit summary.
- Modify `src/seektalent/prompts/reflection.md`: state that rationale should be an audit summary within the schema budget.
- Add/modify tests:
  - `tests/test_runtime_lifecycle.py`
  - `tests/test_exact_llm_cache.py`
  - `tests/test_llm_provider_config.py`
  - `tests/test_runtime_audit.py`
  - `tests/test_controller_contract.py`
  - `tests/test_reflection_contract.py`
  - `tests/test_llm_input_prompts.py`

## Task 1: Runtime Mode And Path Defaults

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/resources.py`
- Test: `tests/test_llm_provider_config.py`

- [ ] **Step 1: Write failing tests for `dev` and `prod` settings**

Add these tests to `tests/test_llm_provider_config.py` near the existing settings tests:

```python
def test_app_settings_runtime_mode_defaults_to_dev_paths() -> None:
    settings = make_settings()

    assert settings.runtime_mode == "dev"
    assert settings.runs_dir == "runs"
    assert settings.llm_cache_dir == ".seektalent/cache"


def test_app_settings_prod_mode_defaults_to_global_user_paths() -> None:
    settings = make_settings(runtime_mode="prod")

    assert settings.runtime_mode == "prod"
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_app_settings_rejects_invalid_runtime_mode() -> None:
    with pytest.raises(ValidationError, match="runtime_mode"):
        make_settings(runtime_mode="production")


def test_packaged_runtime_forces_prod_mode(monkeypatch) -> None:
    monkeypatch.setenv("SEEKTALENT_PACKAGED", "1")

    settings = make_settings(runtime_mode="dev")

    assert settings.runtime_mode == "prod"
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_explicit_paths_override_runtime_mode_defaults() -> None:
    settings = make_settings(
        runtime_mode="prod",
        runs_dir="/tmp/seektalent-runs",
        llm_cache_dir="/tmp/seektalent-cache",
    )

    assert settings.runs_dir == "/tmp/seektalent-runs"
    assert settings.llm_cache_dir == "/tmp/seektalent-cache"
```

Add this path expansion test in `tests/test_llm_provider_config.py`:

```python
def test_resolve_user_path_expands_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolve_user_path("~/.seektalent/runs") == tmp_path / ".seektalent" / "runs"
```

Update imports at the top of `tests/test_llm_provider_config.py`:

```python
from seektalent.resources import resolve_user_path
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py::test_app_settings_runtime_mode_defaults_to_dev_paths tests/test_llm_provider_config.py::test_app_settings_prod_mode_defaults_to_global_user_paths tests/test_llm_provider_config.py::test_app_settings_rejects_invalid_runtime_mode tests/test_llm_provider_config.py::test_packaged_runtime_forces_prod_mode tests/test_llm_provider_config.py::test_explicit_paths_override_runtime_mode_defaults tests/test_llm_provider_config.py::test_resolve_user_path_expands_home -q
```

Expected: tests fail because `runtime_mode` is missing and `resolve_user_path` does not expand `~`.

- [ ] **Step 3: Implement runtime mode resolution**

In `src/seektalent/config.py`, update imports and constants:

```python
import os
import sys
from pathlib import Path
from typing import Literal

RuntimeMode = Literal["dev", "prod"]
DEV_RUNS_DIR = "runs"
DEV_LLM_CACHE_DIR = ".seektalent/cache"
PROD_RUNS_DIR = "~/.seektalent/runs"
PROD_LLM_CACHE_DIR = "~/.seektalent/cache"
```

Add this helper near `_is_qualified_model_id`:

```python
def _packaged_runtime_forces_prod() -> bool:
    return os.environ.get("SEEKTALENT_PACKAGED") == "1" or bool(getattr(sys, "frozen", False))
```

Change the settings fields:

```python
    runtime_mode: RuntimeMode = "dev"
    llm_cache_dir: str | None = None
    openai_prompt_cache_enabled: bool = False
    openai_prompt_cache_retention: str | None = None
    mock_cts: bool = False
    enable_eval: bool = False
    enable_reflection: bool = True
    wandb_entity: str | None = None
    wandb_project: str | None = None
    weave_entity: str | None = None
    weave_project: str | None = None

    runs_dir: str | None = None
```

Add this method before the existing `validate_ranges` validator. Keep the existing `validate_ranges` method unchanged after this new method so runtime defaults are resolved before range validation:

```python
    @model_validator(mode="after")
    def resolve_runtime_defaults(self) -> "AppSettings":
        if _packaged_runtime_forces_prod():
            self.runtime_mode = "prod"
        if self.runs_dir is None:
            self.runs_dir = PROD_RUNS_DIR if self.runtime_mode == "prod" else DEV_RUNS_DIR
        if self.llm_cache_dir is None:
            self.llm_cache_dir = PROD_LLM_CACHE_DIR if self.runtime_mode == "prod" else DEV_LLM_CACHE_DIR
        return self
```

Update `runs_path` to make the non-`None` expectation explicit:

```python
    @property
    def runs_path(self) -> Path:
        if self.runs_dir is None:
            raise ValueError("runs_dir was not resolved")
        return resolve_user_path(self.runs_dir)
```

In `src/seektalent/resources.py`, update `resolve_user_path`:

```python
def resolve_user_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path
```

- [ ] **Step 4: Record runtime mode in public run config**

In `src/seektalent/runtime/orchestrator.py`, add these fields to `_build_public_run_config()` under `"settings"`:

```python
                "runtime_mode": self.settings.runtime_mode,
                "runs_dir": self.settings.runs_dir,
```

Keep the existing `"llm_cache_dir"` entry.

- [ ] **Step 5: Run settings tests**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py tests/test_runtime_audit.py::test_run_config_records_latency_engineering_settings -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/config.py src/seektalent/resources.py src/seektalent/runtime/orchestrator.py tests/test_llm_provider_config.py tests/test_runtime_audit.py
git commit -m "Add runtime mode path defaults"
```

## Task 2: Startup Cleanup

**Files:**
- Modify: `src/seektalent/runtime/exact_llm_cache.py`
- Create: `src/seektalent/runtime/lifecycle.py`
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_exact_llm_cache.py`
- Test: `tests/test_runtime_lifecycle.py`

- [ ] **Step 1: Write failing exact-cache clear test**

Add to `tests/test_exact_llm_cache.py`:

```python
from seektalent.runtime.exact_llm_cache import clear_exact_llm_cache


def test_clear_exact_llm_cache_removes_sqlite_file(tmp_path) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))

    put_cached_json(settings, namespace="scoring", key="k", payload={"value": 1})
    assert get_cached_json(settings, namespace="scoring", key="k") == {"value": 1}

    clear_exact_llm_cache(settings)

    assert get_cached_json(settings, namespace="scoring", key="k") is None
```

- [ ] **Step 2: Implement exact-cache clear**

Add to `src/seektalent/runtime/exact_llm_cache.py`:

```python
def clear_exact_llm_cache(settings: AppSettings) -> None:
    path = _cache_path(settings)
    if path.exists():
        path.unlink()
```

- [ ] **Step 3: Run exact-cache test**

Run:

```bash
uv run pytest tests/test_exact_llm_cache.py::test_clear_exact_llm_cache_removes_sqlite_file -q
```

Expected: PASS.

- [ ] **Step 4: Write failing lifecycle cleanup tests**

Create `tests/test_runtime_lifecycle.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from seektalent.runtime.exact_llm_cache import get_cached_json, put_cached_json
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts
from tests.settings_factory import make_settings


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_dev_cleanup_keeps_runs_and_clears_cache(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="dev",
        runs_dir=str(tmp_path / "runs"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    run_dir = tmp_path / "runs" / "20260401_120000_deadbeef"
    _write_file(run_dir / "trace.log")
    put_cached_json(settings, namespace="scoring", key="k", payload={"value": 1})

    cleanup_runtime_artifacts(settings, now=datetime(2026, 4, 23, 12, 0, 0))

    assert run_dir.exists()
    assert get_cached_json(settings, namespace="scoring", key="k") is None


def test_prod_cleanup_deletes_old_runs_and_keeps_recent_runs(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="prod",
        runs_dir=str(tmp_path / "runs"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    old_run = tmp_path / "runs" / "20260410_120000_deadbeef"
    recent_run = tmp_path / "runs" / "20260422_120000_feedface"
    unrelated_dir = tmp_path / "runs" / "manual-notes"
    old_summary = tmp_path / "runs" / "benchmark_summary_20260410_120000.json"
    recent_summary = tmp_path / "runs" / "benchmark_summary_20260422_120000.json"
    _write_file(old_run / "trace.log")
    _write_file(recent_run / "trace.log")
    _write_file(unrelated_dir / "keep.txt")
    _write_file(old_summary)
    _write_file(recent_summary)

    cleanup_runtime_artifacts(settings, now=datetime(2026, 4, 23, 12, 0, 0))

    assert not old_run.exists()
    assert recent_run.exists()
    assert unrelated_dir.exists()
    assert not old_summary.exists()
    assert recent_summary.exists()


def test_prod_cleanup_clears_cache(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="prod",
        runs_dir=str(tmp_path / "runs"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    put_cached_json(settings, namespace="requirements", key="k", payload={"value": 1})

    cleanup_runtime_artifacts(settings, now=datetime(2026, 4, 23, 12, 0, 0))

    assert get_cached_json(settings, namespace="requirements", key="k") is None
```

- [ ] **Step 5: Implement lifecycle cleanup**

Create `src/seektalent/runtime/lifecycle.py`:

```python
from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from seektalent.config import AppSettings
from seektalent.runtime.exact_llm_cache import clear_exact_llm_cache

RUN_DIR_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")
BENCHMARK_SUMMARY_RE = re.compile(r"^benchmark_summary_(\d{8}_\d{6})\.json$")
PROD_RUN_RETENTION_DAYS = 7


def cleanup_runtime_artifacts(settings: AppSettings, *, now: datetime | None = None) -> None:
    clear_exact_llm_cache(settings)
    if settings.runtime_mode != "prod":
        return
    cleanup_old_run_artifacts(settings.runs_path, now=now or datetime.now(), retention_days=PROD_RUN_RETENTION_DAYS)


def cleanup_old_run_artifacts(runs_root: Path, *, now: datetime, retention_days: int) -> None:
    if not runs_root.exists():
        return
    cutoff = now - timedelta(days=retention_days)
    for path in runs_root.iterdir():
        if path.is_dir() and _run_dir_is_expired(path.name, cutoff):
            shutil.rmtree(path)
            continue
        if path.is_file() and _benchmark_summary_is_expired(path.name, cutoff):
            path.unlink()


def _run_dir_is_expired(name: str, cutoff: datetime) -> bool:
    if not RUN_DIR_RE.fullmatch(name):
        return False
    return _parse_timestamp(name[:15]) < cutoff


def _benchmark_summary_is_expired(name: str, cutoff: datetime) -> bool:
    match = BENCHMARK_SUMMARY_RE.fullmatch(name)
    if match is None:
        return False
    return _parse_timestamp(match.group(1)) < cutoff


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d_%H%M%S")
```

- [ ] **Step 6: Wire cleanup into CLI run and benchmark**

In `src/seektalent/cli.py`, add import:

```python
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts
```

In `_run_command`, after credential validation and before `run_match(...)`, add:

```python
    cleanup_runtime_artifacts(settings)
```

In `_benchmark_command`, after credential validation and before resolving benchmark rows, add:

```python
    cleanup_runtime_artifacts(settings)
```

- [ ] **Step 7: Run lifecycle tests**

Run:

```bash
uv run pytest tests/test_exact_llm_cache.py tests/test_runtime_lifecycle.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/seektalent/runtime/exact_llm_cache.py src/seektalent/runtime/lifecycle.py src/seektalent/cli.py tests/test_exact_llm_cache.py tests/test_runtime_lifecycle.py
git commit -m "Clean runtime cache and old production runs"
```

## Task 3: Provider Usage Snapshot Model

**Files:**
- Modify: `src/seektalent/tracing.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Test: `tests/test_runtime_audit.py`

- [ ] **Step 1: Write failing snapshot usage tests**

Update `test_llm_call_snapshot_accepts_cache_repair_and_prompt_cache_metadata` in `tests/test_runtime_audit.py` by passing `provider_usage`:

```python
        provider_usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cache_read_tokens": 64,
            "cache_write_tokens": 0,
            "details": {"cached_tokens": 64},
        },
```

Add assertions:

```python
    assert dump["provider_usage"]["input_tokens"] == 100
    assert dump["provider_usage"]["output_tokens"] == 20
    assert dump["provider_usage"]["total_tokens"] == 120
    assert dump["provider_usage"]["cache_read_tokens"] == 64
    assert dump["provider_usage"]["details"] == {"cached_tokens": 64}
```

Add a helper test near the snapshot tests:

```python
class _FakeUsage:
    input_tokens = 100
    output_tokens = 20
    cache_read_tokens = 64
    cache_write_tokens = 5
    details = {"cached_tokens": 64}


class _FakeResult:
    def usage(self) -> _FakeUsage:
        return _FakeUsage()


def test_provider_usage_from_result_extracts_cache_tokens() -> None:
    usage = provider_usage_from_result(_FakeResult())

    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.total_tokens == 120
    assert usage.cache_read_tokens == 64
    assert usage.cache_write_tokens == 5
    assert usage.details == {"cached_tokens": 64}
```

Update imports:

```python
from seektalent.tracing import LLMCallSnapshot, RunTracer, json_sha256, provider_usage_from_result
```

Update `test_runtime_snapshot_builder_accepts_reflection_cache_and_repair_metadata` to pass:

```python
        provider_usage={
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
            "cache_read_tokens": 8,
            "cache_write_tokens": 0,
            "details": {},
        },
```

Then assert:

```python
    assert dump["provider_usage"]["cache_read_tokens"] == 8
    assert dump["cached_input_tokens"] == 8
```

Update `test_llm_schema_pressure_includes_cache_repair_and_full_retry` input with:

```python
            "provider_usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "cache_read_tokens": 17,
                "cache_write_tokens": 0,
                "details": {},
            },
```

Add assertion:

```python
    assert pressure_item["provider_usage"]["total_tokens"] == 120
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_runtime_audit.py::test_llm_call_snapshot_accepts_cache_repair_and_prompt_cache_metadata tests/test_runtime_audit.py::test_provider_usage_from_result_extracts_cache_tokens tests/test_runtime_audit.py::test_runtime_snapshot_builder_accepts_reflection_cache_and_repair_metadata tests/test_runtime_audit.py::test_llm_schema_pressure_includes_cache_repair_and_full_retry -q
```

Expected: tests fail because `ProviderUsageSnapshot` and `provider_usage_from_result` are missing.

- [ ] **Step 3: Implement provider usage models**

In `src/seektalent/tracing.py`, add:

```python
class ProviderUsageSnapshot(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    details: dict[str, int] = Field(default_factory=dict)


def provider_usage_from_result(result: Any) -> ProviderUsageSnapshot:
    usage = result.usage()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return ProviderUsageSnapshot(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cache_read_tokens=int(getattr(usage, "cache_read_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_write_tokens", 0) or 0),
        details={
            str(key): int(value)
            for key, value in dict(getattr(usage, "details", {}) or {}).items()
            if isinstance(value, int | float)
        },
    )
```

Add to `LLMCallSnapshot`:

```python
    provider_usage: ProviderUsageSnapshot | None = None
```

- [ ] **Step 4: Wire snapshot builder**

In `src/seektalent/runtime/orchestrator.py`, update `_build_llm_call_snapshot(...)` parameters:

```python
        provider_usage: dict[str, Any] | None = None,
```

Pass into `LLMCallSnapshot`:

```python
            provider_usage=provider_usage,
            cached_input_tokens=(
                cached_input_tokens
                if cached_input_tokens is not None
                else (provider_usage or {}).get("cache_read_tokens")
            ),
```

Update `_llm_schema_pressure_item` return dict:

```python
            "provider_usage": snapshot.get("provider_usage"),
```

- [ ] **Step 5: Run snapshot tests**

Run:

```bash
uv run pytest tests/test_runtime_audit.py::test_llm_call_snapshot_accepts_cache_repair_and_prompt_cache_metadata tests/test_runtime_audit.py::test_provider_usage_from_result_extracts_cache_tokens tests/test_runtime_audit.py::test_runtime_snapshot_builder_accepts_reflection_cache_and_repair_metadata tests/test_runtime_audit.py::test_llm_schema_pressure_includes_cache_repair_and_full_retry -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/tracing.py src/seektalent/runtime/orchestrator.py tests/test_runtime_audit.py
git commit -m "Record provider usage in LLM snapshots"
```

## Task 4: Provider Usage Call-Site Plumbing

**Files:**
- Modify: `src/seektalent/requirements/extractor.py`
- Modify: `src/seektalent/controller/react_controller.py`
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/finalize/finalizer.py`
- Modify: `src/seektalent/scoring/scorer.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Test: `tests/test_requirement_extraction.py`
- Test: `tests/test_controller_contract.py`
- Test: `tests/test_scoring_cache.py`

- [ ] **Step 1: Add fake result helper for usage tests**

In `tests/test_requirement_extraction.py`, add:

```python
class _UsageResult:
    def __init__(self, output: object) -> None:
        self.output = output

    def usage(self):  # noqa: ANN201
        return type(
            "Usage",
            (),
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 64,
                "cache_write_tokens": 0,
                "details": {"cached_tokens": 64},
            },
        )()
```

Add the same helper to `tests/test_controller_contract.py`:

```python
class _UsageResult:
    def __init__(self, output: object) -> None:
        self.output = output

    def usage(self):  # noqa: ANN201
        return type(
            "Usage",
            (),
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 64,
                "cache_write_tokens": 0,
                "details": {"cached_tokens": 64},
            },
        )()
```

- [ ] **Step 2: Write requirements/controller usage metadata tests**

Add this test to `tests/test_requirement_extraction.py`:

```python
def test_requirements_extractor_records_provider_usage(monkeypatch) -> None:
    extractor = RequirementExtractor(make_settings(), LoadedPrompt(
        name="requirements",
        path=Path("requirements.md"),
        content="requirements prompt",
        sha256="requirements-hash",
    ))
    draft = _valid_requirement_draft()

    class FakeAgent:
        async def run(self, prompt: str):  # noqa: ANN201
            assert "JOB TITLE" in prompt
            return _UsageResult(draft)

    monkeypatch.setattr(extractor, "_get_agent", lambda prompt_cache_key=None: FakeAgent())

    got = asyncio.run(extractor._extract_live(
        input_truth=build_input_truth(
            job_title="Senior Python Engineer",
            jd="Build retrieval systems.",
            notes="Prefer production AI.",
        ),
        prompt_cache_key="requirements-cache-key",
    ))

    assert got is draft
    assert extractor.last_provider_usage is not None
    assert extractor.last_provider_usage.input_tokens == 100
    assert extractor.last_provider_usage.output_tokens == 20
    assert extractor.last_provider_usage.cache_read_tokens == 64
```

Add this test to `tests/test_controller_contract.py`:

```python
def test_controller_records_provider_usage(monkeypatch) -> None:
    controller = ReActController(make_settings(), LoadedPrompt(
        name="controller",
        path=Path("controller.md"),
        content="controller prompt",
        sha256="controller-hash",
    ))
    context = _controller_context()
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Need recall.",
        proposed_query_terms=["python", "resume matching"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    class FakeAgent:
        async def run(self, prompt: str, deps: ControllerContext):  # noqa: ANN201
            assert "CONTROLLER_CONTEXT" in prompt
            assert deps is context
            return _UsageResult(decision)

    monkeypatch.setattr(controller, "_get_agent", lambda prompt_cache_key=None: FakeAgent())

    got = asyncio.run(controller._decide_live(context=context, prompt_cache_key="controller-cache-key"))

    assert got is decision
    assert controller.last_provider_usage is not None
    assert controller.last_provider_usage.input_tokens == 100
    assert controller.last_provider_usage.output_tokens == 20
    assert controller.last_provider_usage.cache_read_tokens == 64
```

- [ ] **Step 3: Run usage call-site tests to verify failure**

Run:

```bash
uv run pytest tests/test_requirement_extraction.py::test_requirements_extractor_records_provider_usage tests/test_controller_contract.py::test_controller_records_provider_usage -q
```

Expected: tests fail because `last_provider_usage` is missing.

- [ ] **Step 4: Capture usage in requirements/controller/reflection/finalizer**

In each file, import:

```python
from seektalent.tracing import ProviderUsageSnapshot, provider_usage_from_result
```

For `RequirementExtractor`, add metadata:

```python
        self.last_provider_usage: ProviderUsageSnapshot | None = None
```

Reset it:

```python
        self.last_provider_usage = None
```

Update `_extract_live`:

```python
        result = await self._get_agent(prompt_cache_key=prompt_cache_key).run(render_requirements_prompt(input_truth))
        self.last_provider_usage = provider_usage_from_result(result)
        return result.output
```

For `ReActController`, add this field in `__init__`:

```python
        self.last_provider_usage: ProviderUsageSnapshot | None = None
```

Reset it in `_reset_metadata`:

```python
        self.last_provider_usage = None
```

Update `_decide_live`:

```python
        result = await agent.run(render_controller_prompt(context), deps=context)
        self.last_provider_usage = provider_usage_from_result(result)
        return result.output
```

For `ReflectionCritic`, add this field in `__init__`:

```python
        self.last_provider_usage: ProviderUsageSnapshot | None = None
```

Reset it in `_reset_metadata`:

```python
        self.last_provider_usage = None
```

Update `_reflect_live`:

```python
        result = await agent.run(render_reflection_prompt(context))
        self.last_provider_usage = provider_usage_from_result(result)
        return result.output
```

For `Finalizer`, add this field in `__init__`:

```python
        self.last_provider_usage: ProviderUsageSnapshot | None = None
```

Reset it near the start of `finalize`:

```python
        self.last_provider_usage = None
```

Update `finalize` after `agent.run(...)`:

```python
        self.last_provider_usage = provider_usage_from_result(result)
        self.last_draft_output = result.output
```

- [ ] **Step 5: Capture scoring usage without shared mutable state**

In `src/seektalent/scoring/scorer.py`, update imports:

```python
from seektalent.tracing import LLMCallSnapshot, ProviderUsageSnapshot, RunTracer, provider_usage_from_result
```

Update `_score_one_live` signature:

```python
    async def _score_one_live(
        self,
        *,
        prompt: str,
        agent: Agent[None, ScoredCandidateDraft],
    ) -> tuple[ScoredCandidateDraft, ProviderUsageSnapshot]:
        result = await agent.run(prompt)
        return result.output, provider_usage_from_result(result)
```

Update the live call site:

```python
            draft, provider_usage = await self._score_one_live(prompt=user_prompt, agent=agent)
```

Pass into live scoring `LLMCallSnapshot`:

```python
                    provider_usage=provider_usage,
                    cached_input_tokens=provider_usage.cache_read_tokens,
```

Do not set provider usage on exact cache hits because no provider call happened.

- [ ] **Step 6: Pass stage usage into orchestrator snapshots**

In requirements success/failure snapshots:

```python
                    provider_usage=(
                        self.requirement_extractor.last_provider_usage.model_dump(mode="json")
                        if self.requirement_extractor.last_provider_usage is not None
                        else None
                    ),
```

In controller success/failure snapshots:

```python
                        provider_usage=(
                            self.controller.last_provider_usage.model_dump(mode="json")
                            if getattr(self.controller, "last_provider_usage", None) is not None
                            else None
                        ),
```

In reflection success/failure snapshots:

```python
                        provider_usage=(
                            self.reflection_critic.last_provider_usage.model_dump(mode="json")
                            if getattr(self.reflection_critic, "last_provider_usage", None) is not None
                            else None
                        ),
```

In finalizer success/failure snapshots:

```python
                    provider_usage=(
                        self.finalizer.last_provider_usage.model_dump(mode="json")
                        if self.finalizer.last_provider_usage is not None
                        else None
                    ),
```

- [ ] **Step 7: Run targeted usage tests**

Run:

```bash
uv run pytest tests/test_requirement_extraction.py::test_requirements_extractor_records_provider_usage tests/test_controller_contract.py::test_controller_records_provider_usage tests/test_runtime_audit.py tests/test_scoring_cache.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/seektalent/requirements/extractor.py src/seektalent/controller/react_controller.py src/seektalent/reflection/critic.py src/seektalent/finalize/finalizer.py src/seektalent/scoring/scorer.py src/seektalent/runtime/orchestrator.py tests/test_requirement_extraction.py tests/test_controller_contract.py tests/test_runtime_audit.py tests/test_scoring_cache.py
git commit -m "Capture provider usage at LLM call sites"
```

## Task 5: Rationale Length Governance

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/prompts/controller.md`
- Modify: `src/seektalent/prompts/reflection.md`
- Test: `tests/test_controller_contract.py`
- Test: `tests/test_reflection_contract.py`
- Test: `tests/test_llm_input_prompts.py`

- [ ] **Step 1: Write controller max-length tests**

Add to `tests/test_controller_contract.py`:

```python
def test_controller_decision_rationale_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        SearchControllerDecision(
            thought_summary="Search.",
            action="search_cts",
            decision_rationale="x" * 1201,
            proposed_query_terms=["Python", "FastAPI"],
            proposed_filter_plan=ProposedFilterPlan(),
        )


def test_controller_response_to_reflection_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        SearchControllerDecision(
            thought_summary="Search.",
            action="search_cts",
            decision_rationale="Need recall.",
            proposed_query_terms=["Python", "FastAPI"],
            proposed_filter_plan=ProposedFilterPlan(),
            response_to_reflection="x" * 801,
        )
```

`tests/test_controller_contract.py` already imports `ValidationError`; keep that import:

```python
from pydantic import ValidationError
```

- [ ] **Step 2: Write reflection max-length test**

Add to `tests/test_reflection_contract.py`:

```python
def test_reflection_rationale_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(),
            filter_advice=ReflectionFilterAdviceDraft(),
            reflection_rationale="x" * 1201,
            suggest_stop=False,
            suggested_stop_reason=None,
        )
```

Add `ValidationError` to the existing pydantic import in `tests/test_reflection_contract.py`:

```python
from pydantic import ValidationError
```

- [ ] **Step 3: Write prompt wording tests**

In `tests/test_llm_input_prompts.py`, add:

```python
def test_controller_prompt_says_few_shot_terms_are_not_reusable() -> None:
    prompt = Path("src/seektalent/prompts/controller.md").read_text(encoding="utf-8")

    assert "few-shot terms are examples only" in prompt
    assert "unless they exist in the current active admitted term bank" in prompt


def test_reflection_prompt_mentions_rationale_schema_budget() -> None:
    prompt = Path("src/seektalent/prompts/reflection.md").read_text(encoding="utf-8")

    assert "schema length budget" in prompt
    assert "audit summary" in prompt
```

- [ ] **Step 4: Run rationale tests to verify failure**

Run:

```bash
uv run pytest tests/test_controller_contract.py::test_controller_decision_rationale_has_generous_length_limit tests/test_controller_contract.py::test_controller_response_to_reflection_has_generous_length_limit tests/test_reflection_contract.py::test_reflection_rationale_has_generous_length_limit tests/test_llm_input_prompts.py::test_controller_prompt_says_few_shot_terms_are_not_reusable tests/test_llm_input_prompts.py::test_reflection_prompt_mentions_rationale_schema_budget -q
```

Expected: tests fail because max lengths and prompt wording are missing.

- [ ] **Step 5: Add schema max lengths**

In `src/seektalent/models.py`, define constants near controller/reflection models:

```python
THOUGHT_SUMMARY_MAX_CHARS = 500
DECISION_RATIONALE_MAX_CHARS = 1200
RESPONSE_TO_REFLECTION_MAX_CHARS = 800
REFLECTION_RATIONALE_MAX_CHARS = 1200
```

Update `SearchControllerDecision`:

```python
    thought_summary: str = Field(
        min_length=1,
        max_length=THOUGHT_SUMMARY_MAX_CHARS,
        description="Short summary of the controller's current decision.",
    )
    decision_rationale: str = Field(
        min_length=1,
        max_length=DECISION_RATIONALE_MAX_CHARS,
        description="Short operational rationale for the search decision.",
    )
    response_to_reflection: str | None = Field(
        default=None,
        max_length=RESPONSE_TO_REFLECTION_MAX_CHARS,
        description="Explicit response to the previous round's reflection when one exists.",
    )
```

Update `StopControllerDecision`:

```python
    thought_summary: str = Field(
        min_length=1,
        max_length=THOUGHT_SUMMARY_MAX_CHARS,
        description="Short summary of the controller's current decision.",
    )
    action: Literal["stop"] = Field(description="Stop retrieval and finish the run.")
    decision_rationale: str = Field(
        min_length=1,
        max_length=DECISION_RATIONALE_MAX_CHARS,
        description="Short operational rationale for the stop decision.",
    )
    response_to_reflection: str | None = Field(
        default=None,
        max_length=RESPONSE_TO_REFLECTION_MAX_CHARS,
        description="Explicit response to the previous round's reflection when one exists.",
    )
    stop_reason: str = Field(min_length=1, description="Concrete stop reason for ending retrieval.")
```

Update `ReflectionAdvice` and `ReflectionAdviceDraft`:

```python
    reflection_rationale: str = Field(
        default="",
        max_length=REFLECTION_RATIONALE_MAX_CHARS,
        description="Human-readable explanation for the reflection advice. Used for TUI trace only.",
    )
```

For `ReflectionAdviceDraft`, preserve `min_length=1`:

```python
    reflection_rationale: str = Field(
        min_length=1,
        max_length=REFLECTION_RATIONALE_MAX_CHARS,
        description="Explain the round quality, coverage, and next action within the visible rationale budget.",
    )
```

- [ ] **Step 6: Update prompts**

In `src/seektalent/prompts/controller.md`, add under Query Term Discipline:

```markdown
- The few-shot terms are examples only. Do not reuse example terms such as `FastAPI` unless they exist in the current active admitted term bank.
```

Update Output Style:

```markdown
- Keep `thought_summary` short and within the schema length budget.
- Keep `decision_rationale` as a concise audit summary, not a step-by-step reasoning transcript.
```

In `src/seektalent/prompts/reflection.md`, update Output Style:

```markdown
- Write `reflection_rationale` as a concise audit summary within the schema length budget. Do not include a step-by-step reasoning transcript.
```

- [ ] **Step 7: Run rationale tests**

Run:

```bash
uv run pytest tests/test_controller_contract.py::test_controller_decision_rationale_has_generous_length_limit tests/test_controller_contract.py::test_controller_response_to_reflection_has_generous_length_limit tests/test_reflection_contract.py::test_reflection_rationale_has_generous_length_limit tests/test_llm_input_prompts.py::test_controller_prompt_says_few_shot_terms_are_not_reusable tests/test_llm_input_prompts.py::test_reflection_prompt_mentions_rationale_schema_budget -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/seektalent/models.py src/seektalent/prompts/controller.md src/seektalent/prompts/reflection.md tests/test_controller_contract.py tests/test_reflection_contract.py tests/test_llm_input_prompts.py
git commit -m "Limit visible controller reflection rationale length"
```

## Task 6: Verification

**Files:**
- Verify only

- [ ] **Step 1: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run a no-eval smoke benchmark with one JD**

Use the existing one-row benchmark pattern and do not run eval:

```bash
mkdir -p runs/runtime_lifecycle_smoke
python - <<'PY'
from pathlib import Path
source = Path('/Users/frankqdwang/Agents/SeekTalent-0.2.4/artifacts/benchmarks/agent_jds.jsonl')
target = Path('runs/runtime_lifecycle_smoke/agent_jd_003.only.jsonl')
for line in source.read_text(encoding='utf-8').splitlines():
    if '"jd_id": "agent_jd_003"' in line:
        target.write_text(line + '\n', encoding='utf-8')
        break
else:
    raise SystemExit('agent_jd_003 not found')
PY
env SEEKTALENT_RUNTIME_MODE=dev SEEKTALENT_ENABLE_EVAL=false SEEKTALENT_OPENAI_PROMPT_CACHE_ENABLED=true SEEKTALENT_OPENAI_PROMPT_CACHE_RETENTION=24h SEEKTALENT_WANDB_ENTITY= SEEKTALENT_WANDB_PROJECT= SEEKTALENT_WEAVE_ENTITY= SEEKTALENT_WEAVE_PROJECT= uv run seektalent benchmark --jds-file runs/runtime_lifecycle_smoke/agent_jd_003.only.jsonl --env-file /Users/frankqdwang/Agents/SeekTalent-0.2.4/.env --output-dir runs/runtime_lifecycle_smoke --benchmark-max-concurrency 1 --disable-eval --json
```

Expected:

- command exits 0;
- benchmark JSON has `evaluation_result: null`;
- generated `run_config.json` records `runtime_mode: "dev"`;
- generated LLM call snapshots include `provider_usage` when provider usage is available;
- `decision_rationale` and `reflection_rationale` stay within schema limits.

- [ ] **Step 3: Audit the smoke run**

Run:

```bash
uv run python tools/audit_run_latency.py --limit 1 runs/runtime_lifecycle_smoke
```

Expected: audit completes and includes cache/repair metadata. If `cached_input_tokens` is 0, note that provider cache did not report a cache read in this smoke run.

- [ ] **Step 4: Final status**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: only intended files are modified or the working tree is clean after commits.
