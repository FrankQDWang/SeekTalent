# Liepin Detail-Backed Runtime Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change Liepin from a card-returning Runtime lane to a detail-backed Runtime lane that returns complete resumes only.

**Architecture:** Runtime keeps owning query planning and 70/30 allocation. Liepin receives one frozen `LogicalQueryDispatch` per Pi RPC task, runs exploit and explore tasks concurrently, lets Pi use card summaries only for internal screening, opens promising detail pages, maps detail pages into the shared `ResumeCandidate`/`normalize_resume(...)` path, and returns only detail-backed candidates to Runtime.

**Tech Stack:** Python 3.12, pytest, ruff, ty, Svelte/Vite build, Pi RPC, Pi TypeScript extensions, OpenCLI-backed real Chrome QA.

---

Linked spec: `docs/superpowers/specs/2026-05-24-liepin-detail-backed-runtime-lane-design.md`

## File Structure

- Modify `src/seektalent/providers/liepin/worker_contracts.py`
  - Add `LiepinResumeSearchResponse` for complete-resume search output.
  - Reuse `LiepinWorkerCandidateDetail` for returned candidates.
- Modify `src/seektalent/providers/liepin/pi_executor.py`
  - Add `search_resumes(...)` and strict validation for `seektalent.pi_liepin_resumes.v1`.
  - Keep `search_cards(...)` for legacy tests only until callers are migrated.
- Modify `src/seektalent/providers/pi_agent/pi_external.py`
  - Add task contract text for `liepin.search_resumes`.
  - Make that contract describe must-have/nice-to-have card screening and complete-resume-only output.
  - Do not add a new skill. Keep the prompt contract as the source of task behavior.
- Modify `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`
  - Register one high-level tool `seektalent_opencli_search_liepin_resumes`.
  - Keep low-level tools available only for probes/legacy if needed, not as the recommended main path.
- Modify `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Add `search_liepin_resumes(...)`: search, apply native filters, read cards, score card promise locally, open detail pages, extract complete resume payloads, stop at target count.
  - Reuse existing safe browser policy, allowed hosts, forbidden action fragments, lease/GC, and protected trace writing.
- Modify `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
  - Add action `search_resumes` forwarding `targetResumes`, `mustHaves`, `niceToHaves`, and `nativeFilters`.
- Modify `src/seektalent/providers/liepin/pi_worker_client.py`
  - Make `search(...)` call `executor.search_resumes(...)` for Runtime source search.
  - Map successful detail results through `liepin_resume_search_response_to_search_result(...)`.
- Modify `src/seektalent/providers/liepin/client.py`
  - Add `liepin_resume_search_response_to_search_result(...)`.
- Modify `src/seektalent/providers/liepin/mapper.py`
  - Ensure detail candidates populate raw fields that `normalize_resume(...)` already understands.
- Modify `src/seektalent/providers/liepin/runtime_lane.py`
  - Rename internal card-lane assumptions to complete-resume source lane.
  - Run one Pi task per logical query concurrently with `asyncio.TaskGroup`.
  - Reject `card_only` evidence at the Runtime boundary.
- Modify `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
  - Remove contradictory card-only/detail-forbidden task language if this file remains loaded by Pi bootstrap.
  - Do not create a new skill file.
- Modify `TODOS.md`
  - Keep node-detail visualization completeness as a follow-up.
  - Add any advanced/manual detail approval UI as a follow-up if not covered here.

Tests:
- Modify `tests/test_liepin_runtime_source_lane.py`
- Modify `tests/test_liepin_pi_executor.py`
- Modify `tests/test_liepin_pi_worker_client.py`
- Modify `tests/test_pi_external_agent.py`
- Modify `tests/test_pi_opencli_browser.py`
- Modify `tests/test_pi_agent_boundaries.py`
- Modify `tests/test_normalization.py`
- Modify `tests/test_runtime_multi_source_round_dispatch.py`

## Decisions

- Resume cleaning: reuse `normalize_resume(...)`; do not create a Liepin-specific cleaner.
- Skill: do not add a new skill. The task behavior belongs in `_task_contract_for_prompt(...)` and the high-level Pi extension tool. The existing skill file can remain only as a non-conflicting safety policy because current bootstrap still supplies `--skill`.
- Parallelism: exploit and explore run as two Pi RPC tasks in parallel for each Runtime round.
- Requested count: `LogicalQueryDispatch.requested_count` means complete resumes requested, not cards requested.
- Card budget: implementation may read more cards than requested resumes, but this is an internal adapter budget and must be bounded.
- Liepin default card scan budget: `min(requested_count * 3, liepin_max_cards)` per logical query. The returned complete-resume count remains exactly `requested_count`.
- Detail safety: generic OpenCLI actions still reject resume/detail clicks and URLs. Only `search_liepin_resumes(...)` gets a narrow, audited detail-open path.
- Payload safety: Runtime receives redacted complete-resume payloads. Raw provider detail snapshots stay in protected artifacts and direct contact data is rejected from the returned payload.

## Execution Slices

Build this as three verified slices, even though the plan stays one complete scope:

```text
Slice 1: Contract boundary
  Pi resume envelope -> LiepinResumeSearchResponse -> map_liepin_worker_detail
      -> ResumeCandidate(raw detail_enriched) -> normalize_resume(...)

Slice 2: Browser/Pi execution
  Runtime task prompt -> seektalent_opencli_search_liepin_resumes
      -> search page + native filters -> card screening -> audited detail opens
      -> redacted detail payload + protected trace refs

Slice 3: Runtime ownership
  LogicalQueryDispatch bundle
      -> exploit Pi task requested_count=7
      -> explore Pi task requested_count=3
      -> merge detail-backed candidates only -> Runtime ranking/finalization
