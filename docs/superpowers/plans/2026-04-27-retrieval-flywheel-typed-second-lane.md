# Retrieval Flywheel And Typed Second Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 retrieval flywheel foundation: stable query identity, full lane attribution, typed second-lane routing, `PRF v1` from current candidate feedback, and experiment-ready replay artifacts for `prf_probe` versus `generic_explore`.

**Architecture:** Keep the runtime as a controlled workflow. Implement the Phase 1 contracts in dependency order: identity primitives, typed second-lane skeleton, query-to-resume ledger, PRF extraction, PRF gate integration, score-aware budget, and finally replay plus experiment isolation. Keep company handling isolated: no mainline company rewrite, no company-driven second lane, and no company rescue participation in the primary `PRF vs generic_explore` comparison.

**Tech Stack:** Python 3.12, Pydantic models, existing SeekTalent runtime split modules, pytest, existing benchmark harness and run artifacts

---

## File Map

### New files

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_identity.py`
  Purpose: Build `job_intent_fingerprint`, `query_fingerprint`, `query_instance_id`, and canonical query-spec normalization helpers.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py`
  Purpose: Own typed second-lane selection, PRF-vs-fallback routing, and `SecondLaneDecision` artifact construction.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/policy.py`
  Purpose: Turn shared feedback extraction into a replayable `PRF v1` policy decision, separate from late-rescue use.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_query_identity.py`
  Purpose: Lock query fingerprint and canonical query-spec behavior.

- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py`
  Purpose: Lock pure typed-second-lane helper behavior without requiring a full runtime round trip.

### Modified files

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
  Purpose: Add `LaneType`, `CanonicalQuerySpec`, `QueryResumeHit`, `SecondLaneDecision`, `ReplaySnapshot`, outcome models, and new attribution fields.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py`
  Purpose: Carry lane metadata, persist query-hit ledger rows, and support score-aware second-lane allocation and refill decisions.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
  Purpose: Wire query identity, second-lane runtime, post-score hit enrichment, replay artifacts, and the typed-lane round loop.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
  Purpose: Compute structured query outcomes and replay snapshot payloads.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/models.py`
  Purpose: Represent PRF expression families, term lineage, and conservative classification.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/extraction.py`
  Purpose: Make extraction return shared evidence structures that `PRF v1` and late rescue can both consume.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_execution_runtime.py`
  Purpose: Reuse the shared extractor under a `late_rescue` identity without sharing PRF policy state.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py`
  Purpose: Lock expression-family extraction, term classification, and PRF gate rules.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`
  Purpose: Lock runtime-level typed second-lane selection, post-score refill behavior, and company-isolated primary comparison behavior.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
  Purpose: Lock new artifacts such as `query_resume_hits.json`, `second_lane_decision.json`, and `replay_snapshot.json`.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`
  Purpose: Lock structured outcome-label definitions and replay-row shape.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py`
  Purpose: Lock baseline-versus-candidate experiment wiring and company-isolation defaults.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tools/run_global_benchmark.py`
  Purpose: Add baseline-versus-candidate second-lane comparison entrypoints with Phase 1 company isolation defaults.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/baseline_evaluation.py`
  Purpose: Add typed second-lane experiment modes and replay row export.

- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
  Purpose: Document new artifacts and their intended use.

## Task 1: Add Identity Primitives And Canonical Query Specification

**Files:**
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_identity.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_query_identity.py`

- [ ] **Step 1: Write the failing query identity tests**

```python
from seektalent.models import CanonicalQuerySpec
from seektalent.retrieval.query_identity import (
    build_job_intent_fingerprint,
    build_query_fingerprint,
    build_query_instance_id,
)


def _spec(*, optional_terms: list[str], provider_filters: dict[str, object]) -> CanonicalQuerySpec:
    return CanonicalQuerySpec(
        lane_type="generic_explore",
        anchors=["python"],
        expansion_terms=["resume matching"],
        promoted_prf_expression=None,
        generic_explore_terms=["trace"],
        required_terms=["python"],
        optional_terms=optional_terms,
        excluded_terms=[],
        location_key="shanghai",
        provider_filters=provider_filters,
        boolean_template="required_plus_optional",
        rendered_provider_query='python "resume matching" trace',
        provider_name="cts",
        source_plan_version="2",
    )


def test_query_fingerprint_is_stable_across_runs() -> None:
    spec = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"city": "上海", "experience_years": 5},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    first = build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )
    second = build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )

    assert first == second


def test_query_fingerprint_canonicalizes_unordered_fields() -> None:
    first = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"experience_years": 5, "city": "上海"},
    )
    second = _spec(
        optional_terms=["trace", "resume matching"],
        provider_filters={"city": "上海", "experience_years": 5},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )

    assert build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=first,
        policy_version="typed-second-lane-v1",
    ) == build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=second,
        policy_version="typed-second-lane-v1",
    )


def test_query_instance_id_changes_by_run_but_not_fingerprint() -> None:
    spec = _spec(
        optional_terms=["resume matching", "trace"],
        provider_filters={"city": "上海", "experience_years": 5},
    )
    job_fingerprint = build_job_intent_fingerprint(
        role_title="Python Engineer",
        must_haves=["python", "resume matching"],
        preferred_terms=["trace"],
        hard_filters={"experience_years": 5},
        location_preferences=["shanghai"],
        normalized_intent_hash="intent-001",
        intent_schema_version="v1",
    )
    query_fingerprint = build_query_fingerprint(
        job_intent_fingerprint=job_fingerprint,
        lane_type="generic_explore",
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )

    first = build_query_instance_id(
        run_id="run-a",
        round_no=2,
        lane_type="generic_explore",
        query_fingerprint=query_fingerprint,
        source_plan_version="2",
    )
    second = build_query_instance_id(
        run_id="run-b",
        round_no=2,
        lane_type="generic_explore",
        query_fingerprint=query_fingerprint,
        source_plan_version="2",
    )

    assert first != second
    assert query_fingerprint
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_query_identity.py -q`
Expected: FAIL with import or attribute errors because `CanonicalQuerySpec` and the query identity helpers do not exist yet.

