from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from seektalent_agent_memory.extraction import MemoryStage1Extractor
from seektalent_agent_memory.models import MemoryPhase1RunResult, MemoryPhase2RunResult
from seektalent_agent_memory.privacy import filter_memory_candidate
from seektalent_agent_memory.service import MemoryService
from seektalent_agent_memory.store import MemoryStore
from seektalent_agent_memory.workspace import MemoryWorkspace


class CompletedMemoryConversationLike(Protocol):
    conversation_id: str
    updated_at: str


class ConversationMemoryReader(Protocol):
    def eligible_completed_conversations(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        max_age_days: int,
        min_idle_hours: int,
        now: str,
        limit: int,
    ) -> Sequence[CompletedMemoryConversationLike]: ...

    def read_memory_transcript_items(self, *, conversation_id: str) -> Sequence[object]: ...


class MemoryConsolidator(Protocol):
    async def consolidate(self, request: "Phase2ConsolidationRequest") -> object: ...


class Phase2ConsolidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    workspace_id: str
    raw_memories: str
    rollout_summaries: str
    workspace_diff: str


class Phase2ConsolidationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summaryText: str
    factIds: list[str] = Field(default_factory=list)


class StructuredMemoryRuntime(Protocol):
    async def run_structured(
        self,
        prompt: str,
        *,
        name: str,
        output_type: type[object],
    ) -> object: ...


class AgentRuntimeMemoryConsolidator:
    def __init__(self, *, runtime: StructuredMemoryRuntime) -> None:
        self.runtime = runtime

    async def consolidate(self, request: Phase2ConsolidationRequest) -> object:
        final_output = await self.runtime.run_structured(
            request.model_dump_json(),
            name="SeekTalent Memory Consolidator",
            output_type=Phase2ConsolidationOutput,
        )
        output = Phase2ConsolidationOutput.model_validate(final_output)
        return {"summaryText": output.summaryText, "factIds": output.factIds}


