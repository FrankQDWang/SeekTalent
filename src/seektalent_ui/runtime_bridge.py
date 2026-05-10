from __future__ import annotations

from collections.abc import Callable
import asyncio

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.progress import ProgressCallback
from seektalent.providers.liepin.client import LiepinWorkerClient, build_liepin_worker_client
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent_ui.workbench_store import WorkbenchSourceRunJobContext, WorkbenchStore


RuntimeFactory = Callable[[AppSettings], object]


def run_cts_source_run(
    *,
    context: WorkbenchSourceRunJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> None:
    runtime = runtime_factory(settings)
    artifacts = runtime.run(
        job_title=context.session.job_title,
        jd=context.session.jd_text,
        notes=_notes_with_triage(context),
        progress_callback=progress_callback,
    )
    store.complete_cts_source_run_with_candidate_results(context=context, artifacts=artifacts)


def run_liepin_card_source_run(
    *,
    context: WorkbenchSourceRunJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    worker_client: LiepinWorkerClient | None = None,
) -> None:
    connection = store.get_liepin_source_connection_for_job_context(context=context)
    if connection is None or connection.status != "connected" or connection.provider_account_hash is None:
        raise LiepinWorkerModeError("Liepin source run requires a connected source account.")
    client = worker_client or build_liepin_worker_client(settings)
    result = asyncio.run(
        client.search(
            _liepin_card_search_request(context=context, connection_id=connection.connection_id),
            round_no=1,
            trace_id=context.job.job_id,
            provider_account_hash=connection.provider_account_hash,
        )
    )
    store.complete_liepin_card_source_run_with_search_result(context=context, result=result)


def _notes_with_triage(context: WorkbenchSourceRunJobContext) -> str:
    triage = context.triage
    sections = [
        context.session.notes.strip(),
        "Approved requirement triage:",
        f"must_haves: {_bounded_join(triage.must_haves)}",
        f"nice_to_haves: {_bounded_join(triage.nice_to_haves)}",
        f"synonyms: {_bounded_join(triage.synonyms)}",
        f"seniority_filters: {_bounded_join(triage.seniority_filters)}",
        f"exclusions: {_bounded_join(triage.exclusions)}",
        f"generated_query_hints: {_bounded_join(triage.generated_query_hints)}",
    ]
    return "\n".join(section for section in sections if section)


def _liepin_card_search_request(*, context: WorkbenchSourceRunJobContext, connection_id: str) -> SearchRequest:
    terms = _query_terms(context)
    return SearchRequest(
        query_terms=terms,
        query_role="primary",
        keyword_query=" ".join(terms),
        adapter_notes=[context.session.notes],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=30,
        provider_context={
            "liepin_tenant_id": "local",
            "liepin_workspace_id": context.session.workspace_id,
            "liepin_actor_id": context.session.owner_user_id,
            "liepin_connection_id": connection_id,
            "query_instance_id": context.job.job_id,
            "query_fingerprint": context.job.job_id,
        },
    )


def _query_terms(context: WorkbenchSourceRunJobContext) -> list[str]:
    source_terms = [
        *context.triage.generated_query_hints,
        *context.triage.must_haves,
        *context.triage.synonyms,
        context.session.job_title,
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for value in source_terms:
        text = value.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(text)
        if len(terms) >= 8:
            break
    return terms or [context.session.job_title]


def _bounded_join(values: list[str], *, max_items: int = 12, max_chars: int = 800) -> str:
    text = "; ".join(values[:max_items])
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."
