from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent.models import HardConstraintSlots, QueryTermCandidate, RequirementSheet
from seektalent_runtime_control.models import (
    RuntimeControlCandidateEvidence,
    RuntimeControlCandidateIdentity,
    RuntimeControlEventInput,
)
from seektalent_runtime_control.requirements import draft_from_requirement_sheet
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_workbench_v2.agent_loop import WorkbenchV2RuntimeInput
import seektalent_workbench_v2.runtime_service as runtime_service_module
from seektalent_workbench_v2.runtime_service import WorkbenchV2RuntimeService


NOW = "2026-06-25T01:02:03.000004+00:00"


class RecordingRequirementExtractor:
    def __init__(self, sheet: RequirementSheet) -> None:
        self.sheet = sheet
        self.calls: list[dict[str, object]] = []

    def extract_requirements(
        self,
        *,
        job_title: str,
        jd_text: str,
        notes: str | None,
        requirement_cache_scope: str,
    ) -> RequirementSheet:
        self.calls.append(
            {
                "job_title": job_title,
                "jd_text": jd_text,
                "notes": notes,
                "requirement_cache_scope": requirement_cache_scope,
            }
        )
        return self.sheet


class RecordingJdRequirementExtractor:
    def __init__(self, sheet: RequirementSheet) -> None:
        self.sheet = sheet
        self.calls: list[dict[str, object]] = []

    def extract_requirements(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        requirement_cache_scope: str,
    ) -> RequirementSheet:
        self.calls.append(
            {
                "job_title": job_title,
                "jd": jd,
                "notes": notes,
                "requirement_cache_scope": requirement_cache_scope,
            }
        )
        return self.sheet


class CandidateFactStore:
    def list_candidate_identities(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateIdentity]:
        assert runtime_run_id == "rtrun_candidate"
        return [
            RuntimeControlCandidateIdentity(
                runtime_run_id=runtime_run_id,
                identity_id="identity_1",
                canonical_resume_id="resume_1",
                display_name="吴所谓",
                title="资深体验设计工程师",
                company="平安集团",
                location="上海",
                summary="可独立主导 0-1 产品体验搭建。",
                score=92,
                fit_bucket="fit",
                payload_hash="identity_hash",
                updated_at=NOW,
            )
        ]

    def list_candidate_evidence(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateEvidence]:
        assert runtime_run_id == "rtrun_candidate"
        return [
            RuntimeControlCandidateEvidence(
                runtime_run_id=runtime_run_id,
                evidence_id="evidence_1",
                identity_id="identity_1",
                resume_id="resume_1",
                source_kind="liepin",
                evidence_level="detail",
                provider_candidate_key_hash="provider_hash",
                score=92,
                fit_bucket="fit",
                payload={
                    "candidateProfile": {
                        "age": 32,
                        "gender": "男",
                        "activeStatus": "近30天内活跃",
                        "jobState": "在职，看看新机会",
                        "nowLocation": "上海",
                        "workYear": 10,
                        "expectedJobCategory": "高端设计职位",
                    },
                    "safeSummary": {"educationLevel": "本科"},
                    "normalizedProfile": {
                        "rawTextExcerpt": "URL: https://h.liepin.com/resume/showresumedetail\n新手任务\n账号问候\n页面导航",
                    },
                    "match": {
                        "score": 92,
                        "fitBucket": "fit",
                        "reasoningSummary": "可独立主导 0-1 产品体验搭建。",
                        "strengths": ["擅长通过定量和定性调研挖掘真实痛点。"],
                        "weaknesses": ["AI 产品体验设计项目未在简历中明确体现。"],
                        "sourceRound": 1,
                    },
                    "wtsDetail": {
                        "candidateName": "吴所谓",
                        "activeStatus": "近30天内活跃",
                        "jobStatus": "在职，看看新机会",
                        "gender": "男",
                        "age": 32,
                        "city": "上海",
                        "education": "本科",
                        "workYears": 10,
                        "currentTitle": "资深体验设计工程师",
                        "currentCompany": "平安集团",
                        "jobIntention": {
                            "expectedRole": "高端设计职位",
                            "expectedIndustry": "互联网、其他",
                            "expectedCity": "上海",
                            "expectedSalary": "20-24k*14薪",
                        },
                        "workExperience": [
                            {
                                "dateRange": "2019.06-至今（7年）",
                                "company": "平安好医",
                                "title": "用户体验设计专家",
                                "description": "提供 B 端及 C 端体验设计方案。",
                            }
                        ],
                        "projectExperience": [
                            {
                                "dateRange": "2020.05-至今（6年1个月）",
                                "name": "助力C端业务增长",
                                "role": "-",
                                "description": "通过设计调研提升转化率。",
                            }
                        ],
                        "educationExperience": [
                            {
                                "dateRange": "2011.09-2014.07（2年10个月）",
                                "school": "华东师范大学",
                                "major": "工业设计",
                                "degree": "硕士",
                            }
                        ],
                        "skills": ["用户研究", "交互设计"],
                        "sourceUrl": "https://h.liepin.com/resume/showresumedetail/?res_id_encode=test",
                    },
                    "safeDetail": {
                        "candidateName": "吴所谓",
                        "summary": "SAFE_DETAIL_SUMMARY_SHOULD_NOT_RENDER",
                        "profile": "SAFE_DETAIL_PROFILE_SHOULD_NOT_RENDER",
                        "workExperienceList": [
                            {
                                "duration": "2019.06-至今（7年）",
                                "company": "平安好医",
                                "title": "用户体验设计专家",
                                "summary": (
                                    "提供 B 端及 C 端体验设计方案。\n"
                                    "声明：该人选信息仅供公司招聘使用，严禁以招聘以外的任何目的使用人选信息。\n"
                                    "简历备注\n"
                                    "ICP备案信息"
                                ),
                            }
                        ],
                    },
                },
                payload_hash="evidence_hash",
                updated_at=NOW,
            )
        ]


