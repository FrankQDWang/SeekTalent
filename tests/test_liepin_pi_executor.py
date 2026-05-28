from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re

import pytest

from seektalent.providers.liepin.pi_executor import (
    HmacProviderKeyHasher,
    PiLiepinExecutor,
    PiLiepinResultStatus,
    _tool_source_run_id,
)
from seektalent.models import RequirementSheet
from seektalent.providers.pi_agent.pi_external import PiRpcAgentClient, PiRpcTaskResult, PiRpcTaskStatus
from tests.test_pi_external_agent import FakeRpcTransport


@dataclass(frozen=True)
class FakeProviderKeyHasher:
    def provider_candidate_hash(self, *, provider: str, material_ref: str) -> str:
        return f"hmac:{provider}:{material_ref.rsplit('/', 1)[-1]}"

    def provider_account_hash(self, *, provider: str, material_ref: str) -> str:
        return f"acct:{provider}:{material_ref.rsplit('/', 1)[-1]}"


@dataclass(frozen=True)
class FakeArtifactRefRegistry:
    refs: frozenset[str]
    materials: dict[str, bytes] | None = None

    def contains_public_artifact_ref(self, ref: str) -> bool:
        return ref in self.refs

    def resolve_material(self, ref: str) -> bytes:
        if ref not in self.refs:
            raise ValueError("artifact ref is not registered")
        if self.materials and ref in self.materials:
            return self.materials[ref]
        return ref.encode("utf-8")


def _registry(*refs: str, materials: dict[str, bytes] | None = None) -> FakeArtifactRefRegistry:
    return FakeArtifactRefRegistry(frozenset(refs), materials)


def _client(
    final_text: str = "",
    *,
    observed_tool_names: tuple[str, ...] = (),
    events: tuple[dict[str, object], ...] | None = None,
    rpc_status: PiRpcTaskStatus = PiRpcTaskStatus.SUCCEEDED,
) -> PiRpcAgentClient:
    skill_path = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md")
    rpc_events = events or tuple({"type": "tool_execution_start", "toolName": tool_name} for tool_name in observed_tool_names)
    return PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=Path("artifacts/pi-agent"),
        transport=FakeRpcTransport(PiRpcTaskResult(status=rpc_status, final_text=final_text, events=rpc_events)),
    )


