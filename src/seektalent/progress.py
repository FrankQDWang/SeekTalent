from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ProgressEvent:
    type: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    round_no: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]
