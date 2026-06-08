from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


LIEPIN_RECRUITER_SEARCH_URL = "https://h.liepin.com/search/getConditionItem#session"


@dataclass(frozen=True)
class OpenCliBrowserPolicy:
    source_kind: str
    allowed_hosts: tuple[str, ...]
    allowed_start_urls: tuple[str, ...]
    max_keyword_chars: int = 80


@dataclass(frozen=True)
class OpenCliBrowserConfig:
    command: tuple[str, ...]
    session: str
    timeout_seconds: int
    policy: OpenCliBrowserPolicy
    allowed_click_refs: tuple[str, ...] = ()
    lease_dir: Path | None = None
    artifact_root: Path | None = None
    detail_open_timeout_seconds: int = 90
    idle_close_seconds: int = 120
    close_blank_window: bool = False
    cleanup_worker_enabled: bool = True
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

    def to_pi_tool_payload(self) -> dict[str, object]:
        payload = self.to_public_payload()
        if self.observation:
            payload["observation"] = dict(self.observation)
        return payload


class OpenCliBrowserError(RuntimeError):
    def __init__(self, safe_reason_code: str) -> None:
        super().__init__(safe_reason_code)
        self.safe_reason_code = safe_reason_code


def default_liepin_opencli_policy(
    *,
    allowed_hosts: tuple[str, ...],
    allowed_start_urls: tuple[str, ...],
) -> OpenCliBrowserPolicy:
    return OpenCliBrowserPolicy(
        source_kind="liepin",
        allowed_hosts=allowed_hosts,
        allowed_start_urls=allowed_start_urls,
    )
