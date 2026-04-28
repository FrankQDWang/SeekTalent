from __future__ import annotations

from .models import LogicalArtifactEntry


STATIC_ENTRIES = {
    "runtime.trace_log": LogicalArtifactEntry(path="runtime/trace.log", content_type="text/plain"),
    "runtime.events": LogicalArtifactEntry(path="runtime/events.jsonl", content_type="application/jsonl"),
    "runtime.run_config": LogicalArtifactEntry(path="runtime/run_config.json", content_type="application/json", schema_version="v1"),
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
    "query_resume_hits": "application/json",
    "replay_snapshot": "application/json",
    "second_lane_decision": "application/json",
    "prf_policy_decision": "application/json",
    "controller_decision": "application/json",
    "controller_context": "application/json",
    "reflection_advice": "application/json",
    "reflection_call": "application/json",
    "scorecards": "application/jsonl",
    "scoring_calls": "application/jsonl",
    "scoring_input_refs": "application/jsonl",
}


def resolve_descriptor(logical_name: str) -> LogicalArtifactEntry:
    if logical_name in STATIC_ENTRIES:
        return STATIC_ENTRIES[logical_name]
    if logical_name.startswith("assets.prompts."):
        return asset_prompt_entry(logical_name.removeprefix("assets.prompts."))
    if logical_name.startswith("round."):
        _, round_text, stage, leaf = logical_name.split(".", 3)
        filename = f"{leaf}.jsonl" if ROUND_CONTENT_TYPES.get(leaf) == "application/jsonl" else f"{leaf}.json"
        content_type = ROUND_CONTENT_TYPES.get(leaf, "application/json")
        if leaf == "round_review":
            filename = "round_review.md"
            content_type = "text/markdown"
        _, entry = round_entry(
            round_no=int(round_text),
            stage=stage,
            filename=filename,
            content_type=content_type,
        )
        return entry
    raise KeyError(logical_name)
