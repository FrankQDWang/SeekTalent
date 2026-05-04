from __future__ import annotations

import json
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

import seektalent.artifacts.store as artifact_store_module
from seektalent.artifacts.registry import ROUND_CONTENT_TYPES, resolve_descriptor
from seektalent.artifacts.store import ArtifactResolver, ArtifactStore

FIXED_NOW = datetime(2026, 4, 28, 5, 6, 7, tzinfo=UTC)


def _freeze_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_store_module, "utc_now", lambda: "2026-04-28T05:06:07Z")


def _create_and_finalize_run(root_path: str) -> tuple[str, str]:
    store = ArtifactStore(Path(root_path))
    session = store.create_root(
        kind="run",
        display_name="seek talent workflow run",
        producer="WorkflowRuntime",
    )
    session.finalize(status="completed")
    return session.manifest.artifact_id, str(session.root.parent)


def test_create_run_root_writes_running_manifest_and_runtime_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_time(monkeypatch)
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
    assert manifest.manifest_schema_version == "v1"
    assert manifest.layout_version == "v1"
    assert manifest.status == "running"
    assert manifest.producer == "WorkflowRuntime"
    assert manifest.logical_artifacts["runtime.trace_log"].path == "runtime/trace.log"
    assert manifest.logical_artifacts["runtime.trace_log"].content_type == "text/plain"
    assert manifest.logical_artifacts["runtime.events"].path == "runtime/events.jsonl"
    assert manifest.logical_artifacts["runtime.events"].content_type == "application/jsonl"


@pytest.mark.parametrize(
    ("kind", "collection_root", "manifest_name"),
    [
        ("run", "runs", "run_manifest.json"),
        ("benchmark", "benchmark-executions", "benchmark_manifest.json"),
        ("replay", "replays", "replay_manifest.json"),
        ("debug", "debug", "debug_manifest.json"),
        ("import", "imports", "import_manifest.json"),
    ],
)
def test_create_root_uses_kind_specific_manifest_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    collection_root: str,
    manifest_name: str,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind=kind, display_name=f"{kind} artifact", producer="ArtifactTests")

    assert collection_root in session.root.parts
    assert (session.root / "manifests" / manifest_name).exists()
    assert re.match(rf"^{kind}_[0-9A-HJKMNP-TV-Z]{{26}}$", session.manifest.artifact_id)


def test_manifest_persists_required_top_level_runtime_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    session.write_json("runtime.sent_query_history", {"queries": ["python"]})
    session.write_json("runtime.search_diagnostics", {"total_sent_queries": 1})
    session.write_json("runtime.term_surface_audit", {"terms": []})
    session.write_json("runtime.prf_sidecar_dependency_manifest", {"dependency_manifest_hash": "manifest-hash"})

    manifest = session.load_manifest()
    assert manifest.logical_artifacts["runtime.sent_query_history"].path == "runtime/sent_query_history.json"
    assert manifest.logical_artifacts["runtime.search_diagnostics"].path == "runtime/search_diagnostics.json"
    assert manifest.logical_artifacts["runtime.term_surface_audit"].path == "runtime/term_surface_audit.json"
    assert manifest.logical_artifacts["runtime.prf_sidecar_dependency_manifest"].path == "runtime/prf_sidecar_dependency_manifest.json"


def test_write_json_updates_manifest_and_resolve_many_round_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    session.write_json("round.02.retrieval.query_resume_hits", [{"resume_id": "r1"}])
    session.write_json("round.02.retrieval.replay_snapshot", {"round_no": 2})

    resolver = session.resolver()
    hits_path = resolver.resolve("round.02.retrieval.query_resume_hits")
    replay_paths = resolver.resolve_many("round.*.retrieval.replay_snapshot")

    assert hits_path.read_text(encoding="utf-8").strip().startswith("[")
    assert replay_paths == [session.root / "rounds/02/retrieval/replay_snapshot.json"]


@pytest.mark.parametrize(
    ("logical_name", "expected_path"),
    [
        ("round.02.retrieval.prf_span_candidates", "rounds/02/retrieval/prf_span_candidates.json"),
        ("round.02.retrieval.prf_expression_families", "rounds/02/retrieval/prf_expression_families.json"),
        ("round.02.retrieval.prf_policy_decision", "rounds/02/retrieval/prf_policy_decision.json"),
    ],
)
def test_prf_v1_5_retrieval_artifacts_are_registered_and_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logical_name: str,
    expected_path: str,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    assert logical_name.split(".")[-1] in ROUND_CONTENT_TYPES
    entry = resolve_descriptor(logical_name)
    path = session.write_json(logical_name, {"round_no": 2})

    assert entry.path == expected_path
    assert path == session.root / expected_path
    assert session.resolver().resolve(logical_name) == session.root / expected_path
    assert session.load_manifest().logical_artifacts[logical_name].path == expected_path


