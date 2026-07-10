from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from seektalent.api import MatchRunResult
from seektalent.cli import _result_payload
from seektalent.finalize.deterministic import build_deterministic_final_result
from seektalent.models import FinalizeContext, ScoredCandidate
from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
from seektalent.providers.liepin.detail_payload_text import STRUCTURED_LIEPIN_DETAIL_TEXT_MAX_CHARS
from seektalent.providers.liepin.liepin_site_parsing import stable_liepin_detail_candidate_key_hash
from seektalent.providers.liepin.opencli_retriever import (
    LiepinOpenCliResumeRequest,
    LiepinOpenCliResumeRetriever,
    _response_from_opencli_envelope,
)
from seektalent.providers.liepin.worker_contracts import LiepinResumeSearchResponse
from seektalent.runtime.production_contract import ProductionCandidateV1
from seektalent.source_contracts import RuntimeSourceLanePlan
import seektalent.sources.liepin.runtime_lane as runtime_lane


@dataclass
class FakeOpenCliRunner:
    opened_refs: list[str]
    captured_ranks: list[int]
    artifact_root: Path

    def status(self) -> OpenCliBrowserResult:
        return OpenCliBrowserResult(ok=True, action="status")

    def search_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
        native_filters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del native_filters
        self.opened_refs.extend(["ref-1", "ref-2", "ref-3"][:target_resumes])
        self.captured_ranks.extend(range(1, target_resumes + 1))
        resumes = [
            {
                "provider_rank": index,
                "provider_candidate_key_material_ref": (
                    f"artifact://protected/liepin-opencli/provider-key/{source_run_id}/{index}.txt"
                ),
                "candidate_resume_id": f"liepin-opencli-{index}",
                "protected_snapshot_ref": f"artifact://protected/liepin-opencli/raw/{source_run_id}/{index}.json",
                "normalized_snapshot_ref": (
                    f"artifact://protected/liepin-opencli/normalized/{source_run_id}/{index}.json"
                ),
                "detail_payload": {
                    "sourceUrl": f"https://h.liepin.com/resume/showresumedetail/?res_id_encode=test-{index}",
                    "currentTitle": "数据开发专家",
                    "currentCompany": "Example",
                    "workExperienceList": [
                        {
                            "company": "Example",
                            "title": "数据开发专家",
                            "summary": f"数据平台 Python resume {index}",
                        }
                    ],
                    "educationList": [],
                    "skills": ["Python"],
                    "locations": ["杭州"],
                },
                "normalized_text": f"数据平台 Python resume {index}",
            }
            for index in range(1, target_resumes + 1)
        ]
        return {
            "schema_version": "seektalent.liepin_opencli_resumes.v1",
            "status": "succeeded",
            "stop_reason": "completed",
            "source_run_id": source_run_id,
            "query": query,
            "cards_seen": max_cards,
            "resumes_returned": target_resumes,
            "pages_visited": max_pages,
            "detail_pages_opened": target_resumes,
            "action_trace_ref": f"artifact://protected/liepin-opencli/trace/{source_run_id}/action-trace.json",
            "protected_snapshot_refs": [resume["protected_snapshot_ref"] for resume in resumes],
            "resumes": resumes,
        }


def test_opencli_retriever_opens_only_target_ranked_details(tmp_path: Path) -> None:
    runner = FakeOpenCliRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    retriever = LiepinOpenCliResumeRetriever(runner=runner)

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=2,
            max_cards=10,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
            native_filters=None,
        )
    )

    assert runner.captured_ranks == [1, 2]
    assert len(response.resumes) == 2
    assert response.raw_candidate_count == 10
    assert "数据平台 Python resume 1" in response.resumes[0].normalized_text
    assert "sourceUrl" not in response.resumes[0].payload
    assert "normalizedSnapshotRef" not in response.resumes[0].payload