- [ ] **Step 3: Add canonical query spec and query identity helpers**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py
LaneType = Literal["exploit", "generic_explore", "prf_probe"]


class CanonicalQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lane_type: LaneType
    anchors: list[str] = Field(default_factory=list)
    expansion_terms: list[str] = Field(default_factory=list)
    promoted_prf_expression: str | None = None
    generic_explore_terms: list[str] = Field(default_factory=list)
    required_terms: list[str] = Field(default_factory=list)
    optional_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    location_key: str | None = None
    provider_filters: dict[str, Any] = Field(default_factory=dict)
    boolean_template: str
    rendered_provider_query: str
    provider_name: str
    source_plan_version: str
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_identity.py
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from seektalent.models import CanonicalQuerySpec

UNORDERED_TERM_FIELDS = {
    "anchors",
    "expansion_terms",
    "generic_explore_terms",
    "required_terms",
    "optional_terms",
    "excluded_terms",
}


def _stable_hash(payload: dict[str, object]) -> str:
    blob = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(blob.encode("utf-8")).hexdigest()[:32]


def normalize_term(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _normalize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: normalize_term(item) if isinstance(item, str) else item
        for key, item in sorted(value.items())
    }


def canonicalize_query_spec(spec: CanonicalQuerySpec) -> dict[str, object]:
    payload = spec.model_dump(mode="json")
    for field in UNORDERED_TERM_FIELDS:
        payload[field] = sorted(normalize_term(item) for item in payload[field])
    payload["provider_filters"] = _normalize_mapping(payload["provider_filters"])
    payload["rendered_provider_query"] = " ".join(str(payload["rendered_provider_query"]).split())
    return payload


def build_job_intent_fingerprint(
    *,
    role_title: str,
    must_haves: list[str],
    preferred_terms: list[str],
    hard_filters: dict[str, object] | None = None,
    location_preferences: list[str] | None = None,
    normalized_intent_hash: str | None = None,
    intent_schema_version: str,
) -> str:
    return _stable_hash(
        {
            "role_title": normalize_term(role_title),
            "must_haves": sorted(normalize_term(item) for item in must_haves if item.strip()),
            "preferred_terms": sorted(normalize_term(item) for item in preferred_terms if item.strip()),
            "hard_filters": _normalize_mapping(hard_filters or {}),
            "location_preferences": sorted(
                normalize_term(item) for item in (location_preferences or []) if item.strip()
            ),
            "normalized_intent_hash": normalized_intent_hash,
            "intent_schema_version": intent_schema_version,
        }
    )


def build_query_fingerprint(
    *,
    job_intent_fingerprint: str,
    lane_type: str,
    canonical_query_spec: CanonicalQuerySpec,
    policy_version: str,
) -> str:
    return _stable_hash(
        {
            "job_intent_fingerprint": job_intent_fingerprint,
            "lane_type": lane_type,
            "canonical_query_spec": canonicalize_query_spec(canonical_query_spec),
            "policy_version": policy_version,
        }
    )


def build_query_instance_id(
    *,
    run_id: str,
    round_no: int,
    lane_type: str,
    query_fingerprint: str,
    source_plan_version: str,
) -> str:
    return _stable_hash(
        {
            "run_id": run_id,
            "round_no": round_no,
            "lane_type": lane_type,
            "query_fingerprint": query_fingerprint,
            "source_plan_version": source_plan_version,
        }
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_query_identity.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_identity.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_query_identity.py
git commit -m "Add query identity and canonical query spec"
```

## Task 2: Add Typed Second-Lane Runtime Skeleton

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write failing lane-routing tests**

```python
from seektalent.models import SecondLaneDecision
from seektalent.runtime.second_lane_runtime import build_second_lane_decision


def _retrieval_plan(*, query_terms: list[str]) -> RoundRetrievalPlan:
    return RoundRetrievalPlan(
        plan_version=2,
        round_no=2,
        query_terms=query_terms,
        keyword_query=" ".join(query_terms),
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=["shanghai"],
            preferred_locations=["shanghai"],
            round_no=2,
            target_new=6,
        ),
        target_new=6,
        rationale="test",
    )


def test_build_second_lane_decision_falls_back_to_generic_when_prf_policy_is_unavailable() -> None:
    retrieval_plan = _retrieval_plan(query_terms=["python", "ranking"])

    decision, lane = build_second_lane_decision(
        round_no=2,
        retrieval_plan=retrieval_plan,
        query_term_pool=[],
        sent_query_history=[],
        prf_decision=None,
        run_id="run-a",
        job_intent_fingerprint="job-1",
        source_plan_version="2",
    )

    assert decision == SecondLaneDecision(
        round_no=2,
        attempted_prf=True,
        prf_gate_passed=False,
        selected_lane_type="generic_explore",
        selected_query_instance_id=decision.selected_query_instance_id,
        selected_query_fingerprint=decision.selected_query_fingerprint,
        reject_reasons=["prf_policy_not_available"],
        fallback_lane_type="generic_explore",
        prf_policy_version="unavailable",
        generic_explore_version="v1",
    )
    assert lane is not None
    assert lane.lane_type == "generic_explore"