def _valid_cards_json(
    *,
    source_run_id: str = "run-1",
    action_trace_ref: str = "artifact://protected/pi-trace/run-1",
) -> str:
    return f"""
{{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","stop_reason":"completed","source_run_id":"{source_run_id}","query":"python ranking","cards_seen":1,"cards_returned":1,"pages_visited":1,"action_trace_ref":"{action_trace_ref}","safe_summary_refs":[],"protected_snapshot_refs":["artifact://protected/pi-page/run-1"],"cards":[{{"provider_rank":1,"provider_candidate_key_material_ref":"artifact://protected/pi-provider-key/run-1/1","candidate_resume_id":"liepin-1","display_name_masked":true,"safe_card_summary":{{"display_title":"Senior Backend Engineer","current_or_recent_company":"Example","current_or_recent_title":"Senior Backend Engineer","work_years":8,"age":33,"city":"Shanghai","expected_city":"Shanghai","education_level":"master","school_names":["SJTU"],"major_names":["CS"],"skill_tags":["Python","Ranking"],"job_intention":"Backend Engineer","recent_experience_text":"Built ranking systems","normalized_card_text":"senior backend python ranking"}},"safe_card_summary_ref":"artifact://public-summary/pi-card/run-1/1","protected_snapshot_ref":"artifact://protected/pi-card/run-1/1"}}]}}
""".strip()


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=["AI Agent"],
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agentic retrieval workflows.",
        must_have_capabilities=["LangGraph", "RAG"],
        preferred_capabilities=["evaluation"],
        exclusion_signals=["pure frontend"],
        hard_constraints={},
        preferences={"preferred_query_terms": ["LangGraph", "RAG"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize agent workflow and retrieval evidence.",
    )


def _v2_resume_tool_payload(*, source_run_id: str, query: str, returned: int, target: int | None = None) -> dict[str, object]:
    target_count = target or returned
    return {
        "schema_version": "seektalent.pi_liepin_resumes.v2",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": source_run_id,
        "query": query,
        "lane": {
            "query_instance_id": "q-exploit",
            "query_role": "exploit",
            "target_resumes": target_count,
        },
        "cards_seen": max(returned, target_count),
        "cards_excluded": [],
        "resumes_returned": returned,
        "pages_visited": 1,
        "detail_pages_opened": returned,
        "action_trace_ref": f"artifact://protected/pi-trace/{source_run_id}/action-trace.json",
        "protected_snapshot_refs": [
            f"artifact://protected/pi-detail/{source_run_id}/{index}.json" for index in range(1, returned + 1)
        ],
        "resumes": [
            {
                "provider_rank": index,
                "provider_candidate_key_material_ref": f"artifact://protected/pi-key/{source_run_id}/{index}.txt",
                "candidate_resume_id": f"liepin-detail-{index}",
                "protected_snapshot_ref": f"artifact://protected/pi-detail/{source_run_id}/{index}.json",
                "detail_payload": {"fullText": f"LangGraph RAG detail resume {index}"},
                "normalized_text": f"LangGraph RAG detail resume {index}",
            }
            for index in range(1, returned + 1)
        ],
    }


def _valid_resumes_json(*, source_run_id: str, query: str, returned: int, target: int | None = None) -> str:
    return json.dumps(
        _v2_resume_tool_payload(source_run_id=source_run_id, query=query, returned=returned, target=target),
        ensure_ascii=False,
    )


def _resume_executor_with_transport(
    transport: FakeRpcTransport,
    *,
    source_run_id: str = "run-1",
    returned: int = 7,
) -> PiLiepinExecutor:
    refs = {
        f"artifact://protected/pi-trace/{source_run_id}/action-trace.json",
        *{
            f"artifact://protected/pi-detail/{source_run_id}/{index}.json"
            for index in range(1, returned + 1)
        },
        *{
            f"artifact://protected/pi-key/{source_run_id}/{index}.txt"
            for index in range(1, returned + 1)
        },
    }
    trace_ref = f"artifact://protected/pi-trace/{source_run_id}/action-trace.json"
    return PiLiepinExecutor(
        client=PiRpcAgentClient(
            command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
            skill_path=Path("skill.md"),
            dokobot_tool_name="dokobot",
            timeout_seconds=120,
            artifact_root=Path("artifacts/pi-agent"),
            transport=transport,
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            *refs,
            materials={
                trace_ref: json.dumps(
                    {
                        "schema_version": "seektalent.opencli_action_trace.v1",
                        "mode": "detail_backed_resume_search",
                        "events": [{"action_kind": "open_detail", "route_kind": "detail"}],
                    }
                ).encode("utf-8")
            },
        ),
    )


def test_hmac_provider_key_hasher_resolves_protected_material_inside_runtime() -> None:
    registry = _registry("artifact://protected/pi-provider-key/run-1/1")
    hasher = HmacProviderKeyHasher("runtime-secret", material_resolver=registry)

    candidate_hash = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    )
    account_hash = hasher.provider_account_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    )

    assert candidate_hash != "artifact://protected/pi-provider-key/run-1/1"
    assert candidate_hash != account_hash
    assert hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    ) == candidate_hash


def test_hmac_provider_key_hasher_is_stable_across_artifact_refs_for_same_material() -> None:
    registry = _registry(
        "artifact://protected/pi-provider-key/run-a/1",
        "artifact://protected/pi-provider-key/run-b/1",
        "artifact://protected/pi-provider-key/run-b/2",
        materials={
            "artifact://protected/pi-provider-key/run-a/1": b"same-provider-key",
            "artifact://protected/pi-provider-key/run-b/1": b"same-provider-key",
            "artifact://protected/pi-provider-key/run-b/2": b"different-provider-key",
        },
    )
    hasher = HmacProviderKeyHasher("runtime-secret", material_resolver=registry)

    first = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-a/1",
    )
    retry = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-b/1",
    )
    different = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-b/2",
    )

    assert retry == first
    assert different != first
    assert hasher.provider_account_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-a/1",
    ) != first


