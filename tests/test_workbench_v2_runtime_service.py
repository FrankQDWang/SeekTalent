from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from seektalent.models import HardConstraintSlots, QueryTermCandidate, RequirementSheet
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
    item_sources = [
        item.source
        for section in draft.sections
        for item in section.items
    ]
    assert item_sources
    assert set(item_sources) == {"workbench_v2_agent"}


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
    service = WorkbenchV2RuntimeService(
        store=store,
        runtime_factory=lambda: RecordingRequirementExtractor(_requirement_sheet()),
        draft_revision_id_factory=lambda: "reqdraft_start_1",
        approved_requirement_revision_id_factory=lambda: "reqapproved_1",
        runtime_run_id_factory=lambda: "rtrun_1",
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