def test_claim_aware_detail_uses_derived_identity_without_public_carrier_leak(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=sameSubject"
    candidate_key_hash = stable_liepin_detail_candidate_key_hash(detail_url)
    assert candidate_key_hash is not None
    resume = {
        "claim_aware": True,
        "provider_rank": 9,
        "provider_candidate_key_hash": candidate_key_hash,
        "protected_snapshot_ref": "artifact://protected/liepin-opencli/raw/run-9/9.json",
        "normalized_snapshot_ref": "artifact://protected/liepin-opencli/normalized/run-9/9.json",
        "detail_payload": {
            "sourceUrl": detail_url,
            "providerCandidateKeyHash": candidate_key_hash,
            "providerRank": 9,
            "protectedSnapshotRef": "artifact://protected/liepin-opencli/raw/run-9/9.json",
            "currentTitle": "数据开发专家",
            "currentCompany": "Example",
            "skills": ["Python"],
        },
    }

    response = _response_from_opencli_envelope(
        {
            "status": "succeeded",
            "cards_seen": 1,
            "action_trace_ref": "artifact://protected/liepin-opencli/trace/run-9/action-trace.json",
            "resumes": [resume],
        }
    )
    detail = response.resumes[0]
    result = liepin_resume_search_response_to_search_result(response)
    candidate = result.candidates[0]
    snapshot = result.provider_snapshots[0]

    assert detail.provider_subject_id is None
    assert detail.identity_confidence == "synthetic_fingerprint"
    assert detail.synthetic_candidate_fingerprint != candidate_key_hash
    assert candidate.resume_id != candidate_key_hash
    assert candidate.source_resume_id is None
    assert candidate.dedup_key != candidate_key_hash
    assert candidate.resume_id != candidate.dedup_key
    assert snapshot.provider_subject_id is None
    assert snapshot.synthetic_candidate_fingerprint == candidate.dedup_key
    assert "sourceUrl" not in detail.payload
    assert "providerCandidateKeyHash" not in detail.payload
    assert "providerRank" not in detail.payload
    assert "protectedSnapshotRef" not in detail.payload
    serialized_public_payload = json.dumps(
        [
            detail.model_dump(mode="json"),
            candidate.model_dump(mode="json"),
            asdict(snapshot),
        ],
        ensure_ascii=False,
    )
    assert "sameSubject" not in serialized_public_payload
    assert candidate_key_hash not in serialized_public_payload
    assert "run-9/9.json" not in serialized_public_payload

    final_result = build_deterministic_final_result(
        FinalizeContext(
            run_id="run-9",
            run_dir=str(tmp_path),
            rounds_executed=1,
            stop_reason="controller_stop",
            top_candidates=[_scored_candidate(candidate.resume_id)],
        )
    )
    trace_log_path = tmp_path / "trace.log"
    trace_log_path.write_text("", encoding="utf-8")
    cli_payload = _result_payload(
        MatchRunResult(
            final_result=final_result,
            final_markdown="# Final",
            run_id="run-9",
            run_dir=tmp_path,
            trace_log_path=trace_log_path,
            evaluation_result=None,
        )
    )
    production_candidate = ProductionCandidateV1.from_final_candidate(final_result.candidates[0])

    assert candidate_key_hash not in final_result.model_dump_json()
    assert candidate_key_hash not in json.dumps(cli_payload, ensure_ascii=False)
    assert candidate_key_hash not in production_candidate.model_dump_json()

    evidence = runtime_lane._source_evidence_for_candidate(
        source_plan=RuntimeSourceLanePlan(
            source_plan_id="plan-liepin",
            runtime_run_id="runtime-run-1",
            source="liepin",
            label="Liepin",
        ),
        candidate=candidate,
        collected_at="2026-07-10T00:00:00+00:00",
        evidence_level="detail",
    )
    expected_evidence_hash = hashlib.sha256(
        f"runtime-run-1:liepin:{candidate.dedup_key}".encode("utf-8")
    ).hexdigest()
    assert evidence.provider_candidate_key_hash == expected_evidence_hash
    assert candidate_key_hash not in evidence.model_dump_json()
    assert candidate_key_hash not in json.dumps(evidence.to_public_payload(), ensure_ascii=False)


@pytest.mark.parametrize("carried_key_hash", [None, "not-a-valid-carried-key"])
def test_claim_aware_detail_without_a_valid_carried_key_fails_closed(
    carried_key_hash: str | None,
) -> None:
    resume: dict[str, object] = {
        "claim_aware": True,
        "provider_rank": 1,
        "detail_payload": {"currentTitle": "数据开发专家"},
    }
    if carried_key_hash is not None:
        resume["provider_candidate_key_hash"] = carried_key_hash

    with pytest.raises(RuntimeError, match="liepin_opencli_candidate_identity_mismatch"):
        _response_from_opencli_envelope(
            {
                "status": "succeeded",
                "cards_seen": 1,
                "resumes": [resume],
            }
        )


def test_claim_aware_detail_identity_is_stable_and_distinct_across_artifact_rank_changes() -> None:
    candidate_key_hash = stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=sameSubject"
    )
    assert candidate_key_hash is not None

    different_candidate_key_hash = stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=differentSubject"
    )
    assert different_candidate_key_hash is not None

    def result_for(*, key_hash: str, rank: int, artifact_run: str):
        response = _response_from_opencli_envelope(
            {
                "status": "succeeded",
                "cards_seen": 1,
                "resumes": [
                    {
                        "claim_aware": True,
                        "provider_rank": rank,
                        "provider_candidate_key_hash": key_hash,
                        "protected_snapshot_ref": f"artifact://protected/liepin-opencli/raw/{artifact_run}/{rank}.json",
                        "detail_payload": {"currentTitle": "数据开发专家"},
                    }
                ],
            }
        )
        return liepin_resume_search_response_to_search_result(response)

    first = result_for(key_hash=candidate_key_hash, rank=1, artifact_run="primary").candidates[0]
    second = result_for(key_hash=candidate_key_hash, rank=9, artifact_run="explore").candidates[0]
    different = result_for(key_hash=different_candidate_key_hash, rank=1, artifact_run="primary").candidates[0]

    assert first.source_resume_id is None
    assert first.resume_id == second.resume_id
    assert first.dedup_key == second.dedup_key
    assert first.resume_id == hashlib.sha256(
        f"liepin:detail:presentation:v1:{candidate_key_hash}".encode("utf-8")
    ).hexdigest()
    assert first.dedup_key == hashlib.sha256(
        f"liepin:detail:dedup:v1:{candidate_key_hash}".encode("utf-8")
    ).hexdigest()
    assert first.resume_id != first.dedup_key
    assert first.resume_id != candidate_key_hash
    assert first.dedup_key != candidate_key_hash
    assert different.resume_id != first.resume_id
    assert different.dedup_key != first.dedup_key