def test_pi_liepin_executor_maps_valid_cards_with_runtime_owned_hash() -> None:
    executor = PiLiepinExecutor(
        client=_client(_valid_cards_json()),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "succeeded"
    assert result.card_search is not None
    assert result.card_search.cards[0].payload["providerCandidateKeyHash"] == "hmac:liepin:1"
    assert result.card_search.cards[0].safe_card_summary is not None
    assert result.card_search.cards[0].safe_card_summary.masked_name is True
    assert result.card_search.cards[0].provider_subject_id is None
    assert result.card_search.cards[0].identity_confidence == "synthetic_fingerprint"
    assert result.card_search.cards[0].payload["sourceRunId"] == "run-1"


def test_pi_liepin_executor_uses_short_tool_source_run_id_for_opencli_task() -> None:
    runtime_source_run_id = "run_01KS7MMJRHAYH4Y2HX97BMYQ8N:source:1:liepin:lane:2"
    tool_source_run_id = _tool_source_run_id(runtime_source_run_id)
    transport = FakeRpcTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=_valid_cards_json(
                source_run_id=tool_source_run_id,
                action_trace_ref=f"artifact://protected/pi-trace/{tool_source_run_id}",
            ),
        )
    )
    executor = PiLiepinExecutor(
        client=PiRpcAgentClient(
            command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
            skill_path=Path("skill.md"),
            dokobot_tool_name="dokobot",
            timeout_seconds=120,
            artifact_root=Path("artifacts/pi-agent"),
            transport=transport,
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            f"artifact://protected/pi-trace/{tool_source_run_id}",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id=runtime_source_run_id,
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "succeeded"
    assert ":" not in tool_source_run_id
    assert len(tool_source_run_id) < len(runtime_source_run_id)
    assert runtime_source_run_id not in transport.prompts[0]
    assert f'"source_run_id": "{tool_source_run_id}"' in transport.prompts[0]
    assert result.card_search is not None
    assert result.card_search.cards[0].payload["sourceRunId"] == runtime_source_run_id


def test_pi_liepin_executor_sends_non_secret_session_context_to_pi_task() -> None:
    skill_path = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md")
    transport = FakeRpcTransport(PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=_valid_cards_json()))
    executor = PiLiepinExecutor(
        client=PiRpcAgentClient(
            command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
            skill_path=skill_path,
            dokobot_tool_name="dokobot",
            timeout_seconds=120,
            artifact_root=Path("artifacts/pi-agent"),
            transport=transport,
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
        connection_id="connection-1",
        provider_account_hash="account-hmac-1",
    )

    assert result.status == "succeeded"
    prompt = transport.prompts[0]
    assert '"connection_id": "connection-1"' in prompt
    assert '"provider_account_hash": "account-hmac-1"' in prompt
    assert "session_id" not in prompt
    assert "provider_account_lock_key" not in prompt


def test_search_resumes_sends_requirement_sheet_payload_without_old_fields() -> None:
    transport = FakeRpcTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=_valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7),
            events=({"type": "tool_execution_start", "toolName": "opencli"},),
        )
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=7)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
        connection_id="connection-1",
        provider_account_hash="account-hash",
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    prompt = transport.prompts[0]
    assert '"schema_version": "seektalent.pi_liepin_resumes.v2"' in prompt
    assert '"requirement_sheet"' in prompt
    assert '"job_title": "AI Agent Engineer"' in prompt
    assert '"must_haves"' not in prompt
    assert '"nice_to_haves"' not in prompt


def test_search_resumes_repairs_underfilled_output_inside_one_pi_session() -> None:
    first = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=5, target=7)
    repaired = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7, target=7)
    from tests.test_pi_external_agent import SequentialSessionTransport

    transport = SequentialSessionTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=first,
            events=({"type": "tool_execution_start", "toolName": "opencli"},),
        ),
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=repaired,
            events=({"type": "tool_execution_start", "toolName": "opencli"},),
        ),
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=7)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    assert result.resume_search is not None
    assert len(result.resume_search.resumes) == 7
    assert len(transport.session.prompts) == 2
    assert '"task":"liepin.search_resumes"' in transport.session.prompts[0].replace(" ", "")
    assert '"task":"liepin.repair_resume_output"' in transport.session.prompts[1].replace(" ", "")
    assert '"resume_count":2' in transport.session.prompts[1].replace(" ", "")
    assert transport.session.closed is True


def test_search_resumes_repairs_missing_detail_contract_inside_one_pi_session() -> None:
    first_payload = json.loads(_valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7, target=7))
    first_payload["resumes"][1].pop("detail_payload")
    first_payload["resumes"][2].pop("protected_snapshot_ref")
    repaired = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7, target=7)
    from tests.test_pi_external_agent import SequentialSessionTransport

    transport = SequentialSessionTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=json.dumps(first_payload),
            events=({"type": "tool_execution_start", "toolName": "opencli"},),
        ),
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=repaired,
            events=({"type": "tool_execution_start", "toolName": "opencli"},),
        ),
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=7)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    assert len(transport.session.prompts) == 2
    assert '"task":"liepin.repair_resume_output"' in transport.session.prompts[1].replace(" ", "")
    assert '"detail_payloads":["liepin-detail-2"]' in transport.session.prompts[1].replace(" ", "")
    assert '"protected_snapshot_refs":["liepin-detail-3"]' in transport.session.prompts[1].replace(" ", "")
    assert transport.session.closed is True


