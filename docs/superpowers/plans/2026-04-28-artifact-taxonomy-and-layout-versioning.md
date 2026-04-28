# Artifact Taxonomy And Layout Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mixed `runs/` output root with a typed `artifacts/` layout, resolver-backed logical artifact names, benchmark execution containers, and a safe archive path for historical clutter.

**Architecture:** Land the artifact boundary in dependency order. Introduce artifact primitives and the resolver first, then switch the active single-run writer onto the new layout, then migrate runtime and evaluation call sites onto logical names, then move benchmark execution and legacy archive flows, and finally enforce the new boundary with docs and tests. Historical artifact contents stay byte-preserved; only container layout and lookup rules change.

**Tech Stack:** Python 3.12, Pydantic models, existing SeekTalent runtime split modules, pytest, filesystem utilities in the standard library

---

## File Map

### New files

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/__init__.py`
  Purpose: Export the small public artifact boundary API used by runtime, CLI, and tests.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/models.py`
  Purpose: Define `ArtifactKind`, manifest models, manifest entries, archive migration rows, and ID validation.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/registry.py`
  Purpose: Centralize logical artifact naming and map each active artifact family to relative layout paths and content types.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/store.py`
  Purpose: Own root creation, ULID-based naming, manifest lifecycle, `_index.jsonl`, safe writes, and resolver access.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/legacy.py`
  Purpose: Classify historical `runs/` contents, emit archive migration plans and results, move legacy material, and write decommission sentinels.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_store.py`
  Purpose: Lock manifest lifecycle, path safety, resolver behavior, logical-name mapping, and `_index.jsonl` behavior.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_archive.py`
  Purpose: Lock dry-run archive planning, idempotent execution, collision handling, and `runs/` decommission markers.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py`
  Purpose: Guard against new direct `rounds/round_XX/...` or `run_dir / "evaluation"` path stitching outside the artifact boundary modules.

### Modified files

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py`
  Purpose: Introduce `artifacts_dir` / `artifacts_path`, preserve a small legacy read root for migration commands, and deprecate active writes through `runs_dir`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/tracing.py`
  Purpose: Make `RunTracer` write through `ArtifactStore`, preserve trace/event streaming, and expose logical-artifact writes instead of relative path stitching.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
  Purpose: Stop constructing artifact-relative path strings directly and switch to logical artifact names for run preamble, round artifacts, replay snapshots, and prompt snapshots.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/controller_runtime.py`
  Purpose: Write controller artifacts through logical names under `rounds/<round>/controller/`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py`
  Purpose: Write retrieval artifacts through logical names under `rounds/<round>/retrieval/`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/reflection_runtime.py`
  Purpose: Write reflection artifacts through logical names under `rounds/<round>/reflection/`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/scoring_runtime.py`
  Purpose: Write scorecards and scoring snapshots through logical names under `rounds/<round>/scoring/`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_execution_runtime.py`
  Purpose: Write rescue artifacts through logical names under `rounds/<round>/rescue/`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/company_discovery_runtime.py`
  Purpose: Move company rescue artifact writes into the new rescue subtree without changing company-isolation semantics.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/finalize_runtime.py`
  Purpose: Move finalizer stage artifacts into `output/` logical names.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/post_finalize_runtime.py`
  Purpose: Write run summaries, judge packets, and evaluation outputs through the new output/evaluation logical names.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/scoring/scorer.py`
  Purpose: Stop writing `rounds/round_XX/...` strings directly and emit logical references in LLM call snapshots.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/evaluation.py`
  Purpose: Resolve replay snapshots and evaluation outputs through `ArtifactResolver` rather than `glob("round_*/...")`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/lifecycle.py`
  Purpose: Clean up new `artifacts/runs/...` and benchmark execution containers instead of top-level `runs/` and loose `benchmark_summary_*.json`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/cli.py`
  Purpose: Switch user-facing output defaults from `runs` to `artifacts`, preserve `artifacts/benchmarks` for input JSONL, add explicit legacy archive command, and keep doctor/help output accurate.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/api.py`
  Purpose: Expose the new artifact root through `MatchRunResult` without changing its stable `run_dir` contract.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/baseline_evaluation.py`
  Purpose: Export replay rows from resolver-backed locations.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/claude_code_baseline/harness.py`
  Purpose: Create new single-run artifacts under `artifacts/runs/...`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/jd_text_baseline/harness.py`
  Purpose: Create new single-run artifacts under `artifacts/runs/...`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/openclaw_baseline/harness.py`
  Purpose: Create new single-run artifacts under `artifacts/runs/...`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/settings_factory.py`
  Purpose: Make test settings creation explicit about `artifacts_dir` and keep legacy migration tests isolated.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_api.py`
  Purpose: Update workspace-root and result-path expectations to `artifacts`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py`
  Purpose: Lock CLI defaults, benchmark execution outputs, and legacy archive command behavior.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`
  Purpose: Lock replay export against the new round retrieval layout and resolver.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py`
  Purpose: Lock config defaulting around `artifacts_dir`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_openclaw_baseline.py`
  Purpose: Update tracer-root assumptions to `artifacts`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_run_latency_audit_tool.py`
  Purpose: Update trace discovery expectations for the new runtime subdirectory.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
  Purpose: Lock the new layout for prompt snapshots, stage calls, replay snapshots, and audit references.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_lifecycle.py`
  Purpose: Lock cleanup behavior against `artifacts/runs` and benchmark execution containers.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
  Purpose: Update round artifact location expectations for second-lane and hit-ledger artifacts.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
  Purpose: Document the new artifact taxonomy, new run layout, and archive/decommission semantics.

## Task 1: Add Artifact Boundary Primitives First

**Files:**
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/__init__.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/models.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/registry.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/store.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_store.py`

