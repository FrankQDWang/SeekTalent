from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class ProgressEvent:
    type: str
    message: str
    timestamp: str
    round_index: int | None = None
    payload: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


ProgressCallback = Callable[[ProgressEvent], None]


def make_progress_event(
    event_type: str,
    message: str,
    *,
    round_index: int | None = None,
    payload: dict[str, object] | None = None,
) -> ProgressEvent:
    return ProgressEvent(
        type=event_type,
        message=message,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        round_index=round_index,
        payload=payload,
    )


def emit_progress(
    callback: ProgressCallback | None,
    event_type: str,
    message: str,
    *,
    round_index: int | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    if callback is None:
        return
    callback(
        make_progress_event(
            event_type,
            message,
            round_index=round_index,
            payload=payload,
        )
    )


__all__ = ["ProgressCallback", "ProgressEvent", "emit_progress", "make_progress_event"]
