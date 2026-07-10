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
            self._require_claimed(claim)
            claim.browser_open_attempt_count += 1

    def has_browser_open_attempt(self, provider_candidate_key_hash: str) -> bool:
        with self._lock:
            return self._require_claim(provider_candidate_key_hash).browser_open_attempt_count > 0

    def mark_opened(self, provider_candidate_key_hash: str) -> None:
        with self._lock:
            claim = self._require_claim(provider_candidate_key_hash)
            self._require_claimed(claim)
            if claim.browser_open_attempt_count == 0:
                raise ValueError("detail_open_claim_opened_without_browser_attempt")
            claim.status = "opened"

    def mark_terminal_failed(self, provider_candidate_key_hash: str, *, safe_reason_code: str) -> None:
        with self._lock:
            claim = self._require_claim(provider_candidate_key_hash)
            self._require_claimed(claim)
            if claim.browser_open_attempt_count == 0:
                raise ValueError("detail_open_claim_failure_without_browser_attempt")
            claim.status = "terminal_failed"
            claim.last_safe_reason_code = safe_reason_code

    def release_unattempted(self, provider_candidate_key_hash: str) -> None:
        with self._lock:
            claim = self._require_claim(provider_candidate_key_hash)
            self._require_claimed(claim)
            if claim.browser_open_attempt_count != 0:
                raise ValueError("detail_open_claim_attempted_cannot_release")
            del self._claims[provider_candidate_key_hash]

    def snapshot(self) -> dict[str, RuntimeDetailOpenClaim]:
        with self._lock:
            return {
                provider_candidate_key_hash: claim.model_copy(deep=True)
                for provider_candidate_key_hash, claim in self._claims.items()
            }

    @staticmethod
    def _require_claimed(claim: RuntimeDetailOpenClaim) -> None:
        if claim.status != "claimed":
            raise ValueError("detail_open_claim_not_claimed")

    def _require_claim(self, provider_candidate_key_hash: str) -> RuntimeDetailOpenClaim:
        try:
            return self._claims[provider_candidate_key_hash]
        except KeyError as exc:
            raise ValueError("detail_open_claim_missing") from exc