def test_search_resumes_rejects_forbidden_tool_usage_from_repair_turn() -> None:
    first = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=5, target=7)
    repaired = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7, target=7)
    forbidden_tool = "_".join(("seektalent", "opencli", "search", "liepin", "resumes"))
    from tests.test_pi_external_agent import SequentialSessionTransport

    transport = SequentialSessionTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=first,
            events=({"type": "tool_execution_start", "toolName": "seektalent_opencli_finalize_liepin_resumes"},),
        ),
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=repaired,
            events=({"type": "tool_execution_start", "toolName": forbidden_tool},),
        ),
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=7)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.FAILED
    assert result.safe_reason_code == "failed_provider_error"


def test_search_resumes_rejects_non_allowlisted_opencli_resume_helper() -> None:
    transport = FakeRpcTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=_valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=1),
            events=(
                {"type": "tool_execution_start", "toolName": "seektalent_opencli_search_liepin_cards"},
                {"type": "tool_execution_start", "toolName": "seektalent_opencli_finalize_liepin_resumes"},
            ),
        )
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=1)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=1,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.FAILED
    assert result.safe_reason_code == "failed_provider_error"


def test_search_resumes_keeps_valid_initial_partial_when_repair_times_out() -> None:
    first = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=5, target=7)
    from tests.test_pi_external_agent import SequentialSessionTransport

    transport = SequentialSessionTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=first,
            events=({"type": "tool_execution_start", "toolName": "seektalent_opencli_finalize_liepin_resumes"},),
        ),
        PiRpcTaskResult(
            status=PiRpcTaskStatus.TIMEOUT,
            safe_message="repair timed out",
            events=(),
        ),
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=5)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.PARTIAL
    assert result.safe_reason_code == "partial_timeout"
    assert result.resume_search is not None
    assert len(result.resume_search.resumes) == 5


