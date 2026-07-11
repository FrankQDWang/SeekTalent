# Liepin Detail Open Claim Implementation Plan

> **Status:** Implemented and verified on 2026-07-11; pre-existing static-gate debt is recorded below.
>
> **Completion-record convention:** A checked step records that its intended deliverable is present on `main` and covered by the current verification evidence below. The planned red-phase commands are historical TDD steps and cannot be re-run as failures on the completed tree; they are checked only after the corresponding final behavior was independently verified.

**Goal:** Prevent default OpenCLI detail-backed search from opening the same Liepin candidate more than once per runtime run, while making post-capture identity stable across lanes, rounds, ranks, and artifact paths.

**Architecture:** Runtime owns a checkpointed map of opaque provider-key claims. The Liepin OpenCLI workflow receives a synchronized claim facade, derives a safe candidate key before any browser action, claims it, and only then opens detail. The existing two-attempt OpenCLI navigation retry stays inside that one claim; a browser action without successful capture becomes terminal for later lanes and rounds.

**Tech Stack:** Python 3.12, Pydantic v2, threading synchronization, pytest, OpenCLI/Liepin provider adapter, structured card/detail payloads.

## Global Constraints

- Scope is the default OpenCLI detail-backed Liepin search path only; do not alter the approved-detail `LiepinStore` daily-budget workflow.
- Derive identity only from canonical Liepin detail identity (`res_id_encode`/safe detail URL), then store/use only an opaque SHA-256 hash.
- Do not fall back to rank, card text, display name, artifact path, raw ref, raw URL, fullText, or rawText as an identity key.
- Claim before every browser detail open. A denied claim means no browser click.
- Preserve `_DETAIL_OPEN_MAX_ATTEMPTS == 2` only inside a single granted claim. An attempted browser action without capture is terminal across later lanes/rounds.
- A claim with no browser action may be released; a claim with an attempted browser action may not be released.
- The same opaque key must be used as post-capture provider subject/dedup input.
- Do not put the claim object or raw key into `SearchRequest.provider_context`, external HTTP payloads, public events, BFF DTOs, or React state.
- Keep structured `safeCardSummary`, `safeDetail`, and `wtsDetail` as the only resume-evidence path.

## Execution Gates

- **Gate A — persisted state and injection:** Complete Tasks 1–3, run the lifecycle/composition suite, and verify one facade object is shared by all lanes before touching browser-side claim behavior.
- **Gate B — browser-side effects:** Complete Tasks 4–5 only after Gate A passes; run the default OpenCLI regression suite and the approved-detail ledger regression before merge.

---

## File Structure

- Create: `src/seektalent/source_contracts/detail_open_claims.py` — synchronized lifecycle facade over a run-owned claim map.
- Modify: `src/seektalent/models.py` — persisted `RuntimeDetailOpenClaim` and `RunState.detail_open_claims_by_provider_key`.
- Modify: `src/seektalent/providers/liepin/liepin_site_parsing.py` — safe hash derivation from a canonical detail URL.
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py` — map captured detail using the carried opaque key.
- Modify: source-adapter, Liepin runtime-lane, provider-adapter, worker-client, retriever, site-adapter, and workflow request chain — inject one ledger per runtime run without changing generic worker HTTP contracts.
- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py` — claim before open, terminalize action failures, and record safe counters.
- Add and modify focused provider/runtime tests listed per task below.

### Implemented Boundary Adjustment

The final architecture review placed the synchronized facade in `src/seektalent/source_contracts/detail_open_claims.py`, rather than under the Liepin provider. The map remains runtime-owned and checkpointed; Liepin retains only private provider-key derivation and the browser-side consumer. This corrects dependency direction without expanding the claim contract beyond the approved design.

### Task 1: Persist And Synchronize Detail Open Claims

**Files:**

- Create: `src/seektalent/providers/liepin/detail_open_claims.py`
- Modify: `src/seektalent/models.py:1369-1392`
- Test: `tests/test_liepin_detail_open_claims.py`
- Test: `tests/test_runtime_source_lanes.py`

**Interfaces:**

- Produces `RuntimeDetailOpenClaim`, `RunState.detail_open_claims_by_provider_key`, and `DetailOpenClaimLedger`.
- Later tasks consume `try_claim()`, `record_browser_open_attempt()`, `has_browser_open_attempt()`, `mark_opened()`, `mark_terminal_failed()`, and `release_unattempted()`.

- [x] **Step 1: Write failing claim-lifecycle tests**

Create `tests/test_liepin_detail_open_claims.py`:

```python
from concurrent.futures import ThreadPoolExecutor

from seektalent.models import RuntimeDetailOpenClaim
from seektalent.providers.liepin.detail_open_claims import DetailOpenClaimLedger


def test_concurrent_claims_allow_exactly_one_winner() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: ledger.try_claim("candidate-key"), range(2)))

    assert outcomes.count(True) == 1
    assert claims["candidate-key"].status == "claimed"


def test_unattempted_claim_is_released_but_attempted_failure_is_terminal() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)

    assert ledger.try_claim("first") is True
    ledger.release_unattempted("first")
    assert ledger.try_claim("first") is True

    ledger.record_browser_open_attempt("first")
    ledger.mark_terminal_failed("first", safe_reason_code="liepin_opencli_detail_not_opened")
    assert ledger.try_claim("first") is False
    assert claims["first"].status == "terminal_failed"


def test_opened_claim_cannot_be_claimed_again() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)
    assert ledger.try_claim("opened") is True
    ledger.record_browser_open_attempt("opened")
    ledger.mark_opened("opened")
    assert ledger.try_claim("opened") is False
```

