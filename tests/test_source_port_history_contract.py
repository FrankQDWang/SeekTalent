from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

from pydantic import TypeAdapter, ValidationError
import pytest

from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ExactAuthorizationSelector,
    JSON_SAFE_INTEGER,
    ObservedFailureFact,
    ObservedResultFact,
    SQLITE_MAX_INTEGER,
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryResultV1,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "history_contract.py"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _query_values() -> dict[str, object]:
    return {
        "contract_version": "seektalent.source-port.query.request/v1",
        "run_id": "run-1",
        "operation_id": "operation-1",
        "source": "liepin",
        "operation_kind": "search",
        "idempotency_key": "key-1",
        "request_hash": HASH_A,
        "attempt_no": 1,
        "authorization_selector": {"kind": "exact", "ordinal": 1},
        "accepted_generation_hint": 2,
        "searched_first_generation": 1,
        "searched_last_generation": 3,
        "expected_source_operation_ledger_revision": 4,
        "expected_reconciliation_revision": 0,
    }


def _result_values() -> dict[str, object]:
    return {
        **_query_values(),
        "contract_version": "seektalent.source-port.query.result/v1",
    }


def _accepted_fact_values() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "operation_id": "operation-1",
        "source": "liepin",
        "operation_kind": "search",
        "idempotency_key": "key-1",
        "request_hash": HASH_A,
        "attempt_no": 1,
        "accepted_requirement_revision_id": "requirement-1",
        "runtime_attempt_fence_ref": HASH_B,
        "accepted_generation": 2,
        "accepted_journal_revision": 10,
        "head_generation": 2,
        "head_journal_revision": 10,
        "dispatch_authorization_ordinal": 1,
        "authorized_dispatch_intent_id": "intent-1",
        "authorized_dispatch_intent_revision": 1,
        "authorized_dispatch_intent_digest": HASH_C,
        "profile_binding_generation": 1,
        "browser_control_scope_id": "browser-scope-1",
        "controller_fence_ref": HASH_A,
    }


def test_query_is_strict_closed_and_frozen() -> None:
    query = SourceHistoryQueryV1(**_query_values())

    assert query.contract_version == "seektalent.source-port.query.request/v1"
    assert query.authorization_selector == ExactAuthorizationSelector(kind="exact", ordinal=1)
    with pytest.raises(ValidationError):
        query.attempt_no = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_no", True),
        ("attempt_no", 1.0),
        ("attempt_no", 0),
        ("attempt_no", JSON_SAFE_INTEGER + 1),
        ("searched_first_generation", False),
        ("expected_reconciliation_revision", -1),
        ("request_hash", "A" * 64),
        ("request_hash", "a" * 63),
        ("run_id", "run\n1"),
        ("run_id", "x" * 97),
        ("run_id", "\ud800"),
        ("operation_kind", "arbitrary_command"),
        ("source", "cts"),
    ],
)
def test_query_rejects_invalid_scalar_boundaries(field: str, value: object) -> None:
    payload = _query_values()
    payload[field] = value

    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**payload)


def test_history_wire_integer_domain_is_jcs_safe_without_narrowing_sqlite_storage() -> None:
    query = SourceHistoryQueryV1(**{**_query_values(), "attempt_no": JSON_SAFE_INTEGER})

    assert query.attempt_no == 2**53 - 1
    assert SQLITE_MAX_INTEGER == 2**63 - 1
    assert JSON_SAFE_INTEGER < SQLITE_MAX_INTEGER


@pytest.mark.parametrize(
    "updates",
    [
        {"unknown": "field"},
        {"runtime_run_id": "legacy-alias"},
        {"authorization_selector": {"kind": "exact", "ordinal": 2}},
        {"authorization_selector": {"kind": "anything"}},
        {"searched_first_generation": 4, "searched_last_generation": 3},
        {"accepted_generation_hint": 4},
        {"accepted_generation_hint": 0},
    ],
)
def test_query_rejects_open_or_contradictory_shapes(updates: dict[str, object]) -> None:
    payload = {**_query_values(), **updates}

    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**payload)