def test_card_envelope_preserves_opencli_safe_reason_code() -> None:
    final_text = """
{"schema_version":"seektalent.pi_liepin_cards.v1","status":"blocked","stop_reason":"blocked_backend_unavailable","safe_reason_code":"liepin_opencli_identity_intercept","source_run_id":"run-1","query":"python ranking","cards_seen":0,"cards_returned":0,"pages_visited":1,"action_trace_ref":"artifact://protected/pi-trace/run-1","safe_summary_refs":[],"protected_snapshot_refs":[],"cards":[]}
""".strip()
    executor = PiLiepinExecutor(
        client=_client(final_text),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-trace/run-1"),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.safe_reason_code == "liepin_opencli_identity_intercept"


def test_malformed_pi_final_answer_preserves_opencli_tool_safe_reason_code() -> None:
    executor = PiLiepinExecutor(
        client=_client(
            "not json",
            events=(
                {
                    "type": "tool_execution_result",
                    "toolName": "seektalent_opencli_open_liepin_tab",
                    "result": {"safeReasonCode": "liepin_opencli_window_policy_blocked"},
                },
            ),
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-trace/run-1"),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python",),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "blocked"
    assert result.safe_reason_code == "liepin_opencli_window_policy_blocked"


def test_pi_liepin_executor_rejects_business_invariant_violations() -> None:
    payload = _valid_cards_json().replace('"cards_returned":1', '"cards_returned":2')
    executor = PiLiepinExecutor(
        client=_client(payload),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-trace/run-1"),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_liepin_executor_rejects_inconsistent_status_and_stop_reason() -> None:
    payload = _valid_cards_json().replace('"stop_reason":"completed"', '"stop_reason":"failed_provider_error"')
    executor = PiLiepinExecutor(
        client=_client(payload),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_liepin_executor_rejects_unsafe_external_candidate_resume_id() -> None:
    payload = _valid_cards_json().replace('"candidate_resume_id":"liepin-1"', '"candidate_resume_id":"13800138000"')
    executor = PiLiepinExecutor(
        client=_client(payload),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_liepin_executor_treats_rpc_timeout_without_final_cards_as_failed_provider_error() -> None:
    executor = PiLiepinExecutor(
        client=_client(rpc_status=PiRpcTaskStatus.TIMEOUT),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python",
        query_terms=("python",),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.stop_reason == "failed_provider_error"
    assert result.card_search is None


def test_card_mode_rejects_harmless_ref_when_trace_material_shows_detail_route() -> None:
    trace_ref = "artifact://protected/pi-trace/run-1"
    executor = PiLiepinExecutor(
        client=_client(_valid_cards_json(action_trace_ref=trace_ref)),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            trace_ref,
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
            materials={trace_ref: b'{"route_kind":"detail","action_kind":"read"}'},
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"


def test_capability_probe_accepts_only_observed_dokobot_tool_evidence() -> None:
    executor = PiLiepinExecutor(
        client=_client(
            '{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["dokobot.navigate","dokobot.click","dokobot.type_text"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}',
            observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
    )

    assert result.ready is True


def test_capability_probe_accepts_opencli_status_and_declared_manifest_without_action_side_effects() -> None:
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "ready",
            "read_tool_name": "seektalent_opencli_capabilities",
            "action_tool_names": [
                "seektalent_opencli_status",
                "seektalent_opencli_capabilities",
                "seektalent_opencli_open_liepin_tab",
                "seektalent_opencli_state",
                "seektalent_opencli_fill",
                "seektalent_opencli_click",
            ],
            "proof_kind": "trusted_manifest_and_observed_tool_event",
            "capability_manifest_ref": "artifact://protected/capability/manifest.json",
            "tool_evidence_ref": "artifact://protected/capability/tools.json",
            "allowed_hosts": ["www.liepin.com"],
        },
        observed_tool_names=(
            "seektalent_opencli_status",
            "seektalent_opencli_capabilities",
        ),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=(),
        expected_opencli_observed_tool_names=("seektalent_opencli_status", "seektalent_opencli_capabilities"),
        expected_opencli_declared_tool_names=(
            "seektalent_opencli_status",
            "seektalent_opencli_capabilities",
            "seektalent_opencli_open_liepin_tab",
            "seektalent_opencli_state",
            "seektalent_opencli_fill",
            "seektalent_opencli_click",
        ),
    )

    assert result.ready is True


def test_capability_probe_blocks_when_opencli_tool_unobserved() -> None:
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "ready",
            "read_tool_name": "seektalent_opencli_capabilities",
            "action_tool_names": ["seektalent_opencli_status", "seektalent_opencli_capabilities"],
            "proof_kind": "trusted_manifest_and_observed_tool_event",
            "capability_manifest_ref": "artifact://protected/capability/manifest.json",
            "tool_evidence_ref": "artifact://protected/capability/tools.json",
            "allowed_hosts": ["www.liepin.com"],
        },
        observed_tool_names=("seektalent_opencli_status",),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=(),
        expected_opencli_observed_tool_names=("seektalent_opencli_status", "seektalent_opencli_capabilities"),
        expected_opencli_declared_tool_names=("seektalent_opencli_status", "seektalent_opencli_capabilities"),
    )

    assert result.ready is False
    assert result.safe_reason_code == "liepin_opencli_status_unavailable"


def _capability_executor(
    *,
    envelope: dict[str, object],
    observed_tool_names: tuple[str, ...],
) -> PiLiepinExecutor:
    return PiLiepinExecutor(
        client=_client(json.dumps(envelope), observed_tool_names=observed_tool_names),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/capability/manifest.json",
            "artifact://protected/capability/tools.json",
        ),
    )


def test_capability_probe_requires_configured_observed_dokobot_tools(tmp_path: Path) -> None:
    del tmp_path
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "ready",
            "read_tool_name": "dokobot_read_page",
            "action_tool_names": ["dokobot_click", "dokobot_type_text"],
            "proof_kind": "trusted_manifest_and_observed_tool_event",
            "capability_manifest_ref": "artifact://protected/capability/manifest.json",
            "tool_evidence_ref": "artifact://protected/capability/tools.json",
            "allowed_hosts": ["liepin.com"],
        },
        observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    assert result.ready is True


def test_capability_probe_blocks_missing_configured_observed_tool(tmp_path: Path) -> None:
    del tmp_path
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "ready",
            "read_tool_name": "dokobot_read_page",
            "action_tool_names": ["dokobot_click", "dokobot_type_text"],
            "proof_kind": "trusted_manifest_and_observed_tool_event",
            "capability_manifest_ref": "artifact://protected/capability/manifest.json",
            "tool_evidence_ref": "artifact://protected/capability/tools.json",
            "allowed_hosts": ["liepin.com"],
        },
        observed_tool_names=("dokobot_read_page", "dokobot_click"),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    assert result.ready is False
    assert result.safe_reason_code == "liepin_pi_dokobot_tool_unobserved"


def test_capability_probe_blocks_when_expected_observed_tools_are_not_configured(tmp_path: Path) -> None:
    del tmp_path
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "ready",
            "read_tool_name": "dokobot_read_page",
            "action_tool_names": ["dokobot_click", "dokobot_type_text"],
            "proof_kind": "trusted_manifest_and_observed_tool_event",
            "capability_manifest_ref": "artifact://protected/capability/manifest.json",
            "tool_evidence_ref": "artifact://protected/capability/tools.json",
            "allowed_hosts": ["liepin.com"],
        },
        observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=(),
    )

    assert result.ready is False
    assert result.safe_reason_code == "liepin_pi_dokobot_mcp_tool_names_missing"


