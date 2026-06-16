from __future__ import annotations

import json
from pathlib import Path

from seektalent.models import RequirementSheet
from seektalent_ui.workbench_store import WorkbenchStore

def _sheet(job_title: str = "AI Agent Engineer") -> RequirementSheet:
    return RequirementSheet(
        job_title=job_title,
        title_anchor_terms=["AI Agent"],
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agent workflow and retrieval systems.",
        must_have_capabilities=["LangGraph", "RAG"],
        preferred_capabilities=["evaluation"],
        exclusion_signals=["pure frontend"],
        hard_constraints={},
        preferences={"preferred_query_terms": ["LangGraph", "RAG"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize agent workflow depth and retrieval evidence.",
    )


def _store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / ".seektalent" / "workbench.sqlite3")


def _user(store: WorkbenchStore):
    return store.ensure_local_actor()


def test_workbench_requirement_review_stores_requirement_sheet(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="AI Agent Engineer",
        jd_text="Build LangGraph and RAG systems.",
        notes="Prefer evaluation experience.",
        source_kinds=["cts"],
    )
    sheet = _sheet()

    review = store.update_requirement_review(
        user=user,
        session_id=session.session_id,
        requirement_sheet=sheet,
    )

    assert review is not None
    assert review.requirement_sheet == sheet
    assert review.requirement_sheet.job_title == "AI Agent Engineer"
    payload = json.dumps(review.requirement_sheet.model_dump(mode="json"))
    assert "must_have_capabilities" in payload
    assert "preferred_capabilities" in payload
    assert "exclusion_signals" in payload
    assert "niceToHaves" not in payload
    assert "generatedQueryHints" not in payload


def test_workbench_requirement_review_rejects_job_title_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = _user(store)
    session = store.create_workbench_session(
        user=user,
        job_title="AI Agent Engineer",
        jd_text="Build LangGraph and RAG systems.",
        notes="",
        source_kinds=["cts"],
    )

    try:
        store.update_requirement_review(
            user=user,
            session_id=session.session_id,
            requirement_sheet=_sheet(job_title="Backend Engineer"),
        )
    except ValueError as exc:
        assert str(exc) == "requirement_sheet_job_title_mismatch"
    else:
        raise AssertionError("expected requirement_sheet_job_title_mismatch")
