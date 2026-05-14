from __future__ import annotations

from threading import Lock


class InMemoryPiConnectionLock:
    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = Lock()

    def acquire(
        self,
        *,
        connection_id: str,
        provider_account_lock_key: str,
        source_run_id: str,
    ) -> bool:
        lock_keys = _lock_keys(connection_id, provider_account_lock_key)
        with self._lock:
            if any(key in self._owners for key in lock_keys):
                return False
            for key in lock_keys:
                self._owners[key] = source_run_id
            return True

    def release(
        self,
        *,
        connection_id: str,
        provider_account_lock_key: str,
        source_run_id: str,
    ) -> None:
        lock_keys = _lock_keys(connection_id, provider_account_lock_key)
        with self._lock:
            for key in lock_keys:
                if self._owners.get(key) == source_run_id:
                    del self._owners[key]


def _lock_keys(connection_id: str, provider_account_lock_key: str) -> tuple[str, str]:
    if not connection_id or not provider_account_lock_key:
        raise ValueError("PI connection lock requires connection_id and provider_account_lock_key")
    return (f"connection:{connection_id}", f"provider_account:{provider_account_lock_key}")
