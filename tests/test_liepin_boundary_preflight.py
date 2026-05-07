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
