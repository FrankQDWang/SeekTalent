from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OpenCliBrowserConfig:
    command: tuple[str, ...]
    session: str
    timeout_seconds: int
    current_tab_reuse_url_fragments: tuple[str, ...] = ()
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
