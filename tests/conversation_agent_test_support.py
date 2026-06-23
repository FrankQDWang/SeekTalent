from __future__ import annotations

import asyncio
from pathlib import Path

from seektalent.models import QueryTermCandidate, RequirementSheet
from seektalent.progress import ProgressEvent
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_conversation_agent.source_selection import RuntimeSourceSelectionResolver
from seektalent_conversation_agent.store import ConversationStore
from seektalent_conversation_agent.service_actions import AgentServiceActionAdapter
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.detail import RuntimeDetailService
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.service import RuntimeControlService
from seektalent_runtime_control.store import RuntimeControlStore


class DeterministicRequirementExecutor:
    def extract_requirements(self, *, job_title: str | None, jd_text: str, notes: str | None) -> RequirementSheet:
        del notes
        sheet = sample_requirement_sheet(job_title=job_title or "Python 平台负责人")
        if "平台治理" not in jd_text:
            return sheet
        return sheet.model_copy(
            update={
                "must_have_capabilities": [*sheet.must_have_capabilities, "平台治理经验"],
                "initial_query_term_pool": [
                    *sheet.initial_query_term_pool,
                    QueryTermCandidate(
                        term="平台治理",
                        source="notes",
                        category="domain",
                        priority=90,
                        evidence="用户补充了平台治理经验要求。",
                        first_added_round=0,
                        active=True,
                    ),
                ],
                "scoring_rationale": "优先 Python API、平台工程、检索排序和平台治理证据。",
            }
        )


class DeterministicWorkflowRuntime:
    async def run_async(self, **kwargs: object) -> object:
        runtime_start_callback = kwargs.get("runtime_start_callback")
        if callable(runtime_start_callback):
            runtime_start_callback("workflow_runtime_run_1")
        progress_callback = kwargs.get("progress_callback")
        if callable(progress_callback):
            progress_callback(
                ProgressEvent(
                    type="round_query",
                    message="第 1 轮生成检索词。",
                    round_no=1,
                    payload={"stage": "query", "queryTerms": ["Python", "平台"]},
                )
            )
            progress_callback(
                ProgressEvent(
                    type="source_result",
                    message="CTS 返回 3 个候选人。",
                    round_no=1,
                    payload={"stage": "source", "sourceId": "cts", "candidateCount": 3},
                )
            )
        return {"status": "completed"}


def sample_requirement_sheet(*, job_title: str = "Python 平台负责人") -> RequirementSheet:
    return RequirementSheet(
        job_title=job_title,
        title_anchor_terms=["Python 平台负责人"],
        title_anchor_rationale="岗位标题是稳定检索锚点。",
        role_summary="负责 Python API、平台工程和检索排序。",
        must_have_capabilities=["Python API", "平台工程"],
        preferred_capabilities=["检索排序经验"],
        exclusion_signals=["只做脚本维护"],
        hard_constraints={},
        preferences={"preferred_query_terms": ["Python 后端", "平台工程"]},
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Python 后端",
                source="jd",
                category="domain",
                priority=1,
                evidence="JD 明确要求 Python API。",
                first_added_round=0,
                active=True,
            ),
            QueryTermCandidate(
                term="平台工程",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD 明确要求平台工程。",
                first_added_round=0,
                active=True,
            ),
        ],
        scoring_rationale="优先 Python API、平台工程和检索排序证据。",
    )


def save_approved_requirement(
    runtime_store: RuntimeControlStore,
    *,
    conversation_id: str,
    approved_requirement_revision_id: str = "reqapproved_1",
) -> ApprovedRequirementRevision:
    approved = ApprovedRequirementRevision(
        approved_requirement_revision_id=approved_requirement_revision_id,
        draft_revision_id=None,
        agent_conversation_id=conversation_id,
        requirement_sheet=sample_requirement_sheet(),
        selected_item_ids=[],
        deselected_item_ids=[],
        created_at="2026-06-09T00:00:10.000000Z",
    )
    return runtime_store.save_approved_requirement(approved, idempotency_key=f"{approved_requirement_revision_id}:save")


def build_service(tmp_path: Path) -> tuple[ConversationAgentService, ConversationStore, RuntimeControlStore]:
    conversation_store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    conversation_store.initialize()
    runtime_store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    runtime_store.initialize()
    requirement_executor = DeterministicRequirementExecutor()
    requirement_service = RuntimeControlService(
        store=runtime_store,
        executor=requirement_executor,
    )
    command_service = RuntimeCommandService(store=runtime_store, requirement_extractor=requirement_executor)
    executor = WorkflowRuntimeExecutor(
        store=runtime_store,
        runtime_factory=DeterministicWorkflowRuntime,
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "runtime_executor_1",
        checkpoint_id_factory=lambda: "runtime_checkpoint_1",
        command_service=command_service,
    )
    detail_service = RuntimeDetailService(store=runtime_store, summary_id_factory=lambda: "runtime_final_summary_1")
    adapter = AgentServiceActionAdapter(
        runtime_store=runtime_store,
        requirement_service=requirement_service,
        command_service=command_service,
        workflow_executor=executor,
        detail_service=detail_service,
    )
    service = ConversationAgentService(
        store=conversation_store,
        service_action_adapter=adapter,
        now=_clock(),
        conversation_id_factory=lambda: "agent_conv_1",
        message_id_factory=_sequence("agent_msg"),
        activity_id_factory=_sequence("agent_activity"),
        operation_id_factory=_sequence("operation_audit"),
        summary_id_factory=_sequence("agent_context_summary"),
        compaction_id_factory=_sequence("agent_compaction"),
        source_selection_resolver=RuntimeSourceSelectionResolver(
            registered_runtime_source_ids={"cts", "liepin"}
        ),
    )
    return service, conversation_store, runtime_store


def execute_queued_workflow(
    runtime_store: RuntimeControlStore,
    *,
    runtime_run_id: str = "runtime_run_1",
) -> None:
    requirement_executor = DeterministicRequirementExecutor()
    command_service = RuntimeCommandService(store=runtime_store, requirement_extractor=requirement_executor)
    executor = WorkflowRuntimeExecutor(
        store=runtime_store,
        runtime_factory=DeterministicWorkflowRuntime,
        runtime_run_id_factory=lambda: runtime_run_id,
        executor_id_factory=lambda: "runtime_executor_worker",
        checkpoint_id_factory=lambda: "runtime_checkpoint_worker",
        now=_worker_clock(),
        command_service=command_service,
    )
    claim = runtime_store.claim_next_runnable_run(
        executor_id="runtime_executor_worker",
        claimed_at="2026-06-09T00:01:00.000000Z",
        lease_expires_at="2026-06-09T00:02:00.000000Z",
        runtime_run_id=runtime_run_id,
    )
    assert claim is not None
    asyncio.run(
        executor.execute_claimed_run(
            runtime_run_id=claim.runtime_run.runtime_run_id,
            executor_id=claim.lease.executor_id,
            attempt_no=claim.lease.attempt_no,
        )
    )


def _clock():
    values = [0]

    def now() -> str:
        values[0] += 1
        return f"2026-06-09T00:00:{values[0]:02d}.000000Z"

    return now


def _worker_clock():
    values = [10]

    def now() -> str:
        values[0] += 1
        return f"2026-06-09T00:01:{values[0]:02d}.000000Z"

    return now


def _sequence(prefix: str):
    values = [0]

    def next_id() -> str:
        values[0] += 1
        return f"{prefix}_{values[0]}"

    return next_id