def test_legacy_detail_without_a_carried_key_keeps_existing_fallback_identity() -> None:
    response = _response_from_opencli_envelope(
        {
            "status": "succeeded",
            "cards_seen": 1,
            "resumes": [
                {
                    "provider_rank": 1,
                    "candidate_resume_id": "legacy-run-1-rank-1",
                    "provider_candidate_key_hash": "not-a-valid-carried-key",
                    "detail_payload": {"currentTitle": "数据开发专家"},
                }
            ],
        }
    )

    detail = response.resumes[0]
    expected_provider_subject_id = hashlib.sha256(b"legacy-run-1-rank-1").hexdigest()
    assert detail.provider_subject_id == expected_provider_subject_id
    assert detail.synthetic_candidate_fingerprint == hashlib.sha256(
        f"liepin-opencli:{expected_provider_subject_id}".encode("utf-8")
    ).hexdigest()
    candidate = liepin_resume_search_response_to_search_result(response).candidates[0]
    assert candidate.resume_id == expected_provider_subject_id
    assert candidate.source_resume_id == expected_provider_subject_id


def test_non_claim_aware_detail_with_carried_key_keeps_existing_mapping() -> None:
    carried_key_hash = stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=sameSubject"
    )
    assert carried_key_hash is not None
    response = _response_from_opencli_envelope(
        {
            "status": "succeeded",
            "cards_seen": 1,
            "resumes": [
                {
                    "provider_candidate_key_hash": carried_key_hash,
                    "detail_payload": {"currentTitle": "数据开发专家"},
                }
            ],
        }
    )

    detail = response.resumes[0]
    candidate = liepin_resume_search_response_to_search_result(response).candidates[0]

    assert detail.provider_subject_id == carried_key_hash
    assert candidate.resume_id == carried_key_hash
    assert candidate.source_resume_id == carried_key_hash
    assert candidate.dedup_key == hashlib.sha256(f"liepin-opencli:{carried_key_hash}".encode("utf-8")).hexdigest()


def _scored_candidate(resume_id: str) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        source_provider="liepin",
        fit_bucket="fit",
        overall_score=95,
        must_have_match_score=95,
        preferred_match_score=70,
        risk_score=10,
        reasoning_summary="Strong role match.",
        confidence="high",
        source_round=1,
        score_evidence_source="detail_enriched",
    )