```

Do not start Slice 2 until Slice 1 tests pass. Do not start Slice 3 until Slice 2 can return one valid redacted detail-backed resume through the fake OpenCLI runner.

## Task 1: Add Complete-Resume Worker Contract

**Files:**
- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Test: `tests/test_liepin_pi_worker_client.py`

- [ ] **Step 1: Write failing response conversion test**

Append to `tests/test_liepin_pi_worker_client.py`:

```python
def test_liepin_resume_search_response_maps_detail_candidates_only() -> None:
    from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
    from seektalent.providers.liepin.worker_contracts import LiepinResumeSearchResponse

    response = LiepinResumeSearchResponse.model_validate(
        {
            "resumes": [
                {
                    "payload": {
                        "providerCandidateKeyHash": "hash-1",
                        "providerRank": 1,
                        "fullText": "候选人具备数据仓库、数据治理、Python 和大规模数据平台经验。",
                        "workExperienceList": [
                            {"company": "Example", "title": "数据开发专家", "summary": "负责数据平台建设。"}
                        ],
                        "educationList": [{"school": "北京大学", "degree": "本科", "speciality": "计算机"}],
                    },
                    "normalized_text": "数据开发专家 数据仓库 数据治理 Python 大规模数据平台",
                    "provider_subject_id": "liepin-subject-1",
                    "provider_listing_id": "listing-1",
                    "synthetic_candidate_fingerprint": "fp-1",
                    "identity_confidence": "provider_subject_id",
                    "extraction_source": "dom_fallback",
                    "extractor_version": "pi-agent-liepin-detail-v1",
                    "pii_classification": "no_direct_contact",
                    "retention_policy": "provider_snapshot_30d",
                    "access_scope": "local_run_only",
                    "redaction_state": "redacted",
                }
            ],
            "diagnostics": [],
            "exhausted": True,
            "requestPayload": {"sourceRunId": "run-1", "query": "数据开发"},
            "rawCandidateCount": 4,
        }
    )

    result = liepin_resume_search_response_to_search_result(response)

    assert len(result.candidates) == 1
    assert result.candidates[0].raw["score_evidence_source"] == "detail_enriched"
    assert result.provider_snapshots[0].payload_kind == "detail"
    assert result.provider_snapshots[0].score_evidence_source == "detail_enriched"
    assert result.raw_candidate_count == 4
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_liepin_pi_worker_client.py::test_liepin_resume_search_response_maps_detail_candidates_only -q
```

Expected: FAIL because `LiepinResumeSearchResponse` and `liepin_resume_search_response_to_search_result` do not exist.

- [ ] **Step 3: Add response model**

Add to `src/seektalent/providers/liepin/worker_contracts.py`:

```python
class LiepinResumeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    resumes: list[LiepinWorkerCandidateDetail]
    diagnostics: list[str] = Field(default_factory=list)
    exhausted: bool = False
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    request_payload: dict[str, Any] = Field(default_factory=dict, alias="requestPayload")
    raw_candidate_count: int | None = Field(default=None, alias="rawCandidateCount")


def decode_resume_search_response(payload: dict[str, object]) -> LiepinResumeSearchResponse:
    return LiepinResumeSearchResponse.model_validate(payload)
```

- [ ] **Step 4: Add conversion helper**

Add to `src/seektalent/providers/liepin/client.py`:

```python
def liepin_resume_search_response_to_search_result(response: LiepinResumeSearchResponse) -> SearchResult:
    mapped = [map_liepin_worker_detail(detail) for detail in response.resumes]
    return SearchResult(
        candidates=[item.candidate for item in mapped],
        diagnostics=response.diagnostics,
        exhausted=response.exhausted,
        next_cursor=response.next_cursor,
        request_payload=_safe_search_request_payload(response.request_payload),
        provider_snapshots=[item.provider_snapshot for item in mapped],
        raw_candidate_count=response.raw_candidate_count
        if response.raw_candidate_count is not None
        else len(response.resumes),
    )
```

Also import `LiepinResumeSearchResponse` and `map_liepin_worker_detail`.

- [ ] **Step 5: Run test and verify pass**

Run:

```bash
uv run pytest tests/test_liepin_pi_worker_client.py::test_liepin_resume_search_response_maps_detail_candidates_only -q
```

Expected: PASS.

## Task 2: Reuse Shared Resume Normalization For Liepin Details

**Files:**
- Modify: `src/seektalent/providers/liepin/mapper.py`
- Test: `tests/test_normalization.py`

- [ ] **Step 1: Write failing normalization test**

Append to `tests/test_normalization.py`:

```python
def test_liepin_detail_candidate_reuses_shared_full_resume_normalization() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-detail-1",
        dedup_key="dedup-liepin-detail-1",
        search_text="数据开发专家 数据仓库 数据治理 Python Hive Spark",
        raw={
            "provider": "liepin",
            "score_evidence_source": "detail_enriched",
            "candidate_name": "张三",
            "currentTitle": "数据开发专家",
            "currentCompany": "Example Data",
            "fullText": "负责数据仓库、数据治理、ETL、Python、Hive、Spark 与大规模数据平台建设。",
            "workExperienceList": [
                {
                    "company": "Example Data",
                    "title": "数据开发专家",
                    "duration": "2020.01-至今",
                    "summary": "建设大规模数据平台、数据治理和 ETL 链路。",
                }
            ],
            "educationList": [{"school": "北京大学", "degree": "本科", "speciality": "计算机"}],
            "skills": ["Python", "Hive", "Spark"],
            "locations": ["北京"],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.candidate_name == "张三"
    assert normalized.current_title == "数据开发专家"
    assert normalized.current_company == "Example Data"
    assert normalized.education_summary == "北京大学 计算机 本科"
    assert "Python" in normalized.skills
    assert "北京" in normalized.locations
    assert "大规模数据平台" in normalized.raw_text_excerpt
    assert normalized.score_evidence_source == "detail_enriched"
    assert normalized.completeness_score >= 80
```

- [ ] **Step 2: Run test**

Run:

```bash
uv run pytest tests/test_normalization.py::test_liepin_detail_candidate_reuses_shared_full_resume_normalization -q
```

Expected: PASS if the shared normalizer already covers the raw shape. If it fails only because `educationList` uses `major` instead of `speciality`, extend `_extract_education_summary(...)` to read both keys:

```python
_first_text(item.get("speciality"), item.get("major")),
```

- [ ] **Step 3: Run all normalization tests**

Run:

```bash
uv run pytest tests/test_normalization.py -q
```

Expected: PASS.

## Task 3: Add Pi Detail-Backed Search Envelope And Prompt Contract

**Files:**
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Test: `tests/test_liepin_pi_executor.py`
- Test: `tests/test_pi_external_agent.py`

- [ ] **Step 1: Write failing executor test**

Append to `tests/test_liepin_pi_executor.py`:

```python
def test_pi_executor_search_resumes_returns_detail_enriched_response(tmp_path: Path) -> None:
    envelope = {
        "schema_version": "seektalent.pi_liepin_resumes.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "st-run-1",
        "query": "数据开发",
        "cards_seen": 5,
        "resumes_returned": 1,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "protected_snapshot_refs": ["artifact://protected/pi-detail/run-1/1.json"],
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": "artifact://protected/pi-key/run-1/1.txt",
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "detail_payload": {
                    "fullText": "数据开发专家，负责数据仓库、数据治理和 Python 平台。",
                    "currentTitle": "数据开发专家",
                    "currentCompany": "Example",
                    "workExperienceList": [
                        {"company": "Example", "title": "数据开发专家", "summary": "负责数据平台。"}
                    ],
                    "educationList": [{"school": "北京大学", "degree": "本科", "speciality": "计算机"}],
                    "skills": ["Python", "Hive"],
                    "locations": ["北京"],
                },
                "normalized_text": "数据开发专家 数据仓库 数据治理 Python Hive",
            }
        ],
    }
    executor = PiLiepinExecutor(
        client=_client(json.dumps(envelope)),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1/action-trace.json",
            "artifact://protected/pi-detail/run-1/1.json",
            "artifact://protected/pi-key/run-1/1.txt",
            materials={
                "artifact://protected/pi-trace/run-1/action-trace.json": json.dumps(
                    {
                        "schema_version": "seektalent.opencli_action_trace.v1",
                        "mode": "detail_backed_resume_search",
                        "events": [{"action_kind": "open_detail", "route_kind": "detail"}],
                    }
                ).encode("utf-8")
            },
        ),
    )

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="数据开发",
        query_terms=("数据开发",),
        target_resumes=1,
        max_cards=10,
        max_pages=1,
        must_haves=("数据治理",),
        nice_to_haves=("Python",),
        native_filters=None,
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    assert result.resume_search is not None
    assert result.resume_search.resumes[0].payload["fullText"].startswith("数据开发专家")
    assert result.resume_search.resumes[0].payload["providerCandidateKeyHash"] == "hmac:liepin:1.txt"
