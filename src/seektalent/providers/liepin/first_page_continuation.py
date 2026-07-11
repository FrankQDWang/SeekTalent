from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from threading import RLock
from time import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from seektalent.artifacts import atomic_write_text, safe_artifact_path
from seektalent.providers.liepin.liepin_site_parsing import _safe_artifact_segment


CandidateState = Literal["remaining", "opened", "skipped_seen", "terminal_failed"]
ORPHAN_RETENTION_SECONDS = 7 * 24 * 60 * 60
_RUN_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_CONTINUATION_FILENAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}-[a-f0-9]{16}\.json$"
)


class LiepinFirstPageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=30)
    ref: str = Field(min_length=1)
    detail_url: str = Field(min_length=1)
    provider_candidate_key_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: CandidateState = "remaining"


class LiepinFirstPageContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["liepin.first_page_continuation.v1"] = (
        "liepin.first_page_continuation.v1"
    )
    source_run_id: str
    logical_round_no: int = Field(ge=1)
    query_instance_id: str
    keyword_query: str = Field(min_length=1)
    visible_candidate_count: int = Field(ge=0, le=30)
    candidates: list[LiepinFirstPageCandidate] = Field(max_length=30)
    opaque_ref: str

    @model_validator(mode="after")
    def validate_candidate_ranks(self) -> LiepinFirstPageContinuation:
        ranks = [candidate.rank for candidate in self.candidates]
        if len(ranks) != len(set(ranks)):
            raise ValueError("first_page_continuation_candidate_ranks_duplicate")
        if ranks != sorted(ranks):
            raise ValueError("first_page_continuation_candidate_ranks_out_of_order")
        if len(ranks) > self.visible_candidate_count or any(
            rank > self.visible_candidate_count for rank in ranks
        ):
            raise ValueError("first_page_continuation_candidate_count_invalid")
        return self


class LiepinFirstPageContinuationStore:
    def __init__(self, protected_root: Path) -> None:
        self._protected_root = protected_root.resolve()
        self._lock = RLock()

    def create(
        self,
        *,
        source_run_id: str,
        logical_round_no: int,
        query_instance_id: str,
        keyword_query: str,
        visible_candidate_count: int,
        candidates: list[LiepinFirstPageCandidate],
    ) -> LiepinFirstPageContinuation:
        safe_run_id = _safe_artifact_segment(source_run_id)
        safe_query_id = (
            f"{_safe_artifact_segment(query_instance_id)[:48]}-"
            f"{sha256(query_instance_id.encode('utf-8')).hexdigest()[:16]}"
        )
        relative = (
            Path("pi-detail")
            / safe_run_id
            / "first-page-continuations"
            / f"{safe_query_id}.json"
        )
        opaque_ref = f"artifact://protected/{relative.as_posix()}"
        continuation = LiepinFirstPageContinuation(
            source_run_id=source_run_id,
            logical_round_no=logical_round_no,
            query_instance_id=query_instance_id,
            keyword_query=keyword_query,
            visible_candidate_count=visible_candidate_count,
            candidates=sorted(candidates, key=lambda item: item.rank),
            opaque_ref=opaque_ref,
        )
        self._write(relative, continuation)
        return continuation

    def load(self, opaque_ref: str) -> LiepinFirstPageContinuation:
        with self._lock:
            relative = self._relative_path(opaque_ref)
            continuation = LiepinFirstPageContinuation.model_validate_json(
                safe_artifact_path(self._protected_root, relative.as_posix()).read_text(
                    encoding="utf-8"
                )
            )
            if continuation.opaque_ref != opaque_ref:
                raise ValueError("first_page_continuation_ref_invalid")
            if self._relative_for_ids(
                source_run_id=continuation.source_run_id,
                query_instance_id=continuation.query_instance_id,
            ) != relative:
                raise ValueError("first_page_continuation_ref_invalid")
            return continuation

    def mark_candidate(self, opaque_ref: str, *, rank: int, state: CandidateState) -> None:
        with self._lock:
            continuation = self.load(opaque_ref)
            matches = [item for item in continuation.candidates if item.rank == rank]
            if len(matches) != 1:
                raise ValueError("first_page_continuation_rank_missing")
            updated = [
                LiepinFirstPageCandidate.model_validate(
                    {**item.model_dump(), "state": state}
                )
                if item.rank == rank
                else item
                for item in continuation.candidates
            ]
            relative = self._relative_path(opaque_ref)
            self._write(
                relative,
                LiepinFirstPageContinuation.model_validate(
                    {**continuation.model_dump(), "candidates": updated}
                ),
            )

    def delete(self, opaque_ref: str) -> None:
        with self._lock:
            path = safe_artifact_path(
                self._protected_root,
                self._relative_path(opaque_ref).as_posix(),
            )
            path.unlink(missing_ok=True)

    def delete_expired(self, *, now_timestamp: float | None = None) -> int:
        cutoff = (time() if now_timestamp is None else now_timestamp) - ORPHAN_RETENTION_SECONDS
        removed = 0
        with self._lock:
            root = safe_artifact_path(self._protected_root, "pi-detail")
            for path in (
                root.rglob("first-page-continuations/*.json") if root.exists() else ()
            ):
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _relative_path(self, opaque_ref: str) -> Path:
        prefix = "artifact://protected/"
        if not opaque_ref.startswith(prefix):
            raise ValueError("first_page_continuation_ref_invalid")
        relative = Path(opaque_ref.removeprefix(prefix))
        self._validate_relative_path(relative)
        try:
            safe_artifact_path(self._protected_root, relative.as_posix())
        except ValueError as exc:
            raise ValueError("first_page_continuation_ref_invalid") from exc
        return relative

    def _write(self, relative: Path, continuation: LiepinFirstPageContinuation) -> None:
        self._validate_relative_path(relative)
        path = safe_artifact_path(self._protected_root, relative.as_posix())
        atomic_write_text(path, continuation.model_dump_json())
        path.chmod(0o600)

    @staticmethod
    def _validate_relative_path(relative: Path) -> None:
        parts = relative.parts
        if (
            len(parts) != 4
            or parts[0] != "pi-detail"
            or not _RUN_COMPONENT_PATTERN.fullmatch(parts[1])
            or parts[2] != "first-page-continuations"
            or not _CONTINUATION_FILENAME_PATTERN.fullmatch(parts[3])
        ):
            raise ValueError("first_page_continuation_ref_invalid")

    @staticmethod
    def _relative_for_ids(*, source_run_id: str, query_instance_id: str) -> Path:
        safe_run_id = _safe_artifact_segment(source_run_id)
        safe_query_id = (
            f"{_safe_artifact_segment(query_instance_id)[:48]}-"
            f"{sha256(query_instance_id.encode('utf-8')).hexdigest()[:16]}"
        )
        return (
            Path("pi-detail")
            / safe_run_id
            / "first-page-continuations"
            / f"{safe_query_id}.json"
        )
