import json
from pathlib import Path
from typing import Any

from seektalent.models import RequirementExtractionDraft, RequirementSheet
from seektalent.requirements import build_input_truth, normalize_requirement_draft


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "replay" / "requirements_flink_v1.json"
FORBIDDEN_OUTPUT_TOKENS = (
    "authorization",
    "cookie",
    "raw_provider_payload",
    "raw_resume",
)


def test_requirement_replay_fixture_is_deterministic() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    input_payload = fixture["input"]
    input_truth = build_input_truth(
        job_title=input_payload["job_title"],
        jd=input_payload["jd"],
        notes=input_payload["notes"],
    )
    draft = RequirementExtractionDraft.model_validate(fixture["requirement_extraction_draft"])

    sheet = normalize_requirement_draft(draft, job_title=input_truth.job_title)
    snapshot = _requirement_replay_snapshot(
        case_id=fixture["case_id"],
        source_artifact=fixture["source_artifact"],
        input_truth_hashes={
            "job_title_sha256": input_truth.job_title_sha256,
            "jd_sha256": input_truth.jd_sha256,
            "notes_sha256": input_truth.notes_sha256,
        },
        sheet=sheet,
    )

    assert snapshot == fixture["expected_snapshot"]
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    assert all(token not in serialized.casefold() for token in FORBIDDEN_OUTPUT_TOKENS)


def _requirement_replay_snapshot(
    *,
    case_id: str,
    source_artifact: str,
    input_truth_hashes: dict[str, str],
    sheet: RequirementSheet,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "source_artifact": source_artifact,
        "input_truth_hashes": input_truth_hashes,
        "requirement_sheet": {
            "job_title": sheet.job_title,
            "title_anchor_terms": sheet.title_anchor_terms,
            "role_summary": sheet.role_summary,
            "must_have_capabilities": sheet.must_have_capabilities,
            "preferred_capabilities": sheet.preferred_capabilities,
            "exclusion_signals": sheet.exclusion_signals,
            "hard_constraints": sheet.hard_constraints.model_dump(mode="json"),
            "preferences": sheet.preferences.model_dump(mode="json"),
            "initial_query_term_pool": [
                {
                    "term": item.term,
                    "source": item.source,
                    "category": item.category,
                    "priority": item.priority,
                    "active": item.active,
                    "retrieval_role": item.retrieval_role,
                    "queryability": item.queryability,
                    "family": item.family,
                }
                for item in sheet.initial_query_term_pool
            ],
            "scoring_rationale": sheet.scoring_rationale,
        },
    }