```

Add `PiLiepinResultStatus` to the imports from `seektalent.providers.liepin.pi_executor`. Reuse the existing `_client`, `_registry`, and `FakeProviderKeyHasher` helpers already defined in `tests/test_liepin_pi_executor.py`.

- [ ] **Step 2: Write failing prompt-contract test**

Append to `tests/test_pi_external_agent.py`:

```python
def test_liepin_search_resumes_prompt_contract_is_complete_resume_only() -> None:
    prompt = json.dumps(
        {
            "task": "liepin.search_resumes",
            "query": "数据开发",
            "target_resumes": 7,
            "must_haves": ["数据治理"],
            "nice_to_haves": ["Python"],
        },
        ensure_ascii=False,
    )

    contract = _task_contract_for_prompt(prompt)

    assert "complete resumes only" in contract
    assert "must-have" in contract
    assert "nice-to-have" in contract
    assert "card summaries are internal screening evidence" in contract
    assert "seektalent_opencli_search_liepin_resumes" in contract
```

- [ ] **Step 3: Write failing Pi RPC tool-event extraction test**

Append to `tests/test_pi_external_agent.py`:

```python
def test_liepin_search_resumes_uses_tool_event_envelope_when_agent_final_text_is_missing() -> None:
    tool_payload = {
        "schema_version": "seektalent.pi_liepin_resumes.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "st-run-1",
        "query": "数据开发",
        "cards_seen": 1,
        "resumes_returned": 1,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/st-run-1/action-trace.json",
        "protected_snapshot_refs": ["artifact://protected/pi-detail/st-run-1/1.json"],
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": "artifact://protected/pi-key/st-run-1/1.txt",
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/st-run-1/1.json",
                "detail_payload": {"fullText": "数据开发专家 数据治理 Python"},
                "normalized_text": "数据开发专家 数据治理 Python",
            }
        ],
    }
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
        skill_path=Path("skill.md"),
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=Path("artifacts/pi-agent"),
        transport=FakeRpcTransport(
            PiRpcTaskResult(
                status=PiRpcTaskStatus.SUCCEEDED,
                final_text="not json",
                events=(
                    {
                        "type": "tool_execution_end",
                        "toolName": "seektalent_opencli_search_liepin_resumes",
                        "result": json.dumps(tool_payload, ensure_ascii=False),
                    },
                ),
            )
        ),
    )

    result = client.run_json_task_result(json.dumps({"task": "liepin.search_resumes"}, ensure_ascii=False))

    assert result.ok is True
    assert result.envelope == tool_payload
```

- [ ] **Step 4: Write failing unsafe detail payload rejection test**

Append to `tests/test_liepin_pi_executor.py`:

```python
def test_pi_executor_search_resumes_rejects_contact_data_in_runtime_payload() -> None:
    envelope = {
        "schema_version": "seektalent.pi_liepin_resumes.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "st-run-1",
        "query": "数据开发",
        "cards_seen": 1,
        "resumes_returned": 1,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "protected_snapshot_refs": ["artifact://protected/pi-detail/run-1/1.json"],
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": "artifact://protected/pi-key/run-1/1.txt",
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "detail_payload": {"fullText": "候选人 手机 13800138000 数据治理 Python"},
                "normalized_text": "候选人 数据治理 Python",
            }
        ],
    }
    executor = PiLiepinExecutor(
        client=_client(json.dumps(envelope)),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1/action-trace.json",
            "artifact://protected/pi-detail/run-1/1.json",
            "artifact://protected/pi-key/run-1/1.txt",
            materials={
                "artifact://protected/pi-trace/run-1/action-trace.json": json.dumps(
                    {
                        "schema_version": "seektalent.opencli_action_trace.v1",
                        "mode": "detail_backed_resume_search",
                        "events": [{"action_kind": "open_detail", "route_kind": "detail"}],
                    }
                ).encode("utf-8")
            },
        ),
    )

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="数据开发",
        query_terms=("数据开发",),
        target_resumes=1,
        max_cards=10,
        max_pages=1,
        must_haves=("数据治理",),
        nice_to_haves=("Python",),
        native_filters=None,
    )

    assert result.status == PiLiepinResultStatus.FAILED
    assert result.safe_reason_code == "failed_provider_error"