def test_opencli_retriever_structured_detail_fallback_deduplicates_and_caps_text(tmp_path: Path) -> None:
    runner = FakeOpenCliRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    runner_envelope = runner.search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发 Python",
        target_resumes=1,
        max_pages=1,
        max_cards=2,
    )
    duplicate_summary = "同一段结构化经历"
    runner_envelope["resumes"][0].pop("normalized_text")
    runner_envelope["resumes"][0]["detail_payload"] = {
        "currentTitle": "数据开发专家",
        "currentCompany": "平安好医",
        "workExperienceList": [
            {
                "company": "平安好医",
                "title": "数据开发专家",
                "summary": duplicate_summary,
                "description": duplicate_summary,
            }
        ],
        "skills": ["Python" * 900],
    }

    class EnvelopeRunner(FakeOpenCliRunner):
        def search_liepin_resumes(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            return runner_envelope

    retriever = LiepinOpenCliResumeRetriever(
        runner=EnvelopeRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    )

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=1,
            max_cards=2,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
        )
    )

    text = response.resumes[0].normalized_text
    assert text.count(duplicate_summary) == 1
    assert len(text) <= 4000


def test_opencli_retriever_ignores_envelope_normalized_text_for_details(tmp_path: Path) -> None:
    runner = FakeOpenCliRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    runner_envelope = runner.search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发 Python",
        target_resumes=1,
        max_pages=1,
        max_cards=2,
    )
    sentinel = "PAGE_CHROME_SHOULD_NOT_PERSIST"
    runner_envelope["resumes"][0]["normalized_text"] = f"{sentinel} " * 2000
    runner_envelope["resumes"][0]["detail_payload"] = {
        "currentTitle": "数据开发专家",
        "currentCompany": "平安好医",
        "workExperienceList": [
            {
                "company": "平安好医",
                "title": "数据开发专家",
                "summary": "结构化经历保留",
            }
        ],
        "skills": ["Python"],
    }

    class EnvelopeRunner(FakeOpenCliRunner):
        def search_liepin_resumes(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            return runner_envelope

    retriever = LiepinOpenCliResumeRetriever(
        runner=EnvelopeRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    )

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=1,
            max_cards=2,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
        )
    )

    text = response.resumes[0].normalized_text
    assert "平安好医" in text
    assert "结构化经历保留" in text
    assert sentinel not in text
    assert len(text) <= STRUCTURED_LIEPIN_DETAIL_TEXT_MAX_CHARS


def test_opencli_retriever_does_not_fallback_to_envelope_normalized_text_for_empty_detail_payload(
    tmp_path: Path,
) -> None:
    runner = FakeOpenCliRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    runner_envelope = runner.search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发 Python",
        target_resumes=1,
        max_pages=1,
        max_cards=2,
    )
    sentinel = "OPENCLI_NORMALIZED_TEXT_SHOULD_NOT_PERSIST"
    runner_envelope["resumes"][0]["normalized_text"] = sentinel
    runner_envelope["resumes"][0]["detail_payload"] = {}

    class EnvelopeRunner(FakeOpenCliRunner):
        def search_liepin_resumes(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            return runner_envelope

    retriever = LiepinOpenCliResumeRetriever(
        runner=EnvelopeRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    )

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=1,
            max_cards=2,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
        )
    )

    assert response.resumes[0].normalized_text == ""
    assert sentinel not in response.resumes[0].normalized_text


def test_opencli_retriever_preserves_workflow_steps_in_request_payload(tmp_path: Path) -> None:
    runner = FakeOpenCliRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    runner_envelope = runner.search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发 Python",
        target_resumes=1,
        max_pages=1,
        max_cards=2,
    )
    runner_envelope["workflow_steps"] = [
        {
            "event_type": "source_workflow_step_completed",
            "step_name": "observe_cards",
            "status": "completed",
            "safe_counts": {"visible_cards": 2},
            "safe_metadata": {},
            "artifact_refs": [],
        }
    ]

    class EnvelopeRunner(FakeOpenCliRunner):
        def search_liepin_resumes(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            return runner_envelope

    retriever = LiepinOpenCliResumeRetriever(
        runner=EnvelopeRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    )

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=1,
            max_cards=2,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
        )
    )

    assert response.request_payload["actionTraceRef"] == (
        "artifact://protected/liepin-opencli/trace/run-1/action-trace.json"
    )
    assert response.request_payload["workflowSteps"][0]["step_name"] == "observe_cards"