def test_capability_probe_preserves_adapter_unavailable_stop_reason(tmp_path: Path) -> None:
    del tmp_path
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "blocked",
            "proof_kind": "none",
            "allowed_hosts": [],
            "stop_reason": "liepin_pi_mcp_adapter_unavailable",
        },
        observed_tool_names=(),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page",),
    )

    assert result.ready is False
    assert result.safe_reason_code == "liepin_pi_mcp_adapter_unavailable"


@pytest.mark.parametrize(
    "stop_reason",
    [
        "liepin_pi_dokobot_mcp_command_missing",
        "liepin_pi_dokobot_mcp_config_mismatch",
        "liepin_pi_dokobot_mcp_tool_names_missing",
        "liepin_pi_dokobot_mcp_missing",
        "liepin_pi_dokobot_tool_unobserved",
    ],
)
def test_capability_probe_preserves_safe_dokobot_setup_stop_reason(stop_reason: str) -> None:
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "blocked",
            "proof_kind": "none",
            "allowed_hosts": [],
            "stop_reason": stop_reason,
        },
        observed_tool_names=(),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page",),
    )

    assert result.ready is False
    assert result.safe_reason_code == stop_reason


def test_capability_probe_reports_unobserved_dokobot_tools_with_specific_safe_reason() -> None:
    executor = PiLiepinExecutor(
        client=_client(
            '{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["dokobot.navigate","dokobot.click","dokobot.type_text"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}',
            observed_tool_names=("dokobot.read",),
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
    )

    assert result.ready is False
    assert result.safe_reason_code == "liepin_pi_dokobot_tool_unobserved"


def test_capability_probe_keeps_generic_reason_when_manifest_declares_missing_tools() -> None:
    executor = PiLiepinExecutor(
        client=_client(
            '{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["dokobot.navigate"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}',
            observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
    )

    assert result.ready is False
    assert result.safe_reason_code == "blocked_backend_unavailable"


def test_session_probe_rejects_non_ready_account_material() -> None:
    executor = PiLiepinExecutor(
        client=_client(
            '{"schema_version":"seektalent.pi_liepin_session_probe.v1","status":"login_required","connection_id":"liepin-pi-agent","provider_account_material_ref":"artifact://protected/pi-account/run-1/current","page_origin":"https://www.liepin.com","stop_reason":"blocked_login_required"}'
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-account/run-1/current"),
    )

    result = executor.probe_session(connection_id="liepin-pi-agent")

    assert result.status == "failed"
    assert result.provider_account_hash is None


def test_runtime_and_workbench_do_not_import_old_dokobot_action_surface() -> None:
    forbidden_pattern = re.compile(
        "DokoBotActionSurface|DokoBotActionTransportSession|DokoBotLiepinSearchCardsExecutor|"
        "DOKOBOT_ACTION|LEGACY_WORKER_COMPAT|dokobot_action|legacy_worker_compat"
    )
    matches: list[str] = []
    for path in Path("src/seektalent").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if forbidden_pattern.search(line):
                matches.append(f"{path}:{line_no}:{line.strip()}")

    assert not matches, "\n".join(matches)


def test_pi_executor_search_resumes_returns_detail_enriched_response(tmp_path: Path) -> None:
    del tmp_path
    envelope = {
        "schema_version": "seektalent.pi_liepin_resumes.v2",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "run-1",
        "query": "数据开发",
        "cards_seen": 5,
        "resumes_returned": 1,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "protected_snapshot_refs": ["artifact://protected/pi-detail/run-1/1.json"],
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": "artifact://protected/pi-key/run-1/1.txt",
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "detail_payload": {
                    "fullText": "数据开发专家，负责数据仓库、数据治理和 Python 平台。",
                    "currentTitle": "数据开发专家",
                    "currentCompany": "Example",
                    "workExperienceList": [
                        {"company": "Example", "title": "数据开发专家", "summary": "负责数据平台。"}
                    ],
                    "educationList": [{"school": "北京大学", "degree": "本科", "speciality": "计算机"}],
                    "skills": ["Python", "Hive"],
                    "locations": ["北京"],
                },
                "normalized_text": "数据开发专家 数据仓库 数据治理 Python Hive",
            }
        ],
    }
    executor = PiLiepinExecutor(
        client=_client(
            json.dumps(envelope),
            observed_tool_names=(
                "seektalent_opencli_open_liepin_tab",
                "seektalent_opencli_state",
                "seektalent_opencli_open_liepin_detail",
                "seektalent_opencli_capture_liepin_detail_resume",
                "seektalent_opencli_finalize_liepin_resumes",
            ),
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1/action-trace.json",
            "artifact://protected/pi-detail/run-1/1.json",
            "artifact://protected/pi-key/run-1/1.txt",
            materials={
                "artifact://protected/pi-trace/run-1/action-trace.json": json.dumps(
                    {
                        "schema_version": "seektalent.opencli_action_trace.v1",
                        "mode": "detail_backed_resume_search",
                        "events": [{"action_kind": "open_detail", "route_kind": "detail"}],
                    }
                ).encode("utf-8")
            },
        ),
    )

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="数据开发",
        query_terms=("数据开发",),
        target_resumes=1,
        max_cards=10,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
        native_filters=None,
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    assert result.resume_search is not None
    assert result.resume_search.resumes[0].payload["fullText"].startswith("数据开发专家")
    assert result.resume_search.resumes[0].payload["providerCandidateKeyHash"] == "hmac:liepin:1.txt"


def test_pi_executor_search_resumes_rejects_succeeded_result_below_target() -> None:
    envelope = {
        "schema_version": "seektalent.pi_liepin_resumes.v2",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "run-1",
        "query": "数据开发",
        "cards_seen": 5,
        "resumes_returned": 1,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "protected_snapshot_refs": ["artifact://protected/pi-detail/run-1/1.json"],
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": "artifact://protected/pi-key/run-1/1.txt",
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "detail_payload": {"fullText": "数据开发专家 数据治理 Python"},
                "normalized_text": "数据开发专家 数据治理 Python",
            }
        ],
    }
    from tests.test_pi_external_agent import SequentialSessionTransport

    transport = SequentialSessionTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=json.dumps(envelope),
            events=tuple(
                {"type": "tool_execution_start", "toolName": tool_name}
                for tool_name in (
                    "seektalent_opencli_open_liepin_tab",
                    "seektalent_opencli_open_liepin_detail",
                    "seektalent_opencli_capture_liepin_detail_resume",
                    "seektalent_opencli_finalize_liepin_resumes",
                )
            ),
        ),
        PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=json.dumps(envelope), events=()),
    )
    executor = PiLiepinExecutor(
        client=PiRpcAgentClient(
            command=(
                "pi",
                "--mode",
                "rpc",
                "--no-session",
                "--no-skills",
                "--skill",
                "src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md",
            ),
            skill_path=Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md"),
            dokobot_tool_name="dokobot",
            timeout_seconds=120,
            artifact_root=Path("artifacts/pi-agent"),
            transport=transport,
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1/action-trace.json",
            "artifact://protected/pi-detail/run-1/1.json",
            "artifact://protected/pi-key/run-1/1.txt",
            materials={
                "artifact://protected/pi-trace/run-1/action-trace.json": json.dumps(
                    {
                        "schema_version": "seektalent.opencli_action_trace.v1",
                        "mode": "detail_backed_resume_search",
                        "events": [{"action_kind": "open_detail", "route_kind": "detail"}],
                    }
                ).encode("utf-8")
            },
        ),
    )

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="数据开发",
        query_terms=("数据开发",),
        target_resumes=2,
        max_cards=10,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.FAILED
    assert result.safe_reason_code == "failed_provider_error"
    assert len(transport.session.prompts) == 2


