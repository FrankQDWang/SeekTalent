from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from seektalent.config import DEFAULT_LIEPIN_OPENCLI_COMMAND
from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
    OpenCliWindowMode,
)
from seektalent.providers.liepin.liepin_opencli_policy import (
    LIEPIN_OPENCLI_ALLOWED_HOSTS,
    LIEPIN_RECRUITER_SEARCH_URLS,
)
from seektalent.providers.liepin.liepin_site_adapter import (
    LiepinOpenCliSiteConfig,
    LiepinOpenCliTimingRecorder,
    LiepinSiteAdapter,
)


_REMOVED_CLEANUP_ENV_KEYS = (
    "SEEKTALENT_LIEPIN_OPENCLI_IDLE_" + "CLOSE_SECONDS",
    "SEEKTALENT_LIEPIN_OPENCLI_CLOSE_" + "BLANK_WINDOW",
)


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        _print(OpenCliBrowserResult(ok=False, action=action or "unknown", safe_reason_code="liepin_opencli_helper_invalid_input"))
        return 1
    if not isinstance(payload, dict):
        _print(OpenCliBrowserResult(ok=False, action=action or "unknown", safe_reason_code="liepin_opencli_helper_invalid_input"))
        return 1
    try:
        runner = _runner_from_env()
        result = _run_action(runner, action, payload)
    except OpenCliBrowserError as exc:
        result = OpenCliBrowserResult(ok=False, action=action or "unknown", safe_reason_code=exc.safe_reason_code)
    if isinstance(result, OpenCliBrowserResult):
        _print(result)
        return 0 if result.ok else 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _runner_from_env() -> LiepinSiteAdapter:
    _reject_removed_cleanup_env()
    command = tuple(shlex.split(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_COMMAND") or DEFAULT_LIEPIN_OPENCLI_COMMAND))
    window_mode = _env_window_mode(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_WINDOW_MODE"))
    allowed_hosts = _json_tuple(
        os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_HOSTS_JSON"),
        default=LIEPIN_OPENCLI_ALLOWED_HOSTS,
    )
    allowed_start_urls = _json_tuple(
        os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_START_URLS_JSON"),
        default=LIEPIN_RECRUITER_SEARCH_URLS,
    )
    browser_config = OpenCliBrowserConfig(
        command=command,
        session=os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_SESSION") or "seektalent-liepin",
        timeout_seconds=int(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS") or "900"),
        window_mode=window_mode,
        pacing_enabled=_env_bool(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_PACING_ENABLED"), default=True),
        pacing_min_ms=int(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_PACING_MIN_MS") or "700"),
        pacing_max_ms=int(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_PACING_MAX_MS") or "1800"),
    )
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=allowed_hosts,
        allowed_start_urls=allowed_start_urls,
        detail_open_timeout_seconds=int(
            os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS") or "90"
        ),
        search_navigation_timeout_seconds=float(
            os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_SEARCH_NAVIGATION_TIMEOUT_SECONDS") or "10"
        ),
        allowed_click_refs=_json_tuple(
            os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_CLICK_REFS_JSON"),
            default=(),
        ),
        lease_dir=_optional_path(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR")),
        artifact_root=_optional_path(os.environ.get("SEEKTALENT_PI_ARTIFACT_ROOT")),
    )
    return LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=site_config,
        automation=OpenCliBrowserAutomation(
            config=browser_config,
            timing_recorder=LiepinOpenCliTimingRecorder(
                artifact_root=site_config.artifact_root,
                writes_local_debug_artifacts=(
                    os.environ.get("SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE") or "prod"
                )
                != "prod",
            ),
        ),
    )


def _reject_removed_cleanup_env() -> None:
    if any(key in os.environ for key in _REMOVED_CLEANUP_ENV_KEYS):
        raise OpenCliBrowserError("liepin_opencli_removed_config")


def _run_action(runner: LiepinSiteAdapter, action: str, payload: dict[str, object]) -> OpenCliBrowserResult | dict[str, object]:
    if action == "status":
        return runner.status()
    if action == "recover_connection":
        return runner.recover_connection()
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
    if action == "apply_liepin_filters":
        native_filters = payload.get("nativeFilters") or payload.get("native_filters")
        return runner.apply_liepin_native_filters(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            native_filters=cast(Mapping[str, object], native_filters) if isinstance(native_filters, dict) else {},
        )
    if action == "extract_structured_liepin_cards":
        return runner.extract_structured_liepin_cards(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
        )
    if action == "extract_visible_liepin_cards":
        return runner.extract_visible_liepin_cards(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
        )
    if action == "open_liepin_detail":
        return runner.open_liepin_detail(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            ref=str(payload.get("ref") or ""),
            rank=_payload_int(payload, "rank", default=1),
        )
    if action == "capture_liepin_detail_resume":
        return runner.capture_liepin_detail_resume(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            rank=_payload_int(payload, "rank", default=1),
        )
    if action == "finalize_liepin_resumes":
        return runner.finalize_liepin_resumes(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            query=str(payload.get("query") or ""),
            max_pages=_payload_int(payload, "maxPages", "max_pages", default=1),
            max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
            cards_seen=_optional_payload_int(payload, "cardsSeen", "cards_seen"),
            target_resumes=_optional_payload_int(payload, "targetResumes", "target_resumes"),
        )
    if action == "search_cards":
        native_filters = payload.get("nativeFilters") or payload.get("native_filters")
        return runner.search_liepin_cards(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            query=str(payload.get("query") or ""),
            max_pages=_payload_int(payload, "maxPages", "max_pages", default=1),
            max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
            native_filters=cast(Mapping[str, object], native_filters) if isinstance(native_filters, dict) else None,
        )
    if action == "search_resumes":
        native_filters = payload.get("nativeFilters") or payload.get("native_filters")
        return runner.search_liepin_resumes(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            query=str(payload.get("query") or ""),
            target_resumes=_payload_int(payload, "targetResumes", "target_resumes", default=2),
            max_pages=_payload_int(payload, "maxPages", "max_pages", default=1),
            max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
            native_filters=cast(Mapping[str, object], native_filters) if isinstance(native_filters, dict) else None,
        )
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


def _optional_payload_int(payload: Mapping[str, object], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(cast(Any, value))
        except (TypeError, ValueError):
            return None
    return None


def _json_tuple(value: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise OpenCliBrowserError("liepin_opencli_config_invalid") from exc
    if not isinstance(loaded, list) or not all(isinstance(item, str) and item for item in loaded):
        raise OpenCliBrowserError("liepin_opencli_config_invalid")
    return tuple(loaded)


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_window_mode(value: str | None) -> OpenCliWindowMode:
    if value is None or value == "":
        return "background"
    normalized = value.strip().lower()
    if normalized == "foreground":
        return "foreground"
    if normalized == "background":
        return "background"
    raise SystemExit("SEEKTALENT_LIEPIN_OPENCLI_WINDOW_MODE must be foreground or background")


def _print(result: OpenCliBrowserResult) -> None:
    print(json.dumps(result.to_tool_payload(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
