"""Artifact boundary primitives for logical artifact resolution and storage."""

from .models import ArtifactKind, ArtifactManifest, ChildArtifactRef, LogicalArtifactEntry
from .registry import STATIC_ENTRIES
from .store import ArtifactResolver, ArtifactSession, ArtifactStore, MANIFEST_FILENAME_BY_KIND, atomic_write_text, safe_artifact_path

__all__ = [
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactResolver",
    "ArtifactSession",
    "ArtifactStore",
    "ChildArtifactRef",
    "LogicalArtifactEntry",
    "MANIFEST_FILENAME_BY_KIND",
    "STATIC_ENTRIES",
    "atomic_write_text",
    "safe_artifact_path",
]
