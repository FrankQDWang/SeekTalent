from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserConfig, OpenCliBrowserError


def test_liepin_opencli_browser_facade_is_removed() -> None:
    assert not Path("src/seektalent/providers/liepin/opencli_browser.py").exists()


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_opencli_automation_module_does_not_own_liepin_page_semantics() -> None:
    text = _text("src/seektalent/opencli_browser/automation.py")

    forbidden = (
        "LIEPIN_FILTER_SECTION_LABELS",
        "compile_liepin_native_filters",
        "build_liepin_opencli_detail_payload",
        "looks_like_liepin_card",
        "search_liepin_resumes",
        "open_liepin_detail",
        "capture_liepin_detail_resume",
        "查看完整简历",
    )
    assert all(item not in text for item in forbidden)


def test_liepin_site_adapter_does_not_own_opencli_subprocess_boundary() -> None:
    text = _text("src/seektalent/providers/liepin/liepin_site_adapter.py")

    forbidden = (
        "subprocess.run",
        "SubprocessOpenCliCommandRunner",
        "SubprocessChromeWindowCounter",
        "SubprocessBlankChromeWindowCloser",
        "SubprocessCurrentChromeTabOpener",
    )
    assert all(item not in text for item in forbidden)


def test_liepin_site_adapter_does_not_own_opencli_argv_shape() -> None:
    text = _text("src/seektalent/providers/liepin/liepin_site_adapter.py")

    forbidden = (
        "tuple(self._config.command)",
        '"browser",\n                    self._config.session',
        '"browser", self._config.session',
        ".run_raw(",
    )
    assert all(item not in text for item in forbidden)


def test_opencli_runtime_does_not_own_liepin_click_labels() -> None:
    text = _text("src/seektalent/opencli_browser/runtime.py")

    forbidden = (
        "ALLOWED_CLICK_TARGET_FRAGMENTS",
        "h.liepin.com",
        "www.liepin.com",
        "Liepin",
        "liepin_",
        "搜索",
        "搜 索",
        "查询",
        "下一页",
    )
    assert all(item not in text for item in forbidden)


@dataclass
class RecordingOpenCliCommands:
    outputs: dict[tuple[str, ...], str | BaseException] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        del timeout
        del env
        key = tuple(argv)
        self.calls.append(key)
        output = self.outputs.get(key, "")
        if isinstance(output, BaseException):
            raise output
        return output


def _config() -> OpenCliBrowserConfig:
    return OpenCliBrowserConfig(
        command=("opencli",),
        session="seektalent-liepin",
        timeout_seconds=10,
        pacing_enabled=False,
    )


def test_opencli_browser_automation_runs_generic_get_url_without_liepin_semantics() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(
        {
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            )
        }
    )
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    result = automation.get_url()

    assert result.ok is True
    assert result.private_output == "https://h.liepin.com/search/getConditionItem#session"
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "get", "url")]


def test_opencli_browser_automation_click_ref_owns_opencli_argv_shape() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands()
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    output = automation.click_ref("44")

    assert output == ""
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "click", "44")]


def test_opencli_browser_automation_find_css_owns_opencli_argv_shape() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(
        {
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "find",
                "--css",
                "#resultList .detail-resume-card-wrap",
                "--limit",
                "10",
                "--text-max",
                "1200",
            ): '{"entries":[]}'
        }
    )
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    output = automation.find_css("#resultList .detail-resume-card-wrap", limit=10, text_max=1200)

    assert output == '{"entries":[]}'
    assert commands.calls == [
        (
            "opencli",
            "browser",
            "seektalent-liepin",
            "find",
            "--css",
            "#resultList .detail-resume-card-wrap",
            "--limit",
            "10",
            "--text-max",
            "1200",
        )
    ]


def test_opencli_browser_automation_readonly_eval_owns_opencli_argv_shape() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(
        {
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "eval",
                "(() => null)()",
            ): "null"
        }
    )
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    output = automation.readonly_eval("(() => null)()")

    assert output == "null"
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "eval", "(() => null)()")]


def test_opencli_browser_automation_maps_missing_command() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(outputs={("opencli", "daemon", "status"): FileNotFoundError("opencli")})
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    result = automation.status()

    assert result.ok is False
    assert result.safe_reason_code == "opencli_command_missing"


def test_opencli_browser_automation_maps_subprocess_timeout() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): subprocess.TimeoutExpired(
                cmd=["opencli"],
                timeout=10,
            )
        }
    )
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation.get_url()

    assert raised.value.safe_reason_code == "opencli_timeout"


def test_opencli_browser_automation_maps_structured_opencli_error() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(
                1,
                ["opencli"],
                output='{"error":{"code":"stale_ref","message":"target disappeared"}}',
                stderr="",
            )
        }
    )
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation.run_browser_command("click", ("44",))

    assert raised.value.safe_reason_code == "opencli_stale_ref"


