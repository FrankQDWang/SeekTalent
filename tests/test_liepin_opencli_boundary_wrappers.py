from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from seektalent.providers.liepin.opencli_browser_contracts import (
    LIEPIN_RECRUITER_SEARCH_URL,
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    default_liepin_opencli_policy,
)


def test_opencli_browser_public_compatibility_imports() -> None:
    from seektalent.providers.liepin.opencli_browser import (
        LIEPIN_RECRUITER_SEARCH_URL,
        OpenCliBrowserConfig,
        OpenCliBrowserError,
        OpenCliBrowserResult,
        OpenCliBrowserRunner,
        build_observation,
        classify_liepin_state,
        default_liepin_opencli_policy,
        extract_allowed_click_refs,
    )

    policy = default_liepin_opencli_policy(
        allowed_hosts=("www.liepin.com", "h.liepin.com"),
        allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
    )
    config = OpenCliBrowserConfig(
        command=("opencli",),
        session="seektalent-liepin",
        timeout_seconds=10,
        policy=policy,
    )
    result = OpenCliBrowserResult(ok=True, action="status")

    assert config.policy.source_kind == "liepin"
    assert result.to_public_payload()["safeReasonCode"] == "configured"
    assert issubclass(OpenCliBrowserError, RuntimeError)
    assert OpenCliBrowserRunner is not None
    assert build_observation("搜索 [ref=16] 查询")["allowedClickRefs"] == ("16",)
    assert classify_liepin_state(url=LIEPIN_RECRUITER_SEARCH_URL, text="请登录后继续") == (
        "liepin_opencli_login_required"
    )
    assert extract_allowed_click_refs("搜索 [ref=16] 查询") == ("16",)


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_opencli_automation_module_does_not_own_liepin_page_semantics() -> None:
    text = _text("src/seektalent/providers/liepin/opencli_browser_automation.py")

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
        "import subprocess",
        "subprocess.",
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
    text = _text("src/seektalent/providers/liepin/opencli_runtime.py")

    forbidden = (
        "ALLOWED_CLICK_TARGET_FRAGMENTS",
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

    def run(self, argv: Sequence[str], *, timeout: int) -> str:
        del timeout
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
        policy=default_liepin_opencli_policy(
            allowed_hosts=("www.liepin.com", "h.liepin.com"),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
    )


def test_opencli_browser_automation_runs_generic_get_url_without_liepin_semantics() -> None:
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

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
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands()
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    output = automation.click_ref("44")

    assert output == ""
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "click", "44")]


def test_opencli_browser_automation_find_css_owns_opencli_argv_shape() -> None:
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

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
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

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
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands(outputs={("opencli", "daemon", "status"): FileNotFoundError("opencli")})
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    result = automation.status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_command_missing"


def test_opencli_browser_automation_maps_subprocess_timeout() -> None:
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

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

    assert raised.value.safe_reason_code == "liepin_opencli_timeout"


def test_opencli_browser_automation_maps_structured_opencli_error() -> None:
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

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

    assert raised.value.safe_reason_code == "liepin_opencli_stale_ref"


def test_opencli_browser_automation_maps_structured_opencli_error_with_trailing_diagnostic() -> None:
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

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

    assert raised.value.safe_reason_code == "liepin_opencli_stale_ref"


def test_opencli_browser_automation_rejects_invalid_command_shape_before_run() -> None:
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands()
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation.run_browser_command("tab", ("select", "../../unsafe"))

    assert raised.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_opencli_browser_automation_preserves_original_page_id_shape() -> None:
    from seektalent.providers.liepin.opencli_browser_automation import OpenCliBrowserAutomation

    commands = RecordingOpenCliCommands()
    automation = OpenCliBrowserAutomation(config=_config(), commands=commands)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation.run_browser_command("tab", ("select", "abc:def"))

    assert raised.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


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
