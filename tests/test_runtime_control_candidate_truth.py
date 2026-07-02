from __future__ import annotations

import json
from pathlib import Path


def test_checkpoint_persists_compact_candidate_truth_without_artifacts(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_candidates",
        executor_id="executor_1",
        acquired_at="2026-06-17T00:00:00.000000Z",
        lease_expires_at="2026-06-17T00:01:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_candidates",
            runtime_run_id="runtime_run_candidates",
            stage="finalization",
            round_no=None,
            safe_boundary="runtime_candidate_checkpoint",
            run_state=_run_state_payload(),
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-17T00:00:10.000000Z",
        ),
        executor_id="executor_1",
        attempt_no=lease.attempt_no,
    )

    identities = store.list_candidate_identities(runtime_run_id="runtime_run_candidates")
    evidence = store.list_candidate_evidence(runtime_run_id="runtime_run_candidates")
    revisions = store.list_candidate_finalization_revisions(runtime_run_id="runtime_run_candidates")

    assert [(item.identity_id, item.canonical_resume_id, item.score) for item in identities] == [
        ("identity_1", "resume_1", 92)
    ]
    assert identities[0].display_name == "Alice Chen"
    assert identities[0].source_evidence_ids == ["evidence_1"]
    assert [(item.evidence_id, item.identity_id, item.source_kind) for item in evidence] == [
        ("evidence_1", "identity_1", "cts")
    ]
    assert [(item.revision, item.candidate_identity_ids) for item in revisions] == [(1, ["identity_1"])]
    assert revisions[0].source_checkpoint_id == "rtcheckpoint_candidates"


def test_candidate_truth_projects_scorecard_match_fields() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    scorecard = run_state["scorecards_by_resume_id"]["resume_1"]
    assert isinstance(scorecard, dict)
    scorecard["strengths"] = ["候选人强项：有复杂推荐系统经验"]
    scorecard["weaknesses"] = ["候选人弱项：缺少本地招聘行业经验"]

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    match = truth.evidence[0].payload["match"]
    assert match == {
        "score": 92,
        "fitBucket": "fit",
        "reasoningSummary": "Strong platform engineering match.",
        "strengths": ["候选人强项：有复杂推荐系统经验"],
        "weaknesses": ["候选人弱项：缺少本地招聘行业经验"],
        "sourceRound": 1,
    }


def test_candidate_truth_safe_detail_uses_field_whitelist() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    candidate_store = run_state["candidate_store"]
    assert isinstance(candidate_store, dict)
    resume = candidate_store["resume_1"]
    assert isinstance(resume, dict)
    resume["raw"] = {
        "candidate_name": "Alice Chen",
        "fullText": "https://h.liepin.com/resume/showresumedetail\n新手任务\n页面导航",
        "workExperienceList": [
            {
                "company": "Data Co",
                "title": "Staff Engineer",
                "summary": "Built ranking systems.",
                "browserUrl": "https://h.liepin.com/resume/showresumedetail",
                "pageChrome": "新手任务",
            }
        ],
        "educationList": [
            {
                "school": "浙江大学",
                "degree": "本科",
                "pageFooter": "ICP备案信息",
            }
        ],
    }

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    safe_detail = truth.evidence[0].payload["safeDetail"]
    assert safe_detail == {
        "candidateName": "Alice Chen",
        "workExperienceList": [
            {
                "company": "Data Co",
                "title": "Staff Engineer",
                "summary": "Built ranking systems.",
            }
        ],
        "educationList": [
            {
                "school": "浙江大学",
                "degree": "本科",
            }
        ],
    }


def test_candidate_truth_projects_wts_fields_from_structured_liepin_detail_payload() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    candidate_store = run_state["candidate_store"]
    assert isinstance(candidate_store, dict)
    resume = candidate_store["resume_1"]
    assert isinstance(resume, dict)
    resume["raw"] = {
        "candidate_name": "潘**",
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
            "expectedRole": "高端设计职位、设计经理/主管",
            "expectedIndustry": "互联网、其他",
            "expectedCity": "上海",
            "expectedSalary": "20-24k*14薪",
        },
        "workExperienceList": [
            {
                "company": "平安好医",
                "title": "用户体验设计专家",
                "dateRange": "2019.06-至今（7年）",
                "description": "提供B端及C端体验设计方案。",
            }
        ],
        "projectExperienceList": [
            {
                "name": "助力C端业务增长",
                "role": "-",
                "dateRange": "2020.05-至今（6年1个月）",
                "description": "通过设计调研提升转化率。",
            }
        ],
        "educationList": [
            {
                "school": "华东师范大学",
                "major": "工业设计",
                "degree": "硕士",
                "dateRange": "2011.09-2014.07（2年10个月）",
            }
        ],
        "skills": ["用户研究", "交互设计", "数据分析"],
    }
    run_state["normalized_store"] = {"resume_1": {}}

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    wts = truth.evidence[0].payload["wtsDetail"]
    assert wts["candidateName"] == "潘**"
    assert wts["activeStatus"] == "近30天内活跃"
    assert wts["jobStatus"] == "在职，看看新机会"
    assert wts["gender"] == "男"
    assert wts["age"] == 32
    assert wts["city"] == "上海"
    assert wts["education"] == "本科"
    assert wts["workYears"] == 10
    assert wts["currentTitle"] == "资深体验设计工程师"
    assert wts["currentCompany"] == "平安集团"
    assert wts["jobIntention"]["expectedCity"] == "上海"
    assert wts["workExperience"][0]["company"] == "平安好医"
    assert wts["projectExperience"][0]["name"] == "助力C端业务增长"
    assert wts["educationExperience"][0]["school"] == "华东师范大学"
    assert "交互设计" in wts["skills"]
    serialized_truth = json.dumps(
        {
            "identities": [item.model_dump(mode="json") for item in truth.identities],
            "evidence": [item.model_dump(mode="json") for item in truth.evidence],
        },
        ensure_ascii=False,
    )
    assert "平安好医" in serialized_truth
    assert "fullText" not in serialized_truth
    assert "page_text" not in serialized_truth


