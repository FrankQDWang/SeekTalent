from __future__ import annotations

from typing import Any, cast

from seektalent.core.retrieval.service import RetrievalService
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent.runtime.services import RuntimeServices
from tests.settings_factory import make_settings


class _Provider:
    name = "replacement"


def test_workflow_runtime_exposes_explicit_runtime_services_bundle(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)

    runtime = WorkflowRuntime(settings)

    assert isinstance(runtime.services, RuntimeServices)
    assert runtime.requirement_extractor is runtime.services.requirement_extractor
    assert runtime.controller is runtime.services.controller
    assert runtime.resume_scorer is runtime.services.resume_scorer
    assert runtime.reflection_critic is runtime.services.reflection_critic
    assert runtime.retrieval_runtime is runtime.services.retrieval_runtime
    assert runtime.corpus_store is runtime.services.corpus_store


def test_retrieval_service_assignment_keeps_services_bundle_consistent(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    replacement = RetrievalService(provider=_Provider())

    runtime.retrieval_service = replacement

    assert runtime.services.retrieval_service is replacement
    assert runtime.retrieval_runtime.retrieval_service is replacement


def test_retrieval_service_assignment_preserves_legacy_providerless_fakes(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    original_provider = runtime.provider
    fake_retrieval_service = cast(Any, object())

    runtime.retrieval_service = fake_retrieval_service

    assert runtime.provider is original_provider
    assert runtime.services.retrieval_service is fake_retrieval_service
    assert runtime.retrieval_runtime.retrieval_service is fake_retrieval_service
