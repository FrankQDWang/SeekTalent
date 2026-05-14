# Detail Grants And Backend Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make detail opens grant-gated and add explicit PI backend runner primitives with per-connection plus provider-account/browser-profile locking.

**Architecture:** WorkflowRuntime remains the approval and budget authority by issuing `DetailOpenGrant`; the Liepin policy layer only validates grants before execution. The PI runner dispatches across explicit modes and blocks `dokobot_action` unless DokoBot capability negotiation proves action tools are available. The runner must acquire both the `connection_id` lock and a provider-account/browser-profile lock key before page work starts. Missing action capability must not trigger tool installation, read-only downgrade, or automatic `legacy_worker_compat` fallback.

**Tech Stack:** Python 3.12, dataclasses, existing Liepin policy and provider tests, pytest.

**Spec:** `docs/superpowers/specs/2026-05-13-provider-interaction-agent-dokobot-design.md`

**Depends On:**
- `docs/superpowers/plans/2026-05-13-pi-agent-contracts-and-skill-recipes.md`
- `docs/superpowers/plans/2026-05-13-dokobot-capability-and-protected-artifacts.md`

---

## File Structure

- Modify: `src/seektalent/providers/liepin/policy.py`
  - Add grant-shape validation only. Durable budget reservation and idempotency stay in `LiepinStore.reserve_detail_attempt()`.
- Add: `src/seektalent/providers/pi_agent/locks.py`
  - Thread-safe in-process composite mutex for one active PI run per provider connection and provider account/browser profile.
- Add: `src/seektalent/providers/liepin/pi_runner.py`
  - Explicit backend-mode dispatch for disabled, DokoBot read-only, DokoBot action, legacy worker compatibility, and fake fixture modes.
- Test: `tests/test_liepin_detail_policy.py`
  - Grant missing, expired, and mismatched cases using typed `PiAgentFailureCode` values.
- Test: `tests/test_liepin_detail_ledger.py`
  - Existing persistent reservation/idempotency tests remain part of this plan's verification.
- Test: `tests/test_liepin_pi_runner.py`
  - Backend-mode fail-closed, explicit dispatch, trace artifact writer, connection-lock, and provider-account/browser-profile lock tests.

### Task 1: Add Detail Grant Shape Validation

**Files:**
- Modify: `src/seektalent/providers/liepin/policy.py`
- Test: `tests/test_liepin_detail_policy.py`
- Verify existing: `tests/test_liepin_detail_ledger.py`

- [ ] **Step 1: Write failing detail grant tests**

Add imports to the existing import block in `tests/test_liepin_detail_policy.py`, then append these tests:

```python
from datetime import UTC, datetime, timedelta

from seektalent.providers.liepin.policy import DetailGrantDecision, validate_detail_open_grant
from seektalent.providers.pi_agent.contracts import DetailOpenGrant, PiAgentFailureCode


def _grant(*, candidate_ref: str = "candidate_1", source_run_id: str = "source_run_1", minutes: int = 5) -> DetailOpenGrant:
    return DetailOpenGrant(
        schema_version="detail-open-grant-v1",
        approval_id="approval_1",
        budget_reservation_id="budget_1",
        candidate_ref=candidate_ref,
        source_run_id=source_run_id,
        provider="liepin",
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
        issued_by="workflow_runtime",
        idempotency_key=f"detail_{candidate_ref}_approval_1",
        grant_signature="signature_1",
    )


def test_open_detail_without_grant_is_blocked() -> None:
    decision = validate_detail_open_grant(
        grant=None,
        candidate_ref="candidate_1",
        source_run_id="source_run_1",
    )

    assert decision == DetailGrantDecision(False, PiAgentFailureCode.DETAIL_OPEN_GRANT_MISSING)


def test_expired_detail_grant_is_blocked() -> None:
    decision = validate_detail_open_grant(
        grant=_grant(minutes=-1),
        candidate_ref="candidate_1",
        source_run_id="source_run_1",
    )

    assert decision.allowed is False
    assert decision.failure_code == PiAgentFailureCode.DETAIL_OPEN_GRANT_EXPIRED


def test_candidate_mismatch_is_blocked() -> None:
    decision = validate_detail_open_grant(
        grant=_grant(candidate_ref="candidate_2"),
        candidate_ref="candidate_1",
        source_run_id="source_run_1",
    )

    assert decision.failure_code == PiAgentFailureCode.DETAIL_OPEN_GRANT_CANDIDATE_MISMATCH


def test_source_run_mismatch_is_blocked() -> None:
    decision = validate_detail_open_grant(
        grant=_grant(source_run_id="source_run_2"),
        candidate_ref="candidate_1",
        source_run_id="source_run_1",
    )

    assert decision.failure_code == PiAgentFailureCode.DETAIL_OPEN_GRANT_SOURCE_RUN_MISMATCH


def test_valid_detail_grant_is_allowed() -> None:
    decision = validate_detail_open_grant(
        grant=_grant(candidate_ref="candidate_1"),
        candidate_ref="candidate_1",
        source_run_id="source_run_1",
    )

    assert decision == DetailGrantDecision(True)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_liepin_detail_policy.py tests/test_liepin_detail_ledger.py -q
```