Add a `RunState` JSON round-trip test in `tests/test_runtime_source_lanes.py`:

```python
def test_run_state_roundtrip_preserves_detail_open_claims() -> None:
    state = _run_state()
    state.detail_open_claims_by_provider_key["opaque-key"] = RuntimeDetailOpenClaim(
        status="opened", browser_open_attempt_count=1,
    )
    restored = RunState.model_validate_json(state.model_dump_json())
    assert restored.detail_open_claims_by_provider_key["opaque-key"].status == "opened"
```

- [x] **Step 2: Run the tests and verify the initial failure**

Run:

```bash
uv run pytest -q tests/test_liepin_detail_open_claims.py tests/test_runtime_source_lanes.py
```

Expected: collection fails because the claim model and ledger do not exist.

- [x] **Step 3: Add the persisted claim model**

Add the following immediately after `RoundState` in `src/seektalent/models.py`:

```python
class RuntimeDetailOpenClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["claimed", "opened", "terminal_failed"]
    browser_open_attempt_count: int = Field(default=0, ge=0)
    last_safe_reason_code: str | None = None
```

Add this field to `RunState`:

```python
detail_open_claims_by_provider_key: dict[str, RuntimeDetailOpenClaim] = Field(default_factory=dict)
```

Do not add raw provider identifiers, URLs, rank, source-run IDs, or candidate names to this persistent state.

- [x] **Step 4: Implement the synchronized ledger**

Create `src/seektalent/providers/liepin/detail_open_claims.py`:

```python
from __future__ import annotations

from collections.abc import MutableMapping
from threading import RLock

from seektalent.models import RuntimeDetailOpenClaim


class DetailOpenClaimLedger:
    def __init__(self, claims: MutableMapping[str, RuntimeDetailOpenClaim]) -> None:
        self._claims = claims
        self._lock = RLock()

    def try_claim(self, provider_candidate_key_hash: str) -> bool:
        with self._lock:
            if provider_candidate_key_hash in self._claims:
                return False
            self._claims[provider_candidate_key_hash] = RuntimeDetailOpenClaim(status="claimed")
            return True

    def record_browser_open_attempt(self, provider_candidate_key_hash: str) -> None:
        with self._lock:
            claim = self._require_claim(provider_candidate_key_hash)
            if claim.status != "claimed":
                raise ValueError("detail_open_claim_not_claimed")
            claim.browser_open_attempt_count += 1

    def has_browser_open_attempt(self, provider_candidate_key_hash: str) -> bool:
        with self._lock:
            return self._require_claim(provider_candidate_key_hash).browser_open_attempt_count > 0

    def mark_opened(self, provider_candidate_key_hash: str) -> None:
        with self._lock:
            claim = self._require_claim(provider_candidate_key_hash)
            if claim.browser_open_attempt_count == 0:
                raise ValueError("detail_open_claim_opened_without_browser_attempt")
            claim.status = "opened"

    def mark_terminal_failed(self, provider_candidate_key_hash: str, *, safe_reason_code: str) -> None:
        with self._lock:
            claim = self._require_claim(provider_candidate_key_hash)
            if claim.browser_open_attempt_count == 0:
                raise ValueError("detail_open_claim_failure_without_browser_attempt")
            claim.status = "terminal_failed"
            claim.last_safe_reason_code = safe_reason_code

    def release_unattempted(self, provider_candidate_key_hash: str) -> None:
        with self._lock:
            claim = self._require_claim(provider_candidate_key_hash)
            if claim.browser_open_attempt_count != 0:
                raise ValueError("detail_open_claim_attempted_cannot_release")
            del self._claims[provider_candidate_key_hash]

    def _require_claim(self, provider_candidate_key_hash: str) -> RuntimeDetailOpenClaim:
        try:
            return self._claims[provider_candidate_key_hash]
        except KeyError as exc:
            raise ValueError("detail_open_claim_missing") from exc
```

- [x] **Step 5: Run lifecycle and persistence tests**

Run:

```bash
uv run pytest -q tests/test_liepin_detail_open_claims.py tests/test_runtime_source_lanes.py
```

Expected: PASS. Exactly one concurrent caller can receive the claim.

- [x] **Step 6: Commit claim lifecycle support**

```bash
git add src/seektalent/models.py src/seektalent/providers/liepin/detail_open_claims.py tests/test_liepin_detail_open_claims.py tests/test_runtime_source_lanes.py
git commit -m "feat: add Liepin detail open claims"
```

### Task 2: Derive A Stable Private Candidate Key And Align Captured Identity

**Files:**

