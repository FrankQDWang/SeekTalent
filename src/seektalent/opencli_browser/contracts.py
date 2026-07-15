from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol


OpenCliWindowMode = Literal["foreground", "background"]
OpenCliTabKind = Literal["search", "detail"]
OpenCliTabCloseOutcome = Literal["closed", "already_missing", "failed"]


@dataclass(frozen=True)
class BrowserControlScope:
    scope_id: str
    control_key: str
    fence_token: int


@dataclass(frozen=True)
class BrowserHostTab:
    page_id: str
    url: str
    window_id: int
    active: bool
    window_focused: bool


@dataclass(frozen=True)
class OpenCliOwnedTab:
    tab_token: str
    session: str
    page_id: str
    tab_kind: OpenCliTabKind
    idle_deadline_at: int | None = None


@dataclass(frozen=True)
class OpenCliTabCloseResult:
    tab_token: str
    outcome: OpenCliTabCloseOutcome
    verified: bool
    error_code: str | None = None


@dataclass(frozen=True)
class OpenCliBrowserConfig:
    command: tuple[str, ...]
    session: str
    timeout_seconds: int
    window_mode: OpenCliWindowMode = "background"
    pacing_enabled: bool = True
    pacing_min_ms: int = 700
    pacing_max_ms: int = 1800


@dataclass(frozen=True)
class OpenCliBrowserResult:
    ok: bool
    action: str
    safe_reason_code: str = "configured"
    counts: Mapping[str, int] = field(default_factory=dict)
    observation: Mapping[str, object] = field(default_factory=dict)
    private_output: str = ""

    def to_public_payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "action": self.action,
            "safeReasonCode": self.safe_reason_code,
            "counts": dict(self.counts),
        }

    def to_tool_payload(self) -> dict[str, object]:
        payload = self.to_public_payload()
        if self.observation:
            payload["observation"] = dict(self.observation)
        return payload


class OpenCliBrowserError(RuntimeError):
    def __init__(self, safe_reason_code: str) -> None:
        super().__init__(safe_reason_code)
        self.safe_reason_code = safe_reason_code


@dataclass(frozen=True)
class OpenCliBrowserTiming:
    command: str
    session: str | None
    argv_len: int
    duration_ms: float
    ok: bool
    safe_reason_code: str | None = None


class OpenCliBrowserTimingRecorder(Protocol):
    def record(self, timing: OpenCliBrowserTiming) -> None: ...


class OpenCliBrowserLifecycle(Protocol):
    def record_scope(self, scope: BrowserControlScope) -> None: ...

    def record_tab_allocation(
        self,
        scope: BrowserControlScope,
        *,
        tab_token: str,
        session: str,
        tab_kind: OpenCliTabKind,
    ) -> None: ...

    def record_owned_tab(self, scope: BrowserControlScope, tab: OpenCliOwnedTab) -> None: ...

    def record_idle_deadline(self, tab: OpenCliOwnedTab) -> None: ...

    def request_reclaim(self, scope: BrowserControlScope, tabs: tuple[OpenCliOwnedTab, ...]) -> None: ...
