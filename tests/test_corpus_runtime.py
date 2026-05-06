from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from seektalent.artifacts import ArtifactStore
from seektalent.corpus.runtime import write_raw_payload_artifact


def test_write_raw_payload_artifact_writes_json_and_registers_logical_artifact(tmp_path: Path) -> None:
    session = ArtifactStore(tmp_path / "artifacts").create_root(
        kind="corpus",
        display_name="corpus ingest",
        producer="CorpusRuntime",
    )
    snapshot_sha256 = "a" * 64

    artifact = write_raw_payload_artifact(
        session=session,
        snapshot_sha256=snapshot_sha256,
        raw_payload={"resume_id": "r1", "skills": ["Python"]},
    )

    path = session.root / artifact.relative_path
    content = path.read_bytes()
    logical_name = f"corpus.raw_payloads.{snapshot_sha256}"
    manifest_entry = session.load_manifest().logical_artifacts[logical_name]

    assert session.manifest.artifact_kind.value == "corpus"
    assert artifact.logical_name == logical_name
    assert artifact.relative_path == f"raw_payloads/{snapshot_sha256}.json"
    assert artifact.content_sha256 == sha256(content).hexdigest()
    assert artifact.size_bytes == len(content)
    assert path.exists()
    assert manifest_entry.path == artifact.relative_path
    assert manifest_entry.content_type == "application/json"
    assert manifest_entry.schema_version == "v1"


def test_write_raw_payload_artifact_rejects_unsafe_snapshot_name(tmp_path: Path) -> None:
    session = ArtifactStore(tmp_path / "artifacts").create_root(
        kind="corpus",
        display_name="corpus ingest",
        producer="CorpusRuntime",
    )

    with pytest.raises(ValueError):
        write_raw_payload_artifact(
            session=session,
            snapshot_sha256="../escape",
            raw_payload={"resume_id": "r1"},
        )