- Modify: `src/seektalent/providers/liepin/liepin_site_parsing.py:496-552,1033-1045`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py:151-201`
- Test: `tests/test_liepin_opencli_retriever.py`
- Test: `tests/test_liepin_opencli_browser.py`

**Interfaces:**

- Produces `stable_liepin_detail_candidate_key_hash(detail_url: str) -> str | None` and a carried opaque key in the structured detail mapping path.
- Later tasks consume the opaque hash before browser opening.

- [x] **Step 1: Write failing stable-identity tests**

Add these tests:

```python
def test_stable_detail_key_ignores_rank_and_lane_artifact_path() -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=encodedsubject"
    first = stable_liepin_detail_candidate_key_hash(detail_url)
    second = stable_liepin_detail_candidate_key_hash(detail_url)
    assert first is not None
    assert first == second


def test_stable_detail_key_rejects_noncanonical_or_missing_subject() -> None:
    assert stable_liepin_detail_candidate_key_hash("https://h.liepin.com/resume/showresumedetail/") is None
    assert stable_liepin_detail_candidate_key_hash("https://example.test/?res_id_encode=subject") is None
    assert stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=one&res_id_encode=two"
    ) is None


def test_opencli_resume_identity_uses_stable_key_not_artifact_path() -> None:
    first = _detail_candidate(source_url=_detail_url("same-subject"), artifact_ref="run-a/rank-1")
    second = _detail_candidate(source_url=_detail_url("same-subject"), artifact_ref="run-b/rank-9")
    assert _map(first).dedup_key == _map(second).dedup_key
```

- [x] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_liepin_opencli_retriever.py tests/test_liepin_opencli_browser.py
```

Expected: FAIL because no stable key helper exists and mapped identity still changes with artifact reference.

- [x] **Step 3: Implement safe hash derivation**

Add to `liepin_site_parsing.py` near `safe_liepin_detail_url_for_ref()`:

```python
from hashlib import sha256
import re
from urllib.parse import parse_qs, urlparse


def stable_liepin_detail_candidate_key_hash(detail_url: str) -> str | None:
    parsed = urlparse(detail_url)
    if not _is_liepin_detail_url(detail_url):
        return None
    subjects = parse_qs(parsed.query, keep_blank_values=True).get("res_id_encode", [])
    if len(subjects) != 1 or not re.fullmatch(r"[A-Za-z0-9]+", subjects[0]):
        return None
    canonical_subject = subjects[0]
    return sha256(f"liepin:res_id_encode:v1:{canonical_subject}".encode("utf-8")).hexdigest()
```

Do not return `subject`, persist it, or include it in a trace/event. The only caller-visible value is the hash or `None`.

- [x] **Step 4: Carry the hash through detail mapping**

Extend the internal structured detail result with `provider_candidate_key_hash`. In `opencli_retriever.py`, assign this hash to the mapped candidate's provider subject/dedup input before any artifact reference is considered. Reject mapping when a detail capture was requested through the claim-aware path but the carried key is absent or mismatched.

The new live path has no artifact-path fallback. Keep existing replay-fixture compatibility only if a test proves it is used outside the claim-aware live path; it must not be reachable from live OpenCLI detail-backed search.

- [x] **Step 5: Run identity regression tests**

Run:

```bash
uv run pytest -q tests/test_liepin_opencli_retriever.py tests/test_liepin_opencli_browser.py tests/test_liepin_boundaries.py
```

Expected: PASS. Same provider subject maps to the same identity across rank/artifact changes, while malformed identity fails closed.

- [x] **Step 6: Commit stable identity mapping**

```bash
git add src/seektalent/providers/liepin/liepin_site_parsing.py src/seektalent/providers/liepin/opencli_retriever.py tests/test_liepin_opencli_retriever.py tests/test_liepin_opencli_browser.py
git commit -m "fix: stabilize Liepin detail candidate identity"
```

### Task 3: Inject One Run-Owned Ledger Through The OpenCLI Path

**Files:**

- Modify: `src/seektalent/source_adapters/round_adapters.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/sources/liepin/runtime_lane.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Modify: `src/seektalent/providers/liepin/opencli_worker_client.py`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`
- Test: `tests/test_liepin_provider_adapter.py`
- Test: `tests/test_liepin_provider_source_composition.py`

**Interfaces:**

- Consumes `RunState.detail_open_claims_by_provider_key` and `DetailOpenClaimLedger` from Task 1.
- Produces a claim-aware OpenCLI-only method without changing the generic worker HTTP protocol or `SearchRequest.provider_context`.

- [x] **Step 1: Write failing injection tests**

Add:

```python
def test_claim_aware_opencli_worker_receives_runtime_ledger() -> None:
    ledger = DetailOpenClaimLedger({})
    client = _opencli_worker_client()
    with patch.object(client._retriever, "search_resumes", return_value=_search_response()) as search:
        asyncio.run(client.search_with_detail_open_claim_ledger(
            _search_request(), round_no=1, trace_id="trace-1", detail_open_claim_ledger=ledger,
        ))
    assert search.call_args.args[0].detail_open_claim_ledger is ledger


def test_liepin_source_round_reuses_run_state_detail_claims_across_rounds() -> None:
    run_state = _run_state()
    runtime = _runtime_with_repeated_card_subject("same-subject")
    asyncio.run(_run_two_round_fixture(runtime, run_state))
    assert run_state.detail_open_claims_by_provider_key
    assert _browser_open_count(runtime) == 1


def test_concurrent_lanes_share_one_runtime_ledger_instance() -> None:
    runtime, run_state = _runtime_with_two_concurrent_liepin_lanes("same-subject")
    asyncio.run(_run_one_round_fixture(runtime, run_state))
    assert _browser_open_count(runtime) == 1
    assert len(run_state.detail_open_claims_by_provider_key) == 1
```

