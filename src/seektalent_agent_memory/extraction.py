from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError


ALLOWED_MEMORY_CATEGORIES = {
    "recruiting_preferences",
    "requirement_patterns",
    "user_corrections",
    "team_context",
    "summary_style",
    "terminology",
    "source_usage_preferences",
}


class Stage1CandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_message_ids: list[str] = Field(default_factory=list)
    evidence_activity_ids: list[str] = Field(default_factory=list)


class Stage1ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_memory: str
    rollout_summary: str
    rollout_slug: str | None = None
    candidates: list[Stage1CandidateOutput] = Field(default_factory=list)


class Stage1ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    owner_user_id: str
    workspace_id: str
    serialized_transcript: str


class MemoryStage1Extractor(Protocol):
    async def extract(self, request: Stage1ExtractionRequest) -> Stage1ModelOutput: ...


class StructuredMemoryRuntime(Protocol):
    async def run_structured(
        self,
        prompt: str,
        *,
        name: str,
        output_type: type[object],
    ) -> object: ...


class AgentRuntimeStage1Extractor:
    def __init__(self, *, runtime: StructuredMemoryRuntime) -> None:
        self.runtime = runtime

    async def extract(self, request: Stage1ExtractionRequest) -> Stage1ModelOutput:
        final_output = await self.runtime.run_structured(
            request.serialized_transcript,
            name="SeekTalent Memory Stage1 Extractor",
            output_type=Stage1ModelOutput,
        )
        try:
            return Stage1ModelOutput.model_validate(final_output)
        except ValidationError as exc:
            raise RuntimeError("agent_memory_stage1_model_invalid") from exc


STAGE1_INSTRUCTIONS = """Extract durable, safe recruiting workflow memory from completed SeekTalent conversations.
Return only structured output. Empty output is valid when there is no durable preference or correction.
Do not store candidate PII, raw resumes, raw provider payloads, scores, rankings, full JD text, or confirmed requirement JSON.
"""
