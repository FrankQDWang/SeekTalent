def test_corpus_artifact_kind_exists(tmp_path):
    from seektalent.artifacts import ArtifactStore

    session = ArtifactStore(tmp_path).create_root(
        kind="corpus",
        display_name="preflight",
        producer="test",
    )
    assert session.manifest.artifact_kind.value == "corpus"


def test_search_result_can_be_extended_without_breaking_defaults():
    from seektalent.core.retrieval.provider_contract import SearchResult

    result = SearchResult()
    assert result.candidates == []
    assert result.request_payload == {}
    assert result.provider_snapshots == []


def test_search_result_accepts_empty_provider_snapshots():
    from seektalent.core.retrieval.provider_contract import SearchResult

    result = SearchResult(provider_snapshots=[])
    assert result.provider_snapshots == []


def test_provider_snapshot_contract_captures_raw_provider_payloads():
    from seektalent.core.retrieval.provider_contract import ProviderSnapshot
    from seektalent.core.retrieval.provider_contract import SearchResult

    snapshot = ProviderSnapshot(
        provider_name="liepin",
        payload_kind="card",
        raw_payload={"candidateId": "candidate-1"},
        normalized_text="Python engineer candidate",
        provider_subject_id="subject-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="fingerprint-1",
        identity_confidence="medium",
        extraction_source="fake_fixture",
        extractor_version="v1",
        pii_classification="limited",
        retention_policy="provider-snapshot-local",
        access_scope="run",
        redaction_state="raw",
        score_evidence_source="provider_card",
    )
    result = SearchResult(provider_snapshots=[snapshot])

    assert result.provider_snapshots == [snapshot]