def test_candidate_truth_ignores_full_text_and_normalized_for_wts_detail_fields() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    candidate_store = run_state["candidate_store"]
    assert isinstance(candidate_store, dict)
    resume = candidate_store["resume_1"]
    assert isinstance(resume, dict)
    resume["raw"] = {"fullText": "provider page shell and resume prose are not a WTS detail field source"}
    run_state["normalized_store"] = {
        "resume_1": {
            "candidate_name": "Normalized Name",
            "current_title": "Normalized Title",
            "current_company": "Normalized Company",
        }
    }

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    wts = truth.evidence[0].payload["wtsDetail"]
    safe_detail = truth.evidence[0].payload["safeDetail"]
    assert wts == {}
    assert safe_detail == {}


def test_candidate_truth_ignores_unknown_full_text_for_wts_fields() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    candidate_store = run_state["candidate_store"]
    assert isinstance(candidate_store, dict)
    resume = candidate_store["resume_1"]
    assert isinstance(resume, dict)
    resume.clear()
    resume.update(
        {
            "resume_id": "resume_1",
            "raw": {
                "fullText": "系统提示\n上海本科用户增长\n在职页面展示",
            },
        }
    )
    run_state["normalized_store"] = {"resume_1": {}}

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    wts = truth.evidence[0].payload.get("wtsDetail")
    assert wts == {}


def test_candidate_truth_ignores_header_only_chrome_text_for_wts_fields() -> None:
    from seektalent_runtime_control.candidates import candidate_truth_from_run_state

    run_state = _run_state_payload()
    candidate_store = run_state["candidate_store"]
    assert isinstance(candidate_store, dict)
    resume = candidate_store["resume_1"]
    assert isinstance(resume, dict)
    resume.clear()
    resume.update(
        {
            "resume_id": "resume_1",
            "raw": {
                "fullText": "系统提示\n工作经历\n教育经历\n上海本科用户增长\n在职页面展示",
            },
        }
    )
    run_state["normalized_store"] = {"resume_1": {}}

    truth = candidate_truth_from_run_state(
        runtime_run_id="runtime_run_candidates",
        run_state=run_state,
        source_checkpoint_id="rtcheckpoint_candidates",
        observed_at="2026-06-17T00:00:10.000000Z",
    )

    wts = truth.evidence[0].payload.get("wtsDetail")
    assert wts == {}


def _create_run(store) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_candidates",
            run_intent_id="intent_candidates",
            start_idempotency_key="start_candidates",
            run_kind="primary",
            agent_conversation_id="agent_conv_candidates",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_candidates",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-17T00:00:00.000000Z",
            updated_at="2026-06-17T00:00:00.000000Z",
            completed_at=None,
        )
    )


def _run_state_payload() -> dict[str, object]:
    return {
        "candidate_identities": {
            "identity_1": {
                "identity_id": "identity_1",
                "canonical_identity_id": "identity_1",
                "resume_ids": ["resume_1"],
                "evidence_ids": ["evidence_1"],
            }
        },
        "candidate_identity_by_resume_id": {"resume_1": "identity_1"},
        "canonical_resume_by_identity_id": {
            "identity_1": {
                "identity_id": "identity_1",
                "canonical_resume_id": "resume_1",
            }
        },
        "source_evidence_by_identity_id": {
            "identity_1": [
                {
                    "evidence_id": "evidence_1",
                    "source": "cts",
                    "provider": "cts",
                    "evidence_level": "card",
                    "candidate_resume_id": "resume_1",
                    "provider_candidate_key_hash": "provider_hash_1",
                    "collected_at": "2026-06-17T00:00:02.000000Z",
                }
            ]
        },
        "candidate_store": {
            "resume_1": {
                "resume_id": "resume_1",
                "dedup_key": "dedup_1",
                "expected_job_category": "Staff Engineer",
                "now_location": "Shanghai",
                "search_text": "Distributed systems platform engineer",
                "raw": {"candidate_name": "Alice Chen"},
            }
        },
        "normalized_store": {
            "resume_1": {
                "candidate_name": "Alice Chen",
                "current_title": "Staff Engineer",
                "current_company": "Data Co",
                "locations": ["Shanghai"],
                "headline": "Platform engineering leader",
            }
        },
        "scorecards_by_resume_id": {
            "resume_1": {
                "overall_score": 92,
                "fit_bucket": "fit",
                "reasoning_summary": "Strong platform engineering match.",
                "source_round": 1,
            }
        },
        "finalization_revisions": [
            {
                "revision": 1,
                "runtime_run_id": "runtime_run_candidates",
                "reason_code": "runtime_finalized",
                "candidate_identity_ids": ["identity_1"],
                "coverage_summary": {"status": "complete"},
                "created_at": "2026-06-17T00:00:09.000000Z",
            }
        ],
    }
