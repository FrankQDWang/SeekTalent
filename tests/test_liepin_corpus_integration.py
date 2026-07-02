from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace

import pytest

from seektalent.artifacts import ArtifactStore
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.corpus.runtime import ProviderReturnedCandidate, materialize_corpus_artifacts, record_corpus_provider_results
from seektalent.corpus.store import CorpusStore
from seektalent.providers.liepin.mapper import map_liepin_worker_card, map_liepin_worker_detail
from seektalent.providers.liepin.worker_contracts import LiepinWorkerCandidateCard, LiepinWorkerCandidateDetail
from seektalent.runtime.retrieval_runtime import _validated_provider_snapshots_for_candidates


def _worker_card() -> LiepinWorkerCandidateCard:
    return LiepinWorkerCandidateCard(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "privateCardNote": "Private card raw payload",
            "phone": "13800000000",
            "email": "candidate@example.com",
        },
        normalized_text="Python backend engineer card",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="same-person",
        identity_confidence="provider_subject_id",
        extraction_source="network",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_possible",
        retention_policy="provider_snapshot_30d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _worker_detail() -> LiepinWorkerCandidateDetail:
    return LiepinWorkerCandidateDetail(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "currentTitle": "Python backend engineer",
            "currentCompany": "Structured Corpus Co",
            "workExperienceList": [
                {
                    "company": "Structured Corpus Co",
                    "title": "Python backend engineer",
                    "summary": "Built corpus ingestion services",
                }
            ],
            "projectExperienceList": [
                {"name": "Corpus Pipeline", "role": "Backend engineer", "summary": "Improved artifact export"}
            ],
            "educationList": [{"school": "Corpus University", "degree": "Bachelor"}],
            "skills": ["Python", "SQLite"],
            "privateDetailHtml": "<html>Private Liepin detail body</html>",
            "privateDetailNote": "Private detail raw payload",
            "phone": "13800000000",
            "email": "candidate@example.com",
        },
        normalized_text="Python backend engineer detail",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="same-person",
        identity_confidence="provider_subject_id",
        extraction_source="dom_fallback",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_present",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _record(
    *,
    tmp_path: Path,
    returned_candidates: list[ProviderReturnedCandidate],
) -> tuple[CorpusStore, object]:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    session = ArtifactStore(tmp_path / "artifacts").create_root(
        kind="corpus",
        display_name="corpus ingest",
        producer="CorpusRuntime",
    )
    record_corpus_provider_results(
        session=session,
        store=store,
        run_id="run-1",
        tenant_id="tenant-a",
        workspace_id="workspace",
        returned_candidates=returned_candidates,
    )
    return store, session


def _returned_candidate(mapped, *, query_instance_id: str, request_id: str) -> ProviderReturnedCandidate:
    return ProviderReturnedCandidate(
        candidate=mapped.candidate,
        provider_snapshot=mapped.provider_snapshot,
        stage_id="retrieval",
        round_no=1,
        query_instance_id=query_instance_id,
        query_fingerprint=f"fingerprint-{query_instance_id}",
        provider_name="liepin",
        provider_request_id=request_id,
        provider_rank=1,
        provider_page_no=1,
        provider_fetch_no=1,
        attempt_no=1,
    )


def test_liepin_corpus_writes_raw_payload_from_provider_snapshot_not_candidate_raw(tmp_path: Path) -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")

    store, session = _record(
        tmp_path=tmp_path,
        returned_candidates=[_returned_candidate(mapped, query_instance_id="query-1", request_id="request-1")],
    )

    document = store.rows_for_tenant("resume_documents", "tenant-a", "workspace")[0]
    artifact = store.connect().execute(
        "SELECT relative_path FROM artifact_refs WHERE artifact_ref_id = ?",
        (document["raw_payload_artifact_ref_id"],),
    ).fetchone()
    payload = json.loads((session.root / artifact["relative_path"]).read_text(encoding="utf-8"))

    assert payload == mapped.provider_snapshot.raw_payload
    assert payload != mapped.candidate.raw
    assert document["raw_payload_artifact_ref_id"] is not None


def test_liepin_snapshots_store_privacy_metadata_in_sensitivity_json_without_new_columns(tmp_path: Path) -> None:
    mapped = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")

    store, _session = _record(
        tmp_path=tmp_path,
        returned_candidates=[_returned_candidate(mapped, query_instance_id="query-1", request_id="request-1")],
    )

    document = store.rows_for_tenant("resume_documents", "tenant-a", "workspace")[0]
    sensitivity = document["sensitivity_json"]
    if isinstance(sensitivity, str):
        sensitivity = json.loads(sensitivity)
    columns = {row["name"] for row in store.connect().execute("PRAGMA table_info(resume_documents)").fetchall()}

    assert sensitivity["liepin_snapshot"] == {
        "pii_classification": "direct_contact_present",
        "retention_policy": "provider_snapshot_7d",
        "access_scope": "local_run_only",
        "redaction_state": "raw_provider_payload",
    }
    assert document["retention_policy"] == "provider_snapshot_7d"
    assert document["content_trust_level"] == "untrusted_external"
    assert document["llm_ingestion_policy"] == "quote_as_data_only"
    assert {
        "pii_classification",
        "access_scope",
        "redaction_state",
        "liepin_snapshot_json",
    }.isdisjoint(columns)