def test_pi_executor_recovers_partial_resumes_after_agent_timeout_before_finalize() -> None:
    collected_ref = "artifact://protected/pi-detail/run-1/collected-resumes.json"
    trace_ref = "artifact://protected/pi-trace/run-1/agent-events.json"
    detail_ref = "artifact://protected/pi-detail/run-1/1.json"
    key_ref = "artifact://protected/pi-provider-key/run-1/1.txt"
    collected = {
        "schema_version": "seektalent.opencli_collected_resumes.v1",
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": key_ref,
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": detail_ref,
                "detail_payload": {"fullText": "数据开发专家 数据仓库 数据治理 Python Hive"},
                "normalized_text": "数据开发专家 数据仓库 数据治理 Python Hive",
            }
        ],
    }
    agent_events = {
        "schema_version": "seektalent.opencli_agent_events.v1",
        "events": [
            {"action_kind": "open_detail", "route_kind": "detail", "rank": 1},
            {"action_kind": "observe_detail", "route_kind": "detail", "ok": True, "rank": 1},
        ],
    }
    executor = PiLiepinExecutor(
        client=_client(
            observed_tool_names=(
                "seektalent_opencli_open_liepin_detail",
                "seektalent_opencli_capture_liepin_detail_resume",
            ),
            rpc_status=PiRpcTaskStatus.TIMEOUT,
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            collected_ref,
            trace_ref,
            detail_ref,
            key_ref,
            materials={
                collected_ref: json.dumps(collected, ensure_ascii=False).encode("utf-8"),
                trace_ref: json.dumps(agent_events, ensure_ascii=False).encode("utf-8"),
            },
        ),
    )

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="数据开发",
        query_terms=("数据开发",),
        target_resumes=2,
        max_cards=10,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.PARTIAL
    assert result.safe_reason_code == "partial_timeout"
    assert result.resume_search is not None
    assert result.resume_search.resumes[0].payload["fullText"].startswith("数据开发专家")
    assert result.resume_search.resumes[0].payload["providerCandidateKeyHash"] == "hmac:liepin:1.txt"


