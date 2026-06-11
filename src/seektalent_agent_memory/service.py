from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import TypeAdapter

from seektalent_agent_memory.extraction import (
    ALLOWED_MEMORY_CATEGORIES,
    MemoryStage1Extractor,
    Stage1ExtractionRequest,
)
from seektalent_agent_memory.models import (
    AdvisoryMemoryContext,
    MemoryCandidate,
    MemoryCandidateExtractionResult,
    MemoryClearResult,
    MemoryFact,
    MemoryRetentionCleanupResult,
    MemorySettings,
    MemorySummary,
    Stage1Output,
)
from seektalent_agent_memory.privacy import MemoryPrivacyError, filter_memory_candidate, filter_memory_text, is_recall_safe
from seektalent_agent_memory.store import MemoryStore
from seektalent_agent_memory.transcript import MemoryTranscriptItem, serialize_filtered_transcript_items


logger = logging.getLogger(__name__)


class TranscriptReader(Protocol):
    def read_completed_transcript(self, *, conversation_id: str) -> list[object]: ...


class MemoryService:
    def __init__(
        self,
        *,
        store: MemoryStore,
        now: Callable[[], str],
        candidate_id_factory: Callable[[], str] | None = None,
        fact_id_factory: Callable[[], str] | None = None,
        summary_id_factory: Callable[[], str] | None = None,
        usage_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.now = now
        self.candidate_id_factory = candidate_id_factory or (lambda: f"memcand_{uuid4().hex}")
        self.fact_id_factory = fact_id_factory or (lambda: f"memfact_{uuid4().hex}")
        self.summary_id_factory = summary_id_factory or (lambda: f"memsummary_{uuid4().hex}")
        self.usage_id_factory = usage_id_factory or (lambda: f"memusage_{uuid4().hex}")

    def get_settings(self, *, owner_user_id: str, workspace_id: str) -> MemorySettings:
        return self.store.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id, now=self.now())

    def update_settings(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        memory_enabled: bool,
        review_required: bool,
        generation_enabled: bool | None = None,
        recall_enabled: bool | None = None,
        candidate_retention_days: int | None = None,
        rejected_retention_days: int | None = None,
        source_excerpt_retention_days: int | None = None,
    ) -> MemorySettings:
        return self.store.update_settings(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            memory_enabled=memory_enabled,
            generation_enabled=generation_enabled,
            recall_enabled=recall_enabled,
            review_required=review_required,
            candidate_retention_days=candidate_retention_days,
            rejected_retention_days=rejected_retention_days,
            source_excerpt_retention_days=source_excerpt_retention_days,
            updated_at=self.now(),
        )

    def create_candidate(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        conversation_id: str,
        category: str,
        text: str,
        source_message_ids: list[str],
        source_activity_ids: list[str] | None = None,
        confidence: float | None = None,
        status: str = "pending_review",
        source_stage1_conversation_id: str | None = None,
        reason_code: str | None = None,
    ) -> MemoryCandidate:
        if category not in ALLOWED_MEMORY_CATEGORIES:
            raise RuntimeError("agent_memory_category_invalid")
        review = filter_memory_candidate(text)
        return self.store.save_candidate(
            candidate_id=self.candidate_id_factory(),
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            category=category,
            text=review.safe_text,
            safe_excerpt=review.safe_excerpt,
            source_message_ids=source_message_ids,
            status=status,
            reason_code=reason_code or review.reason_code,
            created_at=self.now(),
            raw_candidate_hash=review.raw_candidate_hash,
            safe_candidate_text=review.safe_text,
            safe_evidence_excerpt=review.safe_excerpt,
            privacy_review_json=review.model_dump(mode="json"),
            confidence=confidence,
            source_stage1_conversation_id=source_stage1_conversation_id,
            source_activity_ids=source_activity_ids or [],
        )

    def extract_candidates(
        self,
        *,
        transcript_reader: TranscriptReader,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
    ) -> MemoryCandidateExtractionResult:
        settings = self.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id)
        if not settings.memory_enabled or not settings.generation_enabled:
            return MemoryCandidateExtractionResult()
        candidates: list[MemoryCandidate] = []
        for message in transcript_reader.read_completed_transcript(conversation_id=conversation_id):
            role = str(getattr(message, "role", ""))
            text = str(getattr(message, "text", ""))
            message_id = str(getattr(message, "message_id", ""))
            if role != "user" or not _looks_like_memory(text):
                continue
            try:
                candidate = self.create_candidate(
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    category=_category_for_text(text),
                    text=_clean_memory_text(text),
                    source_message_ids=[message_id],
                    status="accepted",
                    reason_code="agent_memory_policy_accepted",
                )
                self.accept_candidate(
                    candidate_id=candidate.candidate_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    accepted_text=None,
                )
                candidates.append(
                    self._get_candidate(
                        candidate_id=candidate.candidate_id,
                        owner_user_id=owner_user_id,
                        workspace_id=workspace_id,
                    )
                )
            except MemoryPrivacyError as exc:
                logger.info(
                    "Rejected advisory memory candidate during extraction.",
                    extra={"reason_code": str(exc), "conversation_id": conversation_id},
                )
                continue
        return MemoryCandidateExtractionResult(candidates=candidates)

    async def extract_stage1_from_items(
        self,
        *,
        extractor: MemoryStage1Extractor,
        owner_user_id: str,
        workspace_id: str,
        conversation_id: str,
        source_updated_at: str,
        items: Iterable[object],
    ) -> Stage1Output:
        settings = self.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id)
        if not settings.memory_enabled:
            raise RuntimeError("agent_memory_disabled")
        if not settings.generation_enabled:
            raise RuntimeError("agent_memory_generation_disabled")
        transcript_items = [_coerce_transcript_item(item) for item in items]
        serialized = serialize_filtered_transcript_items(transcript_items, max_chars=24_000)
        model_output = await extractor.extract(
            Stage1ExtractionRequest(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                serialized_transcript=serialized,
            )
        )
        raw_review = filter_memory_candidate(model_output.raw_memory) if model_output.raw_memory.strip() else None
        summary_review = (
            filter_memory_candidate(model_output.rollout_summary) if model_output.rollout_summary.strip() else None
        )
        output = self.store.save_stage1_output(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            source_updated_at=source_updated_at,
            raw_memory=raw_review.safe_text if raw_review else "",
            rollout_summary=summary_review.safe_text if summary_review else "",
            rollout_slug=model_output.rollout_slug,
            generated_at=self.now(),
            privacy_review_json={
                "rawMemory": raw_review.model_dump(mode="json") if raw_review else None,
                "rolloutSummary": summary_review.model_dump(mode="json") if summary_review else None,
            },
            source_message_ids=_unique_ids(
                message_id for item in model_output.candidates for message_id in item.evidence_message_ids
            ),
            source_activity_ids=_unique_ids(
                activity_id for item in model_output.candidates for activity_id in item.evidence_activity_ids
            ),
        )
        for candidate_output in model_output.candidates:
            if candidate_output.category not in ALLOWED_MEMORY_CATEGORIES:
                continue
            try:
                candidate = self.create_candidate(
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    category=candidate_output.category,
                    text=candidate_output.text,
                    source_message_ids=candidate_output.evidence_message_ids,
                    source_activity_ids=candidate_output.evidence_activity_ids,
                    confidence=candidate_output.confidence,
                    status="accepted",
                    source_stage1_conversation_id=conversation_id,
                    reason_code="agent_memory_policy_accepted",
                )
                self.accept_candidate(
                    candidate_id=candidate.candidate_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    accepted_text=None,
                )
            except MemoryPrivacyError as exc:
                logger.info(
                    "Rejected stage-1 advisory memory candidate.",
                    extra={"reason_code": str(exc), "conversation_id": conversation_id},
                )
        return output

    def accept_candidate(
        self,
        *,
        candidate_id: str,
        owner_user_id: str,
        workspace_id: str,
        accepted_text: str | None,
    ) -> MemoryFact:
        candidate = self._get_candidate(
            candidate_id=candidate_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        safe_text = filter_memory_text(accepted_text or candidate.text)
        accepted_at = self.now()
        settings = self.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id)
        return self.store.accept_candidate(
            candidate_id=candidate_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            accepted_text=safe_text,
            accepted_at=accepted_at,
            fact_id=self.fact_id_factory(),
            expires_at=_add_days(accepted_at, settings.candidate_retention_days),
        )

    def reject_candidate(self, *, candidate_id: str, owner_user_id: str, workspace_id: str) -> MemoryCandidate:
        rejected_at = self.now()
        settings = self.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id)
        return self.store.reject_candidate(
            candidate_id=candidate_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            rejected_at=rejected_at,
            expires_at=_add_days(rejected_at, settings.rejected_retention_days),
        )

    def run_retention_cleanup(self, *, owner_user_id: str, workspace_id: str) -> MemoryRetentionCleanupResult:
        settings = self.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id)
        cleaned_at = self.now()
        return self.store.run_retention_cleanup(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            fact_expiry_cutoff=cleaned_at,
            rejected_candidate_cutoff=cleaned_at,
            excerpt_cutoff=_add_days(cleaned_at, -settings.source_excerpt_retention_days),
            cleaned_at=cleaned_at,
            limit=500,
        )

    def delete_fact(self, *, fact_id: str, owner_user_id: str, workspace_id: str) -> MemoryFact:
        return self.store.delete_fact(
            fact_id=fact_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            deleted_at=self.now(),
        )

    def update_fact(self, *, fact_id: str, owner_user_id: str, workspace_id: str, text: str) -> MemoryFact:
        safe_text = filter_memory_text(text)
        return self.store.update_fact_text(
            fact_id=fact_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            text=safe_text,
            updated_at=self.now(),
        )

    def clear_scope(self, *, owner_user_id: str, workspace_id: str) -> MemoryClearResult:
        return self.store.clear_scope(owner_user_id=owner_user_id, workspace_id=workspace_id, cleared_at=self.now())

    def consolidate(self, *, owner_user_id: str, workspace_id: str) -> MemorySummary:
        now = self.now()
        facts = [
            fact
            for fact in self.store.list_facts(owner_user_id=owner_user_id, workspace_id=workspace_id)
            if fact.expires_at is None or fact.expires_at > now
        ]
        safe_facts = [fact for fact in facts if is_recall_safe(fact.text)]
        summary_text = "\n".join(f"{fact.category}: {fact.text}" for fact in safe_facts)
        return self.store.save_summary(
            summary_id=self.summary_id_factory(),
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            summary_text=summary_text,
            fact_ids=[fact.fact_id for fact in safe_facts],
            created_at=self.now(),
            token_estimate=_rough_token_estimate(summary_text),
        )

    def _get_candidate(self, *, candidate_id: str, owner_user_id: str, workspace_id: str) -> MemoryCandidate:
        for candidate in self.store.list_candidates(owner_user_id=owner_user_id, workspace_id=workspace_id):
            if candidate.candidate_id == candidate_id:
                return candidate
        raise RuntimeError("agent_memory_candidate_not_found")

    def recall_for_conversation(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        conversation_id: str,
        turn_id: str,
    ) -> AdvisoryMemoryContext:
        settings = self.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id)
        if not settings.memory_enabled:
            return self._empty_recall(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                reason_code="agent_memory_disabled",
            )
        if not settings.recall_enabled:
            return self._empty_recall(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                reason_code="agent_memory_recall_disabled",
            )
        active_summary = self.store.get_active_summary(owner_user_id=owner_user_id, workspace_id=workspace_id)
        now = self.now()
        if (
            active_summary is not None
            and is_recall_safe(active_summary.summary_text)
            and self._summary_references_recallable_facts(active_summary, owner_user_id, workspace_id, now)
        ):
            context_text = _bound_text(active_summary.summary_text, max_chars=settings.summary_token_budget * 4)
            self.store.save_usage(
                usage_id=self.usage_id_factory(),
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                fact_ids=active_summary.fact_ids,
                summary_id=active_summary.summary_id,
                created_at=now,
            )
            return AdvisoryMemoryContext(
                fact_ids=active_summary.fact_ids,
                summary_id=active_summary.summary_id,
                context_text=context_text,
            )
        if active_summary is not None and not is_recall_safe(active_summary.summary_text):
            return self._empty_recall(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                reason_code="agent_memory_privacy_recall_blocked",
            )
        facts = [
            fact
            for fact in self.store.list_facts(owner_user_id=owner_user_id, workspace_id=workspace_id)
            if fact.expires_at is None or fact.expires_at > now
        ]
        safe_facts = [fact for fact in facts if is_recall_safe(fact.text)]
        fact_ids = [fact.fact_id for fact in safe_facts]
        self.store.save_usage(
            usage_id=self.usage_id_factory(),
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            fact_ids=fact_ids,
            created_at=now,
        )
        return AdvisoryMemoryContext(
            fact_ids=fact_ids,
            context_text=_bound_text("\n".join(f"{fact.category}: {fact.text}" for fact in safe_facts), max_chars=5000),
        )

    def _empty_recall(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        conversation_id: str,
        turn_id: str,
        reason_code: str,
    ) -> AdvisoryMemoryContext:
        self.store.save_usage(
            usage_id=self.usage_id_factory(),
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            fact_ids=[],
            reason_code=reason_code,
            created_at=self.now(),
        )
        return AdvisoryMemoryContext(fact_ids=[], context_text="", reason_code=reason_code)

    def _summary_references_recallable_facts(
        self,
        summary: MemorySummary,
        owner_user_id: str,
        workspace_id: str,
        now: str,
    ) -> bool:
        if not summary.fact_ids:
            return True
        facts = {
            fact.fact_id: fact
            for fact in self.store.list_facts(owner_user_id=owner_user_id, workspace_id=workspace_id)
        }
        for fact_id in summary.fact_ids:
            fact = facts.get(fact_id)
            if fact is None:
                return False
            if fact.expires_at is not None and fact.expires_at <= now:
                return False
            if not is_recall_safe(fact.text):
                return False
        return True