def test_liepin_card_snapshots_store_privacy_metadata_in_sensitivity_json(tmp_path: Path) -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")

    store, _session = _record(
        tmp_path=tmp_path,
        returned_candidates=[_returned_candidate(mapped, query_instance_id="query-1", request_id="request-1")],
    )

    document = store.rows_for_tenant("resume_documents", "tenant-a", "workspace")[0]
    sensitivity = document["sensitivity_json"]
    if isinstance(sensitivity, str):
        sensitivity = json.loads(sensitivity)

    assert sensitivity["liepin_snapshot"] == {
        "pii_classification": "direct_contact_possible",
        "retention_policy": "provider_snapshot_30d",
        "access_scope": "local_run_only",
        "redaction_state": "raw_provider_payload",
    }
    assert document["retention_policy"] == "provider_snapshot_30d"


def test_liepin_raw_payload_is_omitted_from_materialized_export(tmp_path: Path) -> None:
    mapped = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")
    store, _ingest_session = _record(
        tmp_path=tmp_path,
        returned_candidates=[_returned_candidate(mapped, query_instance_id="query-1", request_id="request-1")],
    )
    export_session = ArtifactStore(tmp_path / "export").create_root(
        kind="corpus",
        display_name="corpus export",
        producer="CorpusRuntime",
    )

    materialize_corpus_artifacts(
        session=export_session,
        store=store,
        tenant_id="tenant-a",
        workspace_id="workspace",
    )

    exported_rows = (export_session.root / "corpus/resume_documents.jsonl").read_text(encoding="utf-8").splitlines()
    exported_document = json.loads(exported_rows[0])

    assert "Private detail raw payload" not in exported_rows[0]
    assert "Private Liepin detail body" not in exported_rows[0]
    assert exported_document["raw_payload_json"] is None
    assert exported_document["raw_payload_artifact_ref_id"] is not None


def test_duplicate_liepin_provider_returns_create_one_document_and_multiple_observations(tmp_path: Path) -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")

    store, _session = _record(
        tmp_path=tmp_path,
        returned_candidates=[
            _returned_candidate(mapped, query_instance_id="query-1", request_id="request-1"),
            _returned_candidate(mapped, query_instance_id="query-2", request_id="request-2"),
        ],
    )

    assert len(store.rows_for_tenant("resume_documents", "tenant-a", "workspace")) == 1
    assert len(store.rows_for_tenant("resume_observations", "tenant-a", "workspace")) == 2


def test_liepin_provider_results_require_provider_snapshots(tmp_path: Path) -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")

    with pytest.raises(ValueError, match="Liepin provider results require ProviderSnapshot"):
        _record(
            tmp_path=tmp_path,
            returned_candidates=[
                ProviderReturnedCandidate(
                    candidate=mapped.candidate,
                    stage_id="retrieval",
                    round_no=1,
                    query_instance_id="query-1",
                    query_fingerprint="fingerprint-1",
                    provider_name="liepin",
                    provider_request_id="request-1",
                    provider_rank=1,
                    provider_page_no=1,
                    provider_fetch_no=1,
                    attempt_no=1,
                )
            ],
        )

    assert not list((tmp_path / "artifacts").glob("**/raw_payloads/*.json"))


def test_liepin_provider_results_reject_provider_snapshot_mismatch_before_artifact_write(tmp_path: Path) -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    mismatched = replace(mapped.provider_snapshot, provider_name="cts")

    with pytest.raises(ValueError, match="provider mismatch"):
        _record(
            tmp_path=tmp_path,
            returned_candidates=[
                ProviderReturnedCandidate(
                    candidate=mapped.candidate,
                    provider_snapshot=mismatched,
                    stage_id="retrieval",
                    round_no=1,
                    query_instance_id="query-1",
                    query_fingerprint="fingerprint-1",
                    provider_name="liepin",
                    provider_request_id="request-1",
                    provider_rank=1,
                    provider_page_no=1,
                    provider_fetch_no=1,
                    attempt_no=1,
                )
            ],
        )

    assert not list((tmp_path / "artifacts").glob("**/raw_payloads/*.json"))