```

- [ ] **Step 5: Run tests and verify failure**

Run:

```bash
uv run pytest \
  tests/test_liepin_pi_executor.py::test_pi_executor_search_resumes_returns_detail_enriched_response \
  tests/test_liepin_pi_executor.py::test_pi_executor_search_resumes_rejects_contact_data_in_runtime_payload \
  tests/test_pi_external_agent.py::test_liepin_search_resumes_prompt_contract_is_complete_resume_only \
  tests/test_pi_external_agent.py::test_liepin_search_resumes_uses_tool_event_envelope_when_agent_final_text_is_missing \
  -q
```

Expected: FAIL because the new envelope, executor method, prompt contract, and tool-event extraction do not exist.

- [ ] **Step 6: Add strict resume envelope models**

In `src/seektalent/providers/liepin/pi_executor.py`, add models next to `_PiLiepinCardsEnvelope`:

```python
class _PiLiepinResume(_StrictModel):
    provider_rank: int = Field(ge=1)
    provider_candidate_key_material_ref: str
    candidate_resume_id: str
    protected_snapshot_ref: str
    detail_payload: dict[str, object]
    normalized_text: str


class _PiLiepinResumesEnvelope(_StrictModel):
    schema_version: Literal["seektalent.pi_liepin_resumes.v1"]
    status: Literal["succeeded", "partial", "blocked", "failed"]
    stop_reason: Literal[
        "completed",
        "partial_timeout",
        "blocked_login_required",
        "blocked_permission_required",
        "blocked_backend_unavailable",
        "blocked_budget_exhausted",
        "failed_provider_error",
        "failed_malformed_output",
    ] | None = None
    source_run_id: str
    query: str
    cards_seen: int = Field(ge=0)
    resumes_returned: int = Field(ge=0)
    pages_visited: int = Field(ge=0)
    action_trace_ref: str
    protected_snapshot_refs: list[str] = Field(default_factory=list)
    resumes: list[_PiLiepinResume] = Field(default_factory=list)
    safe_reason_code: str | None = None

    @model_validator(mode="after")
    def validate_counts(self) -> "_PiLiepinResumesEnvelope":
        if self.resumes_returned != len(self.resumes):
            raise ValueError("resumes_returned must equal len(resumes)")
        if self.resumes_returned > self.cards_seen:
            raise ValueError("resumes_returned must not exceed cards_seen")
        ranks = [resume.provider_rank for resume in self.resumes]
        if len(ranks) != len(set(ranks)):
            raise ValueError("provider_rank must be unique")
        if self.status in {"blocked", "failed"} and self.resumes_returned:
            raise ValueError("blocked or failed resume search must not return resumes")
        if self.safe_reason_code is not None and self.safe_reason_code not in OPENCLI_SAFE_REASON_CODES:
            raise ValueError("safe_reason_code must be allowlisted")
        return self
```

- [ ] **Step 7: Add executor result, trace validation, and mapper**

Add:

```python
@dataclass(frozen=True, kw_only=True)
class LiepinPiResumeSearchResult:
    status: PiLiepinResultStatus
    stop_reason: PiLiepinStopReason
    safe_reason_code: str
    action_trace_ref: str | None = None
    resume_search: LiepinResumeSearchResponse | None = None
```

Add method `search_resumes(...)` mirroring `search_cards(...)`, but with:

```python
task = {
    "task": "liepin.search_resumes",
    "schema_version": "seektalent.pi_liepin_resumes.v1",
    "source_run_id": tool_source_run_id,
    "query": keyword_query,
    "query_terms": list(query_terms),
    "target_resumes": target_resumes,
    "max_cards": max_cards,
    "max_pages": max_pages,
    "must_haves": list(must_haves),
    "nice_to_haves": list(nice_to_haves),
    "mode": "detail_backed_resume_search",
}
```

Map each `_PiLiepinResume` into `LiepinWorkerCandidateDetail` with:

```python
"payload": {
    **resume.detail_payload,
    "providerCandidateKeyHash": provider_candidate_hash,
    "providerRank": resume.provider_rank,
    "sourceRunId": source_run_id,
    "protectedSnapshotRef": resume.protected_snapshot_ref,
    "actionTraceRef": action_trace_ref,
},
"normalized_text": resume.normalized_text,
"provider_subject_id": provider_candidate_hash,
"provider_listing_id": None,
"synthetic_candidate_fingerprint": hashlib.sha256(f"liepin:{provider_candidate_hash}".encode("utf-8")).hexdigest(),
"identity_confidence": "provider_subject_id",
"extraction_source": "dom_fallback",
"extractor_version": "pi-agent-liepin-detail-v1",
"pii_classification": "no_direct_contact",
"retention_policy": "provider_snapshot_30d",
"access_scope": "local_run_only",
"redaction_state": "redacted",
```

Before mapping resumes:

- Validate `envelope.action_trace_ref` and each protected snapshot ref with `validate_public_artifact_ref`.
- Add `_validate_resume_mode_trace_ref(...)` that resolves the action trace, requires `mode == "detail_backed_resume_search"`, requires at least one `open_detail` event when `resumes_returned > 0`, and rejects contact/chat/download/payment action events.
- Add `_assert_safe_resume_detail_payload(...)` that rejects direct phone, email, WeChat, cookie/storage/auth strings, raw HTML, local paths, and suspicious raw provider blobs from `detail_payload`.
- Allow redacted full resume text longer than `SafePayloadFirewall.assert_safe_text(...)` if needed, but keep a hard cap such as 30,000 characters and still reject direct contact/secrets.

- [ ] **Step 8: Add Pi RPC tool-event extraction**

In `src/seektalent/providers/pi_agent/pi_external.py`:

- Generalize `_strict_cards_envelope_from_tool_events(...)` into a Liepin tool-envelope extractor that recognizes both `seektalent.pi_liepin_cards.v1` and `seektalent.pi_liepin_resumes.v1`.
- Update `SubprocessPiRpcTransport.request(...)` so a `seektalent_opencli_search_liepin_resumes` tool result is treated like the card result: stop the Pi process and return the strict envelope as `final_text`.
- Update `PiRpcAgentClient._run_json_task_result_once(...)` so `task_name == "liepin.search_resumes"` can recover the resume envelope from events when `agent_end` final text is missing or malformed.

- [ ] **Step 9: Add prompt contract**

In `_task_contract_for_prompt(...)`, add:

```python
if task_name == "liepin.search_resumes":
    return (
        "For task liepin.search_resumes, call seektalent_opencli_search_liepin_resumes exactly once with "
        "sourceRunId, query, targetResumes, maxCards, maxPages, nativeFilters, mustHaves, and niceToHaves from "
        "the input task when present. Return that tool result exactly as final raw JSON. Runtime accepts complete "
        "resumes only; card summaries are internal screening evidence and must not be returned as candidates. "
        "Use card summaries to choose cards likely to satisfy must-have requirements first, then nice-to-have "
        "requirements, then provider order. Do not invent filters, requirements, or new searches. Do not use "
        "contact, chat, payment, download, cookies, storage, eval, provider APIs, or raw network calls.\n"
    )