@pytest.mark.parametrize("missing", ["contract_version", "source"])
def test_query_requires_wire_identity_literals(missing: str) -> None:
    payload = _query_values()
    del payload[missing]

    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**payload)


@pytest.mark.parametrize("ordinal", [True, 1.0, "1"])
def test_exact_selector_rejects_non_integer_literal_one(ordinal: object) -> None:
    payload = _query_values()
    payload["authorization_selector"] = {"kind": "exact", "ordinal": ordinal}

    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**payload)


def test_exact_selector_requires_explicit_ordinal() -> None:
    payload = _query_values()
    payload["authorization_selector"] = {"kind": "exact"}

    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**payload)


def test_all_selector_is_explicit_and_closed() -> None:
    payload = _query_values()
    payload["authorization_selector"] = {"kind": "all"}

    query = SourceHistoryQueryV1(**payload)

    assert query.authorization_selector.kind == "all"
    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**{**payload, "authorization_selector": {"kind": "all", "ordinal": 1}})
    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**{**payload, "authorization_selector": {"kind": "any"}})


def test_closed_results_enforce_complete_and_unavailable_coverage() -> None:
    not_found = SourceHistoryNotFound(
        **_result_values(),
        outcome="not_found",
        oldest_retained_generation=1,
        newest_known_generation=3,
        history_complete=True,
        history_truncated=False,
    )
    future = SourceHistoryUnavailable(
        **{**_result_values(), "searched_last_generation": 4, "accepted_generation_hint": None},
        outcome="history_unavailable",
        reason="unknown_generation",
        oldest_retained_generation=1,
        newest_known_generation=3,
    )
    unreadable = SourceHistoryUnavailable(
        **_result_values(),
        outcome="history_unavailable",
        reason="unreadable",
    )

    assert not_found.history_complete is True
    assert future.searched_last_generation > future.newest_known_generation
    assert unreadable.oldest_retained_generation is None
    assert unreadable.newest_known_generation is None
    with pytest.raises(ValidationError):
        SourceHistoryNotFound(
            **_result_values(),
            outcome="not_found",
            oldest_retained_generation=2,
            newest_known_generation=2,
            history_complete=True,
            history_truncated=False,
        )
    with pytest.raises(ValidationError):
        SourceHistoryUnavailable(
            **_result_values(),
            outcome="history_unavailable",
            reason="unknown_generation",
            newest_known_generation=3,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("history_complete", 1),
        ("history_complete", 1.0),
        ("history_truncated", 0),
        ("history_truncated", 0.0),
    ],
)
def test_complete_coverage_flags_reject_equal_non_boolean_literals(field: str, value: object) -> None:
    payload = {
        **_result_values(),
        "outcome": "not_found",
        "oldest_retained_generation": 1,
        "newest_known_generation": 3,
        "history_complete": True,
        "history_truncated": False,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        SourceHistoryNotFound(**payload)


@pytest.mark.parametrize(
    "missing",
    [
        "contract_version",
        "source",
        "outcome",
        "history_complete",
        "history_truncated",
    ],
)
def test_complete_result_requires_explicit_wire_proof_fields(missing: str) -> None:
    payload = {
        **_result_values(),
        "outcome": "not_found",
        "oldest_retained_generation": 1,
        "newest_known_generation": 3,
        "history_complete": True,
        "history_truncated": False,
    }
    del payload[missing]

    with pytest.raises(ValidationError):
        SourceHistoryNotFound(**payload)


def test_matched_fact_union_has_phase_exact_nullability() -> None:
    accepted = AcceptedNoDispatchFact(**_accepted_fact_values(), conclusion="accepted_no_dispatch")
    dispatched = DispatchNotObservedFact(
        **{**_accepted_fact_values(), "head_journal_revision": 11},
        conclusion="dispatch_not_observed",
        durable_dispatch_intent_ref="dispatch-ref",
        dispatch_intent_generation=2,
        dispatch_intent_journal_revision=11,
    )
    result = ObservedResultFact(
        **{**_accepted_fact_values(), "head_journal_revision": 12},
        conclusion="observed_result",
        durable_dispatch_intent_ref="dispatch-ref",
        dispatch_intent_generation=2,
        dispatch_intent_journal_revision=11,
        observation_generation=2,
        observation_journal_revision=12,
        result_ref="result-ref",
        result_hash=HASH_B,
    )
    failure = ObservedFailureFact(
        **{**_accepted_fact_values(), "head_journal_revision": 12},
        conclusion="observed_failure",
        durable_dispatch_intent_ref="dispatch-ref",
        dispatch_intent_generation=2,
        dispatch_intent_journal_revision=11,
        observation_generation=2,
        observation_journal_revision=12,
        failure_ref="failure-ref",
        failure_hash=HASH_B,
    )

    matched = SourceHistoryMatched(
        **_result_values(),
        outcome="matched",
        oldest_retained_generation=1,
        newest_known_generation=3,
        history_complete=True,
        history_truncated=False,
        facts=(accepted,),
    )
    assert matched.facts[0].conclusion == "accepted_no_dispatch"
    assert dispatched.conclusion == "dispatch_not_observed"
    assert result.result_ref == "result-ref"
    assert failure.failure_ref == "failure-ref"

    with pytest.raises(ValidationError):
        AcceptedNoDispatchFact(
            **_accepted_fact_values(),
            conclusion="accepted_no_dispatch",
            result_ref="forbidden",
        )
    with pytest.raises(ValidationError):
        ObservedResultFact(
            **{**_accepted_fact_values(), "head_journal_revision": 12},
            conclusion="observed_result",
            durable_dispatch_intent_ref="dispatch-ref",
            dispatch_intent_generation=2,
            dispatch_intent_journal_revision=11,
            observation_generation=2,
            observation_journal_revision=12,
            failure_ref="wrong-variant",
            failure_hash=HASH_B,
        )


@pytest.mark.parametrize("ordinal", [True, 1.0, "1"])
def test_fact_authorization_ordinal_rejects_non_integer_literal_one(ordinal: object) -> None:
    payload = {**_accepted_fact_values(), "dispatch_authorization_ordinal": ordinal}

    with pytest.raises(ValidationError):
        AcceptedNoDispatchFact(**payload, conclusion="accepted_no_dispatch")


@pytest.mark.parametrize("missing", ["source", "dispatch_authorization_ordinal", "conclusion"])
def test_fact_requires_explicit_wire_phase_fields(missing: str) -> None:
    payload = {**_accepted_fact_values(), "conclusion": "accepted_no_dispatch"}
    del payload[missing]

    with pytest.raises(ValidationError):
        AcceptedNoDispatchFact(**payload)


def test_phase_facts_reject_generation_regression_and_non_exact_heads() -> None:
    with pytest.raises(ValidationError):
        AcceptedNoDispatchFact(
            **{**_accepted_fact_values(), "head_journal_revision": 11},
            conclusion="accepted_no_dispatch",
        )
    with pytest.raises(ValidationError):
        DispatchNotObservedFact(
            **{
                **_accepted_fact_values(),
                "head_generation": 1,
                "head_journal_revision": 11,
            },
            conclusion="dispatch_not_observed",
            durable_dispatch_intent_ref="dispatch-ref",
            dispatch_intent_generation=1,
            dispatch_intent_journal_revision=11,
        )
    with pytest.raises(ValidationError):
        DispatchNotObservedFact(
            **{**_accepted_fact_values(), "head_journal_revision": 12},
            conclusion="dispatch_not_observed",
            durable_dispatch_intent_ref="dispatch-ref",
            dispatch_intent_generation=2,
            dispatch_intent_journal_revision=11,
        )
    with pytest.raises(ValidationError):
        ObservedResultFact(
            **{**_accepted_fact_values(), "head_journal_revision": 13},
            conclusion="observed_result",
            durable_dispatch_intent_ref="dispatch-ref",
            dispatch_intent_generation=2,
            dispatch_intent_journal_revision=11,
            observation_generation=2,
            observation_journal_revision=12,
            result_ref="result-ref",
            result_hash=HASH_B,
        )


def test_matched_fact_head_cannot_exceed_newest_known_generation() -> None:
    dispatched = DispatchNotObservedFact(
        **{
            **_accepted_fact_values(),
            "head_generation": 4,
            "head_journal_revision": 11,
        },
        conclusion="dispatch_not_observed",
        durable_dispatch_intent_ref="dispatch-ref",
        dispatch_intent_generation=4,
        dispatch_intent_journal_revision=11,
    )

    with pytest.raises(ValidationError):
        SourceHistoryMatched(
            **_result_values(),
            outcome="matched",
            oldest_retained_generation=1,
            newest_known_generation=3,
            history_complete=True,
            history_truncated=False,
            facts=(dispatched,),
        )


def test_result_union_and_conflict_reasons_are_closed() -> None:
    adapter = TypeAdapter(SourceHistoryQueryResultV1)
    conflict = SourceHistoryIdentityConflict(
        **_result_values(),
        outcome="identity_conflict",
        conflict_reasons=("request_hash_mismatch",),
        oldest_retained_generation=1,
        newest_known_generation=3,
    )

    assert adapter.validate_python(conflict.model_dump()).outcome == "identity_conflict"
    with pytest.raises(ValidationError):
        SourceHistoryIdentityConflict(
            **_result_values(),
            outcome="identity_conflict",
            conflict_reasons=(),
        )
    with pytest.raises(ValidationError):
        adapter.validate_python({**conflict.model_dump(), "outcome": "maybe"})


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "authenticated",
        "trusted",
        "verified",
        "authority_valid",
        "retryable",
        "safe_to_retry",
        "retry_posture",
        "product_outcome",
        "main_commit_ref",
        "payload",
        "metadata",
    ],
)
def test_contract_rejects_fake_authority_and_escape_hatches(forbidden_field: str) -> None:
    with pytest.raises(ValidationError):
        SourceHistoryQueryV1(**_query_values(), **{forbidden_field: True})