def test_round_two_serializes_exploit_and_generic_lane_types(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=2)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace")

    try:
        run_state = asyncio.run(runtime._build_run_state(*_sample_inputs(), tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    queries = json.loads((tracer.run_dir / "rounds" / "round_02" / "cts_queries.json").read_text())
    decision = json.loads((tracer.run_dir / "rounds" / "round_02" / "second_lane_decision.json").read_text())
    assert [item["lane_type"] for item in queries] == ["exploit", "generic_explore"]
    assert decision["attempted_prf"] is True
    assert decision["prf_gate_passed"] is False
    assert decision["selected_lane_type"] == "generic_explore"
    assert decision["fallback_lane_type"] == "generic_explore"
    assert decision["reject_reasons"] == ["prf_policy_not_available"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_round_two_serializes_exploit_and_generic_lane_types -q`
Expected: FAIL because there is no typed second-lane runtime helper and the runtime does not serialize lane-aware query states yet.

- [ ] **Step 3: Add second-lane decision model and typed skeleton**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py
class SecondLaneDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    attempted_prf: bool
    prf_gate_passed: bool
    selected_lane_type: LaneType | None = None
    selected_query_instance_id: str | None = None
    selected_query_fingerprint: str | None = None
    accepted_prf_expression: str | None = None
    accepted_prf_term_family_id: str | None = None
    prf_seed_resume_ids: list[str] = Field(default_factory=list)
    prf_candidate_expression_count: int = 0
    reject_reasons: list[str] = Field(default_factory=list)
    fallback_lane_type: LaneType | None = None
    fallback_query_fingerprint: str | None = None
    no_fetch_reason: str | None = None
    prf_policy_version: str
    generic_explore_version: str | None = None
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py
@dataclass
class LogicalQueryState:
    query_role: QueryRole
    lane_type: LaneType
    query_terms: list[str]
    keyword_query: str
    query_instance_id: str
    query_fingerprint: str
    next_page: int = 1
    exhausted: bool = False
    adapter_notes: list[str] = field(default_factory=list)
    city_states: dict[str, CityExecutionState] = field(default_factory=dict)


def build_logical_query_state(
    *,
    run_id: str,
    round_no: int,
    lane_type: LaneType,
    query_terms: list[str],
    job_intent_fingerprint: str,
    source_plan_version: str,
) -> LogicalQueryState:
    keyword_query = serialize_keyword_query(query_terms)
    spec = CanonicalQuerySpec(
        lane_type=lane_type,
        anchors=query_terms[:1],
        expansion_terms=query_terms[1:],
        promoted_prf_expression=query_terms[1] if lane_type == "prf_probe" and len(query_terms) > 1 else None,
        generic_explore_terms=query_terms[1:] if lane_type == "generic_explore" else [],
        required_terms=query_terms[:1],
        optional_terms=query_terms[1:],
        excluded_terms=[],
        location_key=None,
        provider_filters={},
        boolean_template="required_plus_optional",
        rendered_provider_query=keyword_query,
        provider_name="cts",
        source_plan_version=source_plan_version,
    )
    query_fingerprint = build_query_fingerprint(
        job_intent_fingerprint=job_intent_fingerprint,
        lane_type=lane_type,
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )
    return LogicalQueryState(
        query_role="explore" if lane_type != "exploit" else "exploit",
        lane_type=lane_type,
        query_terms=query_terms,
        keyword_query=keyword_query,
        query_instance_id=build_query_instance_id(
            run_id=run_id,
            round_no=round_no,
            lane_type=lane_type,
            query_fingerprint=query_fingerprint,
            source_plan_version=source_plan_version,
        ),
        query_fingerprint=query_fingerprint,
    )
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py
def build_second_lane_decision(
    *,
    round_no: int,
    retrieval_plan: RoundRetrievalPlan,
    query_term_pool: list[QueryTermCandidate],
    sent_query_history: list[SentQueryRecord],
    prf_decision: PRFPolicyDecision | None,
    run_id: str,
    job_intent_fingerprint: str,
    source_plan_version: str,
) -> tuple[SecondLaneDecision, LogicalQueryState | None]:
    if round_no == 1 or len(retrieval_plan.query_terms) <= 1:
        return (
            SecondLaneDecision(
                round_no=round_no,
                attempted_prf=False,
                prf_gate_passed=False,
                reject_reasons=["round_one_or_anchor_only"],
                no_fetch_reason="single_lane_round",
                prf_policy_version="unavailable",
            ),
            None,
        )

    explore_terms = derive_explore_query_terms(
        retrieval_plan.query_terms,
        title_anchor_terms=[],
        query_term_pool=query_term_pool,
        sent_query_history=sent_query_history,
    )
    if not explore_terms:
        return (
            SecondLaneDecision(
                round_no=round_no,
                attempted_prf=True,
                prf_gate_passed=False,
                reject_reasons=["prf_policy_not_available"],
                no_fetch_reason="no_generic_explore_query",
                prf_policy_version="unavailable",
                generic_explore_version="v1",
            ),
            None,
        )

    query_state = build_logical_query_state(
        run_id=run_id,
        round_no=round_no,
        lane_type="generic_explore",
        query_terms=explore_terms,
        job_intent_fingerprint=job_intent_fingerprint,
        source_plan_version=source_plan_version,
    )
    return (
        SecondLaneDecision(
            round_no=round_no,
            attempted_prf=True,
            prf_gate_passed=False,
            selected_lane_type="generic_explore",
            selected_query_instance_id=query_state.query_instance_id,
            selected_query_fingerprint=query_state.query_fingerprint,
            reject_reasons=["prf_policy_not_available"],
            fallback_lane_type="generic_explore",
            fallback_query_fingerprint=query_state.query_fingerprint,
            prf_policy_version="unavailable",
            generic_explore_version="v1",
        ),
        query_state,
    )
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py
tracer.write_json(
    f"rounds/round_{round_no:02d}/second_lane_decision.json",
    second_lane_decision.model_dump(mode="json"),
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_round_two_serializes_exploit_and_generic_lane_types -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py
git commit -m "Add typed second-lane runtime skeleton"
```

## Task 3: Add Query-Resume Hit Ledger And First-Hit Attribution

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write failing attribution and enrichment tests**

```python
class DuplicateAcrossLanesController(SequenceController):
    def search_batches_for_round(self, round_no: int):
        if round_no != 2:
            return super().search_batches_for_round(round_no)
        duplicate = {
            "resume_id": "resume-1",
            "search_text": "python distributed systems",
            "expected_job_category": "Backend Engineer",
            "now_location": "Shanghai",
        }
        return {
            "exploit": [[duplicate]],
            "generic_explore": [[duplicate]],
        }


def test_duplicate_hit_does_not_overwrite_first_hit_attribution(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=2)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=DuplicateAcrossLanesController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace")

    try:
        run_state = asyncio.run(runtime._build_run_state(*_sample_inputs(), tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    candidate = run_state.candidate_store["resume-1"]
    hits = json.loads((tracer.run_dir / "rounds" / "round_02" / "query_resume_hits.json").read_text())
    duplicate_hit = next(item for item in hits if item["resume_id"] == "resume-1" and item["lane_type"] == "generic_explore")

    assert candidate.first_round_no == 2
    assert candidate.first_lane_type == "exploit"
    assert duplicate_hit["was_duplicate"] is True
    assert duplicate_hit["lane_type"] == "generic_explore"


def test_query_resume_hits_are_enriched_after_scoring(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=2)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace")

    try:
        run_state = asyncio.run(runtime._build_run_state(*_sample_inputs(), tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    hit = json.loads((tracer.run_dir / "rounds" / "round_02" / "query_resume_hits.json").read_text())[0]
    assert hit["scored_fit_bucket"] is not None
    assert hit["overall_score"] is not None
    assert hit["must_have_match_score"] is not None
    assert hit["risk_score"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py::test_query_resume_hits_are_enriched_after_scoring /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_duplicate_hit_does_not_overwrite_first_hit_attribution -q`
Expected: FAIL because the runtime does not yet persist first-hit fields or post-score-enriched query-hit rows.

- [ ] **Step 3: Add first-hit fields and post-score-enriched query-hit rows**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py
# Modify existing ResumeCandidate by adding these fields only.
first_query_instance_id: str | None = None
first_query_fingerprint: str | None = None
first_round_no: int | None = None
first_lane_type: LaneType | None = None
first_location_key: str | None = None
first_location_type: str | None = None
first_batch_no: int | None = None


class QueryResumeHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    query_instance_id: str
    query_fingerprint: str
    resume_id: str
    round_no: int
    lane_type: LaneType
    location_key: str | None = None
    location_type: str | None = None
    batch_no: int
    rank_in_query: int
    provider_name: str
    provider_page_no: int | None = None
    provider_fetch_no: int | None = None
    provider_score_if_any: float | None = None
    dedup_key: str | None = None
    was_new_to_pool: bool
    was_duplicate: bool
    scored_fit_bucket: FitBucket | None = None
    overall_score: float | None = None
    must_have_match_score: float | None = None
    risk_score: float | None = None
    off_intent_reason_count: int = 0
    final_candidate_status: str | None = None
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py
if was_new_to_pool:
    candidate = candidate.model_copy(
        update={
            "source_round": round_no,
            "first_query_instance_id": query_instance_id,
            "first_query_fingerprint": query_fingerprint,
            "first_round_no": round_no,
            "first_lane_type": lane_type,
            "first_location_key": location_key,
            "first_location_type": location_type,
            "first_batch_no": batch_no,
        }
    )

query_resume_hits.append(
    QueryResumeHit(
        run_id=run_id,
        query_instance_id=query_instance_id,
        query_fingerprint=query_fingerprint,
        resume_id=candidate.resume_id,
        round_no=round_no,
        lane_type=lane_type,
        location_key=location_key,
        location_type=location_type,
        batch_no=batch_no,
        rank_in_query=rank_in_query,
        provider_name="cts",
        provider_page_no=page_no,
        provider_fetch_no=fetch_no,
        provider_score_if_any=provider_score,
        dedup_key=candidate.dedup_key,
        was_new_to_pool=was_new_to_pool,
        was_duplicate=not was_new_to_pool,
    )
)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py
for hit in query_resume_hits:
    scorecard = run_state.scorecards_by_resume_id.get(hit.resume_id)
    if scorecard is None:
        hit.final_candidate_status = "not_scored"
        continue
    hit.scored_fit_bucket = scorecard.fit_bucket
    hit.overall_score = scorecard.overall_score
    hit.must_have_match_score = scorecard.must_have_match_score
    hit.risk_score = scorecard.risk_score
    hit.off_intent_reason_count = len(scorecard.negative_signals)
    hit.final_candidate_status = "fit" if scorecard.fit_bucket == "fit" else "not_fit"

tracer.write_json(
    f"rounds/round_{round_no:02d}/query_resume_hits.json",
    [item.model_dump(mode="json") for item in query_resume_hits],
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py::test_query_resume_hits_are_enriched_after_scoring /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_duplicate_hit_does_not_overwrite_first_hit_attribution -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py
git commit -m "Add first-hit attribution and query hit ledger"
```

## Task 4: Promote Candidate Feedback Extraction Into PRF Expression Families

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/extraction.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_execution_runtime.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py`

- [ ] **Step 1: Write the failing PRF expression tests**

```python
def test_prf_expression_family_keeps_short_phrase_as_one_unit() -> None:
    expressions = extract_feedback_candidate_expressions(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["distributed systems", "python"]),
            _scored_candidate("seed-2", evidence=["distributed systems", "python"]),
        ],
        negative_resumes=[],
        known_company_entities=set(),
        known_product_platforms=set(),
    )

    expression = next(item for item in expressions if item.canonical_expression == "distributed systems")
    assert expression.surface_forms == ["distributed systems"]
    assert expression.term_family_id


def test_prf_classification_rejects_company_entity_but_keeps_product_platform() -> None:
    expressions = classify_feedback_expressions(
        ["Databricks", "ByteDance", "distributed systems"],
        known_company_entities={"bytedance"},
        known_product_platforms={"databricks"},
    )
    lookup = {item.canonical_expression: item.candidate_term_type for item in expressions}
    assert lookup["Databricks"] == "product_or_platform"
    assert lookup["ByteDance"] == "company_entity"
    assert lookup["distributed systems"] == "technical_phrase"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py -q`
Expected: FAIL because expression families, surface forms, and controlled classification do not exist yet.

- [ ] **Step 3: Refactor extraction into shared PRF expression evidence**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/models.py
class FeedbackCandidateExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term_family_id: str
    canonical_expression: str
    surface_forms: list[str] = Field(default_factory=list)
    candidate_term_type: str
    source_seed_resume_ids: list[str] = Field(default_factory=list)
    linked_requirements: list[str] = Field(default_factory=list)
    field_hits: dict[str, int] = Field(default_factory=dict)
    positive_seed_support_count: int = 0
    negative_support_count: int = 0
    fit_support_rate: float = 0.0
    not_fit_support_rate: float = 0.0
    tried_query_fingerprints: list[str] = Field(default_factory=list)
    score: float = 0.0
    reject_reasons: list[str] = Field(default_factory=list)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/extraction.py
def normalize_feedback_expression(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def build_term_family_id(value: str) -> str:
    return normalize_feedback_expression(value).replace(" ", "-")


def collect_feedback_surface_forms(
    seed_resumes: list[ScoredCandidate],
    negative_resumes: list[ScoredCandidate],
) -> list[str]:
    del negative_resumes
    expressions: list[str] = []
    for candidate in seed_resumes:
        expressions.extend(candidate.evidence)
    return expressions


def classify_feedback_expressions(
    expressions: list[str],
    *,
    known_company_entities: set[str],
    known_product_platforms: set[str],
) -> list[FeedbackCandidateExpression]:
    classified: list[FeedbackCandidateExpression] = []
    for raw in expressions:
        normalized = normalize_feedback_expression(raw)
        if normalized in known_company_entities:
            term_type = "company_entity"
        elif normalized in known_product_platforms:
            term_type = "product_or_platform"
        elif " " in normalized:
            term_type = "technical_phrase"
        else:
            term_type = "skill"
        classified.append(
            FeedbackCandidateExpression(
                term_family_id=build_term_family_id(normalized),
                canonical_expression=raw,
                surface_forms=[raw],
                candidate_term_type=term_type,
            )
        )
    return classified


def extract_feedback_candidate_expressions(
    *,
    seed_resumes: list[ScoredCandidate],
    negative_resumes: list[ScoredCandidate],
    known_company_entities: set[str],
    known_product_platforms: set[str],
) -> list[FeedbackCandidateExpression]:
    raw_expressions = collect_feedback_surface_forms(seed_resumes, negative_resumes)
    return classify_feedback_expressions(
        raw_expressions,
        known_company_entities=known_company_entities,
        known_product_platforms=known_product_platforms,
    )
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_execution_runtime.py
# Keep late rescue using the shared extractor, but do not import PRF policy state here.
candidate_expressions = extract_feedback_candidate_expressions(
    seed_resumes=seed_resumes,
    negative_resumes=negative_resumes,
    known_company_entities=set(),
    known_product_platforms=set(),
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/models.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/extraction.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/rescue_execution_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py
git commit -m "Refactor candidate feedback extraction into PRF evidence"
```

## Task 5: Add Replayable PRF Policy And Integrate It Into The Typed Second Lane

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_plan.py`
- Create: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/policy.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write the failing PRF-policy and lane-selection tests**

```python
def _feedback_expression(
    canonical_expression: str,
    *,
    candidate_term_type: str,
    term_family_id: str | None = None,
    positive_seed_support_count: int = 2,
) -> FeedbackCandidateExpression:
    return FeedbackCandidateExpression(
        term_family_id=term_family_id or canonical_expression.replace(" ", "-"),
        canonical_expression=canonical_expression,
        surface_forms=[canonical_expression],
        candidate_term_type=candidate_term_type,
        positive_seed_support_count=positive_seed_support_count,
    )


def _accepted_prf_decision(expression: str) -> PRFPolicyDecision:
    accepted = _feedback_expression(expression, candidate_term_type="technical_phrase")
    return PRFPolicyDecision(
        attempted=True,
        gate_passed=True,
        accepted_expression=accepted,
        gate_input=PRFGateInput(
            round_no=2,
            seed_resume_ids=["fit-1", "fit-2"],
            seed_count=2,
            negative_resume_ids=[],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            min_seed_count=2,
            max_negative_support_rate=0.4,
            policy_version="prf-v1",
        ),
    )


def test_build_prf_policy_decision_rejects_insufficient_high_quality_seeds() -> None:
    decision = build_prf_policy_decision(
        round_no=2,
        seed_resumes=[_scored_candidate("fit-1", evidence=["distributed systems"])],
        negative_resumes=[],
        candidate_expressions=[
            _feedback_expression("distributed systems", candidate_term_type="technical_phrase", positive_seed_support_count=1)
        ],
        tried_term_family_ids=set(),
        tried_query_fingerprints=set(),
        policy_version="prf-v1",
    )

    assert decision.gate_passed is False
    assert decision.reject_reasons == ["insufficient_high_quality_seeds"]
    assert decision.gate_input.seed_count == 1


def test_build_prf_policy_decision_rejects_tried_family_and_company_entity() -> None:
    decision = build_prf_policy_decision(
        round_no=2,
        seed_resumes=[_scored_candidate("fit-1"), _scored_candidate("fit-2")],
        negative_resumes=[],
        candidate_expressions=[
            _feedback_expression("ByteDance", candidate_term_type="company_entity", term_family_id="fam-company"),
            _feedback_expression("distributed systems", candidate_term_type="technical_phrase", term_family_id="fam-tried"),
        ],
        tried_term_family_ids={"fam-tried"},
        tried_query_fingerprints=set(),
        policy_version="prf-v1",
    )

    assert decision.gate_passed is False
    assert "existing_or_tried_family" in decision.candidate_expressions[1].reject_reasons
    assert "company_entity_rejected" in decision.candidate_expressions[0].reject_reasons


def test_build_second_lane_decision_selects_prf_probe_when_policy_accepts() -> None:
    retrieval_plan = _retrieval_plan(query_terms=["python", "ranking"])
    prf_decision = _accepted_prf_decision("distributed systems")

    decision, lane = build_second_lane_decision(
        round_no=2,
        retrieval_plan=retrieval_plan,
        query_term_pool=[],
        sent_query_history=[],
        prf_decision=prf_decision,
        run_id="run-a",
        job_intent_fingerprint="job-1",
        source_plan_version="2",
    )

    assert decision.prf_gate_passed is True
    assert decision.selected_lane_type == "prf_probe"
    assert decision.accepted_prf_expression == "distributed systems"
    assert lane is not None
    assert lane.lane_type == "prf_probe"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_round_two_uses_prf_probe_when_gate_passes -q`
Expected: FAIL because there is no replayable PRF gate model and the second-lane runtime cannot yet select `prf_probe`.

- [ ] **Step 3: Add PRF gate input, policy decision, and typed-lane integration**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py
# Extend the existing RoundRetrievalPlan instead of creating a new plan type.
role_anchor_terms: list[str] = Field(default_factory=list)
must_have_anchor_terms: list[str] = Field(default_factory=list)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_plan.py
return RoundRetrievalPlan(
    plan_version=plan_version,
    round_no=round_no,
    query_terms=canonical_terms,
    role_anchor_terms=list(title_anchor_terms),
    must_have_anchor_terms=[
        item.term
        for item in query_term_pool
        if item.category == "must_have" and item.is_active
    ],
    keyword_query=serialize_keyword_query(canonical_terms),
    projected_provider_filters=projected_provider_filters,
    runtime_only_constraints=list(runtime_only_constraints),
    location_execution_plan=location_execution_plan,
    target_new=target_new,
    rationale=rationale,
)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/policy.py
class PRFGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    seed_resume_ids: list[str] = Field(default_factory=list)
    seed_count: int
    negative_resume_ids: list[str] = Field(default_factory=list)
    candidate_expression_count: int
    tried_term_family_ids: list[str] = Field(default_factory=list)
    tried_query_fingerprints: list[str] = Field(default_factory=list)
    min_seed_count: int
    max_negative_support_rate: float
    policy_version: str


class PRFPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempted: bool
    gate_passed: bool
    accepted_expression: FeedbackCandidateExpression | None = None
    candidate_expressions: list[FeedbackCandidateExpression] = Field(default_factory=list)
    reject_reasons: list[str] = Field(default_factory=list)
    gate_input: PRFGateInput


def build_prf_policy_decision(
    *,
    round_no: int,
    seed_resumes: list[ScoredCandidate],
    negative_resumes: list[ScoredCandidate],
    candidate_expressions: list[FeedbackCandidateExpression],
    tried_term_family_ids: set[str],
    tried_query_fingerprints: set[str],
    policy_version: str,
    min_seed_count: int = 2,
    max_negative_support_rate: float = 0.4,
) -> PRFPolicyDecision:
    gate_input = PRFGateInput(
        round_no=round_no,
        seed_resume_ids=[item.resume_id for item in seed_resumes],
        seed_count=len(seed_resumes),
        negative_resume_ids=[item.resume_id for item in negative_resumes],
        candidate_expression_count=len(candidate_expressions),
        tried_term_family_ids=sorted(tried_term_family_ids),
        tried_query_fingerprints=sorted(tried_query_fingerprints),
        min_seed_count=min_seed_count,
        max_negative_support_rate=max_negative_support_rate,
        policy_version=policy_version,
    )
    if len(seed_resumes) < min_seed_count:
        return PRFPolicyDecision(
            attempted=True,
            gate_passed=False,
            candidate_expressions=candidate_expressions,
            reject_reasons=["insufficient_high_quality_seeds"],
            gate_input=gate_input,
        )

    allowed_types = {"skill", "technical_phrase", "product_or_platform"}
    for item in candidate_expressions:
        if item.candidate_term_type not in allowed_types:
            item.reject_reasons.append("company_entity_rejected")
        if item.term_family_id in tried_term_family_ids:
            item.reject_reasons.append("existing_or_tried_family")
        if item.not_fit_support_rate >= max_negative_support_rate:
            item.reject_reasons.append("negative_support_too_high")

    eligible = [item for item in candidate_expressions if not item.reject_reasons]
    accepted = max(eligible, key=lambda item: (item.score, item.positive_seed_support_count), default=None)
    if accepted is None:
        return PRFPolicyDecision(
            attempted=True,
            gate_passed=False,
            candidate_expressions=candidate_expressions,
            reject_reasons=["no_safe_prf_expression"],
            gate_input=gate_input,
        )
    return PRFPolicyDecision(
        attempted=True,
        gate_passed=True,
        accepted_expression=accepted,
        candidate_expressions=candidate_expressions,
        gate_input=gate_input,
    )
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py
def select_prf_anchor(retrieval_plan: RoundRetrievalPlan) -> str:
    if retrieval_plan.role_anchor_terms:
        return retrieval_plan.role_anchor_terms[0]
    if retrieval_plan.must_have_anchor_terms:
        return retrieval_plan.must_have_anchor_terms[0]
    if retrieval_plan.query_terms:
        return retrieval_plan.query_terms[0]
    raise ValueError("retrieval_plan.query_terms must contain at least one anchor term")


if prf_decision is not None and prf_decision.gate_passed and prf_decision.accepted_expression is not None:
    anchor = select_prf_anchor(retrieval_plan)
    query_state = build_logical_query_state(
        run_id=run_id,
        round_no=round_no,
        lane_type="prf_probe",
        query_terms=[anchor, prf_decision.accepted_expression.canonical_expression],
        job_intent_fingerprint=job_intent_fingerprint,
        source_plan_version=source_plan_version,
    )
    return (
        SecondLaneDecision(
            round_no=round_no,
            attempted_prf=True,
            prf_gate_passed=True,
            selected_lane_type="prf_probe",
            selected_query_instance_id=query_state.query_instance_id,
            selected_query_fingerprint=query_state.query_fingerprint,
            accepted_prf_expression=prf_decision.accepted_expression.canonical_expression,
            accepted_prf_term_family_id=prf_decision.accepted_expression.term_family_id,
            prf_seed_resume_ids=prf_decision.gate_input.seed_resume_ids,
            prf_candidate_expression_count=prf_decision.gate_input.candidate_expression_count,
            prf_policy_version=prf_decision.gate_input.policy_version,
        ),
        query_state,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_round_two_uses_prf_probe_when_gate_passes -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/retrieval/query_plan.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/policy.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/second_lane_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_candidate_feedback.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_second_lane_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py
git commit -m "Integrate replayable PRF policy into typed second lane"
```

## Task 6: Make Budget, Refill, And Query Outcomes Score-Aware

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write the failing score-aware allocation and outcome tests**

```python
def test_second_lane_starts_with_seventy_thirty_allocation() -> None:
    query_states = [
        LogicalQueryState(query_role="exploit", lane_type="exploit", query_terms=["python"], keyword_query="python", query_instance_id="q1", query_fingerprint="f1"),
        LogicalQueryState(query_role="explore", lane_type="generic_explore", query_terms=["python", "trace"], keyword_query="python trace", query_instance_id="q2", query_fingerprint="f2"),
    ]

    targets = allocate_initial_lane_targets(query_states=query_states, target_new=10)
    assert targets == {"exploit": 7, "generic_explore": 3}


def test_second_lane_allocation_does_not_exceed_small_target() -> None:
    query_states = [
        LogicalQueryState(query_role="exploit", lane_type="exploit", query_terms=["python"], keyword_query="python", query_instance_id="q1", query_fingerprint="f1"),
        LogicalQueryState(query_role="explore", lane_type="generic_explore", query_terms=["python", "trace"], keyword_query="python trace", query_instance_id="q2", query_fingerprint="f2"),
    ]

    assert allocate_initial_lane_targets(query_states=query_states, target_new=1) == {"exploit": 1, "generic_explore": 0}


def test_classify_query_outcome_returns_primary_and_secondary_labels() -> None:
    outcome = classify_query_outcome(
        provider_returned_count=6,
        new_unique_resume_count=2,
        new_fit_or_near_fit_count=1,
        fit_rate=0.16,
        must_have_match_avg=20.0,
        exploit_baseline_must_have_match_avg=50.0,
        off_intent_reason_count=3,
        thresholds=QueryOutcomeThresholds(),
    )

    assert outcome.primary_label == "drift_suspected"
    assert set(outcome.labels) >= {"marginal_gain", "drift_suspected"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_second_lane_starts_with_seventy_thirty_allocation /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_second_lane_allocation_does_not_exceed_small_target -q`
Expected: FAIL because allocation still assumes list-order semantics and query outcomes are still a single string.

- [ ] **Step 3: Add structured outcome classification and lane-target allocation**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py
class QueryOutcomeThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_recall_threshold: int = 2
    high_precision_threshold: float = 0.7
    noise_threshold: float = 0.1
    must_have_noise_threshold: float = 30.0
    drift_must_have_drop: float = 15.0
    drift_off_intent_min_count: int = 2


class QueryOutcomeClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_label: str
    labels: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py
def classify_query_outcome(
    *,
    provider_returned_count: int,
    new_unique_resume_count: int,
    new_fit_or_near_fit_count: int,
    fit_rate: float,
    must_have_match_avg: float,
    exploit_baseline_must_have_match_avg: float,
    off_intent_reason_count: int,
    thresholds: QueryOutcomeThresholds,
) -> QueryOutcomeClassification:
    labels: set[str] = set()
    reasons: list[str] = []

    if provider_returned_count == 0:
        labels.add("zero_recall")
        reasons.append("provider_returned_count == 0")
    if provider_returned_count > 0 and new_unique_resume_count == 0:
        labels.add("duplicate_only")
        reasons.append("new_unique_resume_count == 0")
    if new_fit_or_near_fit_count >= 1:
        labels.add("marginal_gain")
        reasons.append("new_fit_or_near_fit_count >= 1")
    if (
        new_unique_resume_count >= 1
        and fit_rate <= thresholds.noise_threshold
        and must_have_match_avg <= thresholds.must_have_noise_threshold
    ):
        labels.add("broad_noise")
        reasons.append("fit_rate and must_have_match_avg both indicate noise")
    if (
        must_have_match_avg < exploit_baseline_must_have_match_avg - thresholds.drift_must_have_drop
        and off_intent_reason_count >= thresholds.drift_off_intent_min_count
    ):
        labels.add("drift_suspected")
        reasons.append("must_have_match_avg dropped materially against exploit baseline")
    if (
        new_unique_resume_count <= thresholds.low_recall_threshold
        and fit_rate >= thresholds.high_precision_threshold
    ):
        labels.add("low_recall_high_precision")
        reasons.append("small sample but high precision")

    priority = [
        "zero_recall",
        "duplicate_only",
        "drift_suspected",
        "broad_noise",
        "marginal_gain",
        "low_recall_high_precision",
    ]
    primary_label = next((label for label in priority if label in labels), "low_recall_high_precision")
    return QueryOutcomeClassification(primary_label=primary_label, labels=sorted(labels), reasons=reasons)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py
def allocate_initial_lane_targets(*, query_states: list[LogicalQueryState], target_new: int) -> dict[str, int]:
    lane_types = [state.lane_type for state in query_states]
    if len(lane_types) <= 1:
        return {lane_types[0]: target_new}
    if target_new <= 1:
        return {"exploit": target_new, lane_types[1]: 0}

    exploit_target = max(1, math.ceil(target_new * 0.7))
    second_target = max(1, target_new - exploit_target)
    if exploit_target + second_target > target_new:
        exploit_target = target_new - second_target
    return {"exploit": exploit_target, lane_types[1]: second_target}


def allow_lane_refill(*, lane_type: LaneType, outcome: QueryOutcomeClassification) -> bool:
    if lane_type == "exploit":
        return True
    return not set(outcome.labels) & {"zero_recall", "duplicate_only", "broad_noise", "drift_suspected"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_second_lane_starts_with_seventy_thirty_allocation /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py::test_second_lane_allocation_does_not_exceed_small_target -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/retrieval_runtime.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py
git commit -m "Make second-lane budget and outcomes score-aware"
```

## Task 7: Add Replay Snapshot And Primary-Comparison Company Isolation

**Files:**
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/evaluation.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tools/run_global_benchmark.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/baseline_evaluation.py`
- Modify: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py`
- Test: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py`

- [ ] **Step 1: Write the failing replay-snapshot and company-isolation tests**

```python
def test_replay_snapshot_contains_provider_snapshot_and_versions(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=2)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace")

    try:
        run_state = asyncio.run(runtime._build_run_state(*_sample_inputs(), tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    snapshot = json.loads((tracer.run_dir / "rounds" / "round_02" / "replay_snapshot.json").read_text())
    assert snapshot["retrieval_snapshot_id"]
    assert snapshot["provider_request"]
    assert snapshot["provider_response_resume_ids"]
    assert snapshot["provider_response_raw_rank"]
    assert snapshot["dedupe_version"]
    assert snapshot["scoring_model_version"]
    assert snapshot["query_plan_version"]
    assert snapshot["prf_gate_version"]
    assert snapshot["generic_explore_version"]


def test_primary_policy_comparison_does_not_call_company_rescue(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"value": False}

    async def _fail(*args, **kwargs):
        called["value"] = True
        raise AssertionError("company rescue should be disabled in primary comparison")

    monkeypatch.setattr(CompanyDiscoveryService, "discover_web", _fail)
    config = build_policy_comparison_config(mode="candidate")
    assert config.target_company_enabled is False
    assert config.company_discovery_enabled is False
    assert called["value"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py::test_replay_snapshot_contains_provider_snapshot_and_versions /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py::test_primary_policy_comparison_does_not_call_company_rescue -q`
Expected: FAIL because there is no replay snapshot artifact and the benchmark entrypoint only flips one company flag.

- [ ] **Step 3: Add replay snapshot artifact and primary-comparison isolation**

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py
class ReplaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    round_no: int
    retrieval_snapshot_id: str
    baseline_second_lane_query_fingerprint: str | None = None
    candidate_second_lane_query_fingerprint: str | None = None
    provider_request: dict[str, Any]
    provider_response_resume_ids: list[str]
    provider_response_raw_rank: list[str]
    provider_response_raw_metadata: dict[str, Any] = Field(default_factory=dict)
    dedupe_version: str
    scoring_model_version: str
    rerank_model_version: str | None = None
    query_plan_version: str
    prf_extractor_version: str
    prf_gate_version: str
    generic_explore_version: str
    company_rescue_policy_version: str | None = None
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py
provider_requests = [item.request_payload for item in round_state.search_attempts]
provider_response_resume_ids = (
    list(round_state.search_observation.new_resume_ids)
    if round_state.search_observation is not None
    else []
)
snapshot = ReplaySnapshot(
    run_id=run_state.run_id,
    round_no=round_no,
    retrieval_snapshot_id=f"{run_state.run_id}:round:{round_no}",
    baseline_second_lane_query_fingerprint=None,
    candidate_second_lane_query_fingerprint=second_lane_decision.selected_query_fingerprint,
    provider_request={"search_attempts": provider_requests},
    provider_response_resume_ids=provider_response_resume_ids,
    provider_response_raw_rank=provider_response_resume_ids,
    provider_response_raw_metadata={
        "requested_pages": [item.requested_page for item in round_state.search_attempts],
        "provider": "cts",
    },
    dedupe_version="v1",
    scoring_model_version=self.settings.scoring_model,
    query_plan_version=str(retrieval_plan.plan_version),
    prf_extractor_version="v1",
    prf_gate_version=second_lane_decision.prf_policy_version,
    generic_explore_version=second_lane_decision.generic_explore_version or "v1",
    company_rescue_policy_version=None,
)
tracer.write_json(
    f"rounds/round_{round_no:02d}/replay_snapshot.json",
    snapshot.model_dump(mode="json"),
)
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/tools/run_global_benchmark.py
def build_policy_comparison_config(*, mode: str) -> AppSettings:
    settings = load_settings()
    settings.target_company_enabled = False
    settings.company_discovery_enabled = False
    return settings
```

```python
# /Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md
- `rounds/round_XX/second_lane_decision.json`
  Typed second-lane routing decision with PRF gate inputs, selected lane, fallback lane, and query identity.
- `rounds/round_XX/query_resume_hits.json`
  Query-to-resume visibility ledger, enriched after scoring.
- `rounds/round_XX/replay_snapshot.json`
  Minimal retrieval snapshot plus version vector for baseline-vs-candidate replay.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py::test_replay_snapshot_contains_provider_snapshot_and_versions /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py::test_primary_policy_comparison_does_not_call_company_rescue /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/models.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/runtime_diagnostics.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/evaluation.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tools/run_global_benchmark.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/experiments/baseline_evaluation.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/docs/outputs.md \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_experiment_entrypoints.py \
        /Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_evaluation.py
git commit -m "Add replay snapshot and company-isolated comparison artifacts"
```

## Spec Coverage Check

- Query identity and canonical spec: covered by Task 1.
- Typed second-lane routing before attribution: covered by Task 2.
- First-hit attribution and full lane visibility: covered by Task 3.
- `candidate_feedback` promoted into `PRF v1` expression families: covered by Task 4.
- Replayable PRF gate decisions and `prf_probe if safe else generic_explore`: covered by Task 5.
- `70/30` second-lane allocation, small-target safety, and structured outcome labels: covered by Task 6.
- Replay reproducibility snapshot and company-isolated primary comparison: covered by Task 7.

## Self-Review Notes

- Task order now follows dependency order: identity -> lane skeleton -> attribution ledger -> extraction -> PRF gate -> budget/outcomes -> replay/isolation.
- `first_round_no` is explicitly included and duplicate hits never overwrite first-hit attribution.
- `query_resume_hits` is defined as a post-score-enriched ledger, not a retrieval-only artifact.
- `SecondLaneDecision` includes selected lane, selected query IDs, accepted PRF expression, gate inputs, and policy versions.
- `ReplaySnapshot` is a real artifact, not an implied future idea.
- Company isolation in Phase 1 is enforced by disabling company rescue in the primary comparison and by behavior tests, rather than by inventing non-existent mainline company scoring toggles.
