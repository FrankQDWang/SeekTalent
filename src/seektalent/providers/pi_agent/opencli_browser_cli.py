from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path

from seektalent.providers.pi_agent.opencli_browser import (
    LIEPIN_RECRUITER_SEARCH_URL,
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
    OpenCliBrowserRunner,
    default_liepin_opencli_policy,
)


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        _print(OpenCliBrowserResult(ok=False, action=action or "unknown", safe_reason_code="liepin_opencli_malformed_state"))
        return 1
    if not isinstance(payload, dict):
        _print(OpenCliBrowserResult(ok=False, action=action or "unknown", safe_reason_code="liepin_opencli_malformed_state"))
        return 1
    runner = _runner_from_env()
    try:
        result = _run_action(runner, action, payload)
    except OpenCliBrowserError as exc:
        result = OpenCliBrowserResult(ok=False, action=action or "unknown", safe_reason_code=exc.safe_reason_code)
    if isinstance(result, OpenCliBrowserResult):
        _print(result)
        return 0 if result.ok else 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _runner_from_env() -> OpenCliBrowserRunner:
    command = tuple(
        shlex.split(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_COMMAND") or "apps/web-svelte/node_modules/.bin/opencli")
    )
    allowed_hosts = _json_tuple(
        os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_HOSTS_JSON"),
        default=("www.liepin.com", "h.liepin.com"),
    )
    allowed_start_urls = _json_tuple(
        os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_START_URLS_JSON"),
        default=(LIEPIN_RECRUITER_SEARCH_URL,),
    )
    return OpenCliBrowserRunner(
        config=OpenCliBrowserConfig(
            command=command,
            session=os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_SESSION") or "seektalent-liepin",
            timeout_seconds=int(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS") or "20"),
            policy=default_liepin_opencli_policy(
                allowed_hosts=allowed_hosts,
                allowed_start_urls=allowed_start_urls,
            ),
            allowed_click_refs=_json_tuple(
                os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_CLICK_REFS_JSON"),
                default=(),
            ),
            lease_dir=_optional_path(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR")),
            artifact_root=_optional_path(os.environ.get("SEEKTALENT_PI_ARTIFACT_ROOT")),
            idle_close_seconds=int(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS") or "120"),
            close_blank_window=_env_bool(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW"), default=True),
        )
    )


def _run_action(runner: OpenCliBrowserRunner, action: str, payload: dict[str, object]) -> OpenCliBrowserResult | dict[str, object]:
    if action == "status":
        return runner.status()
    if action == "open_liepin_tab":
        return runner.open_liepin_tab(str(payload.get("url") or ""))
    if action == "state":
        return runner.state()
    if action == "get_url":
        return runner.get_url()
    if action == "find":
        return runner.find(query=str(payload.get("query") or ""))
    if action == "fill":
        return runner.fill(target=str(payload.get("target") or ""), text=str(payload.get("text") or ""))
    if action == "click":
        return runner.click(target=str(payload.get("target") or ""))
    if action == "scroll":
        return runner.scroll(direction=str(payload.get("direction") or ""))
    if action == "wait_time":
        return runner.wait_time(seconds=_payload_int(payload, "seconds", default=1))
    if action == "search_cards":
        return runner.search_liepin_cards(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            query=str(payload.get("query") or ""),
            max_pages=_payload_int(payload, "maxPages", "max_pages", default=1),
            max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
        )
    if action == "cleanup_idle_lease":
        return runner.cleanup_idle_lease(force=bool(payload.get("force") or False))
    if action == "cleanup_orphaned_tabs":
        return runner.cleanup_orphaned_tabs(force=bool(payload.get("force") or False))
    if action == "watch_idle_lease":
        return runner.watch_idle_lease()
    raise OpenCliBrowserError("liepin_opencli_forbidden_command")


def _payload_int(payload: Mapping[str, object], *keys: str, default: int) -> int:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip():
            return int(value)
    return default


def _json_tuple(value: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    loaded = json.loads(value)
    if not isinstance(loaded, list) or not all(isinstance(item, str) and item for item in loaded):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    return tuple(loaded)


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _print(result: OpenCliBrowserResult) -> None:
    print(json.dumps(result.to_pi_tool_payload(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