def test_source_port_contract_has_neutral_import_closure_and_no_business_caller() -> None:
    tree = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_modules <= {"__future__", "re", "unicodedata", "typing", "pydantic"}
    assert not any(isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(tree))

    production_callers = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        if path.is_relative_to(CONTRACT_PATH.parent):
            continue
        source = path.read_text(encoding="utf-8")
        if "seektalent.source_port" in source:
            production_callers.append(path.relative_to(PROJECT_ROOT).as_posix())
    # The production-unreachable #379 composition is the only main-side semantic consumer.
    assert production_callers == [
        "src/seektalent/sidecar_readiness.py",
        "src/seektalent/source_history_reconciliation.py",
        "src/seektalent/sidecar_child_session.py",
    ]

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import seektalent.source_port.history_contract; "
                "print('\\n'.join(sorted(name for name in sys.modules "
                "if name.startswith('seektalent.'))))"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = set(completed.stdout.splitlines())
    assert loaded <= {
        "seektalent.source_port",
        "seektalent.source_port.history_contract",
        "seektalent.version",
    }


def test_production_recovery_gate_remains_closed() -> None:
    runner = (PROJECT_ROOT / "src" / "seektalent_workbench_v2" / "runtime_runner.py").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "resume_recoverable=False" in runner
    assert "source_port" not in pyproject