- [ ] **Step 1: Write the failing artifact-store tests**

```python
from pathlib import Path

import pytest

from seektalent.artifacts.store import ArtifactStore


def test_create_run_root_writes_running_manifest_and_runtime_files(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(
        kind="run",
        display_name="seek talent workflow run",
        producer="WorkflowRuntime",
    )

    assert "runs" in session.root.parts
    assert len(session.root.relative_to(tmp_path / "artifacts").parts) == 5
    assert session.root.name.startswith("run_")
    manifest = session.load_manifest()
    assert manifest.status == "running"
    assert manifest.logical_artifacts["runtime.trace_log"].path == "runtime/trace.log"
    assert manifest.logical_artifacts["runtime.events"].path == "runtime/events.jsonl"


def test_write_json_updates_manifest_and_resolve_many_round_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    session.write_json("round.02.retrieval.query_resume_hits", [{"resume_id": "r1"}])
    session.write_json("round.02.retrieval.replay_snapshot", {"round_no": 2})

    resolver = session.resolver()
    hits_path = resolver.resolve("round.02.retrieval.query_resume_hits")
    replay_paths = resolver.resolve_many("round.*.retrieval.replay_snapshot")

    assert hits_path.read_text(encoding="utf-8").strip().startswith("[")
    assert replay_paths == [session.root / "rounds/02/retrieval/replay_snapshot.json"]


def test_manifest_rejects_escape_paths(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    with pytest.raises(ValueError, match="relative"):
        session.register_path("bad.entry", "../outside.json", content_type="application/json")
```

- [ ] **Step 2: Run the tests to verify the new boundary is missing**

Run:

```bash
uv run pytest -q tests/test_artifact_store.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'seektalent.artifacts'` and missing `ArtifactStore` symbols.

- [ ] **Step 3: Implement the minimal artifact package**

```python
# src/seektalent/artifacts/models.py
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ArtifactKind(StrEnum):
    RUN = "run"
    BENCHMARK = "benchmark"
    REPLAY = "replay"
    DEBUG = "debug"
    IMPORT = "import"


class LogicalArtifactEntry(BaseModel):
    path: str
    content_type: str
    schema_version: str | None = None
    collection: bool = False


class ArtifactManifest(BaseModel):
    manifest_schema_version: str = "v1"
    artifact_kind: ArtifactKind
    artifact_id: str
    layout_version: str = "v1"
    created_at: str
    updated_at: str
    completed_at: str | None = None
    display_name: str
    producer: str
    producer_version: str
    git_sha: str | None = None
    status: str
    logical_artifacts: dict[str, LogicalArtifactEntry] = Field(default_factory=dict)
```

```python
# src/seektalent/artifacts/registry.py
from __future__ import annotations

from seektalent.artifacts.models import LogicalArtifactEntry


def top_level_entry(name: str) -> LogicalArtifactEntry:
    mapping = {
        "runtime.trace_log": LogicalArtifactEntry(path="runtime/trace.log", content_type="text/plain"),
        "runtime.events": LogicalArtifactEntry(path="runtime/events.jsonl", content_type="application/jsonl"),
        "output.final_candidates": LogicalArtifactEntry(path="output/final_candidates.json", content_type="application/json", schema_version="v1"),
        "evaluation.evaluation": LogicalArtifactEntry(path="evaluation/evaluation.json", content_type="application/json", schema_version="v1"),
    }
    return mapping[name]


def round_entry(*, round_no: int, stage: str, filename: str, content_type: str) -> tuple[str, LogicalArtifactEntry]:
    logical_name = f"round.{round_no:02d}.{stage}.{filename.removesuffix('.json').removesuffix('.jsonl')}"
    return logical_name, LogicalArtifactEntry(
        path=f"rounds/{round_no:02d}/{stage}/{filename}",
        content_type=content_type,
        schema_version="v1",
    )
```