class CandidateThresholdStore:
    def list_candidate_identities(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateIdentity]:
        return [
            RuntimeControlCandidateIdentity(
                runtime_run_id=runtime_run_id,
                identity_id=identity_id,
                canonical_resume_id=f"resume-{identity_id}",
                display_name=identity_id,
                title="AI Agent Engineer",
                company="Accio",
                location="Hangzhou",
                summary="score threshold fixture",
                score=score,
                fit_bucket="fit" if score is not None else None,
                payload_hash=f"hash-{identity_id}",
                updated_at=NOW,
            )
            for identity_id, score in (("low", 59), ("edge", 60), ("high", 90), ("unscored", None))
        ]

    def list_candidate_evidence(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateEvidence]:
        del runtime_run_id
        return []


def test_candidate_summary_hides_scores_below_sixty_and_reranks() -> None:
    service = WorkbenchV2RuntimeService(store=CandidateThresholdStore())  # type: ignore[arg-type]
    summaries = service.list_candidate_summaries("rtrun_candidate")
    assert [(item["candidateId"], item["rank"]) for item in summaries] == [
        ("high", 1),
        ("edge", 2),
    ]


class CandidateIdentityOnlyStore:
    def list_candidate_identities(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateIdentity]:
        assert runtime_run_id == "rtrun_candidate"
        return [
            RuntimeControlCandidateIdentity(
                runtime_run_id=runtime_run_id,
                identity_id="identity_1",
                canonical_resume_id="resume_1",
                display_name="候选人A",
                title="",
                company="",
                location="",
                summary="",
                score=60,
                fit_bucket="fit",
                payload_hash="identity_hash",
                updated_at=NOW,
            )
        ]

    def list_candidate_evidence(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateEvidence]:
        assert runtime_run_id == "rtrun_candidate"
        return []


class CandidateMergedEvidenceStore:
    def list_candidate_identities(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateIdentity]:
        assert runtime_run_id == "rtrun_candidate"
        return [
            RuntimeControlCandidateIdentity(
                runtime_run_id=runtime_run_id,
                identity_id="identity_merged",
                canonical_resume_id="resume_merged",
                display_name="Candidate 1",
                title="",
                company="",
                location="",
                summary="identity fallback summary",
                score=None,
                fit_bucket=None,
                payload_hash="identity_hash",
                updated_at=NOW,
            )
        ]

    def list_candidate_evidence(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateEvidence]:
        assert runtime_run_id == "rtrun_candidate"
        return [
            RuntimeControlCandidateEvidence(
                runtime_run_id=runtime_run_id,
                evidence_id="evidence_cts_sparse",
                identity_id="identity_merged",
                resume_id="resume_merged",
                source_kind="cts",
                evidence_level="summary",
                provider_candidate_key_hash="provider_hash_cts",
                score=70,
                fit_bucket="possible",
                payload={
                    "match": {"score": 70, "fitBucket": "possible"},
                    "wtsDetail": {
                        "candidateName": "CTS占位",
                        "skills": ["CTS标签"],
                    },
                },
                payload_hash="evidence_hash_cts",
                updated_at=NOW,
            ),
            RuntimeControlCandidateEvidence(
                runtime_run_id=runtime_run_id,
                evidence_id="evidence_liepin_detail",
                identity_id="identity_merged",
                resume_id="resume_merged",
                source_kind="liepin",
                evidence_level="detail",
                provider_candidate_key_hash="provider_hash_liepin",
                score=95,
                fit_bucket="fit",
                payload={
                    "match": {
                        "score": 95,
                        "fitBucket": "fit",
                        "reasoningSummary": "猎聘详情显示候选人与岗位高度匹配。",
                        "strengths": ["有完整 0-1 体验设计项目经验。"],
                        "weaknesses": ["AI 项目经验需要面试确认。"],
                    },
                    "wtsDetail": {
                        "candidateName": "吴所谓",
                        "jobIntention": {
                            "expectedRole": "高端设计职位",
                            "expectedCity": "上海",
                            "expectedSalary": "20-24k*14薪",
                        },
                        "workExperience": [
                            {
                                "dateRange": "2019.06-至今（7年）",
                                "company": "平安好医",
                                "title": "用户体验设计专家",
                            }
                        ],
                        "skills": ["用户研究", "交互设计"],
                        "sourceUrl": "https://h.liepin.com/resume/showresumedetail/?res_id_encode=rich",
                    },
                },
                payload_hash="evidence_hash_liepin",
                updated_at=NOW,
            ),
        ]


def test_runtime_service_extracts_requirement_form(tmp_path: Path) -> None:
    sheet = _requirement_sheet()
    extractor = RecordingRequirementExtractor(sheet)
    service = _service(tmp_path, requirement_extractor=extractor)

    draft = service.extract_requirements(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle=" AI 平台工程师 ", jd=" 需要 Agent 系统经验 ", notes="杭州"),
    )

    assert extractor.calls == [
        {
            "job_title": "AI 平台工程师",
            "jd_text": "需要 Agent 系统经验",
            "notes": "杭州",
            "requirement_cache_scope": "agentv2_1",
        }
    ]
    assert draft.conversation_id == "agentv2_1"
    assert draft.draft_revision_id == "reqdraft_1"
    assert draft.status == "draft_ready"
    item_sources = [item.source for section in draft.sections for item in section.items]
    assert item_sources
    assert set(item_sources) == {"workbench_v2_agent"}


