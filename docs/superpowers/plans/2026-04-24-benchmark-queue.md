# Benchmark Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `seektalent benchmark` load maintained domain JD files by default, run JD workflows concurrently, limit judge calls globally, and serialize Weave/W&B uploads.

**Architecture:** Keep ordinary `seektalent run` synchronous. For benchmark, run local workflows in a bounded thread pool with one shared judge limiter, disable in-run remote eval logging, then send completed eval artifacts to a single uploader queue in completion order. Default benchmark input becomes directory scanning, while explicit `--jds-file` remains supported.

**Tech Stack:** Python 3.12, argparse, `concurrent.futures`, `threading`, asyncio, pytest.

---

## File Structure

- Modify `src/seektalent/evaluation.py`: add a thread-safe async judge limiter, split local eval from remote upload, and let W&B report updates run once at the end of benchmark upload.
- Modify `src/seektalent/runtime/orchestrator.py`: carry `terminal_stop_guidance` through `RunArtifacts` and pass benchmark-supplied eval options into `evaluate_run`.
- Modify `src/seektalent/api.py`: expose optional `judge_limiter` and `eval_remote_logging` parameters for benchmark use.
- Modify `src/seektalent/cli.py`: add benchmark directory loading, retry-aware run scheduling, upload serialization, new CLI flags, inspect metadata, and summary fields.
- Modify `docs/cli.md` and `docs/cli.zh-CN.md`: document directory mode and retry/upload behavior.
- Modify `tests/test_cli.py`, `tests/test_evaluation.py`, and `tests/test_api.py`: cover loading, scheduling, retry, upload queue, and plumbing behavior.

---

### Task 1: Benchmark Directory Input Loading