```

- [ ] **Step 10: Run tests**

Run:

```bash
uv run pytest \
  tests/test_liepin_pi_executor.py::test_pi_executor_search_resumes_returns_detail_enriched_response \
  tests/test_liepin_pi_executor.py::test_pi_executor_search_resumes_rejects_contact_data_in_runtime_payload \
  tests/test_pi_external_agent.py::test_liepin_search_resumes_prompt_contract_is_complete_resume_only \
  tests/test_pi_external_agent.py::test_liepin_search_resumes_uses_tool_event_envelope_when_agent_final_text_is_missing \
  -q
```

Expected: PASS.

## Task 4: Add High-Level OpenCLI Resume Search Tool

**Files:**
- Modify: `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Test: `tests/test_pi_opencli_browser.py`
- Test: `tests/test_pi_agent_boundaries.py`

- [ ] **Step 1: Write failing boundary test**

Append to `tests/test_pi_agent_boundaries.py`:

```python
def test_opencli_extension_exposes_high_level_resume_search_tool() -> None:
    text = Path("src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts").read_text(
        encoding="utf-8"
    )

    assert "seektalent_opencli_search_liepin_resumes" in text
    assert "targetResumes" in text
    assert "mustHaves" in text
    assert "niceToHaves" in text
```

- [ ] **Step 2: Write failing browser runner test**

Append to `tests/test_pi_opencli_browser.py`:

```python
def test_search_liepin_resumes_opens_detail_after_promising_card(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_cards = (
        "[120]<a href=https://h.liepin.com/resume/showresumedetail/1>查看简历</a>\n"
        "王** 男 34岁 工作8年 本科 北京\n"
        "Example Data · 数据开发专家\n"
        "负责数据仓库、数据治理、Python 平台建设"
    )
    state_detail = (
        "URL: https://h.liepin.com/resume/showresumedetail/1\n"
        "姓名：张三\n"
        "当前职位：数据开发专家\n"
        "当前公司：Example Data\n"
        "工作经历：Example Data 数据开发专家 负责数据仓库、数据治理、Python 平台建设\n"
        "教育经历：北京大学 本科 计算机\n"
        "技能：Python Hive Spark"
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://h.liepin.com/search/getConditionItem#session",
                "https://h.liepin.com/resume/showresumedetail/1",
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_cards,
                state_detail,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "120"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    result = runner.search_liepin_resumes(
        source_run_id="source-1",
        query="数据开发",
        target_resumes=1,
        max_cards=5,
        max_pages=1,
        must_haves=("数据治理",),
        nice_to_haves=("Python",),
        native_filters=None,
    )

    assert result["schema_version"] == "seektalent.pi_liepin_resumes.v1"
    assert result["status"] == "succeeded"
    assert result["resumes_returned"] == 1
    assert result["resumes"][0]["detail_payload"]["fullText"]
    assert "数据治理" in result["resumes"][0]["detail_payload"]["fullText"]
    assert ("opencli", "browser", "seektalent-liepin", "click", "120") in commands.calls
```

This test fixes the minimum browser contract: the runner may read card summaries to choose a target, but it must open a detail page and return `detail_payload.fullText`.

Also append these safety regression tests:

```python
def test_generic_click_still_rejects_liepin_detail_targets() -> None:
    runner = _runner(FakeCommands(), allowed_click_refs=("120",))

    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_forbidden_command"):
        runner.click(target="查看简历")


def test_card_state_classification_still_rejects_detail_url() -> None:
    reason = classify_liepin_state(
        url="https://h.liepin.com/resume/showresumedetail/1",
        text="姓名：张三\n工作经历：数据治理",
    )

    assert reason == "liepin_opencli_unknown_modal"
```

The detail-backed runner must use its own narrow helper, not relax generic `click(...)` or `classify_liepin_state(...)`.

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
uv run pytest \
  tests/test_pi_agent_boundaries.py::test_opencli_extension_exposes_high_level_resume_search_tool \
  tests/test_pi_opencli_browser.py::test_search_liepin_resumes_opens_detail_after_promising_card \
  tests/test_pi_opencli_browser.py::test_generic_click_still_rejects_liepin_detail_targets \
  tests/test_pi_opencli_browser.py::test_card_state_classification_still_rejects_detail_url \
  -q
```

Expected: FAIL because the tool and runner method do not exist.

- [ ] **Step 4: Register extension tool**

In `seektalent_opencli_browser.ts`, add:

```typescript
pi.registerTool({
  name: "seektalent_opencli_search_liepin_resumes",
  label: "Search Liepin complete resumes",
  description: "Run bounded Liepin card screening, open promising detail pages, and return complete resume evidence.",
  parameters: Type.Object({
    sourceRunId: Type.String(),
    query: Type.String(),
    targetResumes: Type.Number(),
    maxPages: Type.Optional(Type.Number()),
    maxCards: Type.Optional(Type.Number()),
    nativeFilters: Type.Optional(Type.Object({}, { additionalProperties: true })),
    mustHaves: Type.Optional(Type.Array(Type.String())),
    niceToHaves: Type.Optional(Type.Array(Type.String())),
  }),
  async execute(_toolCallId: string, params: ToolParams) {
    return textResult(await runAction("search_resumes", params));
  },
});
```

Add `"search_resumes"` to the action reset/allowed lists where `search_cards` is currently handled.

- [ ] **Step 5: Add CLI forwarding**

In `opencli_browser_cli.py`, add:

```python
if action == "search_resumes":
    native_filters = payload.get("nativeFilters") or payload.get("native_filters")
    return runner.search_liepin_resumes(
        source_run_id=_payload_string(payload, "sourceRunId", "source_run_id"),
        query=_payload_string(payload, "query"),
        target_resumes=_payload_int(payload, "targetResumes", "target_resumes", default=10),
        max_pages=_payload_int(payload, "maxPages", "max_pages", default=1),
        max_cards=_payload_int(payload, "maxCards", "max_cards", default=30),
        must_haves=_payload_string_tuple(payload, "mustHaves", "must_haves"),
        nice_to_haves=_payload_string_tuple(payload, "niceToHaves", "nice_to_haves"),
        native_filters=cast(Mapping[str, object], native_filters) if isinstance(native_filters, dict) else None,
    )
