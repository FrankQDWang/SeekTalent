from __future__ import annotations


class SourceWorkerError(RuntimeError):
    """Source-neutral worker failure exposed across integration boundaries."""

    def __init__(
        self,
        message: str,
        *,
        setup_status: str | None = None,
        code: str | None = None,
        partial_search_result: object | None = None,
        cards_collected: int = 0,
    ) -> None:
        super().__init__(message)
        self.setup_status = setup_status
        self.code = code or setup_status
        self.partial_search_result = partial_search_result
        self.cards_collected = cards_collected


__all__ = ["SourceWorkerError"]