def _coerce_transcript_item(item: object) -> MemoryTranscriptItem:
    if isinstance(item, MemoryTranscriptItem):
        return item
    if isinstance(item, dict):
        return MemoryTranscriptItem.model_validate(item)
    return MemoryTranscriptItem(
        item_id=str(getattr(item, "item_id", getattr(item, "message_id", getattr(item, "activity_id", "")))),
        item_kind=str(getattr(item, "item_kind", "message")),
        role=str(getattr(item, "role", "user")),
        text=str(getattr(item, "text", getattr(item, "summary", ""))),
        payload=TypeAdapter(dict[str, object]).validate_python(getattr(item, "payload", {})),
        created_at=str(getattr(item, "created_at", getattr(item, "updated_at", ""))),
    )


def _unique_ids(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _looks_like_memory(text: str) -> bool:
    return any(marker in text for marker in ("请记住", "偏好", "以后", "习惯"))


def _clean_memory_text(text: str) -> str:
    return text.replace("请记住：", "").replace("请记住:", "").strip()


def _category_for_text(text: str) -> str:
    if "回答" in text or "沟通" in text:
        return "summary_style"
    if "来源" in text or "渠道" in text:
        return "source_usage_preferences"
    return "recruiting_preferences"


def _bound_text(text: str, *, max_chars: int) -> str:
    clean = text.strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 24] + "\n[memory truncated]\n"


def _rough_token_estimate(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _add_days(value: str, days: int) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return (parsed + timedelta(days=days)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