```

Implement `_payload_string_tuple(...)` by accepting a JSON list of strings and returning a tuple of non-empty strings capped at 20 items.

- [ ] **Step 6: Implement minimal runner method**

In `opencli_browser.py`, add `search_liepin_resumes(...)` next to `search_liepin_cards(...)`. The first implementation may reuse search/filter/card-reading helpers, but must differ at the return boundary:

```python
def search_liepin_resumes(
    self,
    *,
    source_run_id: str,
    query: str,
    target_resumes: int,
    max_pages: int,
    max_cards: int,
    must_haves: Sequence[str] = (),
    nice_to_haves: Sequence[str] = (),
    native_filters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    # Search and filter exactly as card search does.
    # Rank cards by must-have hits, nice-to-have hits, then provider rank.
    # Open detail refs only for selected cards.
    # Extract fullText/workExperienceList/educationList/skills/currentTitle/currentCompany/locations.
    # Return seektalent.pi_liepin_resumes.v1.
```

Do not return `seektalent.pi_liepin_cards.v1` from this method.

Implementation constraints:

- Do not add `"查看简历"`, `"简历详情"`, `"resume"`, or `"detail"` to global `ALLOWED_CLICK_TARGET_FRAGMENTS`.
- Add a private helper such as `_click_liepin_resume_detail_ref(ref, events)` that accepts only a ref discovered from the current card block, records `{"action_kind":"open_detail","route_kind":"detail"}`, clicks that ref, waits, and immediately reads detail state.
- Add a private detail-state reader that allows `https://h.liepin.com/resume/...` only after `_click_liepin_resume_detail_ref(...)` inside `search_liepin_resumes(...)`.
- Keep `classify_liepin_state(...)` unchanged for generic state reads so card mode and low-level tools still fail closed on detail URLs.
- Extract a redacted detail payload from the detail state, write the raw detail state only as a protected artifact, and return only safe fields in `detail_payload`.
- After each detail extraction, navigate back to the search results tab/page or rely on a page id proven by the owned lease before selecting the next card.

- [ ] **Step 7: Run focused tests**

Run:

```bash
uv run pytest \
  tests/test_pi_agent_boundaries.py::test_opencli_extension_exposes_high_level_resume_search_tool \
  tests/test_pi_opencli_browser.py::test_search_liepin_resumes_opens_detail_after_promising_card \
  tests/test_pi_opencli_browser.py::test_generic_click_still_rejects_liepin_detail_targets \
  tests/test_pi_opencli_browser.py::test_card_state_classification_still_rejects_detail_url \
  -q
```

Expected: PASS.

## Task 5: Make Liepin Worker Client Return Complete Resumes

**Files:**
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
- Test: `tests/test_liepin_pi_worker_client.py`

- [ ] **Step 1: Write failing worker-client test**

Append to `tests/test_liepin_pi_worker_client.py`:

```python
def test_pi_worker_client_search_uses_resume_search_not_card_search() -> None:
    executor = FakePiExecutor()
    client = LiepinPiWorkerClient(
        executor,
        session_id="session-1",
        connection_id="conn-1",
        provider_account_lock_key="acct-1",
    )
    request = SearchRequest(
        query_terms=("数据开发",),
        keyword_query="数据开发",
        page_size=7,
        provider_context={
            "liepin_max_cards": "30",
            "liepin_max_pages": "2",
            "liepin_must_haves_json": '["数据治理"]',
            "liepin_nice_to_haves_json": '["Python"]',
        },
    )

    result = asyncio.run(client.search(request, round_no=1, trace_id="trace-1"))

    assert executor.search_resumes_calls[0]["target_resumes"] == 7
    assert executor.search_resumes_calls[0]["max_cards"] == 30
    assert executor.search_cards_calls == []
    assert result.candidates[0].raw["score_evidence_source"] == "detail_enriched"
```

Define `FakePiExecutor.search_resumes(...)` in the test file to return `LiepinPiResumeSearchResult` with one `LiepinResumeSearchResponse`.

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_liepin_pi_worker_client.py::test_pi_worker_client_search_uses_resume_search_not_card_search -q
```

Expected: FAIL because `search(...)` still calls `search_cards(...)`.

- [ ] **Step 3: Change `search(...)` to call `search_resumes(...)`**

In `LiepinPiWorkerClient.search(...)`, replace executor call with:

```python
result = await asyncio.to_thread(
    self._executor.search_resumes,
    source_run_id=trace_id,
    keyword_query=request.keyword_query or " ".join(request.query_terms),
    query_terms=tuple(request.query_terms),
    target_resumes=request.page_size,
    max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
    max_cards=_positive_int(request.provider_context.get("liepin_max_cards"), default=max(request.page_size * 3, request.page_size)),
    connection_id=connection_id,
    provider_account_hash=task_provider_account_hash,
    native_filters=_native_filters_from_request(request),
    must_haves=_json_string_tuple(request.provider_context.get("liepin_must_haves_json")),
    nice_to_haves=_json_string_tuple(request.provider_context.get("liepin_nice_to_haves_json")),
)
```

Map success through `liepin_resume_search_response_to_search_result(...)`.

- [ ] **Step 4: Add `_json_string_tuple(...)`**

Add:

```python
def _json_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item.strip() for item in parsed if isinstance(item, str) and item.strip())[:20]
```

- [ ] **Step 5: Run test**

Run:

```bash
uv run pytest tests/test_liepin_pi_worker_client.py::test_pi_worker_client_search_uses_resume_search_not_card_search -q
```

Expected: PASS.

## Task 6: Run Exploit And Explore Pi Tasks Concurrently

**Files:**
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent/runtime/source_query_intent.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

- [ ] **Step 1: Write failing concurrency/count test**

Append to `tests/test_liepin_runtime_source_lane.py`:

```python
def test_liepin_logical_query_bundle_runs_exploit_and_explore_concurrently() -> None:
    worker = ConcurrentDetailWorker()
    exploit = LogicalQueryDispatch(
        round_no=1,
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-exploit",
        query_fingerprint="fp-exploit",
        query_terms=("数据开发",),
        keyword_query="数据开发",
        requested_count=7,
        source_plan_version="test",
    )
    explore = LogicalQueryDispatch(
        round_no=1,
        query_role="explore",
        lane_type="generic_explore",
        query_instance_id="query-explore",
        query_fingerprint="fp-explore",
        query_terms=("Python",),
        keyword_query="Python",
        requested_count=3,
        source_plan_version="test",
    )

    result = asyncio.run(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="runtime-run-1",
            source_plan_id="plan-liepin",
            job_title="数据开发专家",
            jd="负责数据平台建设",
            notes="Python",
            logical_queries=(exploit, explore),
            source_budget_policy=RuntimeSourceBudgetPolicy(liepin_card_page_size=30, liepin_max_cards=30),
            liepin_context={"provider_account_hash": "acct_hash_123"},
            worker_client=worker,
        )
    )

    assert worker.requested_counts_by_trace["plan-liepin:round:1:lane:1"] == 7
    assert worker.requested_counts_by_trace["plan-liepin:round:1:lane:2"] == 3
    assert worker.max_in_flight >= 2
    assert all(candidate.raw["score_evidence_source"] == "detail_enriched" for candidate in result.candidate_store_updates.values())
