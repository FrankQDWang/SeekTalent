from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from seektalent.providers.liepin.opencli_retriever import (
    LiepinOpenCliResumeRequest,
    LiepinOpenCliResumeRetriever,
)
from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
from seektalent.providers.liepin.worker_contracts import LiepinResumeSearchResponse
from seektalent.opencli_browser.contracts import OpenCliBrowserResult


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
    assert response.resumes[0].normalized_text == "数据平台 Python resume 1"
    assert response.resumes[0].payload["sourceUrl"] == (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=test-1"
    )
    assert (
        response.resumes[0]
        .payload["normalizedSnapshotRef"]
        .startswith("artifact://protected/liepin-opencli/normalized/")
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