- [x] **Step 2: Run injection tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_liepin_provider_adapter.py tests/test_runtime_multi_source_round_dispatch.py tests/test_liepin_provider_source_composition.py
```

Expected: FAIL because no call path carries a ledger into the live OpenCLI retriever.

- [x] **Step 3: Build one ledger per runtime run**

Immediately after `RunState` is created or restored at the top-level `WorkflowRuntime.run_match_async()` execution boundary, construct exactly one facade:

```python
detail_open_claim_ledger = DetailOpenClaimLedger(run_state.detail_open_claims_by_provider_key)
```

Store that same object on the per-run runtime context and pass it into every `RuntimeSourceRoundContext`. Do **not** construct a new facade in each round adapter: separate `RLock` instances around the same dictionary are not mutually atomic. On a checkpoint resume, create one fresh facade around the restored map before any lanes run.

Add this required field to the existing context in `src/seektalent/runtime/orchestrator.py`:

```python
@dataclass(frozen=True, kw_only=True)
class RuntimeSourceRoundContext:
    round_no: int
    retrieval_plan: RoundRetrievalPlan
    proposed_filter_plan: ProposedFilterPlan
    adapter_notes: tuple[str, ...]
    target_new: int
    seen_resume_ids: frozenset[str]
    seen_dedup_keys: frozenset[str]
    run_state: RunState
    source_plan_by_source: Mapping[str, RuntimeSourceLanePlan]
    source_context: Mapping[str, str | int | bool | None] | None
    tracer: RunTracer
    detail_open_claim_ledger: DetailOpenClaimLedger