def test_liepin_resume_response_sanitizes_nested_workflow_step_payload() -> None:
    result = liepin_resume_search_response_to_search_result(
        LiepinResumeSearchResponse(
            resumes=[],
            requestPayload={
                "workflowSteps": [
                    {
                        "event_type": "source_workflow_step_completed",
                        "step_name": "capture_detail",
                        "status": "completed",
                        "safe_counts": {"details_opened": 1, "raw_resume": 999},
                        "safe_metadata": {
                            "rank": 1,
                            "url": "https://h.liepin.com/resume/showresumedetail/private",
                            "cookie": "secret",
                        },
                        "safe_reason_code": "liepin_opencli_detail_not_opened",
                        "artifact_refs": [
                            "artifact://protected/liepin-opencli/raw/run-1/1.json",
                            "https://h.liepin.com/private",
                        ],
                    }
                ],
                "actionTraceRef": "https://h.liepin.com/private",
            },
            rawCandidateCount=0,
        )
    )

    assert result.request_payload == {
        "workflowSteps": [
            {
                "event_type": "source_workflow_step_completed",
                "step_name": "capture_detail",
                "status": "completed",
                "safe_counts": {"details_opened": 1},
                "safe_metadata": {"rank": 1},
                "safe_reason_code": "liepin_opencli_detail_not_opened",
                "artifact_refs": ["artifact://protected/liepin-opencli/raw/run-1/1.json"],
            }
        ]
    }
    assert "liepin.com" not in repr(result.request_payload)
    assert "secret" not in repr(result.request_payload)


def test_opencli_retriever_returns_blocked_reason_when_browser_not_ready(tmp_path: Path) -> None:
    class BlockedRunner(FakeOpenCliRunner):
        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_extension_disconnected",
            )

    retriever = LiepinOpenCliResumeRetriever(
        runner=BlockedRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    )

    with pytest.raises(RuntimeError, match="liepin_opencli_extension_disconnected"):
        retriever.search_resumes(
            LiepinOpenCliResumeRequest(
                source_run_id="run-1",
                keyword_query="数据开发 Python",
                query_terms=("数据开发", "Python"),
                target_resumes=2,
                max_cards=10,
                max_pages=1,
                requirement_sheet={"job_title": "数据开发专家"},
                native_filters=None,
            )
        )


def test_opencli_retriever_allows_browser_command_to_start_stopped_daemon(tmp_path: Path) -> None:
    class StoppedDaemonRunner(FakeOpenCliRunner):
        recover_calls = 0

        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_daemon_not_running",
            )

        def recover_connection(self) -> OpenCliBrowserResult:
            self.recover_calls += 1
            return OpenCliBrowserResult(ok=True, action="recover_connection")

    runner = StoppedDaemonRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    retriever = LiepinOpenCliResumeRetriever(runner=runner)

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=2,
            max_cards=10,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
            native_filters=None,
        )
    )

    assert runner.recover_calls == 0
    assert len(response.resumes) == 2


def test_opencli_retriever_recovers_extension_connection_before_search(tmp_path: Path) -> None:
    class RecoveringRunner(FakeOpenCliRunner):
        recover_calls = 0

        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_extension_disconnected",
            )

        def recover_connection(self) -> OpenCliBrowserResult:
            self.recover_calls += 1
            return OpenCliBrowserResult(ok=True, action="recover_connection")

    runner = RecoveringRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    retriever = LiepinOpenCliResumeRetriever(runner=runner)

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=2,
            max_cards=10,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
            native_filters=None,
        )
    )

    assert runner.recover_calls == 1
    assert len(response.resumes) == 2


def test_opencli_retriever_retries_search_after_extension_recovery(tmp_path: Path) -> None:
    class RetryRunner(FakeOpenCliRunner):
        recover_calls = 0
        search_calls = 0

        def search_liepin_resumes(self, **kwargs: object) -> dict[str, object]:
            self.search_calls += 1
            if self.search_calls == 1:
                return {
                    "schema_version": "seektalent.liepin_opencli_resumes.v1",
                    "status": "blocked",
                    "safe_reason_code": "liepin_opencli_extension_disconnected",
                    "stop_reason": "liepin_opencli_extension_disconnected",
                    "cards_seen": 0,
                    "resumes": [],
                }
            return super().search_liepin_resumes(**kwargs)

        def recover_connection(self) -> OpenCliBrowserResult:
            self.recover_calls += 1
            return OpenCliBrowserResult(ok=True, action="recover_connection")

    runner = RetryRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    retriever = LiepinOpenCliResumeRetriever(runner=runner)

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=2,
            max_cards=10,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
            native_filters=None,
        )
    )

    assert runner.recover_calls == 1
    assert runner.search_calls == 2
    assert len(response.resumes) == 2


def test_opencli_retriever_runner_protocol_is_site_level() -> None:
    import seektalent.providers.liepin.opencli_retriever as module

    assert hasattr(module, "LiepinResumeSearchSite")
    assert module.OpenCliResumeRunner is module.LiepinResumeSearchSite