def test_opencli_browser_automation_maps_structured_opencli_error_with_trailing_diagnostic() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(
                1,
                ["opencli"],
                output='{"error":{"code":"stale_ref","message":"target disappeared"}} trailing diagnostic',
                stderr="",
            )
        }
    )
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation.run_browser_command("click", ("44",))

    assert raised.value.safe_reason_code == "opencli_stale_ref"


def test_opencli_browser_automation_rejects_invalid_command_shape_before_run() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands()
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation.run_browser_command("tab", ("select", "../../unsafe"))

    assert raised.value.safe_reason_code == "opencli_forbidden_command"
    assert commands.calls == []


def test_opencli_browser_automation_preserves_original_page_id_shape() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands()
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation.run_browser_command("tab", ("select", "abc:def"))

    assert raised.value.safe_reason_code == "opencli_forbidden_command"
    assert commands.calls == []


def test_liepin_opencli_policy_mapping_is_complete() -> None:
    from seektalent.providers.liepin.liepin_opencli_policy import OPENCLI_TO_LIEPIN_REASON

    assert OPENCLI_TO_LIEPIN_REASON == {
        "opencli_command_missing": "liepin_opencli_command_missing",
        "opencli_timeout": "liepin_opencli_timeout",
        "opencli_extension_disconnected": "liepin_opencli_extension_disconnected",
        "opencli_status_unavailable": "liepin_opencli_status_unavailable",
        "opencli_daemon_not_running": "liepin_opencli_daemon_not_running",
        "opencli_daemon_stale": "liepin_opencli_daemon_stale",
        "opencli_forbidden_command": "liepin_opencli_forbidden_command",
        "opencli_window_policy_blocked": "liepin_opencli_window_policy_blocked",
        "opencli_stale_ref": "liepin_opencli_stale_ref",
        "opencli_selector_not_found": "liepin_opencli_selector_not_found",
        "opencli_selector_ambiguous": "liepin_opencli_selector_ambiguous",
        "opencli_target_not_found": "liepin_opencli_target_not_found",
    }


def test_liepin_opencli_policy_maps_generic_opencli_reasons_to_public_liepin_reasons() -> None:
    from seektalent.opencli_browser.contracts import OpenCliBrowserResult
    from seektalent.providers.liepin.liepin_opencli_policy import (
        liepin_reason_from_opencli_reason,
        liepin_result_from_opencli_result,
    )

    assert liepin_reason_from_opencli_reason("opencli_stale_ref") == "liepin_opencli_stale_ref"
    assert liepin_reason_from_opencli_reason("opencli_new_future_reason") == "liepin_opencli_status_unavailable"
    assert liepin_reason_from_opencli_reason("liepin_opencli_filter_unapplied") == "liepin_opencli_filter_unapplied"

    mapped = liepin_result_from_opencli_result(
        OpenCliBrowserResult(ok=False, action="status", safe_reason_code="opencli_command_missing")
    )

    assert mapped.safe_reason_code == "liepin_opencli_command_missing"


def test_liepin_site_adapter_maps_generic_opencli_errors_at_public_boundary(tmp_path: Path) -> None:
    from seektalent.opencli_browser.contracts import (
        OpenCliBrowserConfig,
        OpenCliBrowserError,
        OpenCliBrowserResult,
    )
    from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
    from seektalent.providers.liepin.liepin_site_adapter import LiepinOpenCliSiteConfig, LiepinSiteAdapter

    class FailingAutomation:
        commands = object()
        window_counter = object()
        blank_window_closer = object()
        current_tab_opener = object()

        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=False, action="status", safe_reason_code="opencli_timeout")

        def run_browser_command(self, command: str, args: tuple[str, ...]) -> str:
            del command, args
            raise OpenCliBrowserError("opencli_timeout")

        def click_ref(self, ref: str) -> str:
            del ref
            raise OpenCliBrowserError("opencli_stale_ref")

    browser_config = OpenCliBrowserConfig(
        command=("opencli",),
        session="seektalent-liepin",
        timeout_seconds=10,
        pacing_enabled=False,
    )
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=("www.liepin.com", "h.liepin.com"),
        allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        lease_dir=tmp_path,
    )
    adapter = LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=site_config,
        automation=FailingAutomation(),  # type: ignore[arg-type]
    )

    assert adapter.status().safe_reason_code == "liepin_opencli_timeout"
    with pytest.raises(OpenCliBrowserError) as raised:
        adapter.get_url()
    assert raised.value.safe_reason_code == "liepin_opencli_timeout"

    with pytest.raises(OpenCliBrowserError) as raised:
        adapter._run_opencli_call(lambda: adapter._automation.click_ref("44"))
    assert raised.value.safe_reason_code == "liepin_opencli_stale_ref"