```

Add `logical_round_no: int | None = None` to `RuntimeSourceLaneRequest`; `run_liepin_logical_query_bundle()` passes `logical_query.round_no`, and `run_liepin_source_lane()` forwards that exact round to `LiepinProviderAdapter`. This removes the current hard-coded OpenCLI `round_no=1` from the claim-aware path.

Pass the same object through:

```text
round adapter
-> run_liepin_logical_query_bundle(request, detail_open_claim_ledger=ledger)
-> run_liepin_source_lane(request, detail_open_claim_ledger=ledger)
-> LiepinProviderAdapter.search_with_detail_open_claim_ledger(request, detail_open_claim_ledger=ledger)
-> LiepinOpenCliWorkerClient.search_with_detail_open_claim_ledger(request, round_no=round_no, trace_id=trace_id, detail_open_claim_ledger=ledger)
-> LiepinOpenCliResumeRetriever.search_resumes(claim-aware request)
-> LiepinSiteAdapter.search_liepin_resumes(source_run_id=source_run_id, query=query, detail_open_claim_ledger=ledger)
```

`LiepinOpenCliResumeRequest` and `LiepinSearchWorkflowRequest` also carry the current `round_no` and `query_instance_id` internally so the workflow has one unambiguous claim provenance. Do not add the ledger to generic `LiepinWorkerClient.search()`, HTTP request JSON, `SearchRequest.provider_context`, fake worker behavior, or approved-detail lane APIs.

Add the internal request fields exactly:

```python
@dataclass(frozen=True, kw_only=True)
class LiepinOpenCliResumeRequest:
    source_run_id: str
    keyword_query: str
    query_terms: Sequence[str]
    target_resumes: int
    max_cards: int
    max_pages: int
    requirement_sheet: Mapping[str, object]
    round_no: int
    query_instance_id: str
    detail_open_claim_ledger: DetailOpenClaimLedger
    native_filters: dict[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class LiepinSearchWorkflowRequest:
    source_run_id: str
    query: str
    target_resumes: int
    max_pages: int
    max_cards: int
    round_no: int
    query_instance_id: str
    detail_open_claim_ledger: DetailOpenClaimLedger
    native_filters: Mapping[str, object] | None = None
```

- [x] **Step 4: Expose the narrow OpenCLI method**

Refactor the existing OpenCLI client search body into one private helper so the normal and claim-aware methods share the exact request construction, readiness behavior, response conversion, and partial-result handling. Add this narrow public method only to `LiepinOpenCliWorkerClient`:

```python
async def search_with_detail_open_claim_ledger(
    self,
    request: SearchRequest,
    *,
    round_no: int,
    trace_id: str,
    detail_open_claim_ledger: DetailOpenClaimLedger,
) -> SearchResult:
    return await self._search_opencli(
        request,
        round_no=round_no,
        trace_id=trace_id,
        detail_open_claim_ledger=detail_open_claim_ledger,
    )
```

`_search_opencli()` builds `LiepinOpenCliResumeRequest` with the ledger, current round, and logical query instance, then passes it through the existing `_search_resumes_sync` thread boundary to `LiepinOpenCliResumeRetriever.search_resumes()`. Extend the internal retriever/site request protocols with the same arguments; do not change the generic worker HTTP request/response schema.

Refactor `LiepinProviderAdapter.search()` and add `search_with_detail_open_claim_ledger()` over a shared private preflight/search helper. Only when the request is the default `detail_backed_resume_search` and `worker_client` is `LiepinOpenCliWorkerClient` may that helper call the narrow method. Every other worker and every approved-detail search remains on the existing `search()` call path.

- [x] **Step 5: Run injection and composition tests**

Run:

```bash
uv run pytest -q tests/test_liepin_provider_adapter.py tests/test_runtime_multi_source_round_dispatch.py tests/test_liepin_provider_source_composition.py tests/test_liepin_runtime_source_lane.py
```

Expected: PASS. Multiple rounds share one claim map, and generic worker contracts remain unchanged.

- [x] **Step 6: Commit OpenCLI ledger injection**

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/source_adapters/round_adapters.py src/seektalent/sources/liepin/runtime_lane.py src/seektalent/providers/liepin/adapter.py src/seektalent/providers/liepin/opencli_worker_client.py src/seektalent/providers/liepin/opencli_retriever.py src/seektalent/providers/liepin/liepin_site_adapter.py tests/test_runtime_multi_source_round_dispatch.py tests/test_liepin_provider_adapter.py tests/test_liepin_provider_source_composition.py
git commit -m "feat: pass runtime detail claims to OpenCLI"
```

### Task 4: Claim Before Browser Side Effects And Enforce Terminal Failure

**Files:**

- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py:157-324`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Test: `tests/test_liepin_search_workflow.py`
- Test: `tests/test_liepin_opencli_workflow.py`

**Interfaces:**

- Consumes the stable key from Task 2 and run-owned ledger from Task 3.
- Produces safe workflow counters and at-most-once browser detail behavior.

- [x] **Step 1: Write failing workflow tests**

Extend the existing `FakeLiepinSearchWorkflowSite` in `tests/test_liepin_search_workflow.py` with controlled `open_raises`, `capture_raises`, and captured-key overrides. Add `_detail_url(subject)` and `_site_for_subject()` fixture helpers; the latter supplies matching structured-card and search-state refs. Then add:

```python
# Add these fields to FakeLiepinSearchWorkflowSite.
open_raises: bool = False
capture_raises: bool = False
captured_provider_candidate_key_hash: str | None = None

# At the top of open_liepin_detail(), before returning an OpenCliBrowserResult.
if self.open_raises:
    raise OpenCliBrowserError("liepin_opencli_detail_not_opened")

# Replace the capture fake's signature/body with the claim-aware additions.
def capture_liepin_detail_resume(
    self,
    *,
    source_run_id: str,
    rank: int,
    provider_candidate_key_hash: str,
    require_ready: bool = True,
) -> OpenCliBrowserResult:
    del source_run_id
    self.capture_require_ready_values.append(require_ready)
    if self.capture_raises:
        raise OpenCliBrowserError("liepin_opencli_detail_not_opened")
    if not self.capture_ok:
        return OpenCliBrowserResult(
            ok=False,
            action="capture_liepin_detail_resume",
            safe_reason_code=self.capture_safe_reason_code,
        )
    self.resumes.append({
        "provider_rank": rank,
        "provider_candidate_key_hash": self.captured_provider_candidate_key_hash or provider_candidate_key_hash,
        "detail_payload": {"rank": rank},
    })
    return OpenCliBrowserResult(ok=True, action="capture_liepin_detail_resume", counts={"rank": rank})
```

Update the existing `_request()` fixture's default values with `round_no=1`, `query_instance_id="query-1"`, and `detail_open_claim_ledger=DetailOpenClaimLedger({})`. Tests that cross lanes or rounds must pass one explicit shared ledger instead of relying on that default.

```python
import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserError


def _detail_url(subject: str) -> str:
    return f"https://h.liepin.com/resume/showresumedetail/?res_id_encode={subject}"


def _site_for_subject(subject: str, *, rank: int, **overrides: Any) -> FakeLiepinSearchWorkflowSite:
    values: dict[str, Any] = {
        "structured_cards": [[{"ref": subject, "provider_rank": rank}]],
        "search_states": [_search_state_with_detail_targets(subject) for _ in range(4)],
    }
    values.update(overrides)
    return FakeLiepinSearchWorkflowSite(**values)


def test_workflow_skips_preclaimed_candidate_before_browser_open() -> None:
    ledger = DetailOpenClaimLedger({})
    key = stable_liepin_detail_candidate_key_hash(_detail_url("sameSubject"))
    assert key is not None and ledger.try_claim(key) is True
    site = _site_for_subject("sameSubject", rank=1)
    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(
        _request(target_resumes=1, detail_open_claim_ledger=ledger),
    )
    assert site.calls.count("open_liepin_detail") == 0
    assert envelope["safe_counts"]["detail_open_skipped_seen_count"] == 1


def test_workflow_skips_same_candidate_when_rank_changes() -> None:
    ledger = DetailOpenClaimLedger({})
    first_site = _site_for_subject("sameSubject", rank=1)
    second_site = _site_for_subject("sameSubject", rank=9)
    LiepinSearchWorkflow(site=first_site).search_detail_backed_resumes(
        _request(target_resumes=1, query_instance_id="primary-1", detail_open_claim_ledger=ledger),
    )
    LiepinSearchWorkflow(site=second_site).search_detail_backed_resumes(
        _request(target_resumes=1, query_instance_id="explore-2", detail_open_claim_ledger=ledger),
    )
    assert first_site.calls.count("open_liepin_detail") + second_site.calls.count("open_liepin_detail") == 1


def test_attempted_capture_failure_is_terminal_for_later_lane() -> None:
    ledger = DetailOpenClaimLedger({})
    failed_site = _site_for_subject("sameSubject", rank=1, capture_ok=False)
    later_site = _site_for_subject("sameSubject", rank=2)
    LiepinSearchWorkflow(site=failed_site).search_detail_backed_resumes(
        _request(target_resumes=1, detail_open_claim_ledger=ledger),
    )
    LiepinSearchWorkflow(site=later_site).search_detail_backed_resumes(
        _request(target_resumes=1, query_instance_id="explore-1", detail_open_claim_ledger=ledger),
    )
    assert failed_site.calls.count("open_liepin_detail") == 1
    assert later_site.calls.count("open_liepin_detail") == 0


def test_claim_releases_when_no_browser_open_action_occurs() -> None:
    ledger = DetailOpenClaimLedger({})
    site = _site_for_subject("sameSubject", rank=1, search_states=[OpenCliBrowserResult(ok=False, action="state")])
    LiepinSearchWorkflow(site=site).search_detail_backed_resumes(
        _request(target_resumes=1, detail_open_claim_ledger=ledger),
    )
    key = stable_liepin_detail_candidate_key_hash(_detail_url("sameSubject"))
    assert key is not None and ledger.try_claim(key) is True


def test_browser_action_exception_terminalizes_claim_before_it_escapes() -> None:
    ledger = DetailOpenClaimLedger({})
    failed_site = _site_for_subject("sameSubject", rank=1, open_raises=True)
    with pytest.raises(OpenCliBrowserError):
        LiepinSearchWorkflow(site=failed_site).search_detail_backed_resumes(
            _request(target_resumes=1, detail_open_claim_ledger=ledger),
        )
    later_site = _site_for_subject("sameSubject", rank=2)
    LiepinSearchWorkflow(site=later_site).search_detail_backed_resumes(
        _request(target_resumes=1, query_instance_id="explore-1", detail_open_claim_ledger=ledger),
    )
    assert later_site.calls.count("open_liepin_detail") == 0


def test_capture_exception_terminalizes_claim_for_later_lane() -> None:
    ledger = DetailOpenClaimLedger({})
    failed_site = _site_for_subject("sameSubject", rank=1, capture_raises=True)
    with pytest.raises(OpenCliBrowserError):
        LiepinSearchWorkflow(site=failed_site).search_detail_backed_resumes(
            _request(target_resumes=1, detail_open_claim_ledger=ledger),
        )
    later_site = _site_for_subject("sameSubject", rank=2)
    LiepinSearchWorkflow(site=later_site).search_detail_backed_resumes(
        _request(target_resumes=1, query_instance_id="explore-1", detail_open_claim_ledger=ledger),
    )
    assert later_site.calls.count("open_liepin_detail") == 0


def test_capture_key_mismatch_fails_closed_before_candidate_mapping() -> None:
    ledger = DetailOpenClaimLedger({})
    site = _site_for_subject(
        "sameSubject",
        rank=1,
        captured_provider_candidate_key_hash="different-key",
    )
    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(
        _request(target_resumes=1, detail_open_claim_ledger=ledger),
    )
    assert envelope["safe_reason_code"] == "liepin_opencli_candidate_identity_mismatch"
    assert not envelope["resumes"]
```

- [x] **Step 2: Run workflow tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_liepin_search_workflow.py tests/test_liepin_opencli_workflow.py
```

Expected: FAIL because the current workflow opens selected cards without checking a run-level claim.

- [x] **Step 3: Introduce a private detail-candidate value**

Add a private dataclass in `liepin_search_workflow.py`:

```python
@dataclass(frozen=True)
class _DetailCandidate:
    ref: str
    rank: int
    detail_url: str
    provider_candidate_key_hash: str
```

Replace rank-only selection with a resolver that obtains `safe_liepin_detail_url_for_ref(ref)`, calls `stable_liepin_detail_candidate_key_hash(detail_url)`, and returns `None` if either fails. Record the safe identity-missing reason and move to the next card without calling `open_detail`.

- [x] **Step 4: Claim before calling `open_detail`**

Thread `detail_open_claim_ledger` and `provider_candidate_key_hash` into `_open_detail_with_retry()` and `_open_detail_transition()`. Inside the transition's `open_detail()` action closure, call `ledger.record_browser_open_attempt(key)` **immediately before** the actual `open_liepin_detail` or `open_liepin_detail_cached_url` call. Preserve the current maximum of two calls. This order is mandatory: if the browser action raises, the persisted claim already records that an action was attempted and can never be released.

In the detail loop, perform this exact order:

```python
candidate = _resolve_detail_candidate(selected_ref, selected_rank)
if candidate is None:
    continue
if not request.detail_open_claim_ledger.try_claim(candidate.provider_candidate_key_hash):
    skipped_seen += 1
    continue

try:
    open_result = self._open_detail_with_retry(
        source_run_id=request.source_run_id,
        ref=candidate.ref,
        rank=candidate.rank,
        cached_detail_url=candidate.detail_url,
        use_cached=using_cached_card_items,
        provider_candidate_key_hash=candidate.provider_candidate_key_hash,
        detail_open_claim_ledger=request.detail_open_claim_ledger,
    )
    if not open_result.ok:
        if request.detail_open_claim_ledger.has_browser_open_attempt(candidate.provider_candidate_key_hash):
            request.detail_open_claim_ledger.mark_terminal_failed(
                candidate.provider_candidate_key_hash,
                safe_reason_code=open_result.safe_reason_code,
            )
        else:
            request.detail_open_claim_ledger.release_unattempted(candidate.provider_candidate_key_hash)
        continue
    wait_result = self._wait_detail_ready_transition(
        source_run_id=request.source_run_id,
        rank=candidate.rank,
    )
    if not wait_result.ok:
        request.detail_open_claim_ledger.mark_terminal_failed(
            candidate.provider_candidate_key_hash,
            safe_reason_code=wait_result.safe_reason_code,
        )
        continue
    capture_result = self._capture_detail_transition(
        source_run_id=request.source_run_id,
        rank=candidate.rank,
        require_ready=False,
        provider_candidate_key_hash=candidate.provider_candidate_key_hash,
    )
    if not capture_result.ok:
        request.detail_open_claim_ledger.mark_terminal_failed(
            candidate.provider_candidate_key_hash,
            safe_reason_code=capture_result.safe_reason_code,
        )
        continue
    request.detail_open_claim_ledger.mark_opened(candidate.provider_candidate_key_hash)
except Exception:
    if request.detail_open_claim_ledger.has_browser_open_attempt(candidate.provider_candidate_key_hash):
        request.detail_open_claim_ledger.mark_terminal_failed(
            candidate.provider_candidate_key_hash,
            safe_reason_code="liepin_opencli_detail_unexpected_failure",
        )
    else:
        request.detail_open_claim_ledger.release_unattempted(candidate.provider_candidate_key_hash)
    raise
```

Pass `candidate.provider_candidate_key_hash` into a private captured-resume envelope before the site adapter writes the protected artifact. The envelope must assert that its carried key equals the selected candidate key before finalization; the claim-aware OpenCLI retriever rejects an absent or mismatching key and has no artifact-path fallback. Preserve the current `attempted_ranks` only to avoid selecting the same visible row twice in one page; it is no longer a cross-query identity control.

- [x] **Step 5: Add safe counters and public sanitization test**

Add private workflow counts named `detail_claim_granted_count`, `detail_opened_count`, `detail_open_skipped_seen_count`, and `detail_open_terminal_failure_count`; add only these numeric counts to the existing safe allowlist in the worker/client path. Assert the new claim-aware workflow/event and Workbench-facing fields contain only those counts and safe reason codes—not `res_id_encode`, raw URLs, raw refs, or the opaque claim hash. Do not broaden this task into a rewrite of unrelated pre-existing generic source-lane public payloads.

- [x] **Step 6: Run workflow and privacy tests**

Run:

```bash
uv run pytest -q tests/test_liepin_search_workflow.py tests/test_liepin_opencli_workflow.py tests/test_liepin_opencli_browser.py tests/test_workbench_security_audit.py
```

Expected: PASS. The browser opens once across repeated candidate sightings and at most twice inside the one granted claim.

- [x] **Step 7: Commit workflow enforcement**

```bash
git add src/seektalent/providers/liepin/liepin_search_workflow.py src/seektalent/providers/liepin/liepin_site_adapter.py tests/test_liepin_search_workflow.py tests/test_liepin_opencli_workflow.py tests/test_liepin_opencli_browser.py tests/test_workbench_security_audit.py
git commit -m "fix: claim Liepin detail before browser open"
```

### Task 5: Verify End-To-End Runtime Behavior And Regression Boundaries

**Files:**

- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
- Modify: `tests/test_liepin_provider_adapter.py`
- Modify: `tests/test_liepin_provider_source_composition.py`
- Modify: `tests/test_runtime_source_lanes.py`
- Modify: `docs/superpowers/specs/2026-07-10-liepin-detail-open-claim-design.md` only if verification reveals a contradiction with an approved invariant.

**Interfaces:**

- Consumes all prior task interfaces.
- Proves the default OpenCLI path has stable, run-level at-most-once detail openings without touching approved-detail ledger behavior.

- [x] **Step 1: Add the cross-round end-to-end test**

Add a test that executes two logical query rounds against a fixture where both card results resolve to the same `res_id_encode`:

```python
def test_default_opencli_path_opens_same_subject_once_across_rounds() -> None:
    runtime, browser = _runtime_with_liepin_subjects([
        ["same-subject"],
        ["same-subject"],
    ])
    result = asyncio.run(runtime.run_match_async(
        job_title="Platform Engineer",
        jd="Python and Rust platform engineering",
    ))
    assert browser.detail_open_count_for_subject("same-subject") == 1
    assert result.run_state.detail_open_claims_by_provider_key
    assert list(result.run_state.detail_open_claims_by_provider_key.values())[0].status == "opened"
```

Add a companion approved-detail regression test proving `LiepinStore.reserve_detail_attempt()` keeps its existing daily idempotency behavior without importing `DetailOpenClaimLedger`.

Add a checkpoint-resume companion: serialize the `RunState` after the first successful open, restore it into a new runtime execution (which creates one new facade around the restored map), present the same `res_id_encode`, and assert the resumed browser opens it zero times.

- [x] **Step 2: Run the focused end-to-end suite**

Run:

```bash
uv run pytest -q tests/test_liepin_detail_open_claims.py tests/test_liepin_search_workflow.py tests/test_liepin_opencli_retriever.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_provider_adapter.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py tests/test_liepin_provider_source_composition.py tests/test_liepin_detail_ledger.py
```

Expected: PASS. The approved-detail ledger remains independent and the default OpenCLI path respects the new run-level invariant.

- [x] **Step 3: Run static and boundary checks**

Run:

```bash
uv run ruff check src/seektalent/models.py src/seektalent/source_adapters/round_adapters.py src/seektalent/sources/liepin/runtime_lane.py src/seektalent/providers/liepin tests/test_liepin_detail_open_claims.py tests/test_liepin_search_workflow.py tests/test_liepin_opencli_retriever.py
uv run python tools/check_arch_imports.py
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
git diff --check
```

Expected: all commands exit zero. Any pre-existing direct `tach check` failure remains a separately reported architecture issue; do not weaken Tach configuration or add a suppression in this change.

- [x] **Step 4: Commit end-to-end proof**

```bash
git add tests/test_runtime_multi_source_round_dispatch.py tests/test_liepin_provider_adapter.py tests/test_liepin_provider_source_composition.py tests/test_runtime_source_lanes.py docs/superpowers/specs/2026-07-10-liepin-detail-open-claim-design.md
git commit -m "test: cover run-level Liepin detail claims"
```

## Completion Verification (2026-07-11)

All five delivery tasks are implemented on `main` (`cee9c7cc`). The following is a current-state verification record, rather than an assertion that an old red test still fails after completion.

| Task | Verified implementation and evidence |
| --- | --- |
| 1. Persisted synchronized claims | `RuntimeDetailOpenClaim`, checkpointed `RunState.detail_open_claims_by_provider_key`, and a lock-protected claim facade exist. `tests/test_liepin_detail_open_claims.py` covers concurrency, lifecycle transitions, persistence, and invalid transitions. |
| 2. Stable private identity | Liepin derives an opaque SHA-256 key only from canonical detail identity and carries it through capture/finalization. `tests/test_liepin_opencli_retriever.py` and `tests/test_liepin_opencli_browser.py` cover valid, malformed, rank-independent, and mismatch cases. |
| 3. One run-owned ledger through OpenCLI | Orchestrator creates one ledger over the run map, passes it through source dispatch, adapter, worker client, retriever, site adapter, and workflow without putting it in generic provider payloads. `tests/test_liepin_opencli_worker_client.py`, `tests/test_liepin_runtime_source_lane.py`, and `tests/test_runtime_audit.py` cover composition and privacy boundaries. |
| 4. Claim before browser effect | The workflow claims before each detail open, keeps the two navigation attempts inside that claim, releases only unattempted claims, terminalizes attempted failures, and returns safe counters only. `tests/test_liepin_search_workflow.py`, `tests/test_liepin_opencli_workflow.py`, and `tests/test_liepin_opencli_retriever.py` cover repeated sightings, failures, and safe output. |
| 5. End-to-end behavior and regressions | Cross-lane/round and checkpoint behavior keeps a subject to at most one open per run, while the approved-detail daily ledger remains independent. `tests/test_liepin_detail_open_claims.py`, `tests/test_runtime_multi_source_round_dispatch.py`, `tests/test_runtime_source_lanes.py`, `tests/test_liepin_provider_adapter.py`, `tests/test_liepin_provider_source_composition.py`, and `tests/test_liepin_detail_ledger.py` cover the boundary. |

Current verification completed after the merge:

- The combined logical-query/detail-claim focused Python suite passed: **824 tests**.
- The repository Python suite passed: **3476 tests**.
- `uv run python tools/check_arch_imports.py` passed, and `git diff --check` is re-run for this documentation update.

Two repository-wide checks remain unable to satisfy the plan's historical “exit zero” expectation, but the failures pre-date the plan base commit `c6fc0e57` and are unchanged by this delivery: `tools/check_source_boundaries.py` reports the two `normalized_artifacts.py:8` Liepin branches, and `tools/check_tach_baseline.py` reports the three existing imports in `liepin_site_adapter.py` and `workbench_liepin_start_probe.py`. The Tach baseline is empty, so that tool still labels those old imports “New”; this record does not claim that either gate passes. They are recorded as unrelated baseline debt, not attributed to this slice.