```python
# src/seektalent/artifacts/store.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import TextIO

from ulid import ULID

from seektalent.artifacts.models import ArtifactKind, ArtifactManifest, LogicalArtifactEntry
from seektalent.artifacts.registry import round_entry, top_level_entry


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collection_root_for_kind(kind: ArtifactKind) -> str:
    return {
        ArtifactKind.RUN: "runs",
        ArtifactKind.BENCHMARK: "benchmark-executions",
        ArtifactKind.REPLAY: "replays",
        ArtifactKind.DEBUG: "debug",
        ArtifactKind.IMPORT: "imports",
    }[kind]


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def collection_root(self, kind: str) -> Path:
        return self.root / collection_root_for_kind(ArtifactKind(kind))

    def create_root(self, *, kind: str, display_name: str, producer: str) -> "ArtifactSession":
        artifact_kind = ArtifactKind(kind)
        created_at = utc_now()
        artifact_id = f"{artifact_kind.value}_{ULID()}"
        partition = self.root / collection_root_for_kind(artifact_kind) / created_at[:4] / created_at[5:7] / created_at[8:10] / artifact_id
        session = ArtifactSession(
            root=partition,
            manifest=ArtifactManifest(
                artifact_kind=artifact_kind,
                artifact_id=artifact_id,
                created_at=created_at,
                updated_at=created_at,
                display_name=display_name,
                producer=producer,
                producer_version="0.6.1",
                status="running",
            ),
        )
        session.initialize()
        return session


class ArtifactResolver:
    def __init__(self, root: Path, manifest: ArtifactManifest) -> None:
        self.root = root
        self.manifest = manifest

    @classmethod
    def for_root(cls, root: Path) -> "ArtifactResolver":
        manifest_path = root / "manifests" / "run_manifest.json"
        manifest = ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        return cls(root, manifest)

    def resolve(self, logical_name: str) -> Path:
        return self.root / self.manifest.logical_artifacts[logical_name].path

    def resolve_many(self, prefix: str) -> list[Path]:
        return [
            self.root / entry.path
            for name, entry in self.manifest.logical_artifacts.items()
            if fnmatch(name, prefix)
        ]


class ArtifactSession:
    def load_manifest(self) -> ArtifactManifest:
        manifest_path = self.root / "manifests" / "run_manifest.json"
        return ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_manifest()
        self._register_entry("runtime.trace_log", top_level_entry("runtime.trace_log"))
        self._register_entry("runtime.events", top_level_entry("runtime.events"))

    def resolver(self) -> ArtifactResolver:
        return ArtifactResolver(self.root, self.manifest)

    def register_path(self, logical_name: str, relative_path: str, *, content_type: str) -> None:
        if relative_path.startswith("/") or ".." in Path(relative_path).parts:
            raise ValueError("manifest paths must stay relative to the artifact root")
        self._register_entry(logical_name, LogicalArtifactEntry(path=relative_path, content_type=content_type))

    def _descriptor_for(self, logical_name: str) -> LogicalArtifactEntry:
        if logical_name in {"runtime.trace_log", "runtime.events", "output.final_candidates", "evaluation.evaluation"}:
            return top_level_entry(logical_name)
        if logical_name.startswith("round."):
            _, round_text, stage, leaf = logical_name.split(".", 3)
            filename = f"{leaf}.jsonl" if leaf in {"scorecards", "scoring_calls"} else f"{leaf}.json"
            _, entry = round_entry(round_no=int(round_text), stage=stage, filename=filename, content_type="application/json")
            return entry
        raise KeyError(logical_name)

    def write_json(self, logical_name: str, payload: object) -> Path:
        entry = self._descriptor_for(logical_name)
        path = self.root / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._register_entry(logical_name, entry)
        return path

    def open_text_stream(self, logical_name: str) -> tuple[Path, TextIO]:
        entry = self._descriptor_for(logical_name)
        path = self.root / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._register_entry(logical_name, entry)
        return path, path.open("a", encoding="utf-8")

    def set_child_artifacts(self, rows: list[dict[str, object]]) -> None:
        self.manifest.model_extra = {"child_artifacts": rows}
        self._write_manifest()

    def finalize(self, *, status: str, failure_summary: str | None = None) -> None:
        self.manifest.status = status
        self.manifest.updated_at = utc_now()
        self.manifest.completed_at = self.manifest.updated_at
        if failure_summary:
            self.manifest.model_extra = {"failure_summary": failure_summary}
        self._write_manifest()
        self._write_partition_index()

    def _register_entry(self, logical_name: str, entry: LogicalArtifactEntry) -> None:
        self.manifest.logical_artifacts[logical_name] = entry
        self.manifest.updated_at = utc_now()
        self._write_manifest()

    def _write_manifest(self) -> None:
        manifest_path = self.root / "manifests" / "run_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(self.manifest.model_dump_json(indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run the primitive tests to verify the boundary exists**

Run:

```bash
uv run pytest -q tests/test_artifact_store.py
```

Expected: PASS for manifest lifecycle, resolver lookup, and path-safety coverage.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/artifacts tests/test_artifact_store.py
git commit -m "feat: add artifact store primitives"
```

### Task 2: Switch Active Single Runs To `artifacts/` Through `RunTracer`

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/tracing.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/api.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/settings_factory.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_llm_provider_config.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_api.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`

- [ ] **Step 1: Write the failing config and tracer migration tests**

```python
import json