@pytest.mark.parametrize(
    ("logical_name", "expected_path"),
    [
        ("round.02.retrieval.llm_prf_input", "rounds/02/retrieval/llm_prf_input.json"),
        ("round.02.retrieval.llm_prf_call", "rounds/02/retrieval/llm_prf_call.json"),
        ("round.02.retrieval.llm_prf_candidates", "rounds/02/retrieval/llm_prf_candidates.json"),
        ("round.02.retrieval.llm_prf_grounding", "rounds/02/retrieval/llm_prf_grounding.json"),
    ],
)
def test_llm_prf_retrieval_artifacts_are_registered_and_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logical_name: str,
    expected_path: str,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    assert logical_name.split(".")[-1] in ROUND_CONTENT_TYPES
    entry = resolve_descriptor(logical_name)
    path = session.write_json(logical_name, {"schema_version": "llm-prf-v1"})

    assert entry.path == expected_path
    assert path == session.root / expected_path
    assert session.resolver().resolve(logical_name) == session.root / expected_path


def test_benchmark_child_artifacts_are_schema_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="benchmark", display_name="benchmark execution", producer="BenchmarkCLI")
    session.set_child_artifacts(
        [
            {
                "artifact_kind": "run",
                "artifact_id": "run_01JV1W4P9Q6ZP3Q1Q6Q6WQ5N8B",
                "role": "case_run",
                "case_id": "agent_jd_001",
            }
        ]
    )

    manifest = session.load_manifest()
    assert manifest.child_artifacts[0].artifact_kind == "run"
    assert manifest.child_artifacts[0].case_id == "agent_jd_001"


def test_register_path_supports_collection_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    session.register_path(
        "assets.raw_resumes",
        "assets/raw_resumes",
        content_type="application/octet-stream",
        collection=True,
    )

    manifest = session.load_manifest()
    assert manifest.logical_artifacts["assets.raw_resumes"].collection is True
    assert manifest.logical_artifacts["assets.raw_resumes"].path == "assets/raw_resumes"
    assert session.resolver().resolve_for_write("assets.raw_resumes") == session.root / "assets" / "raw_resumes"


def test_registered_custom_paths_are_writable_through_session_apis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    session.register_path("custom.note", "custom/note.txt", content_type="text/plain")

    path = session.write_text("custom.note", "hello artifact")

    assert path == session.root / "custom" / "note.txt"
    assert path.read_text(encoding="utf-8") == "hello artifact"
    assert session.load_manifest().logical_artifacts["custom.note"].content_type == "text/plain"


def test_manifest_rejects_escape_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    with pytest.raises(ValueError, match="relative"):
        session.register_path("bad.entry", "../outside.json", content_type="application/json")


def test_resolver_rejects_escape_paths_from_manifest_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    session.manifest.logical_artifacts["bad.entry"] = {
        "path": "../outside.json",
        "content_type": "application/json",
    }
    session._write_manifest()

    with pytest.raises(ValueError, match="escapes artifact root"):
        session.resolver().resolve("bad.entry")


def test_resolver_rejects_symlink_escape_from_manifest_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    outside_path = tmp_path / "outside.json"
    outside_path.write_text("{}", encoding="utf-8")
    link_path = session.root / "runtime" / "outside-link.json"
    link_path.parent.mkdir(parents=True, exist_ok=True)
    link_path.symlink_to(outside_path)
    session.manifest.logical_artifacts["bad.symlink"] = {
        "path": "runtime/outside-link.json",
        "content_type": "application/json",
    }
    session._write_manifest()

    with pytest.raises(ValueError, match="escapes artifact root"):
        session.resolver().resolve("bad.symlink")


def test_finalize_rejects_invalid_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    with pytest.raises(ValueError, match="Invalid artifact status"):
        session.finalize(status="bogus")


def test_finalize_rejects_running_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")

    with pytest.raises(ValueError, match="Finalization requires a terminal artifact status"):
        session.finalize(status="running")


def test_artifact_resolver_for_root_reads_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    session.write_json("round.01.retrieval.replay_snapshot", {"round_no": 1})

    resolver = ArtifactResolver.for_root(session.root)

    assert resolver.resolve("round.01.retrieval.replay_snapshot") == session.root / "rounds" / "01" / "retrieval" / "replay_snapshot.json"


def test_round_review_descriptor_uses_markdown_content_type() -> None:
    entry = resolve_descriptor("round.01.review.round_review")

    assert entry.path == "rounds/01/review/round_review.md"
    assert entry.content_type == "text/markdown"


def test_create_root_parallel_process_writes_do_not_drop_partition_index_rows(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    with ProcessPoolExecutor(max_workers=24) as executor:
        results = list(executor.map(_create_and_finalize_run, [str(artifacts_root)] * 96))

    artifact_ids = [artifact_id for artifact_id, _ in results]
    partition_dirs = {partition_dir for _, partition_dir in results}

    assert len(partition_dirs) == 1
    index_path = Path(next(iter(partition_dirs))) / "_index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(artifact_ids) == 96
    assert len(set(artifact_ids)) == 96
    assert {row["artifact_id"] for row in rows} == set(artifact_ids)
