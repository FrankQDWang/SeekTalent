from __future__ import annotations


class RuntimeControlError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str | None = None,
        *,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.payload = payload or {}
        super().__init__(message or reason_code)