def test_artifacts_dir_defaults_follow_runtime_mode() -> None:
    assert make_settings(runtime_mode="dev").artifacts_dir == "artifacts"
    assert make_settings(runtime_mode="prod").artifacts_dir == "~/.seektalent/artifacts"


def test_run_tracer_creates_partitioned_run_root(tmp_path: Path) -> None:
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    tracer = RunTracer(settings.artifacts_path)

    assert "artifacts" in str(tracer.run_dir)
    assert tracer.run_dir.parts[-5] == "runs"
    assert tracer.trace_log_path == tracer.run_dir / "runtime" / "trace.log"
    assert tracer.events_path == tracer.run_dir / "runtime" / "events.jsonl"


def test_run_tracer_manifest_is_marked_completed_on_close(tmp_path: Path) -> None:
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    tracer = RunTracer(settings.artifacts_path)
    tracer.close(status="completed")

    manifest = json.loads((tracer.run_dir / "manifests" / "run_manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["completed_at"].endswith("Z")
```

- [ ] **Step 2: Run the focused tracer tests**

Run:

```bash
uv run pytest -q tests/test_llm_provider_config.py tests/test_api.py tests/test_runtime_audit.py -k "artifacts_dir or run_tracer"
```

Expected: FAIL because `artifacts_dir`, `artifacts_path`, and the new runtime subpaths do not exist yet.

- [ ] **Step 3: Add `artifacts_dir` and route `RunTracer` through `ArtifactStore`**

```python
# src/seektalent/config.py
DEV_ARTIFACTS_DIR = "artifacts"
PROD_ARTIFACTS_DIR = "~/.seektalent/artifacts"


class AppSettings(BaseSettings):
    artifacts_dir: str | None = None
    runs_dir: str | None = None

    @model_validator(mode="after")
    def resolve_runtime_defaults(self) -> "AppSettings":
        if self.artifacts_dir is None:
            self.artifacts_dir = PROD_ARTIFACTS_DIR if self.runtime_mode == "prod" else DEV_ARTIFACTS_DIR
        if self.runs_dir is None:
            self.runs_dir = PROD_RUNS_DIR if self.runtime_mode == "prod" else DEV_RUNS_DIR
        return self

    @property
    def artifacts_path(self) -> Path:
        if self.artifacts_dir is None:
            raise ValueError("artifacts_dir was not resolved")
        return resolve_path_from_root(self.artifacts_dir, root=self.project_root)
```

```python
# src/seektalent/tracing.py
from seektalent.artifacts import ArtifactStore


class RunTracer:
    def __init__(self, artifacts_root: Path) -> None:
        self.store = ArtifactStore(artifacts_root)
        self.session = self.store.create_root(
            kind="run",
            display_name="seek talent workflow run",
            producer="WorkflowRuntime",
        )
        self.run_id = self.session.manifest.artifact_id
        self.run_dir = self.session.root
        self.trace_log_path, self._trace_handle = self.session.open_text_stream("runtime.trace_log")
        self.events_path, self._events_handle = self.session.open_text_stream("runtime.events")

    def write_json(self, logical_name: str, payload: Any) -> Path:
        return self.session.write_json(logical_name, payload)

    def close(self, *, status: str = "completed", failure_summary: str | None = None) -> None:
        self._trace_handle.close()
        self._events_handle.close()
        self.session.finalize(status=status, failure_summary=failure_summary)
```

```python
# src/seektalent/runtime/orchestrator.py
tracer = RunTracer(self.settings.artifacts_path)
```

- [ ] **Step 4: Re-run the config and tracer migration tests**

Run:

```bash
uv run pytest -q tests/test_llm_provider_config.py tests/test_api.py tests/test_runtime_audit.py -k "artifacts_dir or run_tracer"
```

Expected: PASS with `artifacts/` defaults, partitioned run roots, and completed manifests.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/config.py src/seektalent/tracing.py src/seektalent/api.py tests/settings_factory.py tests/test_llm_provider_config.py tests/test_api.py tests/test_runtime_audit.py
git commit -m "feat: move active run creation to artifacts roots"
```

### Task 3: Migrate Runtime And Evaluation Call Sites To Logical Artifact Names

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/controller_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/reflection_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/scoring_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_execution_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/company_discovery_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/finalize_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/post_finalize_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/scoring/scorer.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/evaluation.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`

- [ ] **Step 1: Write the failing runtime-layout and resolver tests**

```python
import json
from pathlib import Path


def _single_run_dir(artifacts_root: Path) -> Path:
    return next((artifacts_root / "runs").rglob("run_*"))


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_payload() -> dict[str, object]:
    return {
        "run_id": "run_01TEST",
        "round_no": 2,
        "retrieval_snapshot_id": "snapshot-1",
        "provider_request": {"query": "python"},
        "provider_response_resume_ids": ["r1"],
        "provider_response_raw_rank": ["r1"],
        "dedupe_version": "v1",
        "scoring_model_version": "v1",
        "query_plan_version": "v1",
        "prf_gate_version": "v1",
        "generic_explore_version": "v1",
    }


def test_round_artifacts_move_into_subsystem_directories(tmp_path: Path) -> None:
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True, min_rounds=1, max_rounds=2)
    runtime = WorkflowRuntime(settings)

    runtime.run(job_title="Python Engineer", jd="JD", notes="")

    run_dir = _single_run_dir(settings.artifacts_path)
    assert (run_dir / "rounds" / "02" / "retrieval" / "query_resume_hits.json").exists()
    assert (run_dir / "rounds" / "02" / "retrieval" / "replay_snapshot.json").exists()
    assert (run_dir / "rounds" / "02" / "controller" / "controller_decision.json").exists()
    assert (run_dir / "rounds" / "02" / "reflection" / "reflection_advice.json").exists()


def test_llm_call_snapshots_store_logical_refs(tmp_path: Path) -> None:
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True, min_rounds=1, max_rounds=1)
    runtime = WorkflowRuntime(settings)

    runtime.run(job_title="Python Engineer", jd="JD", notes="")

    run_dir = _single_run_dir(settings.artifacts_path)
    snapshot = _read_json(run_dir / "rounds" / "01" / "reflection" / "reflection_call.json")
    assert snapshot["input_artifact_refs"] == ["round.01.reflection.reflection_context"]
    assert snapshot["output_artifact_refs"] == ["round.01.reflection.reflection_advice"]