def test_pi_executor_search_resumes_rejects_forbidden_legacy_resume_tool(tmp_path: Path) -> None:
    del tmp_path
    forbidden_tool = "_".join(("seektalent", "opencli", "search", "liepin", "resumes"))
    envelope = {
        "schema_version": "seektalent.pi_liepin_resumes.v2",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "run-1",
        "query": "数据开发",
        "cards_seen": 0,
        "resumes_returned": 0,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "protected_snapshot_refs": [],
        "resumes": [],
    }
    executor = PiLiepinExecutor(
        client=_client(
            json.dumps(envelope),
            observed_tool_names=(forbidden_tool,),
        ),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1/action-trace.json",
            materials={
                "artifact://protected/pi-trace/run-1/action-trace.json": json.dumps(
                    {
                        "schema_version": "seektalent.opencli_action_trace.v1",
                        "mode": "detail_backed_resume_search",
                        "events": [],
                    }
                ).encode("utf-8")
            },
        ),
    )

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="数据开发",
        query_terms=("数据开发",),
        target_resumes=1,
        max_cards=10,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.FAILED
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_executor_search_resumes_rejects_contact_data_in_runtime_payload() -> None:
    envelope = {
        "schema_version": "seektalent.pi_liepin_resumes.v2",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "run-1",
        "query": "数据开发",
        "cards_seen": 1,
        "resumes_returned": 1,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "protected_snapshot_refs": ["artifact://protected/pi-detail/run-1/1.json"],
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": "artifact://protected/pi-key/run-1/1.txt",
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "detail_payload": {"fullText": "候选人 手机 13800138000 数据治理 Python"},
                "normalized_text": "候选人 数据治理 Python",
            }
        ],
    }
    executor = PiLiepinExecutor(
        client=_client(json.dumps(envelope)),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1/action-trace.json",
            "artifact://protected/pi-detail/run-1/1.json",
            "artifact://protected/pi-key/run-1/1.txt",
            materials={
                "artifact://protected/pi-trace/run-1/action-trace.json": json.dumps(
                    {
                        "schema_version": "seektalent.opencli_action_trace.v1",
                        "mode": "detail_backed_resume_search",
                        "events": [{"action_kind": "open_detail", "route_kind": "detail"}],
                    }
                ).encode("utf-8")
            },
        ),
    )

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="数据开发",
        query_terms=("数据开发",),
        target_resumes=1,
        max_cards=10,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
        native_filters=None,
    )

    assert result.status == PiLiepinResultStatus.FAILED
    assert result.safe_reason_code == "failed_provider_error"