def test_runtime_service_extracts_requirement_bundle_once(tmp_path: Path) -> None:
    sheet = _requirement_sheet()
    extractor = RecordingRequirementExtractor(sheet)
    service = _service(tmp_path, requirement_extractor=extractor)

    bundle = service.extract_requirement_bundle(
        "agentv2_bundle",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes="杭州"),
    )

    assert extractor.calls == [
        {
            "job_title": "AI 平台工程师",
            "jd_text": "需要 Agent 系统经验",
            "notes": "杭州",
            "requirement_cache_scope": "agentv2_bundle",
        }
    ]
    assert bundle.requirement_sheet == sheet
    assert bundle.draft.conversation_id == "agentv2_bundle"
    assert bundle.draft.draft_revision_id == "reqdraft_1"
    assert bundle.draft.status == "draft_ready"


def test_runtime_service_amends_requirement_bundle_without_losing_deselected_items(tmp_path: Path) -> None:
    base_sheet = _requirement_sheet()
    supplement_sheet = base_sheet.model_copy(
        update={
            "must_have_capabilities": ["熟悉 LangGraph"],
            "preferred_capabilities": [],
            "exclusion_signals": [],
            "initial_query_term_pool": [
                QueryTermCandidate(
                    term="LangGraph",
                    source="notes",
                    category="tooling",
                    priority=90,
                    evidence="补充要求",
                    first_added_round=0,
                )
            ],
        }
    )
    base_draft = draft_from_requirement_sheet(
        conversation_id="agentv2_1",
        draft_revision_id="reqdraft_base",
        base_revision_id=None,
        requirement_sheet=base_sheet,
        source="workbench_v2_agent",
        created_at=NOW,
    )
    base_draft.section("must_have_capabilities").items[0].selected = False
    extractor = RecordingRequirementExtractor(supplement_sheet)
    service = _service(tmp_path, requirement_extractor=extractor)

    bundle = service.amend_requirement_bundle(
        "agentv2_1",
        base_draft=base_draft,
        base_requirement_sheet=base_sheet,
        text="熟悉 LangGraph",
        idempotency_key="confirm-1",
    )

    assert extractor.calls == [
        {
            "job_title": "AI 平台工程师",
            "jd_text": "熟悉 LangGraph",
            "notes": None,
            "requirement_cache_scope": "agentv2_1:confirm-1",
        }
    ]
    assert bundle.draft.base_revision_id == "reqdraft_base"
    must_have_items = bundle.draft.section("must_have_capabilities").items
    assert must_have_items[0].text == "Python 后端开发"
    assert must_have_items[0].selected is False
    assert any(item.text == "熟悉 LangGraph" and item.selected for item in must_have_items)
    assert bundle.requirement_sheet.must_have_capabilities == ["Agent 工作流经验", "熟悉 LangGraph"]


def test_runtime_service_extracts_requirement_form_from_runtime_factory(tmp_path: Path) -> None:
    sheet = _requirement_sheet()
    extractor = RecordingRequirementExtractor(sheet)
    service = _service(tmp_path, runtime_factory=lambda: extractor)

    draft = service.extract_requirements(
        "agentv2_factory",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
    )

    assert extractor.calls == [
        {
            "job_title": "AI 平台工程师",
            "jd_text": "需要 Agent 系统经验",
            "notes": None,
            "requirement_cache_scope": "agentv2_factory",
        }
    ]
    assert draft.conversation_id == "agentv2_factory"
    assert draft.status == "draft_ready"


def test_runtime_service_extracts_requirement_form_from_jd_runtime_signature(tmp_path: Path) -> None:
    sheet = _requirement_sheet()
    extractor = RecordingJdRequirementExtractor(sheet)
    service = _service(tmp_path, runtime_factory=lambda: extractor)

    draft = service.extract_requirements(
        "agentv2_runtime",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
    )

    assert extractor.calls == [
        {
            "job_title": "AI 平台工程师",
            "jd": "需要 Agent 系统经验",
            "notes": "",
            "requirement_cache_scope": "agentv2_runtime",
        }
    ]
    assert draft.conversation_id == "agentv2_runtime"
    assert draft.status == "draft_ready"


def test_runtime_service_candidate_detail_projects_wts_profile_fields() -> None:
    service = WorkbenchV2RuntimeService(store=CandidateFactStore())  # type: ignore[arg-type]

    summary = service.list_candidate_summaries("rtrun_candidate")[0]
    detail = service.get_candidate_detail("rtrun_candidate", "identity_1")

    assert summary["displayName"] == "吴所谓"
    assert summary["currentTitle"] == "资深体验设计工程师"
    assert summary["currentCompany"] == "平安集团"
    assert summary["city"] == "上海"
    assert summary["workYears"] == 10
    assert summary["sourceLabel"] == "猎聘"
    assert detail["displayName"] == "吴所谓"
    assert detail["headline"] == "资深体验设计工程师 · 平安集团"
    assert detail["company"] == "平安集团"
    assert detail["currentTitle"] == "资深体验设计工程师"
    assert detail["currentCompany"] == "平安集团"
    assert detail["location"] == "上海"
    assert detail["city"] == "上海"
    assert detail["education"] == "本科"
    assert detail["experienceYears"] == 10
    assert detail["workYears"] == 10
    assert detail["age"] == 32
    assert detail["gender"] == "男"
    assert detail["activeStatus"] == "近30天内活跃"
    assert detail["jobStatus"] == "在职，看看新机会"
    assert detail["sourceLabel"] == "猎聘"
    assert detail["avatarLabel"] == "吴"
    assert detail["avatarColorKey"] in {f"avatar-{index}" for index in range(6)}
    assert detail["match"] == {
        "summary": "可独立主导 0-1 产品体验搭建。",
        "strengths": ["擅长通过定量和定性调研挖掘真实痛点。"],
        "weaknesses": ["AI 产品体验设计项目未在简历中明确体现。"],
        "score": 92,
        "fitBucket": "fit",
    }
    assert detail["jobIntention"]["expectedSalary"] == "20-24k*14薪"
    assert detail["workExperience"][0]["company"] == "平安好医"
    assert detail["projectExperience"][0]["name"] == "助力C端业务增长"
    assert detail["educationExperience"][0]["school"] == "华东师范大学"
    assert detail["skills"] == ["用户研究", "交互设计"]
    assert detail["sourceUrl"].startswith("https://h.liepin.com/resume/showresumedetail/")
    assert detail["sections"][1]["title"] == "求职意向"
    serialized_sections = "\n".join(item for section in detail["sections"] for item in section["items"])
    assert "提供 B 端及 C 端体验设计方案" in serialized_sections
    assert "h.liepin.com/resume/showresumedetail" not in serialized_sections
    assert "新手任务" not in serialized_sections
    assert "声明：该人选信息仅供公司招聘使用" not in serialized_sections
    assert "简历备注" not in serialized_sections
    assert "ICP备案信息" not in serialized_sections
    assert "SAFE_DETAIL_SUMMARY_SHOULD_NOT_RENDER" not in serialized_sections
    assert "SAFE_DETAIL_PROFILE_SHOULD_NOT_RENDER" not in serialized_sections