def test_export_replay_rows_uses_round_retrieval_layout(tmp_path: Path) -> None:
    run_dir = tmp_path / "artifacts" / "runs" / "2026" / "04" / "28" / "run_01TEST"
    snapshot_path = run_dir / "rounds" / "02" / "retrieval" / "replay_snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(_snapshot_payload()), encoding="utf-8")

    path = export_replay_rows(run_dir=run_dir)

    assert path == run_dir / "evaluation" / "replay_rows.jsonl"
```

- [ ] **Step 2: Run the runtime migration regression slice**

Run:

```bash
uv run pytest -q tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_evaluation.py
```

Expected: FAIL on old `rounds/round_XX/...` assumptions and `evaluation.export_replay_rows()` still globbing legacy paths.

- [ ] **Step 3: Replace direct path strings with logical artifact names**

```python
# src/seektalent/runtime/orchestrator.py
tracer.write_json("runtime.run_config", self._build_public_run_config())
tracer.write_json("input.input_snapshot", input_snapshot)
tracer.write_json(f"round.{round_no:02d}.controller.controller_context", slim_context)
tracer.write_json(f"round.{round_no:02d}.retrieval.replay_snapshot", replay_snapshot.model_dump(mode="json"))
tracer.write_text(f"assets.prompts.{prompt.name}", prompt.content)
```

```python
# src/seektalent/scoring/scorer.py
tracer.append_jsonl(
    f"round.{context.round_no:02d}.scoring.scoring_calls",
    snapshot.model_dump(mode="json"),
)
snapshot = snapshot.model_copy(
    update={
        "input_artifact_refs": [f"round.{context.round_no:02d}.scoring.scoring_input_refs"],
        "output_artifact_refs": [f"round.{context.round_no:02d}.scoring.scorecards"],
    }
)
```

```python
# src/seektalent/evaluation.py
def export_replay_rows(*, run_dir: Path, output_dir: Path | None = None) -> Path | None:
    resolver = ArtifactResolver.for_root(run_dir)
    snapshot_paths = resolver.resolve_many("round.*.retrieval.replay_snapshot")
    snapshots = [
        ReplaySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        for path in snapshot_paths
    ]
    if not snapshots:
        return None
    replay_rows_path = (output_dir or resolver.resolve("evaluation.evaluation").parent) / "replay_rows.jsonl"
    replay_rows_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in build_replay_rows(snapshots)) + "\n",
        encoding="utf-8",
    )
    return replay_rows_path
```

- [ ] **Step 4: Re-run the runtime migration regression slice**

Run:

```bash
uv run pytest -q tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_evaluation.py
```

Expected: PASS with retrieval-flywheel artifacts under subsystem directories and replay export driven by resolver lookups.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/controller_runtime.py src/seektalent/runtime/retrieval_runtime.py src/seektalent/runtime/reflection_runtime.py src/seektalent/runtime/scoring_runtime.py src/seektalent/runtime/rescue_execution_runtime.py src/seektalent/runtime/company_discovery_runtime.py src/seektalent/runtime/finalize_runtime.py src/seektalent/runtime/post_finalize_runtime.py src/seektalent/scoring/scorer.py src/seektalent/evaluation.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_evaluation.py
git commit -m "feat: migrate runtime artifacts to logical names"
```

### Task 4: Move Benchmark Execution Onto Its Own Artifact Kind

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/cli.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/lifecycle.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/baseline_evaluation.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/claude_code_baseline/harness.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/jd_text_baseline/harness.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/openclaw_baseline/harness.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_lifecycle.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_openclaw_baseline.py`

- [ ] **Step 1: Write the failing benchmark execution tests**

```python
import json
from io import StringIO
from pathlib import Path

