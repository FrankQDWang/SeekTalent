from __future__ import annotations

from .models import LogicalArtifactEntry


STATIC_ENTRIES = {
    "runtime.trace_log": LogicalArtifactEntry(path="runtime/trace.log", content_type="text/plain"),
    "runtime.events": LogicalArtifactEntry(path="runtime/events.jsonl", content_type="application/jsonl"),
    "runtime.liepin_connection_events": LogicalArtifactEntry(
        path="runtime/liepin_connection_events.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "runtime.run_config": LogicalArtifactEntry(path="runtime/run_config.json", content_type="application/json", schema_version="v1"),
    "runtime.source_plan": LogicalArtifactEntry(
        path="runtime/source_plan.json",
        content_type="application/json",
        schema_version="runtime_source_plan_v1",
    ),
    "runtime.sent_query_history": LogicalArtifactEntry(
        path="runtime/sent_query_history.json",
        content_type="application/json",
        schema_version="v1",
    ),
    "runtime.search_diagnostics": LogicalArtifactEntry(
        path="runtime/search_diagnostics.json",
        content_type="application/json",
        schema_version="v1",
    ),
    "runtime.term_surface_audit": LogicalArtifactEntry(
        path="runtime/term_surface_audit.json",
        content_type="application/json",
        schema_version="v1",
    ),
    "input.input_snapshot": LogicalArtifactEntry(path="input/input_snapshot.json", content_type="application/json", schema_version="v1"),
    "input.input_truth": LogicalArtifactEntry(path="input/input_truth.json", content_type="application/json", schema_version="v1"),
    "output.final_candidates": LogicalArtifactEntry(
        path="output/final_candidates.json",
        content_type="application/json",
        schema_version="v1",
    ),
    "output.run_summary": LogicalArtifactEntry(path="output/run_summary.md", content_type="text/markdown"),
    "output.judge_packet": LogicalArtifactEntry(path="output/judge_packet.json", content_type="application/json", schema_version="v1"),
    "output.summary": LogicalArtifactEntry(path="output/summary.json", content_type="application/json", schema_version="v1"),
    "evaluation.evaluation": LogicalArtifactEntry(path="evaluation/evaluation.json", content_type="application/json", schema_version="v1"),
    "evaluation.replay_rows": LogicalArtifactEntry(
        path="evaluation/replay_rows.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "flywheel.query_outcomes": LogicalArtifactEntry(
        path="flywheel/query_outcomes.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "flywheel.query_judge_outcomes": LogicalArtifactEntry(
        path="flywheel/query_judge_outcomes.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "flywheel.term_events": LogicalArtifactEntry(
        path="flywheel/term_events.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "flywheel.term_outcomes": LogicalArtifactEntry(
        path="flywheel/term_outcomes.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "flywheel.query_rewrite_samples": LogicalArtifactEntry(
        path="flywheel/query_rewrite_samples.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "flywheel.dataset_export_manifest": LogicalArtifactEntry(
        path="flywheel/dataset_export_manifest.json",
        content_type="application/json",
        schema_version="v1",
    ),
    "corpus.ingest_manifest": LogicalArtifactEntry(
        path="corpus/ingest_manifest.json",
        content_type="application/json",
        schema_version="v1",
    ),
    "corpus.jd_documents": LogicalArtifactEntry(
        path="corpus/jd_documents.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.resume_subjects": LogicalArtifactEntry(
        path="corpus/resume_subjects.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.resume_documents": LogicalArtifactEntry(
        path="corpus/resume_documents.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.resume_observations": LogicalArtifactEntry(
        path="corpus/resume_observations.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.run_corpus_links": LogicalArtifactEntry(
        path="corpus/run_corpus_links.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.corpus_collections": LogicalArtifactEntry(
        path="corpus/corpus_collections.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.corpus_memberships": LogicalArtifactEntry(
        path="corpus/corpus_memberships.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.corpus_exports": LogicalArtifactEntry(
        path="corpus/corpus_exports.jsonl",
        content_type="application/jsonl",
        schema_version="v1",
    ),
    "corpus.export_manifest": LogicalArtifactEntry(
        path="corpus/export_manifest.json",
        content_type="application/json",
        schema_version="v1",
    ),
    "corpus.raw_payloads": LogicalArtifactEntry(
        path="raw_payloads",
        content_type="application/json",
        schema_version="v1",
        collection=True,
    ),
    "assets.provider_snapshots.liepin.cards": LogicalArtifactEntry(
        path="assets/provider_snapshots/liepin/cards",
        content_type="application/json",
        schema_version="v1",
        collection=True,
    ),
    "assets.provider_snapshots.liepin.details": LogicalArtifactEntry(
        path="assets/provider_snapshots/liepin/details",
        content_type="application/json",
        schema_version="v1",
        collection=True,
    ),
}


def top_level_entry(name: str) -> LogicalArtifactEntry:
    return STATIC_ENTRIES[name]


def asset_prompt_entry(prompt_name: str) -> LogicalArtifactEntry:
    return LogicalArtifactEntry(path=f"assets/prompts/{prompt_name}", content_type="text/plain")


def round_entry(*, round_no: int, stage: str, filename: str, content_type: str) -> tuple[str, LogicalArtifactEntry]:
    logical_name = f"round.{round_no:02d}.{stage}.{filename.removesuffix('.json').removesuffix('.jsonl').removesuffix('.md')}"
    return logical_name, LogicalArtifactEntry(
        path=f"rounds/{round_no:02d}/{stage}/{filename}",
        content_type=content_type,
        schema_version="v1",
    )


ROUND_CONTENT_TYPES = {
    "candidate_feedback_decision": "application/json",
    "candidate_feedback_expression_evidence": "application/json",
    "candidate_feedback_input": "application/json",
    "candidate_feedback_terms": "application/json",
    "controller_call": "application/json",
    "controller_context": "application/json",
    "query_resume_hits": "application/json",
    "replay_snapshot": "application/json",
    "llm_prf_call": "application/json",
    "llm_prf_candidates": "application/json",
    "llm_prf_grounding": "application/json",
    "llm_prf_input": "application/json",
    "liepin_connection_status": "application/json",
    "liepin_search_requests": "application/jsonl",
    "liepin_card_extraction": "application/jsonl",
    "liepin_detail_open_plan": "application/json",
    "liepin_detail_open_results": "application/jsonl",
    "liepin_connector_metrics": "application/json",
    "prf_policy_decision": "application/json",
    "reflection_advice": "application/json",
    "reflection_call": "application/json",
    "reflection_context": "application/json",
    "repair_controller_call": "application/json",
    "repair_reflection_call": "application/json",
    "rescue_decision": "application/json",
    "retrieval_plan": "application/json",
    "search_attempts": "application/json",
    "search_observation": "application/json",
    "second_lane_decision": "application/json",
    "scoring_calls": "application/jsonl",
    "scoring_input_refs": "application/jsonl",
    "scorecards": "application/jsonl",
    "top_pool_snapshot": "application/json",
    "tui_summary": "application/json",
    "tui_summary_call": "application/json",
    "controller_decision": "application/json",
}

LEGACY_ROUND_CONTENT_TYPES = {
    "company_discovery_plan_call": "application/json",
    "company_discovery_extract_call": "application/json",
    "company_discovery_reduce_call": "application/json",
}


def _round_leaf_entry(*, logical_name: str, round_text: str, stage: str, leaf: str) -> LogicalArtifactEntry:
    if leaf == "round_review":
        filename = "round_review.md"
        content_type = "text/markdown"
    elif leaf in ROUND_CONTENT_TYPES:
        content_type = ROUND_CONTENT_TYPES[leaf]
        filename = f"{leaf}.jsonl" if content_type == "application/jsonl" else f"{leaf}.json"
    elif leaf in LEGACY_ROUND_CONTENT_TYPES:
        filename = f"{leaf}.json"
        content_type = LEGACY_ROUND_CONTENT_TYPES[leaf]
    else:
        raise KeyError(logical_name)
    _, entry = round_entry(
        round_no=int(round_text),
        stage=stage,
        filename=filename,
        content_type=content_type,
    )
    return entry


def resolve_descriptor(logical_name: str) -> LogicalArtifactEntry:
    if logical_name in STATIC_ENTRIES:
        return STATIC_ENTRIES[logical_name]
    if logical_name.startswith("assets.prompts."):
        return asset_prompt_entry(logical_name.removeprefix("assets.prompts."))
    if logical_name.startswith("round."):
        _, round_text, stage, leaf = logical_name.split(".", 3)
        return _round_leaf_entry(logical_name=logical_name, round_text=round_text, stage=stage, leaf=leaf)
    raise KeyError(logical_name)