class MemoryPipeline:
    def __init__(
        self,
        *,
        store: MemoryStore,
        transcript_reader: ConversationMemoryReader | None,
        extractor: MemoryStage1Extractor | None,
        now: Callable[[], str],
        consolidator: MemoryConsolidator | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.store = store
        self.transcript_reader = transcript_reader
        self.extractor = extractor
        self.consolidator = consolidator
        self.now = now
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None

    async def run_phase1_startup(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        current_conversation_id: str | None = None,
    ) -> MemoryPhase1RunResult:
        settings = self.store.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id, now=self.now())
        if not settings.memory_enabled:
            return MemoryPhase1RunResult(reason_code="agent_memory_disabled")
        if not settings.generation_enabled:
            return MemoryPhase1RunResult(reason_code="agent_memory_generation_disabled")
        if self.transcript_reader is None or self.extractor is None:
            return MemoryPhase1RunResult(reason_code="agent_memory_unavailable")
        service = MemoryService(store=self.store, now=self.now)
        result = MemoryPhase1RunResult()
        conversations = self.transcript_reader.eligible_completed_conversations(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            max_age_days=settings.max_rollout_age_days,
            min_idle_hours=settings.min_rollout_idle_hours,
            now=self.now(),
            limit=settings.max_rollouts_per_startup,
        )
        for conversation in conversations[: settings.max_rollouts_per_startup]:
            if conversation.conversation_id == current_conversation_id:
                continue
            claim = self.store.try_claim_stage1_job(
                conversation_id=conversation.conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                worker_id=current_conversation_id or "memory_startup",
                source_updated_at=conversation.updated_at,
                now=self.now(),
                lease_seconds=300,
                max_running_jobs=settings.max_rollouts_per_startup,
            )
            if claim.status != "claimed" or claim.ownership_token is None:
                continue
            result.claimed += 1
            try:
                output = await service.extract_stage1_from_items(
                    extractor=self.extractor,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation.conversation_id,
                    source_updated_at=conversation.updated_at,
                    items=self.transcript_reader.read_memory_transcript_items(
                        conversation_id=conversation.conversation_id
                    ),
                )
                if output.raw_memory or output.rollout_summary:
                    self.store.mark_stage1_job_succeeded(
                        conversation_id=conversation.conversation_id,
                        owner_user_id=owner_user_id,
                        workspace_id=workspace_id,
                        ownership_token=claim.ownership_token,
                        source_updated_at=conversation.updated_at,
                        now=self.now(),
                    )
                    result.succeeded_with_output += 1
                else:
                    self.store.mark_stage1_job_succeeded_no_output(
                        conversation_id=conversation.conversation_id,
                        owner_user_id=owner_user_id,
                        workspace_id=workspace_id,
                        ownership_token=claim.ownership_token,
                        source_updated_at=conversation.updated_at,
                        now=self.now(),
                    )
                    result.succeeded_no_output += 1
            except (RuntimeError, TypeError, ValueError) as exc:
                self.store.mark_stage1_job_failed(
                    conversation_id=conversation.conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    ownership_token=claim.ownership_token,
                    error_code=str(exc) or "agent_memory_stage1_failed",
                    now=self.now(),
                    retry_delay_seconds=300,
                )
                result.failed += 1
        return result

    async def run_phase2(self, *, owner_user_id: str, workspace_id: str) -> MemoryPhase2RunResult:
        settings = self.store.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id, now=self.now())
        if not settings.memory_enabled:
            return MemoryPhase2RunResult(status="skipped", reason_code="agent_memory_disabled")
        if self.consolidator is None or self.workspace_root is None:
            return MemoryPhase2RunResult(status="skipped", reason_code="agent_memory_unavailable")
        claim = self.store.try_claim_phase2_job(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            worker_id=f"phase2_{uuid4().hex}",
            now=self.now(),
            lease_seconds=300,
        )
        if claim.status != "claimed" or claim.ownership_token is None:
            return MemoryPhase2RunResult(status="running", reason_code=claim.reason_code)
        selected = self.store.get_phase2_input_selection(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            limit=settings.max_stage1_outputs_for_phase2,
        )
        if not selected:
            self.store.mark_phase2_job_succeeded(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                ownership_token=claim.ownership_token,
                now=self.now(),
            )
            return MemoryPhase2RunResult(status="no_changes", reason_code="agent_memory_phase2_no_changes")
        workspace = MemoryWorkspace(_scoped_workspace_root(self.workspace_root, owner_user_id, workspace_id))
        raw_memories = "\n\n".join(item.raw_memory for item in selected if item.raw_memory)
        rollout_summaries = "\n\n".join(
            f"## {item.rollout_slug or item.conversation_id}\n\n{item.rollout_summary}" for item in selected
        )
        workspace.write_artifact("raw_memories.md", raw_memories)
        rollout_artifact_paths: set[str] = set()
        for item in selected:
            slug = item.rollout_slug or item.conversation_id
            rollout_path = f"rollout_summaries/{slug}.md"
            rollout_artifact_paths.add(rollout_path)
            workspace.write_artifact(rollout_path, item.rollout_summary)
        workspace.prune_artifacts({"raw_memories.md", *rollout_artifact_paths})
        diff = workspace.render_workspace_diff(max_bytes=24_000)
        workspace.write_artifact("phase2_workspace_diff.md", diff)
        try:
            consolidated = await self.consolidator.consolidate(
                Phase2ConsolidationRequest(
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    raw_memories=raw_memories,
                    rollout_summaries=rollout_summaries,
                    workspace_diff=diff,
                )
            )
            summary_text, fact_ids = _parse_consolidation_output(consolidated)
            summary_review = filter_memory_candidate(summary_text)
            summary = self.store.save_summary(
                summary_id=f"memsummary_{uuid4().hex}",
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                summary_text=summary_review.safe_text,
                fact_ids=fact_ids,
                source_stage1_conversation_ids=[item.conversation_id for item in selected],
                created_at=self.now(),
            )
            self.store.mark_stage1_outputs_selected_for_phase2(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                conversation_ids=[item.conversation_id for item in selected],
                selected_at=self.now(),
            )
            workspace.write_artifact("final_summary.md", summary.summary_text)
            workspace.prune_artifacts(
                {
                    "final_summary.md",
                    "phase2_workspace_diff.md",
                    "raw_memories.md",
                    *rollout_artifact_paths,
                }
            )
            workspace.reset_baseline()
            workspace.write_artifact("phase2_workspace_diff.md", diff)
            self.store.mark_phase2_job_succeeded(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                ownership_token=claim.ownership_token,
                now=self.now(),
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            self.store.mark_phase2_job_failed(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                ownership_token=claim.ownership_token,
                error_code=str(exc) or "agent_memory_phase2_model_invalid",
                now=self.now(),
            )
            raise
        return MemoryPhase2RunResult(status="succeeded", selected=len(selected), summary_id=summary.summary_id)


def _parse_consolidation_output(output: object) -> tuple[str, list[str]]:
    if not isinstance(output, Mapping):
        raise RuntimeError("agent_memory_phase2_model_invalid")
    output_map = {str(key): item for key, item in output.items()}
    summary = output_map.get("summaryText")
    facts = output_map.get("factIds", [])
    if not isinstance(summary, str) or not isinstance(facts, list):
        raise RuntimeError("agent_memory_phase2_model_invalid")
    return summary, [str(item) for item in facts]


def _scoped_workspace_root(root: Path, owner_user_id: str, workspace_id: str) -> Path:
    return root / _safe_path_segment(owner_user_id) / _safe_path_segment(workspace_id)


def _safe_path_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_.=-]+", "_", value.strip())
    if not segment or segment in {".", ".."}:
        raise ValueError("agent_memory_workspace_scope_invalid")
    return segment


PHASE2_INSTRUCTIONS = """Consolidate SeekTalent memory artifacts into one compact advisory summary.
Use only the provided raw memories, rollout summaries, and workspace diff.
Do not create runtime commands, source choices, candidate facts, scores, rankings, or confirmed requirements.
Return summaryText as a compact markdown string beginning with v1, and factIds as a list of included fact ids.
"""