from seektalent.cli import main as cli_main


def _run_benchmark_command(tmp_path: Path, *, settings: AppSettings) -> dict[str, object]:
    benchmark_file = tmp_path / "artifacts" / "benchmarks" / "agent_jds.jsonl"
    benchmark_file.parent.mkdir(parents=True, exist_ok=True)
    benchmark_file.write_text('{"jd_id":"agent_jd_001","job_title":"Python Engineer","jd":"JD"}\n', encoding="utf-8")
    stdout = StringIO()
    exit_code = cli_main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--json",
        ],
        settings=settings,
        stdout=stdout,
    )
    assert exit_code == 0
    return json.loads(stdout.getvalue())


def test_benchmark_command_writes_summary_under_benchmark_execution_root(tmp_path: Path) -> None:
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    payload = _run_benchmark_command(tmp_path, settings=settings)

    summary_path = Path(payload["summary_path"])
    assert "benchmark-executions" in str(summary_path)
    assert summary_path.name == "summary.json"
    manifest = json.loads((summary_path.parents[1] / "manifests" / "benchmark_manifest.json").read_text())
    assert manifest["artifact_kind"] == "benchmark"


def test_benchmark_manifest_references_child_runs(tmp_path: Path) -> None:
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    payload = _run_benchmark_command(tmp_path, settings=settings)

    summary_path = Path(payload["summary_path"])
    manifest = json.loads((summary_path.parents[1] / "manifests" / "benchmark_manifest.json").read_text())
    assert manifest["child_artifacts"][0]["artifact_kind"] == "run"
    assert manifest["child_artifacts"][0]["artifact_id"].startswith("run_")
```

- [ ] **Step 2: Run the benchmark-focused regression slice**

Run:

```bash
uv run pytest -q tests/test_cli.py tests/test_runtime_lifecycle.py tests/test_openclaw_baseline.py
```

Expected: FAIL because summaries still land in `runs/benchmark_summary_*.json` and harnesses still use `settings.runs_path`.

- [ ] **Step 3: Introduce benchmark execution containers and child-run references**

```python
# src/seektalent/cli.py
benchmark_session = ArtifactStore(settings.artifacts_path).create_root(
    kind="benchmark",
    display_name="seek talent benchmark execution",
    producer="BenchmarkCLI",
)
summary_path = benchmark_session.write_json(
    "output.summary",
    {
        **benchmark_metadata,
        "count": len(results),
        "runs": results,
    },
)
benchmark_session.set_child_artifacts(
    [
        {
            "artifact_kind": "run",
            "artifact_id": row["run_id"],
            "role": "case_run",
            "case_id": row["jd_id"],
        }
        for row in results
        if row.get("status") == "ok"
    ]
)
benchmark_session.write_json(
    "output.summary",
    {
        **benchmark_metadata,
        "count": len(results),
        "runs": results,
    },
)
benchmark_session.finalize(status="completed")
```

```python
# src/seektalent/runtime/lifecycle.py
def cleanup_old_artifact_roots(collection_root: Path, *, now: datetime, retention_days: int) -> None:
    if not collection_root.exists():
        return
    cutoff = now - timedelta(days=retention_days)
    for artifact_root in collection_root.rglob("*_*"):
        if artifact_root.is_dir() and artifact_root.stat().st_mtime < cutoff.timestamp():
            shutil.rmtree(artifact_root)


def cleanup_runtime_artifacts(settings: AppSettings, *, now: datetime | None = None) -> None:
    clear_exact_llm_cache(settings)
    if settings.runtime_mode != "prod":
        return
    artifact_store = ArtifactStore(settings.artifacts_path)
    cleanup_old_artifact_roots(artifact_store.collection_root("run"), now=now or datetime.now(), retention_days=PROD_RUN_RETENTION_DAYS)
    cleanup_old_artifact_roots(artifact_store.collection_root("benchmark"), now=now or datetime.now(), retention_days=PROD_RUN_RETENTION_DAYS)
```

```python
# experiments/jd_text_baseline/harness.py
tracer = RunTracer(settings.artifacts_path)
```

- [ ] **Step 4: Re-run the benchmark-focused regression slice**

Run:

```bash
uv run pytest -q tests/test_cli.py tests/test_runtime_lifecycle.py tests/test_openclaw_baseline.py
```

Expected: PASS with maintained benchmark inputs still under `artifacts/benchmarks` and benchmark execution outputs now under `artifacts/benchmark-executions/...`.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/cli.py src/seektalent/runtime/lifecycle.py experiments/baseline_evaluation.py experiments/claude_code_baseline/harness.py experiments/jd_text_baseline/harness.py experiments/openclaw_baseline/harness.py tests/test_cli.py tests/test_runtime_lifecycle.py tests/test_openclaw_baseline.py
git commit -m "feat: add benchmark execution artifact roots"
```

### Task 5: Archive Legacy `runs/` Safely And Fail Fast On New Legacy Writes

**Files:**
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/legacy.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/cli.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/config.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_archive.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_lifecycle.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_cli.py`

- [ ] **Step 1: Write the failing archive and decommission tests**

```python
import pytest