Expected: missing `validate_detail_open_grant` failure.

- [ ] **Step 3: Implement grant-shape validation**

Add imports to the existing import block in `src/seektalent/providers/liepin/policy.py`, then append this validation code:

```python
from dataclasses import dataclass
from datetime import UTC, datetime

from seektalent.providers.pi_agent.contracts import DetailOpenGrant, PiAgentFailureCode


@dataclass(frozen=True)
class DetailGrantDecision:
    allowed: bool
    failure_code: PiAgentFailureCode | None = None


def validate_detail_open_grant(
    *,
    grant: DetailOpenGrant | None,
    candidate_ref: str,
    source_run_id: str,
    now: datetime | None = None,
) -> DetailGrantDecision:
    if grant is None:
        return DetailGrantDecision(False, PiAgentFailureCode.DETAIL_OPEN_GRANT_MISSING)
    current_time = now or datetime.now(UTC)
    if grant.expires_at <= current_time:
        return DetailGrantDecision(False, PiAgentFailureCode.DETAIL_OPEN_GRANT_EXPIRED)
    if grant.candidate_ref != candidate_ref:
        return DetailGrantDecision(False, PiAgentFailureCode.DETAIL_OPEN_GRANT_CANDIDATE_MISMATCH)
    if grant.source_run_id != source_run_id:
        return DetailGrantDecision(False, PiAgentFailureCode.DETAIL_OPEN_GRANT_SOURCE_RUN_MISMATCH)
    return DetailGrantDecision(True)
```

Do not add an in-memory duplicate/idempotency set to this function. Duplicate grant use and detail-open budget consumption are durable runtime concerns and must continue to flow through `LiepinStore.reserve_detail_attempt()` and its existing SQLite transaction/idempotency tests.

- [ ] **Step 4: Run detail policy tests**

```bash
uv run pytest tests/test_liepin_detail_policy.py tests/test_liepin_detail_ledger.py -q
```

Expected: pass.

- [ ] **Step 5: Commit detail grant validation**

```bash
git add src/seektalent/providers/liepin/policy.py tests/test_liepin_detail_policy.py
git commit -m "feat: require detail open grants"
```

### Task 2: Add Backend Modes, Connection Locking, And Runner Primitives

**Files:**
- Add: `src/seektalent/providers/pi_agent/locks.py`
- Add: `src/seektalent/providers/liepin/pi_runner.py`
- Test: `tests/test_liepin_pi_runner.py`

- [ ] **Step 1: Write failing backend-mode and lock tests**

Create `tests/test_liepin_pi_runner.py`:

```python
from hashlib import sha256
import json

import pytest

from seektalent.providers.liepin.pi_runner import LiepinPiRunner, SearchCardsExecutor
from seektalent.providers.pi_agent.capabilities import DokoBotCapabilities
from seektalent.providers.pi_agent.contracts import (
    PiAgentFailureCode,
    PiAgentResult,
    PiAgentResultStatus,
    PiArtifactRef,
    PiBackendMode,
    ProtectedArtifactClass,
)
from seektalent.providers.pi_agent.locks import InMemoryPiConnectionLock


SEARCH_KWARGS = {
    "session_id": "session_1",
    "source_run_id": "source_run_1",
    "connection_id": "connection_1",
    "provider_account_lock_key": "provider_account_1",
    "keyword_query": "Python",
    "query_terms": ["Python"],
    "max_pages": 1,
    "max_cards": 10,
}


def _trace_writer(
    content: bytes,
    artifact_class: ProtectedArtifactClass,
    policy_id: str,
) -> PiArtifactRef:
    assert artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert policy_id == "liepin-trace-redaction-v1"
    payload = json.loads(content.decode("utf-8"))
    assert payload["schema_version"] == "pi-agent-action-trace-v1"
    content_hash = sha256(content).hexdigest()
    return PiArtifactRef(
        artifact_class=artifact_class,
        artifact_ref=f"trace:{content_hash}",
        content_sha256=content_hash,
        redaction_policy_id=policy_id,
    )


def _capabilities(*, action: bool) -> DokoBotCapabilities:
    return DokoBotCapabilities(
        cli_version="2.11.0",
        supports_read=True,
        supports_chunks_format=True,
        supports_session_continuation=True,
        supports_click=action,
        supports_type=action,
        supports_navigation=action,
        supports_pagination_action=action,
        action_manifest_id="manifest_1" if action else None,
        action_manifest_version="1" if action else None,
        action_manifest_tools=("click", "fill", "navigate", "turn_page") if action else (),
    )


def _runner(
    *,
    backend_mode: PiBackendMode,
    capabilities: DokoBotCapabilities | None = None,
    lock: InMemoryPiConnectionLock | None = None,
    dokobot_search_cards: SearchCardsExecutor | None = None,
    legacy_search_cards: SearchCardsExecutor | None = None,
) -> LiepinPiRunner:
    return LiepinPiRunner(
        backend_mode=backend_mode,
        dokobot_capabilities=capabilities,
        connection_lock=lock or InMemoryPiConnectionLock(),
        trace_artifact_writer=_trace_writer,
        dokobot_search_cards=dokobot_search_cards,
        legacy_search_cards=legacy_search_cards,
    )


def test_dokobot_action_mode_fails_closed_without_action_capability() -> None:
    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=False),
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE
    assert result.action_trace_ref.artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert result.action_trace_ref.content_sha256 != "0" * 64


def test_same_connection_concurrent_run_is_blocked() -> None:
    lock = InMemoryPiConnectionLock()
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_1",
        )
        is True
    )
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_2",
            source_run_id="source_run_2",
        )
        is False
    )


def test_same_provider_account_different_connection_is_blocked() -> None:
    lock = InMemoryPiConnectionLock()
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_1",
        )
        is True
    )
    assert (
        lock.acquire(
            connection_id="connection_2",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_2",
        )
        is False
    )


def test_runner_returns_blocked_when_connection_or_provider_lock_is_held() -> None:
    lock = InMemoryPiConnectionLock()
    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="other_run",
        )
        is True
    )
    runner = _runner(backend_mode=PiBackendMode.FAKE_FIXTURE, lock=lock)

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.PROVIDER_CONNECTION_LOCKED


def test_runner_releases_connection_lock_after_backend_error() -> None:
    lock = InMemoryPiConnectionLock()

    def legacy_search_cards(**kwargs: object) -> PiAgentResult:
        raise RuntimeError("backend crashed")

    runner = _runner(
        backend_mode=PiBackendMode.LEGACY_WORKER_COMPAT,
        lock=lock,
        legacy_search_cards=legacy_search_cards,
    )

    with pytest.raises(RuntimeError, match="backend crashed"):
        runner.search_cards(**SEARCH_KWARGS)

    assert (
        lock.acquire(
            connection_id="connection_1",
            provider_account_lock_key="provider_account_1",
            source_run_id="source_run_2",
        )
        is True
    )


def test_legacy_worker_mode_is_explicit_not_silent_fallback() -> None:
    runner = _runner(
        backend_mode=PiBackendMode.LEGACY_WORKER_COMPAT,
    )

    assert runner.backend_mode == PiBackendMode.LEGACY_WORKER_COMPAT


def test_dokobot_action_mode_never_calls_legacy_backend_as_fallback() -> None:
    called = False

    def legacy_search_cards(**kwargs: object) -> PiAgentResult:
        nonlocal called
        called = True
        raise AssertionError("legacy fallback must not run")

    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=False),
        legacy_search_cards=legacy_search_cards,
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert called is False
    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE


def test_dokobot_action_mode_dispatches_when_capability_is_available() -> None:
    called = False

    def dokobot_search_cards(**kwargs: object) -> PiAgentResult:
        nonlocal called
        called = True
        return PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.SUCCEEDED,
            action_trace_ref=_trace_writer(
                b'{"schema_version":"pi-agent-action-trace-v1","interaction_id":"trace_ok"}',
                ProtectedArtifactClass.REDACTED_EVIDENCE,
                "liepin-trace-redaction-v1",
            ),
        )

    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=True),
        dokobot_search_cards=dokobot_search_cards,
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert called is True
    assert result.status == PiAgentResultStatus.SUCCEEDED


def test_dokobot_action_mode_requires_executor_when_capability_is_available() -> None:
    runner = _runner(
        backend_mode=PiBackendMode.DOKOBOT_ACTION,
        capabilities=_capabilities(action=True),
    )

    with pytest.raises(RuntimeError, match="requires an explicit action executor"):
        runner.search_cards(**SEARCH_KWARGS)


@pytest.mark.parametrize("backend_mode", [PiBackendMode.DISABLED, PiBackendMode.DOKOBOT_READ_ONLY])
def test_modes_that_cannot_submit_search_are_blocked(backend_mode: PiBackendMode) -> None:
    runner = _runner(
        backend_mode=backend_mode,
        capabilities=_capabilities(action=False),
    )

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE


def test_legacy_worker_mode_without_executor_is_blocked() -> None:
    runner = _runner(backend_mode=PiBackendMode.LEGACY_WORKER_COMPAT)

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.BLOCKED
    assert result.stop_reason == PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE


def test_fake_fixture_mode_returns_success_with_real_trace_ref() -> None:
    runner = _runner(backend_mode=PiBackendMode.FAKE_FIXTURE)

    result = runner.search_cards(**SEARCH_KWARGS)

    assert result.status == PiAgentResultStatus.SUCCEEDED
    assert result.action_trace_ref.artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert result.action_trace_ref.content_sha256 != "0" * 64
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_liepin_pi_runner.py -q
```