def test_runtime_service_candidate_detail_prefers_rich_liepin_evidence_over_sparse_cts() -> None:
    service = WorkbenchV2RuntimeService(store=CandidateMergedEvidenceStore())  # type: ignore[arg-type]

    detail = service.get_candidate_detail("rtrun_candidate", "identity_merged")

    assert detail["displayName"] == "吴所谓"
    assert detail["sourceUrl"] == "https://h.liepin.com/resume/showresumedetail/?res_id_encode=rich"
    assert detail["workExperience"] == [
        {
            "dateRange": "2019.06-至今（7年）",
            "company": "平安好医",
            "title": "用户体验设计专家",
        }
    ]
    assert detail["jobIntention"]["expectedSalary"] == "20-24k*14薪"
    assert detail["skills"] == ["用户研究", "交互设计"]
    assert detail["match"] == {
        "summary": "猎聘详情显示候选人与岗位高度匹配。",
        "strengths": ["有完整 0-1 体验设计项目经验。"],
        "weaknesses": ["AI 项目经验需要面试确认。"],
        "score": 95,
        "fitBucket": "fit",
    }


def test_runtime_service_does_not_claim_source_without_evidence() -> None:
    service = WorkbenchV2RuntimeService(store=CandidateIdentityOnlyStore())  # type: ignore[arg-type]

    summary = service.list_candidate_summaries("rtrun_candidate")[0]
    detail = service.get_candidate_detail("rtrun_candidate", "identity_1")

    assert summary["sourceKinds"] == []
    assert summary["avatarColorKey"] in {f"avatar-{index}" for index in range(6)}
    assert detail["sourceKinds"] == []
    assert detail["avatarColorKey"] in {f"avatar-{index}" for index in range(6)}
    assert detail["evidenceLevel"] == "unknown"


@pytest.mark.parametrize(
    "runtime_input",
    [
        None,
        WorkbenchV2RuntimeInput.model_construct(jobTitle="", jd="需要 Agent 系统经验", notes="杭州"),
        WorkbenchV2RuntimeInput.model_construct(jobTitle="AI 平台工程师", jd=" ", notes=None),
    ],
)
def test_runtime_service_refuses_start_without_required_fields(
    tmp_path: Path,
    runtime_input: WorkbenchV2RuntimeInput | None,
) -> None:
    service = _service(tmp_path, runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()))

    with pytest.raises(ValueError, match="^workbench_v2_runtime_input_required$"):
        service.start_run("agentv2_1", runtime_input, _requirement_sheet())


def test_runtime_service_enqueues_run_with_job_title_jd_and_notes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    queued_run_ids: list[str] = []
    service = WorkbenchV2RuntimeService(
        store=store,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        draft_revision_id_factory=lambda: "reqdraft_start_1",
        approved_requirement_revision_id_factory=lambda: "reqapproved_1",
        runtime_run_id_factory=lambda: "rtrun_1",
        on_run_queued=queued_run_ids.append,
        now=lambda: NOW,
    )
    runtime_input = WorkbenchV2RuntimeInput(
        jobTitle="AI 平台工程师",
        jd="需要 Python 和 Agent 工作流经验",
        notes="杭州",
    )

    run = service.start_run("agentv2_1", runtime_input, _requirement_sheet())

    assert run.runtime_run_id == "rtrun_1"
    assert run.status == "queued"
    assert queued_run_ids == ["rtrun_1"]
    assert run.source_ids == ["liepin"]
    approved = store.get_approved_requirement("reqapproved_1")
    assert approved.agent_conversation_id == "agentv2_1"
    assert approved.draft_revision_id == "reqdraft_start_1"
    assert approved.requirement_sheet == _requirement_sheet()
    assert approved.selected_item_ids
    assert approved.deselected_item_ids == []
    snapshot = store.get_snapshot(runtime_run_id=run.runtime_run_id)
    assert snapshot is not None
    assert snapshot.snapshot["workflowInput"] == {
        "jobTitle": "AI 平台工程师",
        "jdText": "需要 Python 和 Agent 工作流经验",
        "notes": "杭州",
        "sourceIds": ["liepin"],
        "sourceContext": {
            "tenant_id": "local",
            "workspace_id": "default",
            "actor_id": "local",
            "connection_id": "liepin-opencli",
            "provider_account_hash": "liepin-opencli-local",
        },
    }