def test_archive_migration_writes_dry_run_plan_and_result(tmp_path: Path) -> None:
    legacy_runs = tmp_path / "runs"
    (legacy_runs / "20260422_192141_deadbeef" / "trace.log").parent.mkdir(parents=True, exist_ok=True)
    (legacy_runs / "debug_openclaw").mkdir(parents=True)

    report = dry_run_archive_migration(project_root=tmp_path, legacy_runs_root=legacy_runs, artifacts_root=tmp_path / "artifacts")

    assert report.plan_path == tmp_path / "artifacts" / "archive" / "archive_migration_plan.json"
    assert report.rows[0].destination_path.startswith("artifacts/archive/")


def test_archive_migration_is_idempotent_and_leaves_decommissioned_runs_root(tmp_path: Path) -> None:
    legacy_runs = tmp_path / "runs"
    (legacy_runs / "20260422_192141_deadbeef" / "trace.log").parent.mkdir(parents=True, exist_ok=True)

    execute_archive_migration(project_root=tmp_path, legacy_runs_root=legacy_runs, artifacts_root=tmp_path / "artifacts")
    execute_archive_migration(project_root=tmp_path, legacy_runs_root=legacy_runs, artifacts_root=tmp_path / "artifacts")

    assert (legacy_runs / ".decommissioned").exists()
    assert (legacy_runs / "README.md").exists()


def test_runtime_rejects_legacy_runs_root_as_active_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="decommissioned"):
        make_settings(artifacts_dir=str(tmp_path / "runs"), mock_cts=True).artifacts_path
```

- [ ] **Step 2: Run the archive regression slice**

Run:

```bash
uv run pytest -q tests/test_artifact_archive.py tests/test_cli.py tests/test_runtime_lifecycle.py -k "archive or decommissioned"
```

Expected: FAIL because no archive planner exists, `runs/` has no sentinel behavior, and active output roots still accept legacy `runs`.

- [ ] **Step 3: Add archive planning, execution, sentinels, and fail-fast rules**

```python
# src/seektalent/artifacts/legacy.py
def dry_run_archive_migration(*, project_root: Path, legacy_runs_root: Path, artifacts_root: Path) -> ArchiveMigrationReport:
    rows = classify_legacy_entries(legacy_runs_root)
    plan_path = artifacts_root / "archive" / "archive_migration_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps([row.model_dump(mode="json") for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return ArchiveMigrationReport(plan_path=plan_path, rows=rows)


