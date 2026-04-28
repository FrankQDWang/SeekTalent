from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatch
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import TextIO

import fcntl
from ulid import ULID

from .models import ArtifactKind, ArtifactManifest, ChildArtifactRef, LogicalArtifactEntry
from .registry import resolve_descriptor, top_level_entry


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collection_root_for_kind(kind: ArtifactKind) -> str:
    return {
        ArtifactKind.RUN: "runs",
        ArtifactKind.BENCHMARK: "benchmark-executions",
        ArtifactKind.REPLAY: "replays",
        ArtifactKind.DEBUG: "debug",
        ArtifactKind.IMPORT: "imports",
    }[kind]


MANIFEST_FILENAME_BY_KIND = {
    ArtifactKind.RUN: "run_manifest.json",
    ArtifactKind.BENCHMARK: "benchmark_manifest.json",
    ArtifactKind.REPLAY: "replay_manifest.json",
    ArtifactKind.DEBUG: "debug_manifest.json",
    ArtifactKind.IMPORT: "import_manifest.json",
}

VALID_FINAL_STATUSES = {"completed", "failed"}
SUMMARY_LOGICAL_ARTIFACT_BY_KIND = {
    ArtifactKind.RUN: "output.run_summary",
    ArtifactKind.BENCHMARK: "output.summary",
}
_PARTITION_INDEX_LOCKS: dict[Path, threading.Lock] = {}
_PARTITION_INDEX_LOCKS_GUARD = threading.Lock()


def _producer_version() -> str:
    try:
        return package_version("seektalent")
    except PackageNotFoundError:
        return "0.6.1"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def safe_artifact_path(root: Path, relative_path: str) -> Path:
    raw = Path(relative_path)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("manifest path escapes artifact root")
    candidate = root / raw
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve(strict=False)
    if root_resolved not in [candidate_resolved, *candidate_resolved.parents]:
        raise ValueError("manifest path escapes artifact root through symlink")
    return candidate


class ArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def collection_root(self, kind: str) -> Path:
        return self.root / collection_root_for_kind(ArtifactKind(kind))

    def create_root(self, *, kind: str, display_name: str, producer: str) -> ArtifactSession:
        artifact_kind = ArtifactKind(kind)
        created_at = utc_now()
        artifact_id = f"{artifact_kind.value}_{ULID()}"
        partition = (
            self.root
            / collection_root_for_kind(artifact_kind)
            / created_at[:4]
            / created_at[5:7]
            / created_at[8:10]
            / artifact_id
        )
        session = ArtifactSession(
            root=partition,
            manifest=ArtifactManifest(
                artifact_kind=artifact_kind,
                artifact_id=artifact_id,
                created_at=created_at,
                updated_at=created_at,
                display_name=display_name,
                producer=producer,
                producer_version=_producer_version(),
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
    def for_root(cls, root: Path) -> ArtifactResolver:
        manifests_dir = root / "manifests"
        candidates = sorted(manifests_dir.glob("*_manifest.json"))
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one manifest under {manifests_dir}")
        manifest = ArtifactManifest.model_validate_json(candidates[0].read_text(encoding="utf-8"))
        return cls(root, manifest)

    def resolve(self, logical_name: str) -> Path:
        return safe_artifact_path(self.root, self.manifest.logical_artifacts[logical_name].path)

    def resolve_optional(self, logical_name: str) -> Path | None:
        entry = self.manifest.logical_artifacts.get(logical_name)
        if entry is None:
            return None
        return safe_artifact_path(self.root, entry.path)

    def resolve_for_write(self, logical_name: str) -> Path:
        entry = self.manifest.logical_artifacts.get(logical_name) or resolve_descriptor(logical_name)
        path = safe_artifact_path(self.root, entry.path)
        if entry.collection:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_many(self, prefix: str) -> list[Path]:
        return [
            safe_artifact_path(self.root, entry.path)
            for name, entry in sorted(self.manifest.logical_artifacts.items())
            if fnmatch(name, prefix)
        ]


@dataclass
class ArtifactSession:
    root: Path
    manifest: ArtifactManifest

    @property
    def manifest_path(self) -> Path:
        filename = MANIFEST_FILENAME_BY_KIND[self.manifest.artifact_kind]
        return self.root / "manifests" / filename

    def load_manifest(self) -> ArtifactManifest:
        return ArtifactManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_manifest()
        self._register_entry("runtime.trace_log", top_level_entry("runtime.trace_log"))
        self._register_entry("runtime.events", top_level_entry("runtime.events"))

    def resolver(self) -> ArtifactResolver:
        return ArtifactResolver(self.root, self.manifest)

    def register_path(
        self,
        logical_name: str,
        relative_path: str,
        *,
        content_type: str,
        schema_version: str | None = None,
        collection: bool = False,
    ) -> None:
        if relative_path.startswith("/") or ".." in Path(relative_path).parts:
            raise ValueError("manifest paths must stay relative to the artifact root")
        self._register_entry(
            logical_name,
            LogicalArtifactEntry(
                path=relative_path,
                content_type=content_type,
                schema_version=schema_version,
                collection=collection,
            ),
        )

    def _descriptor_for(self, logical_name: str) -> LogicalArtifactEntry:
        return self.manifest.logical_artifacts.get(logical_name) or resolve_descriptor(logical_name)

    def write_json(self, logical_name: str, payload: object) -> Path:
        entry = self._descriptor_for(logical_name)
        path = safe_artifact_path(self.root, entry.path)
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        self._register_entry(logical_name, entry)
        return path

    def write_jsonl(self, logical_name: str, rows: list[object]) -> Path:
        entry = self._descriptor_for(logical_name)
        path = safe_artifact_path(self.root, entry.path)
        lines = [json.dumps(row, ensure_ascii=False) for row in rows]
        atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
        self._register_entry(logical_name, entry)
        return path

    def append_jsonl(self, logical_name: str, row: object) -> Path:
        entry = self._descriptor_for(logical_name)
        path = safe_artifact_path(self.root, entry.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._register_entry(logical_name, entry)
        return path

    def write_text(self, logical_name: str, content: str) -> Path:
        entry = self._descriptor_for(logical_name)
        path = safe_artifact_path(self.root, entry.path)
        atomic_write_text(path, content)
        self._register_entry(logical_name, entry)
        return path

    def open_text_stream(self, logical_name: str) -> tuple[Path, TextIO]:
        entry = self._descriptor_for(logical_name)
        path = safe_artifact_path(self.root, entry.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._register_entry(logical_name, entry)
        return path, path.open("a", encoding="utf-8")

    def set_child_artifacts(self, rows: list[dict[str, object]]) -> None:
        self.manifest.child_artifacts = [ChildArtifactRef.model_validate(row) for row in rows]
        self._write_manifest()

    def finalize(self, *, status: str, failure_summary: str | None = None) -> None:
        if status not in VALID_FINAL_STATUSES:
            if status == "running":
                raise ValueError("Finalization requires a terminal artifact status")
            raise ValueError(f"Invalid artifact status: {status}")
        self.manifest.status = status
        self.manifest.updated_at = utc_now()
        self.manifest.completed_at = self.manifest.updated_at
        if failure_summary:
            self.manifest.failure_summary = failure_summary
        self._write_manifest()

    def _register_entry(self, logical_name: str, entry: LogicalArtifactEntry) -> None:
        self.manifest.logical_artifacts[logical_name] = entry
        self.manifest.updated_at = utc_now()
        self._write_manifest()

    def _write_manifest(self) -> None:
        self.manifest.logical_artifacts = {
            logical_name: entry if isinstance(entry, LogicalArtifactEntry) else LogicalArtifactEntry.model_validate(entry)
            for logical_name, entry in self.manifest.logical_artifacts.items()
        }
        self.manifest.child_artifacts = [
            entry if isinstance(entry, ChildArtifactRef) else ChildArtifactRef.model_validate(entry)
            for entry in self.manifest.child_artifacts
        ]
        atomic_write_text(self.manifest_path, self.manifest.model_dump_json(indent=2))
        self._write_partition_index()

    def _write_partition_index(self) -> None:
        index_path = self.root.parent / "_index.jsonl"
        with _locked_partition_index(index_path):
            rows_by_id: dict[str, dict[str, object]] = {}
            if index_path.exists():
                for line in index_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    rows_by_id[str(row["artifact_id"])] = row
            row = {
                "artifact_id": self.manifest.artifact_id,
                "artifact_kind": self.manifest.artifact_kind.value,
                "created_at": self.manifest.created_at,
                "status": self.manifest.status,
                "display_name": self.manifest.display_name,
                "producer": self.manifest.producer,
                "summary_logical_artifact": SUMMARY_LOGICAL_ARTIFACT_BY_KIND.get(self.manifest.artifact_kind),
            }
            rows_by_id[self.manifest.artifact_id] = row
            atomic_write_text(
                index_path,
                "\n".join(json.dumps(item, ensure_ascii=False) for item in rows_by_id.values()) + "\n",
            )


def _partition_index_lock(index_path: Path) -> threading.Lock:
    with _PARTITION_INDEX_LOCKS_GUARD:
        return _PARTITION_INDEX_LOCKS.setdefault(index_path, threading.Lock())


@contextmanager
def _locked_partition_index(index_path: Path):
    lock_path = index_path.with_name(f"{index_path.name}.lock")
    with _partition_index_lock(index_path):
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