def test_runtime_service_start_run_replays_default_idempotency_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_run_ids = iter(["rtrun_1", "rtrun_2"])
    service = WorkbenchV2RuntimeService(
        store=store,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: next(runtime_run_ids),
        now=lambda: NOW,
    )
    runtime_input = WorkbenchV2RuntimeInput(
        jobTitle="AI 平台工程师",
        jd="需要 Python 和 Agent 工作流经验",
        notes="杭州",
    )

    first = service.start_run("agentv2_replay", runtime_input, _requirement_sheet())
    second = service.start_run("agentv2_replay", runtime_input, _requirement_sheet())

    assert first.runtime_run_id == "rtrun_1"
    assert second.runtime_run_id == "rtrun_1"
    assert _runtime_run_count(store) == 1


def test_runtime_service_submits_next_round_requirement_to_runtime_control(tmp_path: Path) -> None:
    store = _store(tmp_path)
    extractor = RecordingRequirementExtractor(_requirement_sheet())
    service = WorkbenchV2RuntimeService(
        store=store,
        requirement_extractor=extractor,
        runtime_factory=lambda: extractor,
        runtime_run_id_factory=lambda: "rtrun_1",
        now=lambda: NOW,
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Python 和 Agent 工作流经验", notes="杭州"),
        _requirement_sheet(),
    )

    result = service.submit_next_round_requirement(
        run.runtime_run_id,
        "补充：候选人必须有 LangGraph 经验。",
        idempotency_key="next-round-1",
    )

    assert result["status"] == "pending_target_round"
    assert result["targetRoundNo"] == 1
    amendments = store.list_runtime_requirement_amendments(
        runtime_run_id=run.runtime_run_id,
        target_round_no=1,
        statuses={"pending_target_round"},
    )
    assert len(amendments) == 1
    assert amendments[0].input_text == "补充：候选人必须有 LangGraph 经验。"
    assert extractor.calls[-1] == {
        "job_title": "AI 平台工程师",
        "jd_text": "补充：候选人必须有 LangGraph 经验。",
        "notes": None,
        "requirement_cache_scope": "rtrun_1",
    }


def test_runtime_service_start_run_replays_explicit_idempotency_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_run_ids = iter(["rtrun_1", "rtrun_2"])
    service = WorkbenchV2RuntimeService(
        store=store,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: next(runtime_run_ids),
        now=lambda: NOW,
    )
    runtime_input = WorkbenchV2RuntimeInput(
        jobTitle="AI 平台工程师",
        jd="需要 Python 和 Agent 工作流经验",
        notes="杭州",
    )

    first = service.start_run(
        "agentv2_replay",
        runtime_input,
        _requirement_sheet(),
        idempotency_key="confirm-current-draft",
    )
    second = service.start_run(
        "agentv2_replay",
        runtime_input,
        _requirement_sheet(),
        idempotency_key="confirm-current-draft",
    )

    assert first.runtime_run_id == "rtrun_1"
    assert second.runtime_run_id == "rtrun_1"
    assert _runtime_run_count(store) == 1


def test_runtime_service_start_run_preserves_explicit_draft_lineage_and_selected_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = WorkbenchV2RuntimeService(
        store=store,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        approved_requirement_revision_id_factory=lambda: "reqapproved_1",
        runtime_run_id_factory=lambda: "rtrun_1",
        now=lambda: NOW,
    )

    service.start_run(
        "agentv2_real_draft",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Python 和 Agent 工作流经验", notes=None),
        _requirement_sheet(),
        draft_revision_id="reqdraft_real",
        selected_item_ids=["sql"],
        deselected_item_ids=["java"],
    )

    approved = store.get_approved_requirement("reqapproved_1")
    assert approved.draft_revision_id == "reqdraft_real"
    assert approved.selected_item_ids == ["sql"]
    assert approved.deselected_item_ids == ["java"]


def test_runtime_service_start_run_from_runtime_input_extracts_sheet_and_enqueues(tmp_path: Path) -> None:
    store = _store(tmp_path)
    extractor = RecordingRequirementExtractor(_requirement_sheet())
    service = WorkbenchV2RuntimeService(
        store=store,
        requirement_extractor=extractor,
        runtime_factory=lambda: extractor,
        approved_requirement_revision_id_factory=lambda: "reqapproved_1",
        runtime_run_id_factory=lambda: "rtrun_1",
        now=lambda: NOW,
    )

    run = service.start_run_from_runtime_input(
        "agentv2_real_draft",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Python 和 Agent 工作流经验", notes="杭州"),
        idempotency_key="confirm-current-draft",
        draft_revision_id="reqdraft_real",
        selected_item_ids=["sql"],
        deselected_item_ids=["java"],
    )

    assert extractor.calls == [
        {
            "job_title": "AI 平台工程师",
            "jd_text": "需要 Python 和 Agent 工作流经验",
            "notes": "杭州",
            "requirement_cache_scope": "agentv2_real_draft",
        }
    ]
    assert run.runtime_run_id == "rtrun_1"
    approved = store.get_approved_requirement("reqapproved_1")
    assert approved.draft_revision_id == "reqdraft_real"
    assert approved.selected_item_ids == ["sql"]
    assert approved.deselected_item_ids == ["java"]


def test_runtime_service_module_does_not_import_ui_or_tests() -> None:
    source = inspect.getsource(runtime_service_module)

    assert "seektalent_ui" not in source
    assert "tests." not in source


def test_runtime_service_missing_extractor_raises(tmp_path: Path) -> None:
    service = _service(tmp_path, runtime_factory=lambda: object())

    with pytest.raises(RuntimeError, match="^workbench_v2_requirement_extractor_unavailable$"):
        service.extract_requirements(
            "agentv2_1",
            WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        )


def test_runtime_service_get_status_maps_queued_to_readable_summary(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )

    assert service.get_status(run.runtime_run_id) == {
        "runtimeRunId": "rtrun_1",
        "status": "queued",
        "stage": "queued",
        "summary": "招聘流程已排队，等待开始。",
    }