def test_liepin_provider_results_reject_fingerprint_mismatch_before_artifact_write(tmp_path: Path) -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    mismatched = replace(mapped.provider_snapshot, synthetic_candidate_fingerprint="other-person")

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        _record(
            tmp_path=tmp_path,
            returned_candidates=[
                ProviderReturnedCandidate(
                    candidate=mapped.candidate,
                    provider_snapshot=mismatched,
                    stage_id="retrieval",
                    round_no=1,
                    query_instance_id="query-1",
                    query_fingerprint="fingerprint-1",
                    provider_name="liepin",
                    provider_request_id="request-1",
                    provider_rank=1,
                    provider_page_no=1,
                    provider_fetch_no=1,
                    attempt_no=1,
                )
            ],
        )

    assert not list((tmp_path / "artifacts").glob("**/raw_payloads/*.json"))


def test_liepin_provider_results_reject_same_fingerprint_payload_hash_mismatch_before_artifact_write(
    tmp_path: Path,
) -> None:
    card = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    detail = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")

    with pytest.raises(ValueError, match="payload hash mismatch"):
        _record(
            tmp_path=tmp_path,
            returned_candidates=[
                ProviderReturnedCandidate(
                    candidate=card.candidate,
                    provider_snapshot=detail.provider_snapshot,
                    stage_id="retrieval",
                    round_no=1,
                    query_instance_id="query-1",
                    query_fingerprint="fingerprint-1",
                    provider_name="liepin",
                    provider_request_id="request-1",
                    provider_rank=1,
                    provider_page_no=1,
                    provider_fetch_no=1,
                    attempt_no=1,
                )
            ],
        )

    assert not list((tmp_path / "artifacts").glob("**/raw_payloads/*.json"))


def test_liepin_provider_results_reject_empty_candidate_snapshot_hash_before_artifact_write(
    tmp_path: Path,
) -> None:
    card = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    detail = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")
    empty_hash_candidate = card.candidate.model_copy(update={"snapshot_sha256": ""})

    with pytest.raises(ValueError, match="snapshot hash"):
        _record(
            tmp_path=tmp_path,
            returned_candidates=[
                ProviderReturnedCandidate(
                    candidate=empty_hash_candidate,
                    provider_snapshot=detail.provider_snapshot,
                    stage_id="retrieval",
                    round_no=1,
                    query_instance_id="query-1",
                    query_fingerprint="fingerprint-1",
                    provider_name="liepin",
                    provider_request_id="request-1",
                    provider_rank=1,
                    provider_page_no=1,
                    provider_fetch_no=1,
                    attempt_no=1,
                )
            ],
        )

    assert not list((tmp_path / "artifacts").glob("**/raw_payloads/*.json"))


def test_liepin_retrieval_runtime_rejects_short_provider_snapshot_batch() -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    result = SearchResult(candidates=[mapped.candidate], provider_snapshots=[])

    with pytest.raises(ValueError, match="provider snapshot count"):
        _validated_provider_snapshots_for_candidates(result, provider_name="liepin", require_snapshots=True)


def test_liepin_retrieval_runtime_rejects_mismatched_provider_snapshot_batch() -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    mismatched = replace(mapped.provider_snapshot, synthetic_candidate_fingerprint="other-person")
    result = SearchResult(candidates=[mapped.candidate], provider_snapshots=[mismatched])

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        _validated_provider_snapshots_for_candidates(result, provider_name="liepin", require_snapshots=True)


def test_liepin_retrieval_runtime_rejects_same_fingerprint_payload_hash_mismatch() -> None:
    card = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    detail = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")
    result = SearchResult(candidates=[card.candidate], provider_snapshots=[detail.provider_snapshot])

    with pytest.raises(ValueError, match="payload hash mismatch"):
        _validated_provider_snapshots_for_candidates(result, provider_name="liepin", require_snapshots=True)


def test_liepin_retrieval_runtime_rejects_empty_candidate_snapshot_hash() -> None:
    card = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    detail = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")
    empty_hash_candidate = card.candidate.model_copy(update={"snapshot_sha256": ""})
    result = SearchResult(candidates=[empty_hash_candidate], provider_snapshots=[detail.provider_snapshot])

    with pytest.raises(ValueError, match="snapshot hash"):
        _validated_provider_snapshots_for_candidates(result, provider_name="liepin", require_snapshots=True)


def test_search_result_provider_snapshots_can_be_passed_to_corpus_runtime(tmp_path: Path) -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    result = SearchResult(candidates=[mapped.candidate], provider_snapshots=[mapped.provider_snapshot])

    store, _session = _record(
        tmp_path=tmp_path,
        returned_candidates=[
            ProviderReturnedCandidate(
                candidate=result.candidates[0],
                provider_snapshot=result.provider_snapshots[0],
                stage_id="retrieval",
                round_no=1,
                query_instance_id="query-1",
                query_fingerprint="fingerprint-1",
                provider_name="liepin",
                provider_request_id="request-1",
                provider_rank=1,
                provider_page_no=1,
                provider_fetch_no=1,
                attempt_no=1,
            )
        ],
    )

    assert len(store.rows_for_tenant("resume_documents", "tenant-a", "workspace")) == 1