Expected: import failure for `seektalent.providers.liepin.pi_runner`.

- [ ] **Step 3: Implement composite connection and provider-account lock**

Add `src/seektalent/providers/pi_agent/locks.py`:

```python
from threading import Lock


class InMemoryPiConnectionLock:
    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = Lock()

    def acquire(self, *, connection_id: str, provider_account_lock_key: str, source_run_id: str) -> bool:
        lock_keys = _lock_keys(connection_id, provider_account_lock_key)
        with self._lock:
            if any(self._owners.get(key) not in {None, source_run_id} for key in lock_keys):
                return False
            for key in lock_keys:
                self._owners[key] = source_run_id
            return True

    def release(self, *, connection_id: str, provider_account_lock_key: str, source_run_id: str) -> None:
        lock_keys = _lock_keys(connection_id, provider_account_lock_key)
        with self._lock:
            for key in lock_keys:
                if self._owners.get(key) == source_run_id:
                    del self._owners[key]


def _lock_keys(connection_id: str, provider_account_lock_key: str) -> tuple[str, str]:
    if not connection_id or not provider_account_lock_key:
        raise ValueError("PI connection lock requires connection_id and provider_account_lock_key")
    return (f"connection:{connection_id}", f"provider_account:{provider_account_lock_key}")
```