```

Define `ConcurrentDetailWorker` in the test file. It should increment `in_flight`, `await asyncio.sleep(0.05)`, return detail-backed candidates, and record `max_in_flight`.

- [ ] **Step 2: Write failing Liepin card-scan budget test**

Append to `tests/test_runtime_multi_source_round_dispatch.py`:

```python
def test_liepin_source_query_intent_uses_card_scan_budget_larger_than_resume_target() -> None:
    dispatches = (
        LogicalQueryDispatch(
            round_no=1,
            query_role="exploit",
            lane_type="exploit",
            query_instance_id="query-exploit",
            query_fingerprint="fp-exploit",
            query_terms=("数据开发",),
            keyword_query="数据开发",
            requested_count=7,
            source_plan_version="test",
        ),
        LogicalQueryDispatch(
            round_no=1,
            query_role="explore",
            lane_type="generic_explore",
            query_instance_id="query-explore",
            query_fingerprint="fp-explore",
            query_terms=("Python",),
            keyword_query="Python",
            requested_count=3,
            source_plan_version="test",
        ),
    )

    intents = build_runtime_source_query_intents(
        source_kinds=("liepin",),
        logical_dispatches=dispatches,
        filter_intents=(),
        location_intent=None,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(liepin_max_cards=30),
    )["liepin"]

    assert [intent.requested_count for intent in intents] == [7, 3]
    assert [intent.provider_scan_limit for intent in intents] == [21, 9]
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
uv run pytest \
  tests/test_liepin_runtime_source_lane.py::test_liepin_logical_query_bundle_runs_exploit_and_explore_concurrently \
  tests/test_runtime_multi_source_round_dispatch.py::test_liepin_source_query_intent_uses_card_scan_budget_larger_than_resume_target \
  -q
```

Expected: FAIL because current implementation loops sequentially and Liepin scan budget is still capped to `requested_count`.

- [ ] **Step 4: Fix Liepin source scan budget**

In `src/seektalent/runtime/source_query_intent.py`, change Liepin `_provider_scan_limit(...)` to:

```python
if source_kind == "liepin":
    return min(max(dispatch.requested_count * 3, dispatch.requested_count), source_budget_policy.liepin_max_cards)
```

Keep CTS unchanged.

- [ ] **Step 5: Refactor logical query loop to TaskGroup**

In `run_liepin_logical_query_bundle(...)`, create one task per `LogicalQueryDispatch`:

```python
tasks: list[asyncio.Task[RuntimeSourceLaneResult]] = []
async with asyncio.TaskGroup() as group:
    for index, logical_query in enumerate(logical_queries, start=1):
        tasks.append(
            group.create_task(
                _run_liepin_logical_query(
                    worker=worker,
                    request=request,
                    logical_query=logical_query,
                    lane_index=index,
                    detail_request_count=logical_query.requested_count,
                )
            )
        )

for task in tasks:
    result = task.result()
    merged_result = result if merged_result is None else merge_liepin_card_lane_results(merged_result, result)
```

Add `_run_liepin_logical_query(...)` by moving the existing per-logical-query worker call and candidate mapping out of the sequential loop. The helper must build a worker search request with `page_size=logical_query.requested_count`, `logical_provider_scan_limit=intent.provider_scan_limit`, and `query_instance_id/query_fingerprint` copied from `logical_query`.

Rename `merge_liepin_card_lane_results(...)` to `merge_liepin_lane_results(...)` or keep the old name as an alias during the slice to minimize churn.

- [ ] **Step 6: Reject card-only Runtime candidates**

Before returning a result from `run_liepin_source_lane(...)`, validate:

```python
def _assert_detail_backed_liepin_result(result: RuntimeSourceLaneResult) -> None:
    for candidate in result.candidate_store_updates.values():
        if candidate.raw.get("score_evidence_source") != "detail_enriched":
            raise ValueError("liepin_runtime_candidate_must_be_detail_enriched")
    for evidence in result.source_evidence_updates:
        if evidence.evidence_level != "detail":
            raise ValueError("liepin_runtime_evidence_must_be_detail")
```

Call this only for completed/partial results with candidates.

- [ ] **Step 7: Run tests**

Run:

```bash
uv run pytest \
  tests/test_liepin_runtime_source_lane.py::test_liepin_logical_query_bundle_runs_exploit_and_explore_concurrently \
  tests/test_runtime_multi_source_round_dispatch.py::test_liepin_source_query_intent_uses_card_scan_budget_larger_than_resume_target \
  tests/test_runtime_multi_source_round_dispatch.py \
  -q
```

Expected: PASS.

## Task 7: Remove Contradictory Card-Only Skill Language

**Files:**
- Modify: `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
- Test: `tests/test_pi_external_agent.py`

- [ ] **Step 1: Write failing skill text test**

Append to `tests/test_pi_external_agent.py`:

```python
def test_liepin_pi_skill_does_not_forbid_detail_for_runtime_resume_search() -> None:
    text = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md").read_text(encoding="utf-8")

    assert "Do not open candidate detail pages in card mode" not in text
    assert "Runtime complete-resume search" in text
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py::test_liepin_pi_skill_does_not_forbid_detail_for_runtime_resume_search -q
```

Expected: FAIL because the current skill forbids detail opening.

- [ ] **Step 3: Update card-only wording**

Change the skill heading and key bullets so it says:

```markdown
# Liepin Runtime Resume Search

Use only SeekTalent Pi-owned browser tools. Runtime complete-resume search may
read card summaries only as internal screening evidence, then open selected
resume detail pages to produce complete resume evidence. Do not return card
summaries as Runtime candidates.
```

Keep the forbidden contact/chat/download/cookie/storage/account rules.

- [ ] **Step 4: Run test**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py::test_liepin_pi_skill_does_not_forbid_detail_for_runtime_resume_search -q
```

Expected: PASS.

## Task 8: Focused Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
uv run pytest \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_liepin_pi_executor.py \
  tests/test_liepin_pi_worker_client.py \
  tests/test_pi_external_agent.py \
  tests/test_pi_opencli_browser.py \
  tests/test_pi_agent_boundaries.py \
  tests/test_normalization.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run lint/type/build checks**

Run:

```bash
uv run ruff check \
  src/seektalent/providers/liepin \
  src/seektalent/providers/pi_agent \
  src/seektalent/runtime \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_liepin_pi_executor.py \
  tests/test_liepin_pi_worker_client.py \
  tests/test_pi_external_agent.py \
  tests/test_pi_opencli_browser.py \
  tests/test_pi_agent_boundaries.py \
  tests/test_normalization.py
uv run ty check \
  src/seektalent/providers/liepin \
  src/seektalent/providers/pi_agent \
  src/seektalent/runtime
uv build
cd apps/web-svelte && bun run build
```

Expected: all pass. Vite chunk-size warnings are acceptable if the build exits 0.

## Task 9: Real Chrome QA

**Files:**
- No code changes unless QA finds bugs.

- [ ] **Step 1: Start Workbench**

Run:

```bash
env SEEKTALENT_LIEPIN_OPENCLI_START_DAEMON=1 scripts/start-dev-workbench.sh
```

Expected:
- Backend prints `http://127.0.0.1:8012`.
- Svelte Workbench prints `http://127.0.0.1:5178`.
- Liepin worker mode is `pi_agent`.

- [ ] **Step 2: Open Chrome and use the complete historical input**

Use Chrome, not the Codex in-app browser. Use:

- Job title: the exact contents of `/tmp/seektalent-liepin-filter-parity-qa/full-input/job_title.txt`
- JD: the exact contents of `/tmp/seektalent-liepin-filter-parity-qa/full-input/jd_text.txt`
- Notes: the exact contents of `/tmp/seektalent-liepin-filter-parity-qa/full-input/notes.txt`

Do not crop or summarize the input.

- [ ] **Step 3: Capture required screenshots**

Capture:
- Before starting search, with full input filled.
- Triage ready.
- Search running.
- Liepin page after filters are applied.
- A Liepin detail page opened by the Pi task.
- Workbench after candidates return.
- Final Top 10 if the run completes within reasonable test time.

- [ ] **Step 4: Verify runtime data**

Run SQLite checks:

```bash
sqlite3 .seektalent/workbench.sqlite3 "
select status,runtime_run_id,error_message from runtime_sourcing_jobs order by created_at desc limit 3;
select count(*) from candidate_review_items where session_id = '<SESSION_ID>';
select count(*) from candidate_evidence where session_id = '<SESSION_ID>' and source_kind = 'liepin';
select count(*) from candidate_evidence where session_id = '<SESSION_ID>' and source_kind = 'liepin' and evidence_level = 'detail';
"
```

Expected:
- Liepin evidence exists when the provider is not blocked.
- Liepin candidates have detail-backed evidence.
- Runtime does not persist card-only candidates as final candidates.
- Do not use `detail_open_requests` as the success signal for this slice. Detail opens are internal to the Pi resume-search task, so the authoritative checks are candidate evidence, provider snapshots, and protected Pi traces.

- [ ] **Step 5: Inspect protected trace**

Open the latest protected Pi trace and verify:
- exploit task and explore task have separate source lane run ids.
- both tasks applied Runtime native filters when available.
- cards were read before detail selection.
- at least one detail page was opened when cards were available and login/risk did not block.
- no contact/chat/download/payment action appears.

- [ ] **Step 6: Cleanup**

Before ending:

```bash
NODE_PATH="$PWD/apps/web-svelte/node_modules" \
PYTHONPATH="$PWD/src" \
SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$PWD/apps/web-svelte/node_modules/.bin/opencli" \
SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR="$PWD/.seektalent/opencli_leases" \
uv run python -m seektalent.providers.pi_agent.opencli_browser_cli cleanup_orphaned_tabs \
  <<< '{"force":true}'
```

Also close agent-created Chrome tabs/windows via the Chrome automation finalizer.

## Self-Review

- Spec coverage: all product contract requirements map to Tasks 1-9.
- Placeholder scan: implementation tasks name concrete files, tests, commands, concrete fake-command states, and expected behavior. No test step relies on unnamed helpers or blank command fixtures.
- Type consistency: `LiepinResumeSearchResponse`, `LiepinPiResumeSearchResult`, `search_resumes(...)`, and `seektalent.pi_liepin_resumes.v1` are consistently named across tasks.
- Scope check: this is one coherent subsystem slice: Liepin detail-backed Runtime source lane. Graph node detail completeness remains in `TODOS.md`.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | Direction already chosen: Runtime Core owns budget/query flow; CTS/Liepin are adapters. |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | Not run for this plan-review gate. |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 4 issues found and incorporated: scope sliced into 3 build phases, detail-safe OpenCLI path, Pi RPC resume event extraction, Liepin card-scan budget. |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | SKIPPED | No frontend UI/layout changes in this slice; graph node detail completeness remains in `TODOS.md`. |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | Not needed for this backend/browser Runtime slice. |

- **UNRESOLVED:** 0.
- **VERDICT:** ENG CLEARED — ready for `fw-build` after explicit user confirmation.