def test_runtime_service_get_status_includes_current_stage_in_running_summary(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="starting",
        current_stage="startup",
        updated_at=NOW,
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="running",
        current_stage="round",
        updated_at=NOW,
    )

    assert service.get_status(run.runtime_run_id) == {
        "runtimeRunId": "rtrun_1",
        "status": "running",
        "stage": "round",
        "summary": "招聘流程运行中，当前阶段：检索轮次。",
    }


def test_runtime_service_lists_public_progress_events_as_user_readable_payloads(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="starting",
        current_stage="startup",
        updated_at=NOW,
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="running",
        current_stage="source_dispatch",
        updated_at=NOW,
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_internal_checkpoint",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_checkpoint_written",
            stage="round",
            status="completed",
            summary="checkpoint written",
            payload={},
            visibility="internal",
            created_at=NOW,
        )
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_query_round_1",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_round_query_ready",
            stage="round_query",
            round_no=1,
            status="completed",
            summary="round_query",
            payload={
                "details": {
                    "keywordQuery": "legacy keyword must not render",
                    "queryTerms": ["legacy"],
                    "queryGroups": [
                        {
                            "queryInstanceId": "query-1",
                            "termGroupKey": "group-1",
                            "queryRole": "exploit",
                            "laneType": "exploit",
                            "queryTerms": ["数据科学家", "SQL"],
                            "keywordQuery": "数据科学家 SQL",
                            "lifecycle": "planned",
                            "executionStatus": "failed",
                            "attempted": True,
                            "rawCandidateCount": 99,
                            "uniqueCandidateCount": 99,
                            "duplicateCandidateCount": 99,
                            "executions": [{"sourceKind": "cts", "status": "failed"}],
                        }
                    ],
                }
            },
            visibility="public",
            created_at=NOW,
        )
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_source_result_round_1",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_round_source_result",
            stage="source_result",
            round_no=1,
            source_id="liepin",
            status="completed",
            summary="source_result",
            payload={"counts": {"roundReturned": 9, "roundIdentities": 3}},
            visibility="public",
            created_at=NOW,
        )
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_finalization_completed",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_finalization_completed",
            stage="finalization",
            status="completed",
            summary="Selected 10 final candidates by deterministic runtime ranking.",
            payload={},
            visibility="public",
            created_at=NOW,
        )
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_run_completed",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_run_completed",
            stage="completed",
            status="completed",
            summary="Run completed after 7 retrieval rounds; controller stopped in round 8.",
            payload={},
            visibility="public",
            created_at=NOW,
        )
    )

    progress = service.list_progress_events(run.runtime_run_id, after_seq=0)

    assert [event["runtimeEventType"] for event in progress] == [
        "runtime_round_query_ready",
        "runtime_round_source_result",
        "runtime_finalization_completed",
        "runtime_run_completed",
    ]
    assert progress[0]["summary"] == "第 1 轮查询策略已生成。"
    assert progress[0]["details"] == {
        "queryGroups": [
            {
                "queryInstanceId": "query-1",
                "termGroupKey": "group-1",
                "queryRole": "exploit",
                "laneType": "exploit",
                "queryTerms": ["数据科学家", "SQL"],
                "keywordQuery": "数据科学家 SQL",
                "lifecycle": "planned",
                "executionStatus": None,
                "attempted": False,
                "rawCandidateCount": 0,
                "uniqueCandidateCount": 0,
                "duplicateCandidateCount": 0,
                "executions": [],
            }
        ]
    }
    assert progress[1]["summary"] == "第 1 轮猎聘检索完成：返回 9 条，新增 3 位候选人。"
    assert progress[1]["counts"] == {"roundReturned": 9, "roundIdentities": 3}
    assert progress[2]["summary"] == "最终短名单已生成。"
    assert progress[3]["summary"] == "招聘流程已完成。"
    assert progress[0]["state"] == "completed"


@pytest.mark.parametrize(
    ("reason_code", "expected_safe_reason_code", "expected_summary_reason"),
    [
        (
            "blocked_backend_unavailable",
            "source_browser_backend_unavailable",
            "source_browser_backend_unavailable",
        ),
        ("Bearer private-token", None, "猎聘检索受阻，请稍后重试。"),
        ("unknown_private_reason", None, "猎聘检索受阻，请稍后重试。"),
    ],
)
def test_runtime_service_source_result_summary_uses_only_canonical_safe_reason(
    tmp_path: Path,
    reason_code: str,
    expected_safe_reason_code: str | None,
    expected_summary_reason: str,
) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_source_result_blocked",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_round_source_result",
            stage="source_result",
            round_no=1,
            source_id="liepin",
            status="blocked",
            summary="Bearer event-summary-private",
            payload={
                "schemaVersion": "runtime_public_event_v1",
                "runtimeRunId": run.runtime_run_id,
                "eventId": f"{run.runtime_run_id}:1:source_result:liepin",
                "eventSeq": 1,
                "stage": "source_result",
                "roundNo": 1,
                "sourceKind": "liepin",
                "status": "blocked",
                "counts": {"roundReturned": 0, "roundIdentities": 0},
                "details": {},
                "safeReasonCode": reason_code,
                "createdAt": NOW,
            },
            visibility="public",
            created_at=NOW,
        )
    )

    [progress] = service.list_progress_events(run.runtime_run_id, after_seq=0)
    serialized = json.dumps(progress, ensure_ascii=False)

    assert progress.get("safeReasonCode") == expected_safe_reason_code
    assert progress["summary"] == f"第 1 轮猎聘检索受阻：{expected_summary_reason}"
    assert reason_code not in serialized
    assert "Bearer event-summary-private" not in serialized


