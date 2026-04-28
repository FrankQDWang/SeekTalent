from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactKind(StrEnum):
    RUN = "run"
    BENCHMARK = "benchmark"
    REPLAY = "replay"
    DEBUG = "debug"
    IMPORT = "import"


class LogicalArtifactEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content_type: str
    schema_version: str | None = None
    collection: bool = False


ArtifactStatus = Literal["running", "completed", "failed"]


class ChildArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_kind: ArtifactKind
    artifact_id: str
    role: str
    case_id: str | None = None


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    status: ArtifactStatus
    failure_summary: str | None = None
    child_artifacts: list[ChildArtifactRef] = Field(default_factory=list)
    logical_artifacts: dict[str, LogicalArtifactEntry] = Field(default_factory=dict)