def test_liepin_site_adapter_reobserves_and_retries_stale_ref_once(tmp_path: Path) -> None:
    from seektalent.opencli_browser.contracts import OpenCliBrowserConfig, OpenCliBrowserError, OpenCliBrowserResult
    from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
    from seektalent.providers.liepin.liepin_site_adapter import LiepinOpenCliSiteConfig, LiepinSiteAdapter

    class Automation:
        commands = object()
        window_counter = object()
        blank_window_closer = object()
        current_tab_opener = object()

        def __init__(self) -> None:
            self.click_calls = 0
            self.state_calls = 0

        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="status")

        def run_browser_command(self, command: str, args: tuple[str, ...]) -> str:
            if command == "state":
                self.state_calls += 1
                return "猎聘 搜索结果 [ref=44] 查看详情"
            if command == "get" and args == ("url",):
                return LIEPIN_RECRUITER_SEARCH_URL
            return ""

        def find_css(self, selector: str, *, limit: int, text_max: int) -> str:
            del selector, limit, text_max
            return '{"entries":[]}'

        def click_ref(self, ref: str) -> str:
            assert ref == "44"
            self.click_calls += 1
            if self.click_calls == 1:
                raise OpenCliBrowserError("opencli_stale_ref")
            return "clicked"

    automation = Automation()
    adapter = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-liepin",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("www.liepin.com", "h.liepin.com"),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
            lease_dir=tmp_path,
        ),
        automation=automation,  # type: ignore[arg-type]
    )

    adapter._click_liepin_detail_ref("44")

    assert automation.click_calls == 2
    assert automation.state_calls == 1


def test_liepin_site_adapter_propagates_persistent_stale_ref_after_single_retry(tmp_path: Path) -> None:
    from seektalent.opencli_browser.contracts import OpenCliBrowserConfig, OpenCliBrowserError, OpenCliBrowserResult
    from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
    from seektalent.providers.liepin.liepin_site_adapter import LiepinOpenCliSiteConfig, LiepinSiteAdapter

    class Automation:
        commands = object()
        window_counter = object()
        blank_window_closer = object()
        current_tab_opener = object()

        def __init__(self) -> None:
            self.click_calls = 0
            self.state_calls = 0

        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="status")

        def run_browser_command(self, command: str, args: tuple[str, ...]) -> str:
            if command == "state":
                self.state_calls += 1
                return "猎聘 搜索结果 [ref=44] 查看详情"
            if command == "get" and args == ("url",):
                return LIEPIN_RECRUITER_SEARCH_URL
            return ""

        def find_css(self, selector: str, *, limit: int, text_max: int) -> str:
            del selector, limit, text_max
            return '{"entries":[]}'

        def click_ref(self, ref: str) -> str:
            assert ref == "44"
            self.click_calls += 1
            raise OpenCliBrowserError("opencli_stale_ref")

    automation = Automation()
    adapter = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-liepin",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("www.liepin.com", "h.liepin.com"),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
            lease_dir=tmp_path,
        ),
        automation=automation,  # type: ignore[arg-type]
    )

    with pytest.raises(OpenCliBrowserError) as raised:
        adapter._click_liepin_detail_ref("44")

    assert raised.value.safe_reason_code == "liepin_opencli_stale_ref"
    assert automation.click_calls == 2
    assert automation.state_calls == 1


def test_liepin_site_adapter_launches_idle_cleanup_worker_at_provider_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent.opencli_browser.contracts import OpenCliBrowserConfig, OpenCliBrowserResult
    from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
    from seektalent.providers.liepin.liepin_site_adapter import LiepinOpenCliSiteConfig, LiepinSiteAdapter

    class Automation:
        commands = object()
        window_counter = object()
        blank_window_closer = object()
        current_tab_opener = object()

        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="status")

    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_popen(argv, **kwargs):
        calls.append((tuple(argv), dict(kwargs)))
        return object()

    monkeypatch.setattr("seektalent.providers.liepin.liepin_site_adapter.subprocess.Popen", fake_popen)

    browser_config = OpenCliBrowserConfig(
        command=("opencli",),
        session="seektalent-liepin",
        timeout_seconds=10,
    )
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=("www.liepin.com", "h.liepin.com"),
        allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        lease_dir=tmp_path,
        idle_close_seconds=3,
        close_blank_window=False,
        cleanup_worker_enabled=True,
    )
    adapter = LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=site_config,
        automation=Automation(),  # type: ignore[arg-type]
    )

    adapter._launch_idle_cleanup_worker()

    argv, kwargs = calls[0]
    assert argv[-2:] == ("seektalent.providers.liepin.opencli_browser_cli", "watch_idle_lease")
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR"] == str(tmp_path)
    assert env["SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS"] == "3"
    assert env["SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW"] == "false"


def test_liepin_site_adapter_exposes_stable_search_surface() -> None:
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter

    expected = {
        "status",
        "open_liepin_tab",
        "state",
        "get_url",
        "find",
        "fill",
        "click",
        "scroll",
        "wait_time",
        "apply_liepin_native_filters",
        "extract_visible_liepin_cards",
        "open_liepin_detail",
        "capture_liepin_detail_resume",
        "search_liepin_cards",
        "search_liepin_resumes",
        "finalize_liepin_resumes",
    }

    assert expected <= set(dir(LiepinSiteAdapter))