def test_runtime_service_ignores_developer_visibility_events_for_progress_and_status(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="starting",
        current_stage="startup",
        updated_at=NOW,
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="running",
        current_stage="round",
        updated_at=NOW,
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_developer_progress",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_search_started",
            stage="round",
            round_no=1,
            status="running",
            summary="INTERNAL_DEVELOPER_SUMMARY_SHOULD_NOT_RENDER",
            payload={"details": {"keywordQuery": "INTERNAL_DEVELOPER_QUERY_SHOULD_NOT_RENDER"}},
            visibility="developer",
            created_at=NOW,
        )
    )

    assert service.list_progress_events(run.runtime_run_id, after_seq=0) == []
    assert service.get_status(run.runtime_run_id) == {
        "runtimeRunId": "rtrun_1",
        "status": "running",
        "stage": "round",
        "summary": "招聘流程运行中，当前阶段：检索轮次。",
    }


@pytest.mark.parametrize(
    ("status", "expected_summary"),
    [
        ("completed", "招聘流程已完成。"),
        ("failed", "招聘流程失败，请查看运行详情。"),
        ("cancelled", "招聘流程已取消。"),
    ],
)
def test_runtime_service_get_status_maps_terminal_status_to_chinese_summary(
    tmp_path: Path,
    status: str,
    expected_summary: str,
) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    if status == "completed":
        service.store.update_run_status(
            runtime_run_id=run.runtime_run_id,
            status="starting",
            current_stage="startup",
            updated_at=NOW,
        )
        service.store.update_run_status(
            runtime_run_id=run.runtime_run_id,
            status="running",
            current_stage="round",
            updated_at=NOW,
        )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status=status,
        current_stage="finalization",
        updated_at=NOW,
    )

    assert service.get_status(run.runtime_run_id)["summary"] == expected_summary


def test_runtime_service_get_status_uses_latest_public_failure_summary(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_runtime_search_failed",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_search_failed",
            stage="source",
            status="failed",
            summary="source_browser_backend_unavailable",
            payload={"reasonCode": "source_browser_backend_unavailable"},
            visibility="public",
            created_at=NOW,
        )
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="failed",
        current_stage="source",
        updated_at=NOW,
    )

    assert service.get_status(run.runtime_run_id)["summary"] == "本轮检索失败：source_browser_backend_unavailable"


def test_runtime_service_distinguishes_search_and_run_failure_summaries(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    for seq, event_type in enumerate(["runtime_search_failed", "runtime_run_failed"], start=1):
        service.store.append_event(
            RuntimeControlEventInput(
                event_id=f"evt_{event_type}",
                runtime_run_id=run.runtime_run_id,
                event_type=event_type,
                stage="source_lanes",
                round_no=1,
                status="failed",
                summary="source_browser_backend_unavailable",
                payload={"reasonCode": "source_browser_backend_unavailable"},
                visibility="public",
                created_at=f"{NOW}-{seq}",
            )
        )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="failed",
        current_stage="source_lanes",
        updated_at=NOW,
    )

    progress = service.list_progress_events(run.runtime_run_id, after_seq=0)

    failure_summaries = [
        event["summary"]
        for event in progress
        if event["runtimeEventType"] in {"runtime_search_failed", "runtime_run_failed"}
    ]
    assert failure_summaries == [
        "第 1 轮检索失败：source_browser_backend_unavailable",
        "招聘流程失败：source_browser_backend_unavailable",
    ]


@pytest.mark.parametrize(
    ("event_type", "expected_summary"),
    [
        ("runtime_search_failed", "第 1 轮检索失败：运行失败，请查看详情。"),
        ("runtime_run_failed", "招聘流程失败：运行失败，请查看详情。"),
    ],
)
def test_runtime_service_failure_progress_drops_raw_event_failure_text(
    tmp_path: Path,
    event_type: str,
    expected_summary: str,
) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    raw_summary = "OpenCLI CDP target 98b37a browser session failed"
    raw_reason = "private_provider_reason"
    raw_error = "browser_target=98b37a"
    service.store.append_event(
        RuntimeControlEventInput(
            event_id=f"evt_{event_type}",
            runtime_run_id=run.runtime_run_id,
            event_type=event_type,
            stage="source_lanes",
            round_no=1,
            status="failed",
            summary=raw_summary,
            payload={"reasonCode": raw_reason, "errorCode": raw_error},
            visibility="public",
            created_at=NOW,
        )
    )
    service.store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="failed",
        current_stage="source_lanes",
        updated_at=NOW,
    )

    [progress] = service.list_progress_events(run.runtime_run_id, after_seq=0)
    status = service.get_status(run.runtime_run_id)
    serialized = json.dumps({"progress": progress, "status": status}, ensure_ascii=False)

    assert progress["summary"] == expected_summary
    assert status["summary"] == expected_summary
    assert raw_summary not in serialized
    assert raw_reason not in serialized
    assert raw_error not in serialized


@pytest.mark.parametrize(
    ("event_type", "status", "expected_summary"),
    [
        ("runtime_search_started", "running", "第 1 轮开始检索候选人。"),
        ("runtime_search_completed", "completed", "第 1 轮检索完成。"),
        ("runtime_scoring_started", "running", "第 1 轮开始候选人评分。"),
        ("runtime_scoring_completed", "completed", "第 1 轮候选人评分完成。"),
        ("runtime_resume_quality_comment_completed", "completed", "第 1 轮简历质量评估完成。"),
        ("runtime_reflection_started", "running", "第 1 轮开始复盘检索效果。"),
        ("runtime_round_completed", "completed", "第 1 轮完成。"),
    ],
)
def test_runtime_service_progress_summary_ignores_arbitrary_public_event_summary(
    event_type: str,
    status: str,
    expected_summary: str,
) -> None:
    raw_summary = "OpenCLI CDP target 98b37a browser session failed"
    event = SimpleNamespace(
        event_type=event_type,
        stage="round",
        status=status,
        summary=raw_summary,
        payload={},
        round_no=1,
    )

    summary = runtime_service_module._runtime_event_user_summary(event)

    assert summary == expected_summary
    assert raw_summary not in summary


