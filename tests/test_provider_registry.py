from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from seektalent.core.retrieval.provider_contract import ProviderCapabilities
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import ResumeCandidate
from seektalent.providers import get_provider_adapter
from seektalent.providers import get_provider_adapter_for_source
from tests.settings_factory import make_settings


def test_provider_registry_returns_cts_adapter() -> None:
    settings = make_settings()

    provider = get_provider_adapter(settings)

    assert provider.name == "cts"
    capabilities = provider.describe_capabilities()
    assert capabilities.supports_structured_filters is True
    assert capabilities.supports_fetch_mode_summary is True
    assert capabilities.paging_mode == "cursor"


def test_provider_registry_returns_source_specific_cts_without_mutating_global_provider() -> None:
    settings = make_settings(
        provider_name="liepin",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
    )

    provider = get_provider_adapter_for_source(settings, "cts")

    assert provider.name == "cts"
    assert settings.provider_name == "liepin"


def test_provider_registry_returns_liepin_adapter_for_fake_fixture_mode() -> None:
    settings = make_settings(
        provider_name="liepin",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
    )

    provider = get_provider_adapter(settings)

    assert provider.name == "liepin"
    assert provider.__class__.__name__ == "LiepinProviderAdapter"
    capabilities = provider.describe_capabilities()
    assert capabilities.supports_detail_fetch is True
    assert capabilities.supports_fetch_mode_summary is True
    assert capabilities.supports_fetch_mode_detail is True
    assert capabilities.paging_mode == "cursor"


def test_provider_registry_returns_source_specific_liepin_with_injected_worker() -> None:
    worker = object()
    settings = make_settings(
        provider_name="cts",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
    )

    provider = get_provider_adapter_for_source(settings, "liepin", liepin_worker_client=worker)

    assert provider.name == "liepin"
    assert provider.__class__.__name__ == "LiepinProviderAdapter"
    assert provider.worker_client is worker
    assert settings.provider_name == "cts"


def test_source_specific_provider_selection_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="Unsupported source"):
        get_provider_adapter_for_source(make_settings(), "boss")


def test_liepin_fake_fixture_worker_requires_explicit_allow_flag() -> None:
    with pytest.raises(ValidationError, match="fake_fixture"):
        make_settings(
            provider_name="liepin",
            liepin_worker_mode="fake_fixture",
            liepin_allow_fake_fixture_worker=False,
        )


def test_liepin_disabled_mode_fails_at_provider_selection() -> None:
    settings = make_settings(provider_name="liepin", liepin_worker_mode="disabled")

    with pytest.raises(ValueError, match="disabled"):
        get_provider_adapter(settings)


def test_liepin_external_http_requires_worker_base_url() -> None:
    with pytest.raises(ValidationError, match="liepin_worker_base_url"):
        make_settings(liepin_worker_mode="external_http")


def test_provider_registry_creates_live_store_for_opencli(tmp_path: Path) -> None:
    worker = object()
    settings = make_settings(
        provider_name="cts",
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="opencli",
        liepin_connector_db_path=str(tmp_path / "liepin.sqlite3"),
    )

    provider = get_provider_adapter_for_source(settings, "liepin", liepin_worker_client=worker)

    assert provider.name == "liepin"
    assert provider.worker_client is worker
    assert provider.store is not None


@pytest.mark.parametrize(
    "field_name",
    [
        "liepin_worker_startup_timeout_seconds",
        "liepin_worker_timeout_seconds",
    ],
)
def test_liepin_worker_timeouts_must_be_positive(field_name: str) -> None:
    with pytest.raises(ValidationError, match=field_name):
        make_settings(**{field_name: 0})


def test_liepin_daily_detail_budget_must_be_non_negative() -> None:
    with pytest.raises(ValidationError, match="liepin_default_daily_detail_budget"):
        make_settings(liepin_default_daily_detail_budget=-1)


def test_provider_contract_fake_provider_search() -> None:
    class FakeProvider:
        name = "fake"

        def describe_capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                supports_structured_filters=True,
                supports_detail_fetch=False,
                supports_fetch_mode_summary=True,
                supports_fetch_mode_detail=False,
                paging_mode="cursor",
                recommended_max_concurrency=2,
                has_stable_external_id=True,
                has_stable_dedup_key=True,
            )

        async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
            assert request.query_terms == ["python"]
            assert request.query_role == "primary"
            assert request.fetch_mode == "summary"
            assert round_no == 1
            assert trace_id == "trace-1"
            return SearchResult(
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

    provider = FakeProvider()
    request = SearchRequest(
        query_terms=["python"],
        query_role="primary",
        keyword_query="python",
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=25,
    )
    result = asyncio.run(provider.search(request, round_no=1, trace_id="trace-1"))

    assert provider.name == "fake"
    capabilities = provider.describe_capabilities()
    assert capabilities.supports_structured_filters is True
    assert capabilities.paging_mode == "cursor"
    assert result.candidates[0].resume_id == "resume-1"
    assert result.next_cursor == "page=2"


def test_runtime_orchestrator_no_longer_imports_cts_client_directly() -> None:
    source = Path("src/seektalent/runtime/orchestrator.py").read_text(encoding="utf-8")

    assert "seektalent.clients.cts_client" not in source
    assert "CTSClient(" not in source
    assert "MockCTSClient(" not in source


def test_provider_registry_core_does_not_import_workbench_ui() -> None:
    source = Path("src/seektalent/providers/registry.py").read_text(encoding="utf-8")

    assert "seektalent_ui" not in source