Use the approved `provider_account_hash` as `provider_account_lock_key` when it is available. If a future backend isolates separate browser profiles for the same account, use the browser-profile lock key for that run instead; the important invariant is that two runs sharing a logged-in provider account or browser profile cannot execute page actions concurrently.

- [ ] **Step 4: Implement runner dispatch**

Add `src/seektalent/providers/liepin/pi_runner.py`:

```python
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from seektalent.providers.pi_agent.capabilities import DokoBotCapabilities
from seektalent.providers.pi_agent.contracts import (
    PiAgentActionTraceEntry,
    PiAgentActionType,
    PiAgentFailureCode,
    PiAgentResult,
    PiAgentResultStatus,
    PiArtifactRef,
    PiBackendMode,
    ProtectedArtifactClass,
)
from seektalent.providers.pi_agent.locks import InMemoryPiConnectionLock


TraceArtifactWriter = Callable[[bytes, ProtectedArtifactClass, str], PiArtifactRef]


class SearchCardsExecutor(Protocol):
    def __call__(
        self,
        *,
        session_id: str,
        source_run_id: str,
        connection_id: str,
        provider_account_lock_key: str,
        keyword_query: str,
        query_terms: list[str],
        max_pages: int,
        max_cards: int,
    ) -> PiAgentResult: ...


@dataclass
class LiepinPiRunner:
    backend_mode: PiBackendMode
    dokobot_capabilities: DokoBotCapabilities | None
    connection_lock: InMemoryPiConnectionLock
    trace_artifact_writer: TraceArtifactWriter
    dokobot_search_cards: SearchCardsExecutor | None = None
    legacy_search_cards: SearchCardsExecutor | None = None

    def search_cards(
        self,
        *,
        session_id: str,
        source_run_id: str,
        connection_id: str,
        provider_account_lock_key: str,
        keyword_query: str,
        query_terms: list[str],
        max_pages: int,
        max_cards: int,
    ) -> PiAgentResult:
        if not self.connection_lock.acquire(
            connection_id=connection_id,
            provider_account_lock_key=provider_account_lock_key,
            source_run_id=source_run_id,
        ):
            return self._blocked(
                failure_code=PiAgentFailureCode.PROVIDER_CONNECTION_LOCKED,
                source_run_id=source_run_id,
                connection_id=connection_id,
            )
        try:
            if self.backend_mode == PiBackendMode.DOKOBOT_ACTION:
                if self.dokobot_capabilities is None or not self.dokobot_capabilities.can_execute_liepin_actions:
                    return self._blocked(
                        failure_code=PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE,
                        source_run_id=source_run_id,
                        connection_id=connection_id,
                    )
                if self.dokobot_search_cards is None:
                    raise RuntimeError("DokoBot action mode requires an explicit action executor.")
                return self.dokobot_search_cards(
                    session_id=session_id,
                    source_run_id=source_run_id,
                    connection_id=connection_id,
                    provider_account_lock_key=provider_account_lock_key,
                    keyword_query=keyword_query,
                    query_terms=query_terms,
                    max_pages=max_pages,
                    max_cards=max_cards,
                )
            if self.backend_mode in {PiBackendMode.DOKOBOT_READ_ONLY, PiBackendMode.DISABLED}:
                return self._blocked(
                    failure_code=PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE,
                    source_run_id=source_run_id,
                    connection_id=connection_id,
                )
            if self.backend_mode == PiBackendMode.LEGACY_WORKER_COMPAT:
                if self.legacy_search_cards is None:
                    return self._blocked(
                        failure_code=PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE,
                        source_run_id=source_run_id,
                        connection_id=connection_id,
                    )
                return self.legacy_search_cards(
                    session_id=session_id,
                    source_run_id=source_run_id,
                    connection_id=connection_id,
                    provider_account_lock_key=provider_account_lock_key,
                    keyword_query=keyword_query,
                    query_terms=query_terms,
                    max_pages=max_pages,
                    max_cards=max_cards,
                )
            if self.backend_mode == PiBackendMode.FAKE_FIXTURE:
                return self._fake_fixture_result(source_run_id=source_run_id, connection_id=connection_id)
            return self._blocked(
                failure_code=PiAgentFailureCode.DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE,
                source_run_id=source_run_id,
                connection_id=connection_id,
            )
        finally:
            self.connection_lock.release(
                connection_id=connection_id,
                provider_account_lock_key=provider_account_lock_key,
                source_run_id=source_run_id,
            )

    def _blocked(
        self,
        *,
        failure_code: PiAgentFailureCode,
        source_run_id: str,
        connection_id: str,
    ) -> PiAgentResult:
        return PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.BLOCKED,
            stop_reason=failure_code,
            action_trace_ref=self._write_trace(
                result_code="blocked",
                failure_code=failure_code,
                source_run_id=source_run_id,
                connection_id=connection_id,
            ),
        )

    def _fake_fixture_result(self, *, source_run_id: str, connection_id: str) -> PiAgentResult:
        return PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.SUCCEEDED,
            cards_seen=0,
            cards_selected=0,
            action_trace_ref=self._write_trace(
                result_code="ok",
                failure_code=None,
                source_run_id=source_run_id,
                connection_id=connection_id,
            ),
        )

    def _write_trace(
        self,
        *,
        result_code: Literal["ok", "blocked", "failed", "partial"],
        failure_code: PiAgentFailureCode | None,
        source_run_id: str,
        connection_id: str,
    ) -> PiArtifactRef:
        trace = PiAgentActionTraceEntry(
            schema_version="pi-agent-action-trace-v1",
            timestamp=datetime.now(UTC),
            provider_skill_id="liepin.search_cards.v1",
            interaction_id=f"{source_run_id}:search_cards:1",
            source_run_id=source_run_id,
            connection_id=connection_id,
            action_sequence=1,
            action_type=PiAgentActionType.LIEPIN_SUBMIT_KEYWORD_SEARCH,
            backend_mode=self.backend_mode,
            capability_version=self.dokobot_capabilities.action_manifest_version
            if self.dokobot_capabilities and self.dokobot_capabilities.action_manifest_version
            else "none",
            safe_target_descriptor="liepin search cards",
            result_code=result_code,
            duration_ms=0,
            retry_count=0,
            redaction_policy_id="liepin-trace-redaction-v1",
            failure_code=failure_code,
        )
        return self.trace_artifact_writer(
            json.dumps(trace.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8"),
            ProtectedArtifactClass.REDACTED_EVIDENCE,
            "liepin-trace-redaction-v1",
        )
```

