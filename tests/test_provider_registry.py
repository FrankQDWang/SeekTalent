from __future__ import annotations

from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import ResumeCandidate
from seektalent.providers import get_provider_adapter
from tests.settings_factory import make_settings


def test_provider_registry_returns_cts_adapter() -> None:
    settings = make_settings()

    provider = get_provider_adapter(settings)

    assert provider.name == "cts"
    assert provider.describe_capabilities().supports_structured_filters is True


def test_provider_contract_search_result_shape() -> None:
    result = SearchResult(
        candidates=[
            ResumeCandidate(
                resume_id="resume-1",
                source_resume_id="source-1",
                snapshot_sha256="snap-1",
                dedup_key="resume-1",
                search_text="candidate summary",
                raw={"resumeId": "resume-1"},
            )
        ],
        diagnostics=["used native location filter"],
        exhausted=False,
        next_cursor="page=2",
    )

    assert result.candidates[0].resume_id == "resume-1"
    assert result.next_cursor == "page=2"