def test_runtime_service_status_labels_fail_closed_for_unknown_metadata() -> None:
    raw_status = "OpenCLI browser target 98b37a"
    raw_stage = "https://provider.example/private-stage"

    assert runtime_service_module._status_summary("running", raw_stage) == "招聘流程运行中，当前阶段：未标记。"
    assert runtime_service_module._status_summary(raw_status, raw_stage) == "招聘流程状态未知。"
    assert runtime_service_module._status_label(raw_status) == "未知状态"
    assert runtime_service_module._stage_label(raw_stage) == "未标记"
    assert runtime_service_module._runtime_event_summary("running", raw_stage) == "招聘流程运行中，当前阶段：未标记。"
    assert runtime_service_module._runtime_event_summary("failed", raw_stage) == "招聘流程失败，请查看运行详情。"


def test_runtime_service_public_progress_payload_fail_closes_unknown_metadata_and_requirement_title() -> None:
    internal_status = "INTERNAL_STATUS_SHOULD_NOT_RENDER"
    internal_stage = "https://provider.example/SHOULD_NOT_RENDER"
    internal_title = "INTERNAL_JOB_TITLE_SHOULD_NOT_RENDER"
    progress_event = SimpleNamespace(
        visibility="public",
        event_type="runtime_search_started",
        runtime_run_id="rtrun_1",
        event_seq=1,
        status=internal_status,
        stage=internal_stage,
        summary="ignored",
        payload={},
        round_no=1,
        source_id=None,
    )
    requirements_event = SimpleNamespace(
        event_type="runtime_requirements_completed",
        stage="requirements",
        status="completed",
        summary="ignored",
        payload={"job_title": internal_title},
        round_no=None,
    )

    progress = runtime_service_module._progress_payload_from_runtime_event(progress_event)
    requirements_summary = runtime_service_module._runtime_event_user_summary(requirements_event)
    serialized = json.dumps({"progress": progress, "summary": requirements_summary}, ensure_ascii=False)

    assert progress is not None
    assert progress["status"] == "running"
    assert progress["stage"] == "runtime"
    assert requirements_summary == "岗位需求解析完成。"
    assert internal_status not in serialized
    assert internal_stage not in serialized
    assert internal_title not in serialized


def test_runtime_service_status_fail_closes_unknown_run_metadata() -> None:
    internal_status = "INTERNAL_STATUS_SHOULD_NOT_RENDER"
    internal_stage = "https://provider.example/SHOULD_NOT_RENDER"
    service = WorkbenchV2RuntimeService(
        store=SimpleNamespace(
            get_run=lambda _runtime_run_id: SimpleNamespace(
                runtime_run_id="rtrun_1",
                status=internal_status,
                current_stage=internal_stage,
                latest_event_seq=0,
            )
        )
    )

    status = service.get_status("rtrun_1")
    serialized = json.dumps(status, ensure_ascii=False)

    assert status["status"] == "running"
    assert status["stage"] == "runtime"
    assert status["summary"] == "招聘流程状态未知。"
    assert internal_status not in serialized
    assert internal_stage not in serialized


def test_runtime_service_maps_public_browser_extension_disconnect_reason(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        runtime_run_id_factory=lambda: "rtrun_1",
    )
    run = service.start_run(
        "agentv2_1",
        WorkbenchV2RuntimeInput(jobTitle="AI 平台工程师", jd="需要 Agent 系统经验", notes=None),
        _requirement_sheet(),
    )
    service.store.append_event(
        RuntimeControlEventInput(
            event_id="evt_runtime_run_failed",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_run_failed",
            stage="source_lanes",
            status="failed",
            summary="source_browser_extension_disconnected",
            payload={"reasonCode": "source_browser_extension_disconnected"},
            visibility="public",
            created_at=NOW,
        )
    )

    [event] = service.list_progress_events(run.runtime_run_id, after_seq=0)

    assert event["summary"] == "招聘流程失败：猎聘浏览器桥扩展未连接，请确认扩展已连接后重试。"


def _service(
    tmp_path: Path,
    *,
    requirement_extractor: object | None = None,
    runtime_factory: object | None = None,
    runtime_run_id_factory: object | None = None,
) -> WorkbenchV2RuntimeService:
    return WorkbenchV2RuntimeService(
        store=_store(tmp_path),
        requirement_extractor=requirement_extractor,
        runtime_factory=runtime_factory,
        draft_revision_id_factory=lambda: "reqdraft_1",
        approved_requirement_revision_id_factory=lambda: "reqapproved_1",
        runtime_run_id_factory=runtime_run_id_factory,
        now=lambda: NOW,
    )


def _store(tmp_path: Path) -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    return store


def _runtime_run_count(store: RuntimeControlStore) -> int:
    with sqlite3.connect(store.path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0]
    return int(count)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI 平台工程师",
        title_anchor_terms=["AI 平台工程师"],
        title_anchor_rationale="The job title names the platform role.",
        role_summary="Build AI agent platform systems.",
        must_have_capabilities=["Python 后端开发", "Agent 工作流经验"],
        preferred_capabilities=["RAG 经验"],
        exclusion_signals=["没有生产系统经验"],
        hard_constraints=HardConstraintSlots(locations=["杭州"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="AI 平台工程师",
                source="job_title",
                category="role_anchor",
                priority=100,
                evidence="岗位名称",
                first_added_round=0,
            )
        ],
        scoring_rationale="Prioritize platform engineering and agent workflow experience.",
    )