**Files:**
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_cli.py`
- Docs later: `docs/cli.md`, `docs/cli.zh-CN.md`

- [ ] **Step 1: Write failing tests for directory scanning**

Add these tests near the existing benchmark tests in `tests/test_cli.py`:

```python
def test_load_benchmark_directory_skips_generated_and_temporary_files(tmp_path: Path) -> None:
    benchmarks_dir = tmp_path / "benchmarks"
    benchmarks_dir.mkdir()
    (benchmarks_dir / "agent_jds.jsonl").write_text(
        json.dumps({"jd_id": "agent_1", "job_title": "Agent", "job_description": "Agent JD"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (benchmarks_dir / "bigdata.jsonl").write_text(
        json.dumps({"jd_id": "bigdata_1", "job_title": "Bigdata", "job_description": "Bigdata JD"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    for name in ("phase_2_2_pilot.jsonl", "scratch.tmp.jsonl", "agent.only.jsonl", "small.subset.jsonl"):
        (benchmarks_dir / name).write_text(
            json.dumps({"jd_id": name, "job_title": "Skip", "job_description": "Skip JD"}, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

    rows, files = _load_benchmark_directory(benchmarks_dir)

    assert [row["jd_id"] for row in rows] == ["agent_1", "bigdata_1"]
    assert [Path(file).name for file in files] == ["agent_jds.jsonl", "bigdata.jsonl"]
    assert rows[0]["benchmark_file"] == str(benchmarks_dir / "agent_jds.jsonl")
    assert rows[0]["benchmark_group"] == "agent_jds"
    assert rows[0]["input_index"] == 0
    assert rows[1]["benchmark_group"] == "bigdata"
    assert rows[1]["input_index"] == 1


def test_load_benchmark_file_preserves_explicit_group_and_adds_source_metadata(tmp_path: Path) -> None:
    benchmark_file = tmp_path / "custom.jsonl"
    benchmark_file.write_text(
        json.dumps(
            {
                "jd_id": "custom_1",
                "job_title": "Custom",
                "job_description": "Custom JD",
                "benchmark_group": "manual_group",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _load_benchmark_rows(benchmark_file)

    assert rows == [
        {
            "jd_id": "custom_1",
            "job_title": "Custom",
            "job_description": "Custom JD",
            "benchmark_group": "manual_group",
            "benchmark_file": str(benchmark_file),
            "input_index": 0,
        }
    ]
```

Also add `_load_benchmark_directory` to the import list if the test file imports private helpers explicitly. If it does not, reference it through `seektalent.cli._load_benchmark_directory`.

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_load_benchmark_directory_skips_generated_and_temporary_files tests/test_cli.py::test_load_benchmark_file_preserves_explicit_group_and_adds_source_metadata -v
```

Expected: fails because `_load_benchmark_directory` is not defined and `_load_benchmark_rows` does not add metadata.

- [ ] **Step 3: Implement file and directory loading**

In `src/seektalent/cli.py`, add constants near the other module constants:

```python
DEFAULT_BENCHMARKS_DIR = Path("artifacts/benchmarks")
SKIPPED_BENCHMARK_FILE_PATTERNS = (
    "phase_*.jsonl",
    "*.tmp.jsonl",
    "*.only.jsonl",
    "*.subset.jsonl",
)
```

Replace `_load_benchmark_rows` with:

```python
def _load_benchmark_rows(path: Path, *, input_index_start: int = 0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path} line {line_no}: {exc.msg}") from exc
        if "job_description" not in payload:
            raise ValueError(f"Missing job_description in {path} line {line_no}.")
        if "job_title" not in payload:
            raise ValueError(f"Missing job_title in {path} line {line_no}.")
        row = dict(payload)
        row["benchmark_file"] = str(path)
        row["benchmark_group"] = str(row.get("benchmark_group") or path.stem)
        row["input_index"] = input_index_start + len(rows)
        rows.append(row)
    if not rows:
        raise ValueError(f"No benchmark rows found in {path}.")
    return rows


def _skip_default_benchmark_file(path: Path) -> bool:
    return any(path.match(pattern) for pattern in SKIPPED_BENCHMARK_FILE_PATTERNS)


def _load_benchmark_directory(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    files = [item for item in sorted(path.glob("*.jsonl")) if not _skip_default_benchmark_file(item)]
    if not files:
        raise ValueError(f"No benchmark JSONL files found in {path}.")
    rows: list[dict[str, object]] = []
    for file_path in files:
        rows.extend(_load_benchmark_rows(file_path, input_index_start=len(rows)))
    return rows, [str(file_path) for file_path in files]
```

- [ ] **Step 4: Run the loading tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_load_benchmark_directory_skips_generated_and_temporary_files tests/test_cli.py::test_load_benchmark_file_preserves_explicit_group_and_adds_source_metadata -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/seektalent/cli.py tests/test_cli.py
git commit -m "Add benchmark directory input loading"
```

---

### Task 2: Local Eval and Remote Upload Split

**Files:**
- Modify: `src/seektalent/evaluation.py`
- Test: `tests/test_evaluation.py`

- [ ] **Step 1: Write failing tests for remote logging control and report deferral**

Add these tests near existing `evaluate_run` logging tests in `tests/test_evaluation.py`:

```python
def test_evaluate_run_can_skip_remote_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_judge_many(self, *, jd, notes, candidates, cache, judge_limiter=None):  # noqa: ANN001
        del self, jd, notes, cache, judge_limiter
        result = ResumeJudgeResult(score=3, rationale="Strong.")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    monkeypatch.setattr("seektalent.evaluation._log_to_weave", lambda **kwargs: calls.append("weave"))
    monkeypatch.setattr("seektalent.evaluation._log_to_wandb", lambda **kwargs: calls.append("wandb"))
    settings = make_settings(runs_dir=str(tmp_path / "runs"), enable_eval=True)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        raw={"resume_id": "resume-1"},
    )

    artifacts = asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=tmp_path / "run-1",
            jd="JD text",
            round_01_candidates=[candidate],
            final_candidates=[candidate],
            rounds_executed=3,
            log_remote=False,
        )
    )

    assert calls == []
    assert artifacts.path.exists()


def test_log_evaluation_remotely_can_defer_wandb_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    evaluation = EvaluationResult(
        run_id="run-1",
        judge_model="openai-responses:gpt-5.4",
        jd_sha256="jd",
        round_01=EvaluationStageResult(stage="round_01", ndcg_at_10=1.0, precision_at_10=1.0, total_score=1.0, candidates=[]),
        final=EvaluationStageResult(stage="final", ndcg_at_10=1.0, precision_at_10=1.0, total_score=1.0, candidates=[]),
    )
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        enable_eval=True,
        wandb_project="seektalent",
        weave_project="seektalent",
    )
    artifact_root = tmp_path / "run-1"
    (artifact_root / "evaluation").mkdir(parents=True)
    (artifact_root / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
    (artifact_root / "raw_resumes").mkdir()

    monkeypatch.setattr("seektalent.evaluation._log_to_weave", lambda **kwargs: calls.append("weave"))
    monkeypatch.setattr(
        "seektalent.evaluation._log_to_wandb",
        lambda **kwargs: calls.append(f"wandb:{kwargs['update_report']}") or {"run_name": "run-1"},
    )

    report_row = log_evaluation_remotely(
        settings=settings,
        artifact_root=artifact_root,
        evaluation=evaluation,
        rounds_executed=3,
        terminal_stop_guidance=None,
        update_report=False,
    )

    assert calls == ["weave", "wandb:False"]
    assert report_row == {"run_name": "run-1"}
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
uv run pytest tests/test_evaluation.py::test_evaluate_run_can_skip_remote_logging tests/test_evaluation.py::test_log_evaluation_remotely_can_defer_wandb_report -v
```

Expected: fails because `log_remote`, `log_evaluation_remotely`, and `update_report` are not implemented.

- [ ] **Step 3: Add a thread-safe async judge limiter**

In `src/seektalent/evaluation.py`, add imports:

```python
import threading
```

Add this class near `ResumeJudge`:

```python
class AsyncJudgeLimiter:
    def __init__(self, max_concurrency: int) -> None:
        self._semaphore = threading.BoundedSemaphore(max_concurrency)

    async def __aenter__(self) -> "AsyncJudgeLimiter":
        await asyncio.to_thread(self._semaphore.acquire)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._semaphore.release()
```

Change `ResumeJudge.judge_many` signature to:

```python
    async def judge_many(
        self,
        *,
        jd: str,
        notes: str = "",
        candidates: list[ResumeCandidate],
        cache: JudgeCache,
        judge_limiter: AsyncJudgeLimiter | None = None,
    ) -> tuple[dict[str, tuple[ResumeJudgeResult, bool, int]], list[JudgeLabelWrite]]:
```

Replace the local semaphore assignment with:

```python
        limiter = judge_limiter or AsyncJudgeLimiter(self.settings.judge_max_concurrency)
```

Replace `async with semaphore:` inside `worker` with:

```python
            async with limiter:
                judged = await agent.run(prompt)
```

- [ ] **Step 4: Split remote logging from local eval**

Change `_log_to_wandb` signature:

```python
def _log_to_wandb(
    *,
    settings: AppSettings,
    artifact_root: Path,
    evaluation: EvaluationResult,
    rounds_executed: int,
    terminal_stop_guidance: StopGuidance | None = None,
    update_report: bool = True,
) -> dict[str, Any] | None:
```

At the early return, return `None`:

```python
    if not settings.wandb_project:
        return None
```

At the end of `_log_to_wandb`, replace the report update with:

```python
    if update_report:
        _upsert_wandb_report(settings, extra_rows=[report_row])
    return report_row
```

Add this function after `_log_to_wandb`:

```python
def log_evaluation_remotely(
    *,
    settings: AppSettings,
    artifact_root: Path,
    evaluation: EvaluationResult,
    rounds_executed: int,
    terminal_stop_guidance: StopGuidance | None = None,
    update_report: bool = True,
) -> dict[str, Any] | None:
    _log_to_weave(settings=settings, evaluation=evaluation)
    return _log_to_wandb(
        settings=settings,
        artifact_root=artifact_root,
        evaluation=evaluation,
        rounds_executed=rounds_executed,
        terminal_stop_guidance=terminal_stop_guidance,
        update_report=update_report,
    )
```

Change `evaluate_run` signature to include:

```python
    judge_limiter: AsyncJudgeLimiter | None = None,
    log_remote: bool = True,
```

Pass `judge_limiter` into `judge_many`:

```python
        judged, pending_cache_writes = await ResumeJudge(settings, prompt).judge_many(
            jd=jd,
            notes=notes,
            candidates=list(unique_candidates.values()),
            cache=cache,
            judge_limiter=judge_limiter,
        )
```

Replace the direct `_log_to_weave` and `_log_to_wandb` calls with:

```python
        if log_remote:
            log_evaluation_remotely(
                settings=settings,
                artifact_root=temp_root,
                evaluation=evaluation,
                rounds_executed=rounds_executed,
                terminal_stop_guidance=terminal_stop_guidance,
            )
```

- [ ] **Step 5: Run focused evaluation tests**

Run:

```bash
uv run pytest tests/test_evaluation.py::test_evaluate_run_can_skip_remote_logging tests/test_evaluation.py::test_log_evaluation_remotely_can_defer_wandb_report tests/test_evaluation.py::test_resume_judge_uses_judge_concurrency_limit tests/test_evaluate_run_logs_weave_before_wandb -v
```

Expected: all selected tests pass. If the last test selector is wrong, run `uv run pytest tests/test_evaluation.py -k "remote_logging or concurrency_limit or weave_before_wandb" -v`.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/seektalent/evaluation.py tests/test_evaluation.py
git commit -m "Split eval upload from local evaluation"
```

---

### Task 3: Runtime and API Plumbing

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/api.py`
- Test: `tests/test_api.py`
- Test: existing runtime tests

- [ ] **Step 1: Write a failing API plumbing test**

Add this test to `tests/test_api.py`:

```python
def test_run_match_passes_eval_options_to_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    limiter = object()

    class FakeRuntime:
        def __init__(self, settings, *, judge_limiter=None, eval_remote_logging=True):  # noqa: ANN001
            captured["settings"] = settings
            captured["judge_limiter"] = judge_limiter
            captured["eval_remote_logging"] = eval_remote_logging

        def run(self, *, job_title, jd, notes, progress_callback=None):  # noqa: ANN001
            del job_title, jd, notes, progress_callback
            return RunArtifacts(
                final_result=FinalResult(
                    run_id="run-1",
                    run_dir="/tmp/run-1",
                    rounds_executed=1,
                    stop_reason="controller_stop",
                    candidates=[],
                    summary="done",
                ),
                final_markdown="# Final",
                run_id="run-1",
                run_dir=Path("/tmp/run-1"),
                trace_log_path=Path("/tmp/run-1/trace.log"),
                candidate_store={},
                normalized_store={},
                evaluation_result=None,
                terminal_stop_guidance=None,
            )

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)

    run_match(
        job_title="Role",
        jd="JD",
        settings=AppSettings(mock_cts=True),
        env_file=None,
        judge_limiter=limiter,
        eval_remote_logging=False,
    )

    assert captured["judge_limiter"] is limiter
    assert captured["eval_remote_logging"] is False
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
uv run pytest tests/test_api.py::test_run_match_passes_eval_options_to_runtime -v
```

Expected: fails because `run_match` does not accept the new parameters and `RunArtifacts` lacks `terminal_stop_guidance`.

- [ ] **Step 3: Add runtime fields and constructor parameters**

In `src/seektalent/runtime/orchestrator.py`, update imports if needed:

```python
from seektalent.evaluation import TOP_K, AsyncJudgeLimiter, EvaluationResult, evaluate_run
```

Add a field to `RunArtifacts`:

```python
    terminal_stop_guidance: StopGuidance | None
```

Change `WorkflowRuntime.__init__` signature:

```python
    def __init__(
        self,
        settings: AppSettings,
        *,
        judge_limiter: AsyncJudgeLimiter | None = None,
        eval_remote_logging: bool = True,
    ) -> None:
```

Inside `__init__`, store:

```python
        self.judge_limiter = judge_limiter
        self.eval_remote_logging = eval_remote_logging
```

In the `evaluate_run` call, add:

```python
                    judge_limiter=self.judge_limiter,
                    log_remote=self.eval_remote_logging,
```

In the `RunArtifacts(...)` return, add:

```python
                terminal_stop_guidance=(
                    terminal_controller_round.stop_guidance if terminal_controller_round is not None else None
                ),
```

- [ ] **Step 4: Add API parameters**

In `src/seektalent/api.py`, import:

```python
from seektalent.evaluation import AsyncJudgeLimiter, EvaluationResult
from seektalent.models import FinalResult, StopGuidance
```

Add a field to `MatchRunResult`:

```python
    terminal_stop_guidance: StopGuidance | None
```

In `MatchRunResult.from_artifacts`, add:

```python
            terminal_stop_guidance=artifacts.terminal_stop_guidance,
```

Change `run_match` signature to include:

```python
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
```

Construct runtime as:

```python
    runtime = WorkflowRuntime(
        _effective_settings(settings=settings, env_file=env_file),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )
```

Change `run_match_async` with the same two parameters and runtime construction.

- [ ] **Step 5: Update affected tests that construct `RunArtifacts` or `MatchRunResult`**

Search:

```bash
rg -n "RunArtifacts\\(|MatchRunResult\\(" tests src
```

For each direct construction in tests, add:

```python
terminal_stop_guidance=None,
```

For helper functions returning `MatchRunResult`, add the same field.

- [ ] **Step 6: Run focused API/runtime tests**

Run:

```bash
uv run pytest tests/test_api.py tests/test_runtime_lifecycle.py tests/test_cli.py::test_run_json_allows_null_evaluation_result -v
```

Expected: selected tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/api.py tests/test_api.py tests/test_cli.py
git commit -m "Plumb benchmark eval controls through runtime"
```

---

### Task 4: Benchmark Scheduler, Retries, and Upload Queue

**Files:**
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing scheduler tests**

Add these tests near the existing benchmark parallelism test in `tests/test_cli.py`:

```python
def test_benchmark_retries_failed_row_once_and_keeps_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    calls = 0

    def fake_run_match(**kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        run_dir = tmp_path / "run-1"
        run_dir.mkdir(exist_ok=True)
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id="run-1",
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--benchmark-run-retries",
            "1",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert calls == 2
    assert payload["runs"][0]["status"] == "succeeded"
    assert payload["runs"][0]["attempts"] == 2


def test_benchmark_returns_one_when_row_exhausts_retries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("seektalent.cli.run_match", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("failed")))

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--benchmark-run-retries",
            "1",
            "--json",
        ]
    ) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"][0]["status"] == "failed"
    assert payload["runs"][0]["attempts"] == 2
    assert "failed" in payload["runs"][0]["error"]
    assert Path(payload["summary_path"]).exists()


def test_benchmark_uploads_eval_results_serially_in_completion_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        "\n".join(
            [
                json.dumps({"jd_id": "slow", "job_title": "Slow", "job_description": "JD slow"}, ensure_ascii=False),
                json.dumps({"jd_id": "fast", "job_title": "Fast", "job_description": "JD fast"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    upload_order: list[str] = []
    active_uploads = 0
    max_active_uploads = 0
    lock = threading.Lock()

    def fake_run_match(**kwargs):  # noqa: ANN001
        jd = kwargs["jd"]
        if jd == "JD slow":
            time.sleep(0.04)
        run_id = "slow-run" if jd == "JD slow" else "fast-run"
        run_dir = tmp_path / run_id
        (run_dir / "evaluation").mkdir(parents=True)
        (run_dir / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
        (run_dir / "raw_resumes").mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        evaluation = EvaluationResult(
            run_id=run_id,
            judge_model="openai-responses:gpt-5.4",
            jd_sha256="jd",
            round_01=EvaluationStageResult(stage="round_01", ndcg_at_10=1.0, precision_at_10=1.0, total_score=1.0, candidates=[]),
            final=EvaluationStageResult(stage="final", ndcg_at_10=1.0, precision_at_10=1.0, total_score=1.0, candidates=[]),
        )
        return MatchRunResult(
            final_result=FinalResult(
                run_id=run_id,
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id=run_id,
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=evaluation,
            terminal_stop_guidance=None,
        )

    def fake_log_remote(**kwargs):  # noqa: ANN001
        nonlocal active_uploads, max_active_uploads
        with lock:
            active_uploads += 1
            max_active_uploads = max(max_active_uploads, active_uploads)
        time.sleep(0.02)
        upload_order.append(kwargs["evaluation"].run_id)
        with lock:
            active_uploads -= 1
        return {"run_name": kwargs["evaluation"].run_id}

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)
    monkeypatch.setattr("seektalent.cli.log_evaluation_remotely", fake_log_remote)
    monkeypatch.setattr("seektalent.cli._upsert_wandb_report", lambda settings, extra_rows=(): None)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--benchmark-max-concurrency",
            "2",
            "--enable-eval",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert upload_order == ["fast-run", "slow-run"]
    assert max_active_uploads == 1
    assert [row["completion_index"] for row in payload["runs"]] == [2, 1]
    assert [row["upload_status"] for row in payload["runs"]] == ["succeeded", "succeeded"]
```

Ensure `tests/test_cli.py` imports `threading`, `time`, `EvaluationResult`, and `EvaluationStageResult` if not already imported.

- [ ] **Step 2: Run scheduler tests and verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_benchmark_retries_failed_row_once_and_keeps_summary tests/test_cli.py::test_benchmark_returns_one_when_row_exhausts_retries tests/test_cli.py::test_benchmark_uploads_eval_results_serially_in_completion_order -v
```

Expected: fails because the scheduler and uploader are not implemented.

- [ ] **Step 3: Add benchmark result and upload helpers**

In `src/seektalent/cli.py`, update imports:

```python
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from queue import Queue
from typing import Any
```

Extend the evaluation import:

```python
from seektalent.evaluation import AsyncJudgeLimiter, log_evaluation_remotely, migrate_judge_assets, _upsert_wandb_report
```

Add these dataclasses near the other CLI dataclasses:

```python
@dataclass
class BenchmarkAttempt:
    row: dict[str, object]
    attempt: int
    started_at: str


@dataclass
class BenchmarkUploadTask:
    result_row: dict[str, object]
    result: MatchRunResult
```

Add timestamp and error helpers:

```python
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"
```

Add uploader class:

```python
class BenchmarkUploader:
    def __init__(self, *, settings: AppSettings, retries: int) -> None:
        self.settings = settings
        self.retries = retries
        self.report_rows: list[dict[str, Any]] = []
        self.queue: Queue[BenchmarkUploadTask | None] = Queue()
        self.thread = threading.Thread(target=self._work, name="seektalent-benchmark-uploader")
        self.thread.start()

    def submit(self, task: BenchmarkUploadTask) -> None:
        self.queue.put(task)

    def close(self) -> None:
        self.queue.put(None)
        self.thread.join()
        if self.report_rows:
            _upsert_wandb_report(self.settings, extra_rows=self.report_rows)

    def _work(self) -> None:
        while True:
            task = self.queue.get()
            try:
                if task is None:
                    return
                self._upload(task)
            finally:
                self.queue.task_done()

    def _upload(self, task: BenchmarkUploadTask) -> None:
        attempts = 0
        last_error = ""
        for attempt in range(1, self.retries + 2):
            attempts = attempt
            try:
                report_row = log_evaluation_remotely(
                    settings=self.settings,
                    artifact_root=task.result.run_dir,
                    evaluation=task.result.evaluation_result,
                    rounds_executed=task.result.final_result.rounds_executed,
                    terminal_stop_guidance=task.result.terminal_stop_guidance,
                    update_report=False,
                )
                if report_row is not None:
                    self.report_rows.append(report_row)
                task.result_row["upload_status"] = "succeeded"
                task.result_row["upload_attempts"] = attempts
                task.result_row.pop("upload_error", None)
                return
            except Exception as exc:
                last_error = _error_text(exc)
        task.result_row["upload_status"] = "failed"
        task.result_row["upload_attempts"] = attempts
        task.result_row["upload_error"] = last_error
```

- [ ] **Step 4: Add run row builder**

Add this helper in `src/seektalent/cli.py`:

```python
def _benchmark_result_row(
    *,
    row: dict[str, object],
    result: MatchRunResult,
    attempts: int,
    started_at: str,
    completed_at: str,
    completion_index: int,
) -> dict[str, object]:
    result_row: dict[str, object] = {
        "jd_id": row.get("jd_id"),
        "job_title": row.get("job_title"),
        "benchmark_file": row.get("benchmark_file"),
        "benchmark_group": row.get("benchmark_group"),
        "input_index": row.get("input_index"),
        "status": "succeeded",
        "attempts": attempts,
        "completion_index": completion_index,
        "run_started_at": started_at,
        "run_completed_at": completed_at,
        "upload_status": "skipped",
        "upload_attempts": 0,
        "run_id": result.run_id,
        "run_dir": str(result.run_dir),
        "trace_log_path": str(result.trace_log_path),
        "evaluation_result": (
            result.evaluation_result.model_dump(mode="json") if result.evaluation_result is not None else None
        ),
    }
    term_surface_audit_path = result.run_dir / "term_surface_audit.json"
    if term_surface_audit_path.exists():
        result_row["term_surface_audit_path"] = str(term_surface_audit_path)
    return result_row
```

Add a failed row helper:

```python
def _failed_benchmark_result_row(
    *,
    row: dict[str, object],
    attempts: int,
    started_at: str,
    completed_at: str,
    error: str,
) -> dict[str, object]:
    return {
        "jd_id": row.get("jd_id"),
        "job_title": row.get("job_title"),
        "benchmark_file": row.get("benchmark_file"),
        "benchmark_group": row.get("benchmark_group"),
        "input_index": row.get("input_index"),
        "status": "failed",
        "attempts": attempts,
        "run_started_at": started_at,
        "run_completed_at": completed_at,
        "upload_status": "skipped",
        "upload_attempts": 0,
        "run_id": None,
        "run_dir": None,
        "trace_log_path": None,
        "evaluation_result": None,
        "error": error,
    }
```

- [ ] **Step 5: Replace benchmark execution with retry-aware scheduling**

In `_benchmark_command`, after loading rows and validating concurrency/retry counts, create:

```python
    judge_limiter = AsyncJudgeLimiter(settings.judge_max_concurrency) if settings.enable_eval else None
    uploader = (
        BenchmarkUploader(settings=settings, retries=args.benchmark_upload_retries)
        if settings.enable_eval and (settings.wandb_project or settings.weave_project)
        else None
    )
```

Define `run_row` with deferred remote logging:

```python
    def run_row(row: dict[str, object]) -> MatchRunResult:
        return run_match(
            job_title=str(row["job_title"]),
            jd=str(row["job_description"]),
            notes=str(row.get("hiring_notes", "") or ""),
            settings=settings,
            env_file=args.env_file,
            judge_limiter=judge_limiter,
            eval_remote_logging=False if settings.enable_eval else True,
        )
```

Replace the old sequential/map block with:

```python
    pending: deque[tuple[dict[str, object], int]] = deque((row, 1) for row in rows)
    running: dict[Future[MatchRunResult], BenchmarkAttempt] = {}
    result_rows_by_index: dict[int, dict[str, object]] = {}
    completion_index = 0

    try:
        with ThreadPoolExecutor(max_workers=args.benchmark_max_concurrency) as executor:
            while pending or running:
                while pending and len(running) < args.benchmark_max_concurrency:
                    row, attempt = pending.popleft()
                    future = executor.submit(run_row, row)
                    running[future] = BenchmarkAttempt(row=row, attempt=attempt, started_at=_now_iso())
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    attempt = running.pop(future)
                    completed_at = _now_iso()
                    input_index = int(attempt.row["input_index"])
                    try:
                        result = future.result()
                    except Exception as exc:
                        if attempt.attempt <= args.benchmark_run_retries:
                            pending.append((attempt.row, attempt.attempt + 1))
                            continue
                        result_rows_by_index[input_index] = _failed_benchmark_result_row(
                            row=attempt.row,
                            attempts=attempt.attempt,
                            started_at=attempt.started_at,
                            completed_at=completed_at,
                            error=_error_text(exc),
                        )
                        continue

                    completion_index += 1
                    result_row = _benchmark_result_row(
                        row=attempt.row,
                        result=result,
                        attempts=attempt.attempt,
                        started_at=attempt.started_at,
                        completed_at=completed_at,
                        completion_index=completion_index,
                    )
                    result_rows_by_index[input_index] = result_row
                    if uploader is not None and result.evaluation_result is not None:
                        uploader.submit(BenchmarkUploadTask(result_row=result_row, result=result))
    finally:
        if uploader is not None:
            uploader.close()

    results = [result_rows_by_index[index] for index in sorted(result_rows_by_index)]
    has_failed_rows = any(row.get("status") == "failed" for row in results)
```

At the end of `_benchmark_command`, return `1 if has_failed_rows else 0` in both JSON and human output paths. For JSON:

```python
    if args.json_output:
        _emit_json(sys.stdout, payload)
        return 1 if has_failed_rows else 0
```

For human output:

```python
    return 1 if has_failed_rows else 0
```

- [ ] **Step 6: Run scheduler tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_benchmark_retries_failed_row_once_and_keeps_summary tests/test_cli.py::test_benchmark_returns_one_when_row_exhausts_retries tests/test_cli.py::test_benchmark_uploads_eval_results_serially_in_completion_order -v
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/seektalent/cli.py tests/test_cli.py
git commit -m "Add benchmark queue scheduler"
```

---

### Task 5: CLI Flags, Inspect Output, and Summary Shape

**Files:**
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for default directory mode and inspect metadata**

Add tests:

```python
def test_benchmark_defaults_to_benchmarks_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmarks_dir = tmp_path / "benchmarks"
    benchmarks_dir.mkdir()
    (benchmarks_dir / "agent_jds.jsonl").write_text(
        json.dumps({"jd_id": "agent_1", "job_title": "Agent", "job_description": "Agent JD"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    captured: list[str] = []

    def fake_run_match(**kwargs):  # noqa: ANN001
        captured.append(kwargs["job_title"])
        run_dir = tmp_path / "run-1"
        run_dir.mkdir(exist_ok=True)
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id="run-1",
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(
        [
            "benchmark",
            "--benchmarks-dir",
            str(benchmarks_dir),
            "--output-dir",
            str(tmp_path / "runs"),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert captured == ["Agent"]
    assert payload["benchmark_dir"] == str(benchmarks_dir)
    assert payload["benchmark_files"] == [str(benchmarks_dir / "agent_jds.jsonl")]
    assert "benchmark_file" not in payload


def test_inspect_describes_benchmark_directory_and_retry_flags() -> None:
    payload = _inspect_payload()
    benchmark_args = {item["name"]: item for item in payload["commands"]["benchmark"]["arguments"]}

    assert "--benchmarks-dir" in benchmark_args
    assert "--benchmark-run-retries" in benchmark_args
    assert "--benchmark-upload-retries" in benchmark_args
    assert benchmark_args["--jds-file"]["default"] is None
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_benchmark_defaults_to_benchmarks_directory tests/test_cli.py::test_inspect_describes_benchmark_directory_and_retry_flags -v
```

Expected: fails because flags and inspect output are not updated.

- [ ] **Step 3: Update parser flags**

In `build_exec_parser`, change `benchmark_parser` help:

```python
    benchmark_parser = subparsers.add_parser("benchmark", help="Run benchmark JDs from domain JSONL files.")
```

Change `--jds-file` default to `None`:

```python
    benchmark_parser.add_argument(
        "--jds-file",
        default=None,
        help="Path to one JSONL file with benchmark JDs. When omitted, --benchmarks-dir is scanned.",
    )
```

Add:

```python
    benchmark_parser.add_argument(
        "--benchmarks-dir",
        default=str(DEFAULT_BENCHMARKS_DIR),
        help="Directory of maintained benchmark JSONL files.",
    )
    benchmark_parser.add_argument(
        "--benchmark-run-retries",
        type=int,
        default=1,
        help="Retry each failed benchmark row this many times.",
    )
    benchmark_parser.add_argument(
        "--benchmark-upload-retries",
        type=int,
        default=1,
        help="Retry each failed remote eval upload this many times.",
    )
```

In `_benchmark_command`, load rows with:

```python
    benchmark_file: Path | None = resolve_user_path(args.jds_file) if args.jds_file else None
    if benchmark_file is not None:
        rows = _load_benchmark_rows(benchmark_file)
        benchmark_files = [str(benchmark_file)]
        benchmark_dir = None
    else:
        benchmark_dir_path = resolve_user_path(args.benchmarks_dir)
        rows, benchmark_files = _load_benchmark_directory(benchmark_dir_path)
        benchmark_dir = str(benchmark_dir_path)
```

Add retry validation:

```python
    if args.benchmark_run_retries < 0:
        raise ValueError("benchmark_run_retries must be >= 0")
    if args.benchmark_upload_retries < 0:
        raise ValueError("benchmark_upload_retries must be >= 0")
```

Build summary body with mode-specific fields:

```python
    summary_payload: dict[str, object] = {
        "count": len(results),
        "runs": results,
    }
    if benchmark_file is not None:
        summary_payload["benchmark_file"] = str(benchmark_file)
    else:
        summary_payload["benchmark_dir"] = benchmark_dir
        summary_payload["benchmark_files"] = benchmark_files
```

Use `summary_payload` for the file and stdout payload, then add `summary_path`.

- [ ] **Step 4: Update inspect payload**

In `_inspect_payload`, update benchmark command description, `--jds-file` default, and add arg specs for:

```python
_arg_spec("--benchmarks-dir", "path", "Directory of maintained benchmark JSONL files.", default="artifacts/benchmarks"),
_arg_spec("--benchmark-run-retries", "integer", "Retry each failed benchmark row this many times.", default=1),
_arg_spec("--benchmark-upload-retries", "integer", "Retry each failed remote eval upload this many times.", default=1),
```

Update side effects text to mention directory mode and serialized remote uploads when eval is enabled.

- [ ] **Step 5: Run CLI metadata tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_benchmark_defaults_to_benchmarks_directory tests/test_cli.py::test_inspect_describes_benchmark_directory_and_retry_flags tests/test_cli.py::test_benchmark_json_runs_rows_sequentially tests/test_cli.py::test_benchmark_json_can_run_rows_in_parallel -v
```

Expected: selected tests pass. Existing sequential/parallel tests may need assertions updated for the new `status`, `attempts`, and metadata fields while preserving old fields.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/seektalent/cli.py tests/test_cli.py
git commit -m "Expose benchmark directory mode"
```

---

### Task 6: Global Judge Limiter Verification

**Files:**
- Modify: `tests/test_cli.py`
- May modify: `src/seektalent/cli.py`

- [ ] **Step 1: Write failing global limiter integration test**

Add:

```python
def test_benchmark_shares_one_judge_limiter_across_parallel_runs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        "\n".join(
            json.dumps(
                {"jd_id": f"jd_{index}", "job_title": f"Role {index}", "job_description": f"JD {index}"},
                ensure_ascii=False,
            )
            for index in range(3)
        )
        + "\n",
        encoding="utf-8",
    )
    limiter_ids: list[int] = []

    def fake_run_match(**kwargs):  # noqa: ANN001
        limiter_ids.append(id(kwargs["judge_limiter"]))
        run_dir = tmp_path / kwargs["job_title"].replace(" ", "-")
        run_dir.mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id=kwargs["job_title"],
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id=kwargs["job_title"],
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--benchmark-max-concurrency",
            "3",
            "--enable-eval",
            "--json",
        ]
    ) == 0

    json.loads(capsys.readouterr().out)
    assert len(set(limiter_ids)) == 1
```

- [ ] **Step 2: Run and verify behavior**

Run:

```bash
uv run pytest tests/test_cli.py::test_benchmark_shares_one_judge_limiter_across_parallel_runs -v
```

Expected: passes if Task 4 already wires `judge_limiter` correctly. If it fails, fix `_benchmark_command` so the limiter is created once before scheduling and passed to every `run_match` call.

- [ ] **Step 3: Run evaluation limiter unit test**

Run:

```bash
uv run pytest tests/test_evaluation.py::test_resume_judge_uses_judge_concurrency_limit -v
```

Expected: pass. This confirms the limiter path still enforces concurrency inside a single run.

- [ ] **Step 4: Commit**

Run:

```bash
git add src/seektalent/cli.py tests/test_cli.py tests/test_evaluation.py
git commit -m "Verify benchmark global judge limiter"
```

---

### Task 7: CLI Documentation

**Files:**
- Modify: `docs/cli.md`
- Modify: `docs/cli.zh-CN.md`

- [ ] **Step 1: Update English CLI docs**

In `docs/cli.md`, update the benchmark section so it includes:

```markdown
Run all maintained benchmark JSONL files from the default directory:

```bash
seektalent benchmark \
  --benchmarks-dir ./artifacts/benchmarks \
  --output-dir ./runs/benchmark \
  --benchmark-max-concurrency 6 \
  --enable-eval
```

Run one explicit JSONL file:

```bash
seektalent benchmark \
  --jds-file ./artifacts/benchmarks/agent_jds.jsonl \
  --output-dir ./runs/benchmark
```
```

Update the options table rows:

```markdown
| `--jds-file PATH` | Optional input JSONL file. When omitted, `--benchmarks-dir` is scanned. |
| `--benchmarks-dir PATH` | Directory of maintained benchmark JSONL files. Defaults to `artifacts/benchmarks`. |
| `--benchmark-max-concurrency N` | Run up to N benchmark rows in parallel. Defaults to `1`. |
| `--benchmark-run-retries N` | Retry each failed benchmark row N times. Defaults to `1`. |
| `--benchmark-upload-retries N` | Retry each failed remote eval upload N times. Defaults to `1`. |
```

Add this paragraph:

```markdown
Default directory mode skips generated or temporary JSONL files such as `phase_*.jsonl`, `*.tmp.jsonl`, `*.only.jsonl`, and `*.subset.jsonl`. When eval is enabled, local runs may execute in parallel, judge requests share one process-level limit, and Weave/W&B uploads are serialized after local eval artifacts are written.
```

- [ ] **Step 2: Update Chinese CLI docs**

In `docs/cli.zh-CN.md`, add the same commands and options in Chinese:

```markdown
运行默认目录下维护的所有 benchmark JSONL：

```bash
seektalent benchmark \
  --benchmarks-dir ./artifacts/benchmarks \
  --output-dir ./runs/benchmark \
  --benchmark-max-concurrency 6 \
  --enable-eval
```

只运行一个显式 JSONL 文件：

```bash
seektalent benchmark \
  --jds-file ./artifacts/benchmarks/agent_jds.jsonl \
  --output-dir ./runs/benchmark
```
```

Use these option descriptions:

```markdown
| `--jds-file PATH` | 可选的单个输入 JSONL。省略时扫描 `--benchmarks-dir`。 |
| `--benchmarks-dir PATH` | 维护中的 benchmark JSONL 目录；默认 `artifacts/benchmarks`。 |
| `--benchmark-max-concurrency N` | 最多并行运行 N 条 benchmark；默认 `1`。 |
| `--benchmark-run-retries N` | 每条失败的 benchmark row 重试 N 次；默认 `1`。 |
| `--benchmark-upload-retries N` | 每个失败的远端 eval 上传重试 N 次；默认 `1`。 |
```

Add:

```markdown
默认目录模式会跳过生成或临时 JSONL，例如 `phase_*.jsonl`、`*.tmp.jsonl`、`*.only.jsonl` 和 `*.subset.jsonl`。开启 eval 时，本地 run 可以并行，judge 请求共享进程级并发上限，Weave/W&B 上传会在本地 eval artifact 写完后串行执行。
```

- [ ] **Step 3: Run docs grep checks**

Run:

```bash
rg -n "benchmarks-dir|benchmark-run-retries|benchmark-upload-retries|phase_\\*\\.jsonl" docs/cli.md docs/cli.zh-CN.md
```

Expected: both docs mention every new flag and skip pattern.

- [ ] **Step 4: Commit**

Run:

```bash
git add docs/cli.md docs/cli.zh-CN.md
git commit -m "Document benchmark queue mode"
```

---

### Task 8: Final Verification

**Files:**
- No planned source edits unless verification exposes a real failure.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_evaluation.py tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run CLI inspect smoke**

Run:

```bash
uv run seektalent inspect --json > /tmp/seektalent-inspect.json
python -m json.tool /tmp/seektalent-inspect.json >/dev/null
```

Expected: both commands exit `0`.

- [ ] **Step 4: Run no-eval benchmark smoke with temporary directory**

Run:

```bash
tmpdir="$(mktemp -d)"
mkdir -p "$tmpdir/benchmarks" "$tmpdir/runs"
printf '%s\n' '{"jd_id":"smoke_1","job_title":"Smoke Role","job_description":"Python retrieval engineer"}' > "$tmpdir/benchmarks/smoke.jsonl"
uv run seektalent benchmark \
  --benchmarks-dir "$tmpdir/benchmarks" \
  --output-dir "$tmpdir/runs" \
  --benchmark-max-concurrency 1 \
  --disable-eval \
  --json
```

Expected: command exits `0`, prints JSON with `count: 1`, `benchmark_dir`, `benchmark_files`, and one `runs` row with `status: succeeded` and `upload_status: skipped`.

- [ ] **Step 5: Confirm git status**

Run:

```bash
git status --short
```

Expected: no unstaged or uncommitted changes.

---

## Self-Review

- Spec coverage: directory loading is covered by Tasks 1 and 5; run concurrency and completion-order metadata by Task 4; global judge limit by Tasks 2, 3, and 6; serial upload and report-once behavior by Tasks 2 and 4; retry and exit code semantics by Task 4; docs by Task 7; verification by Task 8.
- Placeholder scan: this plan uses concrete filenames, functions, tests, commands, and expected outcomes. It contains no open requirement markers.
- Type consistency: `AsyncJudgeLimiter`, `log_evaluation_remotely`, `terminal_stop_guidance`, `benchmark_file`, `benchmark_group`, `input_index`, `upload_status`, and retry flag names are used consistently across tasks.
