from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from seektalent.models import RuntimeDetailOpenClaim
from seektalent.providers.liepin.detail_open_claims import DetailOpenClaimLedger


def test_concurrent_claims_allow_exactly_one_winner() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: ledger.try_claim("candidate-key"), range(2)))

    assert outcomes.count(True) == 1
    assert claims["candidate-key"].status == "claimed"
    assert claims["candidate-key"].browser_open_attempt_count == 0


def test_unattempted_claim_is_released_but_attempted_failure_is_terminal() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)

    assert ledger.try_claim("candidate-key") is True
    ledger.release_unattempted("candidate-key")
    assert ledger.try_claim("candidate-key") is True

    ledger.record_browser_open_attempt("candidate-key")
    ledger.mark_terminal_failed(
        "candidate-key",
        safe_reason_code="liepin_opencli_detail_not_opened",
    )

    assert ledger.try_claim("candidate-key") is False
    assert claims["candidate-key"].status == "terminal_failed"
    assert claims["candidate-key"].browser_open_attempt_count == 1
    assert claims["candidate-key"].last_safe_reason_code == "liepin_opencli_detail_not_opened"


def test_opened_claim_cannot_be_claimed_again() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)

    assert ledger.try_claim("candidate-key") is True
    ledger.record_browser_open_attempt("candidate-key")
    ledger.mark_opened("candidate-key")

    assert ledger.try_claim("candidate-key") is False
    assert claims["candidate-key"].status == "opened"


def test_lifecycle_guards_reject_invalid_state_transitions() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)

    with pytest.raises(ValueError, match="detail_open_claim_missing"):
        ledger.record_browser_open_attempt("candidate-key")

    assert ledger.try_claim("candidate-key") is True

    with pytest.raises(ValueError, match="detail_open_claim_opened_without_browser_attempt"):
        ledger.mark_opened("candidate-key")
    with pytest.raises(ValueError, match="detail_open_claim_failure_without_browser_attempt"):
        ledger.mark_terminal_failed("candidate-key", safe_reason_code="safe_reason")

    ledger.record_browser_open_attempt("candidate-key")
    with pytest.raises(ValueError, match="detail_open_claim_attempted_cannot_release"):
        ledger.release_unattempted("candidate-key")


def test_snapshot_is_independent_from_claim_map() -> None:
    claims: dict[str, RuntimeDetailOpenClaim] = {}
    ledger = DetailOpenClaimLedger(claims)
    assert ledger.try_claim("candidate-key") is True

    snapshot = ledger.snapshot()
    snapshot["candidate-key"].status = "opened"
    snapshot["another-key"] = RuntimeDetailOpenClaim(status="claimed")

    assert claims["candidate-key"].status == "claimed"
    assert "another-key" not in claims