The runner must not construct `PiArtifactRef` values with fake refs or zero hashes. Every `PiAgentResult.action_trace_ref` must come from the injected trace artifact writer.

- [ ] **Step 5: Run runner tests**

```bash
uv run pytest tests/test_liepin_pi_runner.py tests/test_liepin_detail_policy.py tests/test_liepin_detail_ledger.py -q
```

Expected: pass.

- [ ] **Step 6: Commit backend dispatch primitives**

```bash
git add src/seektalent/providers/pi_agent/locks.py src/seektalent/providers/liepin/pi_runner.py tests/test_liepin_pi_runner.py
git commit -m "feat: add explicit liepin pi backend runner"
```

## Self-Review

- Spec coverage: runtime detail grants, durable store-owned idempotency, explicit backend modes, fail-closed DokoBot action mode, and one active PI run per connection plus provider-account/browser-profile key are covered.
- Placeholder scan: every step names concrete files, tests, commands, and expected outcomes.
- Type consistency: this plan imports contract and capability types from the two earlier plans and does not redefine them.
- Artifact discipline: runner results never invent trace artifact refs; they use an injected trace artifact writer.
- Backend discipline: `dokobot_action` dispatches only when capability negotiation succeeds and an explicit action executor is bound. It never calls legacy worker compatibility as fallback.