def execute_archive_migration(*, project_root: Path, legacy_runs_root: Path, artifacts_root: Path) -> ArchiveMigrationReport:
    plan = dry_run_archive_migration(project_root=project_root, legacy_runs_root=legacy_runs_root, artifacts_root=artifacts_root)
    for row in plan.rows:
        source = project_root / row.source_path
        destination = project_root / row.destination_path
        if source == destination or not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    _write_decommission_markers(legacy_runs_root)
    result_path = artifacts_root / "archive" / "archive_migration_result.json"
    result_path.write_text(json.dumps([row.model_dump(mode="json") for row in plan.rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return ArchiveMigrationReport(plan_path=plan.plan_path, result_path=result_path, rows=plan.rows)
```

```python
# src/seektalent/config.py
@property
def artifacts_path(self) -> Path:
    path = resolve_path_from_root(self.artifacts_dir, root=self.project_root)
    if path.name == "runs":
        raise ValueError("The legacy runs/ root is decommissioned as an active output target. Use artifacts/ instead.")
    return path
```

```python
# src/seektalent/cli.py
def _archive_legacy_artifacts_command(args: argparse.Namespace) -> int:
    report = execute_archive_migration(
        project_root=resolve_user_path(args.project_root),
        legacy_runs_root=resolve_user_path(args.runs_dir),
        artifacts_root=resolve_user_path(args.artifacts_dir),
    )
    print(f"archive_plan: {report.plan_path}")
    print(f"archive_result: {report.result_path}")
    return 0
```

- [ ] **Step 4: Re-run the archive regression slice**

Run:

```bash
uv run pytest -q tests/test_artifact_archive.py tests/test_cli.py tests/test_runtime_lifecycle.py -k "archive or decommissioned"
```

Expected: PASS with dry-run planning, idempotent archive execution, and active-root rejection for legacy `runs/`.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/artifacts/legacy.py src/seektalent/cli.py src/seektalent/config.py tests/test_artifact_archive.py tests/test_cli.py tests/test_runtime_lifecycle.py
git commit -m "feat: archive legacy runs and decommission old root"
```

### Task 6: Add Partition Review Index, Docs, And Path-Enforcement Tests

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/artifacts/store.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_artifact_path_contract.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_run_latency_audit_tool.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`

- [ ] **Step 1: Write the failing review-index and enforcement tests**

```python
import json
from pathlib import Path

def test_partition_index_lists_new_run_metadata(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    session.finalize(status="completed")

    index_path = session.root.parent / "_index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["artifact_id"] == session.manifest.artifact_id
    assert rows[0]["summary_logical_artifact"] == "output.run_summary"


def test_core_modules_do_not_stitch_legacy_round_paths() -> None:
    disallowed = ['"rounds/round_', '"evaluation/evaluation.json"', '"trace.log"']
    allowed_files = {
        "src/seektalent/artifacts/legacy.py",
        "src/seektalent/artifacts/store.py",
    }
    offenders = scan_for_disallowed_path_literals(disallowed=disallowed, allowed_files=allowed_files)
    assert offenders == []
```

- [ ] **Step 2: Run the boundary-enforcement slice**

Run:

```bash
uv run pytest -q tests/test_artifact_store.py tests/test_artifact_path_contract.py tests/test_run_latency_audit_tool.py tests/test_runtime_audit.py
```

Expected: FAIL because `_index.jsonl` is not maintained yet and direct literal path stitching still exists in migrated modules.

- [ ] **Step 3: Add `_index.jsonl`, update docs, and lock the path contract**

```python
# src/seektalent/artifacts/store.py
def _write_partition_index(self) -> None:
    index_path = self.root.parent / "_index.jsonl"
    row = {
        "artifact_id": self.manifest.artifact_id,
        "created_at": self.manifest.created_at,
        "status": self.manifest.status,
        "display_name": self.manifest.display_name,
        "producer": self.manifest.producer,
        "summary_logical_artifact": "output.run_summary",
    }
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
```

```markdown
<!-- docs/outputs.md -->
## Artifact roots

- Active single runs now write to `artifacts/runs/YYYY/MM/DD/run_<ulid>/`
- Maintained benchmark input files remain in `artifacts/benchmarks/`
- Benchmark execution outputs now write to `artifacts/benchmark-executions/YYYY/MM/DD/benchmark_<ulid>/`
- Historical `runs/` content is archived under `artifacts/archive/` and `runs/` is no longer an active write root
```

```python
# tests/test_artifact_path_contract.py
ROOT = Path("/Users/frankqdwang/Agents/SeekTalent-0.2.4")
CHECKED_FILES = [
    ROOT / "src/seektalent/runtime/orchestrator.py",
    ROOT / "src/seektalent/runtime/controller_runtime.py",
    ROOT / "src/seektalent/runtime/retrieval_runtime.py",
    ROOT / "src/seektalent/runtime/reflection_runtime.py",
    ROOT / "src/seektalent/runtime/rescue_execution_runtime.py",
    ROOT / "src/seektalent/scoring/scorer.py",
    ROOT / "src/seektalent/evaluation.py",
]


def scan_for_disallowed_path_literals(*, disallowed: list[str], allowed_files: set[str]) -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in CHECKED_FILES:
        repo_relative = str(path.relative_to(ROOT))
        if repo_relative in allowed_files:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in disallowed:
            if needle in text:
                offenders.append((repo_relative, needle))
    return offenders
```

- [ ] **Step 4: Run the final focused regression sweep**

Run:

```bash
uv run pytest -q tests/test_artifact_store.py tests/test_artifact_archive.py tests/test_artifact_path_contract.py tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_runtime_lifecycle.py tests/test_evaluation.py tests/test_cli.py tests/test_api.py tests/test_run_latency_audit_tool.py
```

Expected: PASS with `_index.jsonl`, docs-aligned artifact roots, and no new direct legacy path stitching in the migrated core modules.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/artifacts/store.py docs/outputs.md tests/test_artifact_path_contract.py tests/test_run_latency_audit_tool.py tests/test_runtime_audit.py
git commit -m "docs: finalize artifact taxonomy boundary"
```

## Self-Review

### Spec coverage

- `ArtifactStore / ArtifactResolver` is front-loaded in Task 1, before any layout switch.
- New active `artifacts/` roots and partitioned single-run creation land in Task 2.
- Retrieval flywheel artifacts, logical-name mapping, and resolver-based replay export land in Task 3.
- Benchmark execution roots, benchmark input/output separation, and child-run references land in Task 4.
- Dry-run archive migration, migration plan/result files, idempotency, decommission sentinels, and fail-fast rules land in Task 5.
- `_index.jsonl`, path-safety enforcement, and documentation land in Task 6.
- Historical contents are moved, not rewritten; no database or training-export work appears in this plan.

### Placeholder scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- Every task includes explicit files, tests, commands, and code snippets.
- The plan stays within the artifact taxonomy migration boundary and does not defer core spec work to an unnamed follow-up.

### Type consistency

- Active output root uses `artifacts_dir` / `artifacts_path` consistently after Task 2.
- Artifact kinds remain singular in manifest/API (`run`, `benchmark`, `replay`, `debug`, `import`) and plural only in collection roots.
- Logical names stay dotted throughout (`round.02.retrieval.query_resume_hits`, `output.final_candidates`, `evaluation.evaluation`).
- Benchmark execution output root is consistently named `benchmark-executions`, keeping `artifacts/benchmarks` reserved for maintained input JSONL files.
