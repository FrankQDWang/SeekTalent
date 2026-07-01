from __future__ import annotations

import io
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from seektalent.providers.liepin import opencli_browser_cli
from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
)
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
from seektalent.providers.liepin.liepin_site_adapter import (
    LiepinOpenCliSiteConfig,
    LiepinSiteAdapter,
    build_observation,
    bucket_text,
    classify_liepin_state,
    extract_allowed_click_refs,
    extract_known_modal_close_ref,
    extract_liepin_card_summaries,
    extract_liepin_search_button_ref,
    extract_liepin_search_input_ref,
)


LIEPIN_SEARCH_URL = LIEPIN_RECRUITER_SEARCH_URL


class FakeCommands:
    def __init__(
        self,
        *,
        outputs: dict[tuple[str, ...], str | list[str]] | None = None,
        fail: bool = False,
    ) -> None:
        self.outputs = outputs or {}
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[Mapping[str, str] | None] = []

    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        del timeout
        call = tuple(argv)
        self.calls.append(call)
        self.envs.append(env)
        if self.fail:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=1)
        output = self.outputs.get(call, "{}")
        if output == "{}" and len(call) == 6 and call[3:5] == ("tab", "new"):
            return json.dumps({"page": "page-1", "url": call[5]})
        if isinstance(output, list):
            if output:
                item = output.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            return "{}"
        if isinstance(output, BaseException):
            raise output
        return output

    def prepend_output(self, call: tuple[str, ...], output: str) -> None:
        existing = self.outputs.get(call)
        if existing is None:
            self.outputs[call] = output
            return
        if isinstance(existing, list):
            existing.insert(0, output)


class EvalCommands(FakeCommands):
    def __init__(self, *, eval_output: str, outputs: dict[tuple[str, ...], str | list[str]] | None = None) -> None:
        super().__init__(outputs=outputs)
        self.eval_output = eval_output

    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        call = tuple(argv)
        if len(call) >= 4 and call[3] == "eval":
            del timeout
            self.calls.append(call)
            self.envs.append(env)
            return self.eval_output
        return super().run(argv, timeout=timeout, env=env)


class RefEvalCommands(FakeCommands):
    def __init__(
        self,
        *,
        eval_outputs_by_ref: dict[str, str],
        default_eval_output: str = "null",
        outputs: dict[tuple[str, ...], str | list[str]] | None = None,
    ) -> None:
        super().__init__(outputs=outputs)
        self.eval_outputs_by_ref = eval_outputs_by_ref
        self.default_eval_output = default_eval_output

    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        call = tuple(argv)
        if len(call) >= 4 and call[3] == "eval":
            del timeout
            self.calls.append(call)
            self.envs.append(env)
            script = call[4] if len(call) > 4 else ""
            for ref, output in self.eval_outputs_by_ref.items():
                if f'data-opencli-ref="{ref}"' in script:
                    return output
            return self.default_eval_output
        return super().run(argv, timeout=timeout, env=env)


class FakeWindowCounter:
    def __init__(self, counts: Sequence[int | None] = (1,)) -> None:
        self._counts = list(counts)
        self.calls = 0

    def count(self) -> int | None:
        self.calls += 1
        if self._counts:
            return self._counts.pop(0)
        return 1


class FakeBlankWindowCloser:
    def __init__(self) -> None:
        self.calls = 0

    def close_blank(self) -> bool:
        self.calls += 1
        return True


class FakeCurrentChromeTabOpener:
    def __init__(self, result: bool = True, commands: FakeCommands | None = None) -> None:
        self.result = result
        self.commands = commands
        self.calls: list[str] = []

    def open_tab(self, url: str) -> bool:
        self.calls.append(url)
        if self.result and self.commands is not None:
            page_id = self._page_id_for_url(url)
            self.commands.prepend_output(("opencli", "browser", "seektalent-liepin", "get", "url"), url)
            self.commands.prepend_output(
                ("opencli", "browser", "seektalent-liepin", "tab", "list"),
                _single_tab_list(page_id=page_id, url=url),
            )
        return self.result

    def _page_id_for_url(self, url: str) -> str:
        if "getConditionItem" in url:
            return "page-search"
        match = re.search(r"(?:id=|abc)(\d+)", url)
        if match:
            return f"page-detail-{match.group(1)}"
        return "page-current-window"


def _runner(
    commands: FakeCommands,
    *,
    allowed_click_refs: tuple[str, ...] = (),
    lease_dir: Path | None = None,
    detail_open_timeout_seconds: int = 5,
    idle_close_seconds: int = 120,
    close_blank_window: bool = True,
    window_counter: FakeWindowCounter | None = None,
    blank_window_closer: FakeBlankWindowCloser | None = None,
    current_tab_opener: FakeCurrentChromeTabOpener | None = None,
    pacing_enabled: bool = False,
    pacing_min_ms: int = 0,
    pacing_max_ms: int = 0,
) -> LiepinSiteAdapter:
    browser_config = OpenCliBrowserConfig(
        command=("opencli",),
        session="seektalent-liepin",
        timeout_seconds=10,
        pacing_enabled=pacing_enabled,
        pacing_min_ms=pacing_min_ms,
        pacing_max_ms=pacing_max_ms,
    )
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=("www.liepin.com", "h.liepin.com"),
        allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        allowed_click_refs=allowed_click_refs,
        lease_dir=lease_dir,
        artifact_root=lease_dir,
        detail_open_timeout_seconds=detail_open_timeout_seconds,
        idle_close_seconds=idle_close_seconds,
        close_blank_window=close_blank_window,
        cleanup_worker_enabled=False,
    )
    return LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=site_config,
        automation=OpenCliBrowserAutomation(
            config=browser_config,
            commands=commands,
            window_counter=window_counter or FakeWindowCounter(),
            blank_window_closer=blank_window_closer,
            current_tab_opener=current_tab_opener or FakeCurrentChromeTabOpener(commands=commands),
        ),
    )


def _single_tab_list(*, page_id: str = "page-1", url: str = LIEPIN_SEARCH_URL) -> str:
    return json.dumps([{"page": page_id, "url": url, "active": True}])


def _current_window_open_outputs(
    *, page_id: str = "page-1", url: str = LIEPIN_SEARCH_URL
) -> dict[tuple[str, ...], str]:
    return {
        ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
        ("opencli", "browser", "seektalent-liepin", "tab", "new", url): json.dumps({"page": page_id, "url": url}),
        ("opencli", "browser", "seektalent-liepin", "get", "url"): url,
    }


def _liepin_detail_payload_json(
    *,
    candidate_name: str = "王**",
    full_text: str = "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家",
) -> str:
    return json.dumps(
        {
            "candidate_name": candidate_name,
            "activeStatus": "7天内活跃",
            "jobStatus": "离职，正在找工作",
            "gender": "男",
            "age": 40,
            "city": "上海",
            "education": "硕士",
            "workYears": 14,
            "currentTitle": "数据开发专家",
            "currentCompany": "海光集成电路",
            "jobIntention": {"expectedRole": "数据开发专家", "expectedCity": "上海"},
            "workExperienceList": [
                {
                    "company": "海光集成电路",
                    "title": "高级主管工程师",
                    "dateRange": "2023.10-至今",
                    "description": "负责数据仓库、数据治理、Python 平台和 Hive 数仓。",
                }
            ],
            "educationList": [
                {"school": "北京大学", "degree": "本科", "major": "计算机"}
            ],
            "skills": ["Python", "Hive"],
            "fullText": full_text,
        },
        ensure_ascii=False,
    )


detail_state = (
    "王** 40岁 工作14年 硕士 上海\n"
    "当前职位：数据开发专家\n"
    "海光集成电路 · 高级主管工程师 2023.10-至今\n"
    "负责数据仓库、数据治理、Python 平台和 Hive 数仓。\n"
    "北京大学 · 本科 · 计算机"
)

detail70_state = "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理、Python 平台和 Hive 数仓。"


def test_build_observation_does_not_block_browser_markup_text() -> None:
    observation = build_observation("<html><script></script>localStorage cookie=placeholder</html>")

    assert observation["chars"] > 0
    assert "<html>" in str(observation["text"])


def test_opencli_mutating_actions_apply_pacing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.sleep", sleeps.append)
    monkeypatch.setattr("seektalent.opencli_browser.automation.random.uniform", lambda low, high: low)
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "fill", "--role", "combobox", "--nth", "0", "python"): "{}",
        }
    )
    runner = _runner(
        commands,
        lease_dir=tmp_path,
        pacing_enabled=True,
        pacing_min_ms=700,
        pacing_max_ms=1800,
    )

    runner.fill(target="搜索", text="python")

    assert sleeps == [0.7]


def test_extract_visible_liepin_cards_returns_structured_safe_cards(tmp_path: Path) -> None:
    state_text = (
        "[70]<button><span>查看完整简历</span></button>\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "数据仓库 数据治理 Python Hive\n"
        "某科技公司 · 大数据开发工程师2022.08-至今(3年9个月)\n"
        "沈阳工业大学 · 本科\n"
        "[71]<button><span>查看完整简历</span></button>\n"
        "李** 29岁 工作6年 本科 杭州\n"
        "Flink Spark 实时数仓\n"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        }
    )

    result = _runner(commands, lease_dir=tmp_path).extract_visible_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["schema_version"] == "seektalent.opencli_liepin_visible_cards.v1"
    assert result.to_tool_payload()["observation"] == payload
    first = payload["cards"][0]
    assert first["provider_rank"] == 1
    assert first["ref"] == "70"
    assert first["current_or_recent_company"] == "某科技公司"
    assert first["current_or_recent_title"].startswith("大数据开发工程师")
    assert first["education_level"] == "硕士"
    assert first["work_years"] == 14
    assert "数据仓库" in first["visible_text"]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "raw_html" not in encoded
    assert "cookie" not in encoded.lower()


def test_extract_visible_liepin_cards_binds_ref_to_same_card_summary(tmp_path: Path) -> None:
    state_text = (
        "<div id=resultList>\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "数据仓库 数据治理 Python Hive\n"
        "某科技公司 · 大数据开发工程师2022.08-至今(3年9个月)\n"
        "沈阳工业大学 · 本科\n"
        "李** 29岁 工作6年 本科 杭州\n"
        "Flink Spark 实时数仓\n"
        "杭州科技公司 · 实时数仓工程师2021.01-至今\n"
        "浙江大学 · 本科\n"
    )
    second_card = {
        "entries": [
            {
                "ref": "71",
                "text": (
                    "李** 29岁 工作6年 本科 杭州\n"
                    "Flink Spark 实时数仓\n"
                    "杭州科技公司 · 实时数仓工程师2021.01-至今\n"
                    "浙江大学 · 本科"
                ),
            }
        ]
    }
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
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
            ): json.dumps(second_card, ensure_ascii=False),
        }
    )

    result = _runner(commands, lease_dir=tmp_path).extract_visible_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["card_count"] == 1
    card = payload["cards"][0]
    assert card["ref"] == "71"
    assert card["current_or_recent_company"] == "杭州科技公司"
    assert card["current_or_recent_title"].startswith("实时数仓工程师")
    assert "李**" in card["visible_text"]
    assert "王**" not in card["visible_text"]


def test_status_maps_opencli_doctor_success() -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail_state),
        outputs={
            ("opencli", "daemon", "status"): (
                "Daemon: running (PID 123)\nVersion: 1.8.0\nExtension: connected (v1.8.0)\nProfiles: default v1.8.0\n"
            )
        }
    )
    result = _runner(commands).status()

    assert result.ok is True
    assert result.safe_reason_code == "configured"
    assert commands.calls == [("opencli", "daemon", "status")]


def test_status_does_not_call_doctor_or_start_browser_probe() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "Daemon: not running\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_daemon_not_running"
    assert commands.calls == [("opencli", "daemon", "status")]
    assert all("doctor" not in call for call in commands.calls for call in call)


def test_status_blocks_when_daemon_is_stale() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "Daemon: stale\nExtension: connected\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_daemon_stale"


def test_status_reports_unavailable_for_malformed_daemon_output() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "unexpected status text\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_status_unavailable"


def test_opencli_commands_inherit_background_window_mode() -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            )
        }
    )

    result = _runner(commands).get_url()

    assert result.ok is True
    assert commands.envs
    assert commands.envs[-1] is not None
    assert commands.envs[-1]["OPENCLI_WINDOW"] == "background"


def test_status_blocks_when_extension_is_disconnected() -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail_state),
        outputs={
            ("opencli", "daemon", "status"): ("Daemon: running (PID 123)\nVersion: 1.8.0\nExtension: disconnected\n")
        }
    )

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_extension_disconnected"


def test_open_liepin_tab_rejects_wrong_host_before_opencli_call() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).open_liepin_tab("https://example.com/")

    assert error.value.safe_reason_code == "liepin_opencli_host_blocked"
    assert commands.calls == []


def test_open_liepin_tab_rejects_unapproved_start_url() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).open_liepin_tab("https://www.liepin.com/")

    assert error.value.safe_reason_code == "liepin_opencli_start_url_blocked"
    assert commands.calls == []


def test_open_liepin_tab_reuses_verified_owned_lease_instead_of_opening_duplicate_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (f'[{{"page":"page-0","url":"{liepin_url}"}}]'),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-0"): "{}",
        }
    )
    owner_nonce = "nonce-owned-0"
    blank_window_closer = FakeBlankWindowCloser()
    runner = _runner(commands, lease_dir=tmp_path, blank_window_closer=blank_window_closer)
    runner._write_owned_page_marker(
        page_id="page-0",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce=owner_nonce,
        opened_at=9_999_999_999.0,
    )
    (tmp_path / "seektalent-liepin.json").write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-0",
                "url": liepin_url,
                "last_activity_at": 9_999_999_999,
                "owner_nonce": owner_nonce,
            }
        ),
        encoding="utf-8",
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-0"),
        ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-0", liepin_url),
    ]
    assert blank_window_closer.calls == 0
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-0"


def test_open_liepin_tab_reuses_canonical_search_marker_without_lease(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-old"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                f'{{"url":"{liepin_url}","page":"page-search-newly-opened"}}'
            ),
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-search-new",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:2",
        owner_nonce="nonce-new",
        opened_at=9_999_999_999.0,
    )
    runner._write_owned_page_marker(
        page_id="page-search-old",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-old",
        opened_at=9_999_999_900.0,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-new") not in commands.calls
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-old"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search-old", liepin_url),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-search-old"


def test_open_liepin_tab_skips_stale_canonical_marker_when_reset_fails(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    reset_error = subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable")
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-stale"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search-stale", liepin_url): reset_error,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"page": "page-search-live", "url": liepin_url, "active": False}]
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-live"): "{}",
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search-live", liepin_url): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-search-stale",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-stale",
        opened_at=9_999_999_900.0,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-search-live"
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    assert "page-search-stale" not in owned_pages
    assert "page-search-live" in owned_pages


def test_open_liepin_tab_selects_existing_search_tab_when_current_active_tab_is_workbench(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    workbench_url = "http://127.0.0.1:8123/sessions/session_bd4363d1c367424d"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [
                    {"page": "page-workbench", "url": workbench_url, "active": True},
                    {"page": "page-search", "url": liepin_url, "active": False},
                ]
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search", liepin_url): "{}",
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener(result=False)

    result = _runner(
        commands,
        lease_dir=tmp_path,
        current_tab_opener=current_tab_opener,
    ).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-search"


def test_open_liepin_tab_rejects_malformed_page_id(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): (
                f'[{{"page":"bad/page","url":"{LIEPIN_SEARCH_URL}"}}]'
            ),
        }
    )

    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert error.value.safe_reason_code == "liepin_opencli_tab_response_malformed"


def test_open_liepin_tab_accepts_singleton_tab_new_list_response(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                [{"id": "page-2", "url": LIEPIN_SEARCH_URL}]
            ),
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_recovers_page_id_from_tab_list_when_tab_new_output_is_unexpected(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_recovers_page_id_from_redirected_liepin_search_url(tmp_path: Path) -> None:
    redirected_url = "https://h.liepin.com/search/getConditionItem?city=010#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": redirected_url, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_binds_new_window_before_recovering_opened_page_id(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "bind") in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_ignores_before_tab_list_status_unavailable(tmp_path: Path) -> None:
    tab_list_error = subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable")
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                tab_list_error,
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"page": "page-2", "url": LIEPIN_SEARCH_URL}
            ),
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_recovers_when_tab_new_reports_status_unavailable_but_window_opened(tmp_path: Path) -> None:
    tab_new_error = subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable")
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): tab_new_error,
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_allows_bound_liepin_page_when_page_id_is_unavailable(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                "[]",
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): LIEPIN_SEARCH_URL,
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    assert result.counts == {"opened": 1, "unleased": 1}
    assert not (tmp_path / "seektalent-liepin.json").exists()


def test_open_liepin_tab_keeps_failing_when_bound_page_is_not_liepin(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                "[]",
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://example.com/",
        }
    )

    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert error.value.safe_reason_code == "liepin_opencli_tab_response_malformed"


def test_cleanup_idle_lease_releases_lease_without_closing_tabs(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/resume/showresumedetail?id=357"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                f'[{{"id":"page-1","url":"{liepin_url}"}}]',
                f'[{{"id":"page-1","url":"{liepin_url}"}}]',
                "[]",
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-1"): '{"closed":"page-1"}',
        }
    )
    blank_window_closer = FakeBlankWindowCloser()
    runner = _runner(commands, lease_dir=tmp_path, blank_window_closer=blank_window_closer)
    runner._write_owned_page_marker(
        page_id="page-1",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )
    lease_path = tmp_path / "seektalent-liepin.json"
    lease_path.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-1",
                "url": liepin_url,
                "last_activity_at": 1,
                "owner_nonce": "nonce-owned-1",
            }
        ),
        encoding="utf-8",
    )

    result = runner.cleanup_idle_lease(force=True)

    assert result.ok is True
    assert result.counts == {"leases": 1, "closed": 0}
    assert commands.calls == []
    assert blank_window_closer.calls == 0
    assert not lease_path.exists()
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    assert "page-1" in owned_pages


def test_cleanup_idle_lease_preserves_owned_search_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"id":"page-search","url":"{liepin_url}"}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search"): '{"closed":"page-search"}',
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-search",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-search",
        opened_at=9_999_999_999.0,
    )
    lease_path = tmp_path / "seektalent-liepin.json"
    lease_path.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-search",
                "url": liepin_url,
                "last_activity_at": 1,
                "owner_nonce": "nonce-owned-search",
            }
        ),
        encoding="utf-8",
    )

    result = runner.cleanup_idle_lease(force=True)

    assert result.ok is True
    assert result.counts == {"leases": 1, "closed": 0}
    assert commands.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search") not in commands.calls
    assert not lease_path.exists()


def test_cleanup_idle_lease_skips_close_when_owned_tab_cannot_be_reverified(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/resume/showresumedetail?id=357"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                f'[{{"id":"page-1","url":"{liepin_url}"}}]',
                "[]",
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-1"): '{"closed":"page-1"}',
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-1",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )
    (tmp_path / "seektalent-liepin.json").write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-1",
                "url": liepin_url,
                "last_activity_at": 1,
                "owner_nonce": "nonce-owned-1",
            }
        ),
        encoding="utf-8",
    )

    result = runner.cleanup_idle_lease(force=True)

    assert result.ok is True
    assert result.counts == {"leases": 1, "closed": 0}
    assert commands.calls == []


def test_cleanup_idle_lease_does_not_unbind_or_retry_when_close_fails(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/resume/showresumedetail?id=357"
    close_error = subprocess.CalledProcessError(
        returncode=1,
        cmd=["opencli", "browser", "seektalent-liepin", "tab", "close", "page-1"],
        stderr="status unavailable",
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (f'[{{"id":"page-1","url":"{liepin_url}"}}]'),
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-1"): close_error,
            ("opencli", "browser", "seektalent-liepin", "unbind"): '{"unbound":true}',
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-1",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )
    (tmp_path / "seektalent-liepin.json").write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-1",
                "url": liepin_url,
                "last_activity_at": 1,
                "owner_nonce": "nonce-owned-1",
            }
        ),
        encoding="utf-8",
    )

    result = runner.cleanup_idle_lease(force=True)

    assert result.ok is True
    assert result.counts == {"leases": 1, "closed": 0}
    assert commands.calls == []


def test_cleanup_idle_lease_keeps_owned_page_marker_for_user_managed_tabs(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/resume/showresumedetail?id=357"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                f'[{{"id":"page-1","url":"{liepin_url}"}}]',
                f'[{{"id":"page-1","url":"{liepin_url}"}}]',
                "[]",
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-1"): '{"closed":"page-1"}',
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-1",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )
    (tmp_path / "seektalent-liepin.json").write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-1",
                "url": liepin_url,
                "last_activity_at": 1,
                "owner_nonce": "nonce-owned-1",
            }
        ),
        encoding="utf-8",
    )

    result = runner.cleanup_idle_lease(force=True)

    assert result.ok is True
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    assert "page-1" in owned_pages


def test_cleanup_idle_lease_does_not_close_without_owned_marker(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-user"): '{"closed":"page-user"}',
        }
    )
    lease_path = tmp_path / "seektalent-liepin.json"
    lease_path.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-user",
                "url": "https://h.liepin.com/search/getConditionItem#session",
                "last_activity_at": 1,
            }
        ),
        encoding="utf-8",
    )

    result = _runner(commands, lease_dir=tmp_path, close_blank_window=False).cleanup_idle_lease(force=True)

    assert result.ok is True
    assert result.counts == {"leases": 1, "closed": 0}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-user") not in commands.calls
    assert not lease_path.exists()


def test_cleanup_orphaned_tabs_without_lease_never_closes_chrome_tabs(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [
                    {"id": "page-owned-1", "url": "https://h.liepin.com/search/getConditionItem#session"},
                    {"id": "page-user-1", "url": "https://h.liepin.com/search/getConditionItem#session"},
                    {"id": "page-other-1", "url": "https://example.com/"},
                ]
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-owned-1"): "",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-owned-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )

    result = runner.cleanup_orphaned_tabs(force=True)

    assert result.ok
    assert result.counts == {"leases": 0, "closedTabs": 0, "blankWindows": 0, "skipped": 1}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-owned-1") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-user-1") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-other-1") not in commands.calls


def test_cleanup_orphaned_owned_tabs_keeps_tabs_when_force_is_false(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"id": "page-owned-1", "url": "https://h.liepin.com/search/getConditionItem#session"}]
            )
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-owned-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )

    result = runner.cleanup_orphaned_tabs(force=False)

    assert result.ok
    assert result.counts == {"leases": 0, "closedTabs": 0, "blankWindows": 0}
    assert commands.calls == []


def test_cleanup_orphaned_owned_tabs_ignores_stale_marker(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"id": "page-owned-1", "url": "https://h.liepin.com/search/getConditionItem#session"}]
            )
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-owned-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=1.0,
    )

    result = runner.cleanup_orphaned_tabs(force=True)

    assert result.ok
    assert result.counts == {"leases": 0, "closedTabs": 0, "blankWindows": 0, "skipped": 1}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-owned-1") not in commands.calls
    assert not (tmp_path / "seektalent-liepin-owned-pages.json").exists()


def test_cleanup_orphaned_owned_tabs_never_closes_for_malformed_marker(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"id": "page-owned-1", "url": "https://h.liepin.com/search/getConditionItem#session"}]
            )
        }
    )
    marker_path = tmp_path / "seektalent-liepin-owned-pages.json"
    marker_path.write_text("{not-json", encoding="utf-8")
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)

    with pytest.raises(OpenCliBrowserError) as error:
        runner.cleanup_orphaned_tabs(force=True)

    assert error.value.safe_reason_code == "liepin_opencli_owned_marker_malformed"
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-owned-1") not in commands.calls


def test_open_liepin_tab_quarantines_malformed_owned_marker_and_writes_fresh_marker(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-2"),
        }
    )
    marker_path = tmp_path / "seektalent-liepin-owned-pages.json"
    marker_path.write_text("{not-json", encoding="utf-8")

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    owned_pages = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(owned_pages) == {"page-2"}
    assert owned_pages["page-2"]["url"] == LIEPIN_SEARCH_URL
    assert list(tmp_path.glob("seektalent-liepin-owned-pages.json.malformed-*"))


def test_open_liepin_tab_quarantines_malformed_lease_and_opens_new_tab(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-2"),
        }
    )
    lease_path = tmp_path / "seektalent-liepin.json"
    lease_path.write_text("{not-json", encoding="utf-8")

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"
    assert list(tmp_path.glob("seektalent-liepin.json.malformed-*"))


def test_cleanup_idle_lease_keeps_active_lease(tmp_path: Path) -> None:
    commands = FakeCommands()
    lease_path = tmp_path / "seektalent-liepin.json"
    lease_path.write_text(
        json.dumps({"page_id": "page-1", "last_activity_at": 9_999_999_999}),
        encoding="utf-8",
    )

    result = _runner(commands, lease_dir=tmp_path).cleanup_idle_lease()

    assert result.ok is True
    assert result.counts == {"leases": 1, "closed": 0}
    assert commands.calls == []
    assert lease_path.exists()


def test_fill_rejects_long_or_sensitive_text() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).fill(target="16", text="x" * 81)

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_text"
    assert commands.calls == []


def test_fill_allows_short_keyword_text() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "fill",
                "16",
                "数据开发专家",
            ): '{"filled":true}'
        }
    )

    result = _runner(commands).fill(target="16", text="数据开发专家")

    assert result.ok is True
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "fill", "16", "数据开发专家")]


@pytest.mark.parametrize(
    "target",
    [
        "查看完整简历",
        "简历详情",
        "联系候选人",
        "聊天",
        "下载简历",
        "payment button",
        "resume detail",
    ],
)
def test_click_rejects_detail_or_contact_targets_before_opencli_call(target: str) -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).click(target=target)

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


@pytest.mark.parametrize("target", ["16", "ref=16", "[ref=16]"])
def test_click_rejects_opaque_targets_before_opencli_call(target: str) -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).click(target=target)

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_click_allows_explicit_search_target() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}'
        }
    )

    result = _runner(commands).click(target="搜索")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    ]


def test_click_allows_state_derived_ref_target() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}'
        }
    )

    result = _runner(commands, allowed_click_refs=("16",)).click(target="16")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    ]


def test_click_allows_state_derived_ref_marker() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}'
        }
    )

    result = _runner(commands, allowed_click_refs=("16",)).click(target="ref=16")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    ]


def test_fill_rejects_contact_or_detail_targets_before_opencli_call() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).fill(target="联系输入框", text="数据开发专家")

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_forbidden_opencli_command_is_rejected() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands)._run_browser_command("eval", ("document.cookie",))

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_internal_detail_url_probe_rejects_unknown_probe_name(tmp_path: Path) -> None:
    commands = FakeCommands()
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as error:
        runner._run_fixed_readonly_eval_probe(probe_name="arbitrary", ref="70")

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_run_maps_opencli_structured_stale_ref_error(tmp_path: Path) -> None:
    error = subprocess.CalledProcessError(
        1,
        ["opencli"],
        output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
        stderr="",
    )
    commands = FakeCommands(outputs={("opencli", "browser", "seektalent-liepin", "click", "44"): error})
    runner = _runner(commands, lease_dir=tmp_path, allowed_click_refs=("44",))

    with pytest.raises(OpenCliBrowserError) as raised:
        runner._click_native_filter_ref("44")

    assert raised.value.safe_reason_code == "liepin_opencli_stale_ref"


def test_run_maps_opencli_structured_selector_error(tmp_path: Path) -> None:
    error = subprocess.CalledProcessError(
        1,
        ["opencli"],
        output="",
        stderr='{"error":{"code":"selector_not_found","message":"not found"}}',
    )
    commands = FakeCommands(
        outputs={("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): error}
    )
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as raised:
        runner._click_native_filter_menu("city")

    assert raised.value.safe_reason_code == "liepin_opencli_selector_not_found"


def test_restricted_command_shape_rejects_forbidden_click_target() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands)._run_browser_command("click", ("联系候选人",))

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_public_payload_does_not_include_raw_output() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )

    result = _runner(commands).state()

    payload = result.to_public_payload()
    assert payload == {"ok": True, "action": "state", "safeReasonCode": "configured", "counts": {}}
    assert "搜索职位" not in json.dumps(payload, ensure_ascii=False)


def test_state_classifier_blocks_login_and_risk_pages_before_next_action() -> None:
    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text="请登录后继续") == (
        "liepin_opencli_login_required"
    )
    assert classify_liepin_state(
        url="https://h.liepin.com/search/getConditionItem#session", text="安全验证 请完成验证码"
    ) == ("liepin_opencli_risk_page")
    assert classify_liepin_state(url="https://safe.liepin.com/v/intercept/verifysms", text="") == (
        "liepin_opencli_risk_page"
    )
    assert classify_liepin_state(url="https://lpt.liepin.com/", text="请选择招聘身份") == (
        "liepin_opencli_identity_intercept"
    )
    assert classify_liepin_state(url="https://www.liepin.com/resume/detail/123", text="候选人详情") == (
        "liepin_opencli_unknown_modal"
    )


def test_state_classifier_does_not_block_recruiter_search_page_copy() -> None:
    assert (
        classify_liepin_state(
            url="https://h.liepin.com/search/getConditionItem#session",
            text="找简历\n你好，夏诚\n安全退出\n使用本机 Chrome 登录态",
        )
        is None
    )


def test_state_blocks_forbidden_url_before_reading_page_text() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): ("https://www.liepin.com/resume/detail/123"),
            ("opencli", "browser", "seektalent-liepin", "state"): "raw detail resume text",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_unknown_modal"
    assert result.to_tool_payload()["observation"] == {
        "text": "",
        "chars": 0,
        "truncated": False,
        "terminal": True,
    }
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "get", "url")]


def test_state_reports_safe_liepin_intercept_as_risk_page_before_reading_text() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://safe.liepin.com/v/intercept/verifysms?backurl=https://api-h.liepin.com"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): "安全中心-风险提示",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_risk_page"
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "get", "url")]


def test_state_returns_terminal_classification_to_pi_payload_only() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "请登录后继续 [ref=login]",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_login_required"
    pi_payload = result.to_tool_payload()
    assert pi_payload["observation"]["terminal"] is True
    public_payload = result.to_public_payload()
    assert "请登录" not in json.dumps(public_payload, ensure_ascii=False)


def test_state_returns_bounded_observation_to_pi_only() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )

    result = _runner(commands).state()

    pi_payload = result.to_tool_payload()
    public_payload = result.to_public_payload()
    assert pi_payload["observation"]["text"] == "搜索职位、公司 [ref=16]"
    assert pi_payload["observation"]["terminal"] is False
    assert "搜索职位" not in json.dumps(public_payload, ensure_ascii=False)


def test_state_exposes_only_safe_click_refs_to_pi() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "button 搜索 [ref=16]\n"
                "button 查看完整简历 [ref=99]\n"
                "button 下一页 [ref=next]\n"
                "[29]<button />\n"
                "  <span>搜 索</span>\n"
                "[30]<input type=search />\n"
                "text 14年经验 [ref=profile]"
            ),
        }
    )

    result = _runner(commands).state()

    assert result.ok is True
    assert result.to_tool_payload()["observation"]["allowedClickRefs"] == ("16", "next", "29")
    assert "allowedClickRefs" not in result.to_public_payload()


def test_build_observation_exposes_structured_liepin_detail_targets_to_pi() -> None:
    text = (
        "候选人 张某\n"
        "数据开发专家\n"
        "10年经验\n"
        "上海\n"
        "数据治理 Python 离线数仓\n"
        "button 查看完整简历 [ref=99]\n"
        "button 下一页 [ref=next]"
    )

    observation = build_observation(text)

    assert observation["detailTargets"] == (
        {
            "rank": 1,
            "ref": "99",
            "summary": "候选人 张某\n数据开发专家\n10年经验\n上海\n数据治理 Python 离线数仓\n查看完整简历",
            "score": 0,
        },
    )


def test_open_liepin_detail_without_claim_reports_timeout(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n"
                "数据仓库 数据治理 Python Hive\n"
                "[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "70"): subprocess.TimeoutExpired(
                cmd=["opencli", "browser", "seektalent-liepin", "click", "70"],
                timeout=8,
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "5"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_timeout"
    events = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "agent-events.json").read_text())
    assert {"action_kind": "open_detail", "route_kind": "detail", "ref": "70", "rank": 1} in events["events"]
    assert {
        "action_kind": "open_detail_timeout",
        "route_kind": "detail",
        "ref": "70",
        "rank": 1,
        "safe_reason_code": "liepin_opencli_timeout",
    } in events["events"]


def test_open_liepin_detail_waits_for_delayed_detail_tab_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.sleep", lambda _: None)
    monkeypatch.setattr("seektalent.providers.liepin.liepin_site_adapter.time.sleep", lambda _: None)
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://h.liepin.com/search/getConditionItem#session",
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n"
                "数据仓库 数据治理 Python Hive\n"
                "[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                (
                    '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":false},'
                    f'{{"page":"DETAILPAGE1","url":"{detail_url}","active":true}}]'
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "DETAILPAGE1"): "{}",
        }
    )

    result = _runner(
        commands,
        lease_dir=tmp_path,
        detail_open_timeout_seconds=4,
    ).open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "tab", "list")) == 4
    events = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "agent-events.json").read_text())
    assert all(event["action_kind"] != "open_detail_timeout" for event in events["events"])


def test_open_liepin_detail_opens_card_detail_url_in_controlled_tab(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
        "&index=5&position=5&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={"357": detail_url},
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url, detail_url],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "摆** 31岁 工作7年 本科 北京\n"
                "数据开发 ETL Python\n"
                "[357]<div class=detail-resume-card-wrap>查看完整简历</div>"
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): _single_tab_list(
                page_id="page-detail-357",
                url=detail_url,
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): (
                json.dumps({"page": "page-detail-357", "url": detail_url})
            ),
        },
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_detail(source_run_id="run-1", ref="357", rank=1)

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "click", "357") not in commands.calls
    assert any(call[3] == "eval" for call in commands.calls)
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-detail-357"
    assert lease["url"] == detail_url


def test_open_liepin_detail_reuses_already_opened_ref_without_duplicate_click(tmp_path: Path) -> None:
    commands = FakeCommands()
    runner = _runner(commands, lease_dir=tmp_path)
    runner._append_agent_event(
        "run-1",
        {"action_kind": "open_detail_succeeded", "route_kind": "detail", "ref": "70", "rank": 1},
    )

    result = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    assert result.counts == {"rank": 1, "reused": 1}
    assert commands.calls == []


def test_failed_detail_open_does_not_mark_ref_reusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.sleep", lambda _: None)
    monkeypatch.setattr("seektalent.providers.liepin.liepin_site_adapter.time.sleep", lambda _: None)
    commands = EvalCommands(
        eval_output="null",
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://h.liepin.com/search/getConditionItem#session",
                "https://h.liepin.com/search/getConditionItem#session",
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "click", "70"): subprocess.CalledProcessError(
                1,
                ["opencli"],
            ),
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)

    first = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)
    second = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert first.ok is False
    assert second.counts.get("reused") != 1
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "70")) == 2


def test_captured_detail_resume_reuse_is_allowed_without_duplicate_open(tmp_path: Path) -> None:
    runner = _runner(FakeCommands(outputs={}), lease_dir=tmp_path)
    safe_run_id = "run-1"
    runner._write_collected_resumes(
        safe_run_id,
        [
            {
                "provider_rank": 1,
                "candidate_resume_id": "liepin-opencli-detail-run-1-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "normalized_text": "Python RAG",
            }
        ],
    )

    result = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    assert result.counts["reused"] == 1


def test_open_liepin_detail_claims_new_detail_tab_without_binding_current_window(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://h.liepin.com/search/getConditionItem#session",
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n"
                "数据仓库 数据治理 Python Hive\n"
                "[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                (
                    '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":false},'
                    f'{{"page":"DETAILPAGE1","url":"{detail_url}","active":true}}]'
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "DETAILPAGE1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "5"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "DETAILPAGE1"
    assert lease["url"] == detail_url
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    assert owned_pages["DETAILPAGE1"]["url"] == detail_url
    assert all(call[3] not in {"bind", "unbind"} for call in commands.calls)


def test_state_exposes_liepin_result_card_refs_as_detail_targets(tmp_path: Path) -> None:
    state_text = "id=resultList\n立即沟通\n共 30 位人选"
    cards_payload = json.dumps(
        {
            "entries": [
                {
                    "ref": "448",
                    "visible": True,
                    "text": "张某 32岁 工作10年 本科 上海 求职期望 数据开发专家 数据治理 Python 离线数仓",
                },
                {"ref": "449", "visible": True, "text": "立即沟通"},
            ],
            "matches_n": 2,
        },
        ensure_ascii=False,
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail_state),
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "find",
                "--css",
                "#resultList .detail-resume-card-wrap",
                "--limit",
                "20",
                "--text-max",
                "1200",
            ): cards_payload,
        }
    )

    result = _runner(commands, lease_dir=tmp_path).state()

    assert result.ok is True
    assert result.to_tool_payload()["observation"]["detailTargets"] == (
        {
            "rank": 1,
            "ref": "448",
            "summary": "张某 32岁 工作10年 本科 上海 求职期望 数据开发专家 数据治理 Python 离线数仓",
            "score": 0,
        },
    )


def test_extract_allowed_click_refs_supports_opencli_ref_forms() -> None:
    text = "button 搜索 [ref=16]\nbutton 下一页 ref=next\nbutton 查询 [query-ref]"

    assert extract_allowed_click_refs(text) == ("16", "next", "query-ref")


def test_extract_liepin_search_input_ref_uses_keyword_combobox_near_label() -> None:
    text = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "<span>职位名称：</span>\n"
        "  [139]<input autocomplete=off placeholder=岁 id=ageLow type=text />"
    )

    assert extract_liepin_search_input_ref(text) == "26"


def test_extract_liepin_search_button_ref_uses_visible_search_button() -> None:
    text = (
        "<span>包含全部关键词</span>\n"
        "  [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "[70]<button><span>查看完整简历</span></button>"
    )

    assert extract_liepin_search_button_ref(text) == "29"


def test_extract_liepin_search_input_ref_falls_back_to_keyword_input_id() -> None:
    text = (
        "[316]<input type=search autocomplete=off role=combobox id=rc_select_0 />\n"
        "[26]<input type=search autocomplete=off role=combobox value=数据开发 id=rc_select_1 />\n"
        "[30]<input type=search autocomplete=off role=combobox id=rc_select_2 />\n"
    )

    assert extract_liepin_search_input_ref(text) == "26"


def test_extract_known_modal_close_ref_is_limited_to_known_liepin_modal() -> None:
    text = "[1]<a>X</a>\n<div>新增人才</div>\n[26]<input role=combobox />"

    assert extract_known_modal_close_ref(text) == "1"
    assert extract_known_modal_close_ref("[1]<a>X</a>\n<div>其他弹窗</div>") is None


def test_bucket_text_is_count_only() -> None:
    assert bucket_text("数据开发专家") == {"chars": 6}


def test_search_liepin_cards_runs_bounded_opencli_flow_and_writes_valid_artifacts(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "FTI SDP CXL Pcie verilog"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail70_state),
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [state_before, state_before, state_after],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["schema_version"] == "seektalent.pi_liepin_cards.v1"
    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    assert envelope["cards"][0]["safe_card_summary"]["current_or_recent_company"] == "海光集成电路"
    assert envelope["cards"][0]["safe_card_summary"]["current_or_recent_title"] == "高级主管工程师"
    assert envelope["cards"][0]["safe_card_summary"]["work_years"] == 14
    assert envelope["cards"][0]["safe_card_summary_ref"].startswith("artifact://public-summary/pi-card/run-1/")
    assert (tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").is_file()
    assert (tmp_path / "public-summary" / "pi-card" / "run-1" / "1.json").is_file()
    assert ("opencli", "browser", "seektalent-liepin", "tab", "list") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "get", "url") in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-1"
    assert (
        "opencli",
        "browser",
        "seektalent-liepin",
        "fill",
        "26",
        "数据开发专家",
    ) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "29") in commands.calls
    fill_index = commands.calls.index(
        (
            "opencli",
            "browser",
            "seektalent-liepin",
            "fill",
            "26",
            "数据开发专家",
        )
    )
    click_index = commands.calls.index(
        ("opencli", "browser", "seektalent-liepin", "click", "29")
    )
    assert fill_index < click_index
    assert ("opencli", "browser", "seektalent-liepin", "state") in commands.calls[
        fill_index + 1 : click_index
    ]


def test_search_liepin_cards_reobserves_search_ref_after_stale_submit(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_retry = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[31]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_retry,
                state_after,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): subprocess.CalledProcessError(
                1,
                ["opencli"],
                output='{"error":{"code":"stale_ref","message":"target disappeared"}}',
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "31"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "29") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "31") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "click_search_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_search_liepin_cards_retries_transient_status_after_search_click(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [state_before, state_before, state_after],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): [
                "{}",
                subprocess.CalledProcessError(1, ["opencli"]),
            ],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "observe_results_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_status_unavailable",
    } in trace["events"]


def test_search_liepin_cards_retries_stale_observe_results_once(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                subprocess.CalledProcessError(
                    1,
                    ["opencli"],
                    output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
                    stderr="",
                ),
                state_after,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "observe_results_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_agent_driven_detail_tools_capture_and_finalize_resume_envelope(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail_url = "https://h.liepin.com/resume/showresumedetail?id=70"
    search_state = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive\n"
        "[70]<button><span>查看完整简历</span></button>\n"
    )
    detail_state = (
        "王** 40岁 工作14年 硕士 上海\n"
        "当前职位：数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "负责数据仓库、数据治理、Python 平台和 Hive 数仓。\n"
        "北京大学 · 本科 · 计算机"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                search_url,
                search_url,
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-70", "url": detail_url, "active": True},
                    ]
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, detail_state],
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    opened = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)
    captured = runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1)
    finalized = runner.finalize_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=7,
        cards_seen=1,
    )

    assert opened.ok is True
    assert captured.ok is True
    assert finalized["schema_version"] == "seektalent.liepin_opencli_resumes.v1"
    assert finalized["resumes_returned"] == 1
    assert finalized["detail_pages_opened"] == 1
    assert finalized["cards_excluded"] == []
    assert finalized["resumes"][0]["detail_payload"]["fullText"].startswith("王** 40岁")
    assert ("opencli", "browser", "seektalent-liepin", "click", "70") in commands.calls
    trace_ref = str(finalized["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert {"action_kind": "open_detail", "route_kind": "detail", "ref": "70", "rank": 1} in trace["events"]


def test_finalize_liepin_resumes_leaves_owned_detail_tabs_for_user_cleanup(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
        "&index=5&position=5&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal"
    )
    search_state = (
        "摆** 31岁 工作7年 本科 北京\n数据开发 ETL Python\n[357]<div class=detail-resume-card-wrap>查看完整简历</div>"
    )
    detail_state = "摆** 31岁 工作7年 本科 北京\n当前职位：数据开发专家\n负责 ETL、Python、离线数仓和数据治理。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={"357": detail_url},
        default_eval_output=_liepin_detail_payload_json(candidate_name="摆**", full_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url, detail_url],
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, detail_state],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): (
                f'{{"url":"{detail_url}","page":"page-detail-357"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-357"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps(
                    [
                        {"page": "page-detail-357", "url": detail_url, "active": True},
                        {"page": "user-github", "url": "https://github.com/", "active": False},
                    ]
                ),
                json.dumps([{"page": "user-github", "url": "https://github.com/", "active": False}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-357"): "{}",
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-search",
        url=search_url,
        source_run_id=None,
        runtime_run_id="run-1",
        source_lane_run_id="run-1:source:liepin:round:1:lane:1",
        owner_nonce="owned-search",
        opened_at=9_999_999_999.0,
    )

    assert runner.open_liepin_detail(source_run_id="run-1", ref="357", rank=1).ok is True
    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1).ok is True
    finalized = runner.finalize_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=7,
        cards_seen=1,
    )

    assert finalized["resumes_returned"] == 1
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-357") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "user-github") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search") not in commands.calls
    marker_path = tmp_path / "seektalent-liepin-owned-pages.json"
    owned_pages = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else {}
    assert any(marker.get("url") == detail_url for marker in owned_pages.values())
    assert "page-search" in owned_pages


def test_agent_driven_open_detail_restores_search_tab_for_next_ref(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail70_url = "https://h.liepin.com/resume/showresumedetail?id=70"
    detail71_url = "https://h.liepin.com/resume/showresumedetail?id=71"
    search_state = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive\n"
        "[70]<button><span>查看完整简历</span></button>\n"
        "张** 女 36岁 工作11年 硕士 上海\n"
        "求职期望：上海 数据平台专家\n"
        "云栖数据 · 数据平台负责人 2020.01-至今\n"
        "数据治理 Python Spark\n"
        "[71]<button><span>查看完整简历</span></button>"
    )
    detail70_state = (
        "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
    )
    detail71_state = "张** 36岁 工作11年 硕士 上海\n当前职位：数据平台专家\n负责数据治理、Python 和 Spark 平台。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(full_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", search_url): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-search"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                search_url,
                detail70_url,
                detail70_url,
                search_url,
                search_url,
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-detail-70", "url": detail70_url, "active": True},
                        {"page": "page-search", "url": search_url, "active": False},
                    ]
                ),
                json.dumps(
                    [
                        {"page": "page-detail-70", "url": detail70_url, "active": False},
                        {"page": "page-search", "url": search_url, "active": True},
                    ]
                ),
                json.dumps(
                    [
                        {"page": "page-detail-71", "url": detail71_url, "active": True},
                        {"page": "page-search", "url": search_url, "active": False},
                    ]
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_state,
                detail70_state,
                search_state,
                detail71_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "71"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "5"): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    assert runner.open_liepin_tab(search_url).ok is True
    assert runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1).ok is True
    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1).ok is True
    commands.default_eval_output = _liepin_detail_payload_json(
        candidate_name="张**",
        full_text=detail71_state,
    )
    assert runner.open_liepin_detail(source_run_id="run-1", ref="71", rank=2).ok is True
    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=2).ok is True

    second_select = [
        index
        for index, call in enumerate(commands.calls)
        if call == ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search")
    ][0]
    assert second_select < commands.calls.index(("opencli", "browser", "seektalent-liepin", "click", "71"))


def test_search_liepin_resumes_leaves_detail_tabs_open_and_restores_search_for_next_capture(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail70_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc70"
    detail71_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc71"
    search_form_state = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    search_results_state = (
        "id=resultList\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "[70]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>\n"
        "张** 36岁 工作11年 硕士 上海\n"
        "求职期望：上海 数据平台专家\n"
        "云栖数据 · 数据平台负责人 2020.01-至今\n"
        "[71]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>"
    )
    refreshed_search_results_state = (
        "id=resultList\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "[170]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>\n"
        "张** 36岁 工作11年 硕士 上海\n"
        "求职期望：上海 数据平台专家\n"
        "云栖数据 · 数据平台负责人 2020.01-至今\n"
        "[171]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>"
    )
    detail70_state = (
        "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
    )
    detail71_state = "张** 36岁 工作11年 硕士 上海\n当前职位：数据平台专家\n负责数据治理、Python 和 Spark 平台。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={"70": detail70_url, "171": detail71_url},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", search_url): (
                f'{{"url":"{search_url}","page":"page-search"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                search_url,
                search_url,
                search_url,
                search_url,
                search_url,
                detail70_url,
                search_url,
                search_url,
                search_url,
                search_url,
                detail71_url,
                search_url,
                search_url,
            ],
                ("opencli", "browser", "seektalent-liepin", "state"): [
                    search_form_state,
                    search_results_state,
                    search_results_state,
                    search_results_state,
                    search_results_state,
                    detail70_state,
                    refreshed_search_results_state,
                    refreshed_search_results_state,
                    refreshed_search_results_state,
                    detail71_state,
                ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail70_url): (
                f'{{"url":"{detail70_url}","page":"page-detail-70"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url): (
                f'{{"url":"{detail71_url}","page":"page-detail-71"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-70", "url": detail70_url, "active": True},
                    ]
                ),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-71", "url": detail71_url, "active": True},
                    ]
                ),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
            ],
        },
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        target_resumes=2,
        max_pages=1,
        max_cards=2,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    workflow_steps = envelope["workflow_steps"]
    assert not any(step["step_name"] == "cleanup_detail_tabs" for step in workflow_steps)
    assert any(step["step_name"] == "finalize" and step["status"] == "completed" for step in workflow_steps)
    search_select_indexes = [
        index
        for index, call in enumerate(commands.calls)
        if call == ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search")
    ]
    assert search_select_indexes
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url) in commands.calls
    assert any(len(call) > 4 and call[3] == "eval" and 'data-opencli-ref="171"' in call[4] for call in commands.calls)
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-71") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search") not in commands.calls
    trace_ref = str(envelope["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert any(event.get("action_kind") == "return_to_search_after_capture" for event in trace["events"])


def test_search_liepin_resumes_does_not_open_details_after_filter_failure(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(1, ["opencli"]),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        target_resumes=2,
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
    assert envelope["resumes"] == []
    assert all("showresumedetail" not in " ".join(call) for call in commands.calls)


def test_search_liepin_resumes_uses_cached_detail_urls_when_refresh_after_return_loses_cards(
    tmp_path: Path,
) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail70_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc70"
    detail71_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc71"
    search_form_state = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    search_results_state = (
        "id=resultList\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "[70]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>\n"
        "张** 36岁 工作11年 硕士 上海\n"
        "求职期望：上海 数据平台专家\n"
        "云栖数据 · 数据平台负责人 2020.01-至今\n"
        "[71]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>"
    )
    empty_search_state = "id=resultList\n暂无数据"
    detail70_state = (
        "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
    )
    detail71_state = "张** 36岁 工作11年 硕士 上海\n当前职位：数据平台专家\n负责数据治理、Python 和 Spark 平台。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={"70": detail70_url, "71": detail71_url},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", search_url): (
                f'{{"url":"{search_url}","page":"page-search"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url] * 12,
                ("opencli", "browser", "seektalent-liepin", "state"): [
                    search_form_state,
                    search_results_state,
                    search_results_state,
                    search_results_state,
                    search_results_state,
                    detail70_state,
                    empty_search_state,
                    empty_search_state,
                    detail71_state,
                ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail70_url): (
                f'{{"url":"{detail70_url}","page":"page-detail-70"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url): (
                f'{{"url":"{detail71_url}","page":"page-detail-71"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-70", "url": detail70_url, "active": True},
                    ]
                ),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-71", "url": detail71_url, "active": True},
                    ]
                ),
            ],
        },
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        target_resumes=2,
        max_pages=1,
        max_cards=2,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search") not in commands.calls
    trace_ref = str(envelope["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert any(
        event.get("action_kind") == "visible_cards_refreshed_after_return" and event.get("visible_cards") == 0
        for event in trace["events"]
    )
    assert any(
        event.get("action_kind") == "open_detail_succeeded"
        and event.get("rank") == 2
        and event.get("open_mode") == "cached_url"
        for event in trace["events"]
    )


def test_finalize_liepin_resumes_marks_partial_when_target_is_not_met(tmp_path: Path) -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail?id=70\n"
                "王** 40岁 工作14年 硕士 上海\n"
                "当前职位：数据开发专家\n"
                "负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
            )
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1).ok is True
    finalized = runner.finalize_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=1,
        cards_seen=1,
        target_resumes=2,
    )

    assert finalized["status"] == "partial"
    assert finalized["stop_reason"] == "partial_timeout"
    assert finalized["resumes_returned"] == 1
    assert finalized["workflow_steps"][-1]["step_name"] == "finalize"
    assert finalized["workflow_steps"][-1]["status"] == "partial"
    assert finalized["workflow_steps"][-1]["safe_reason_code"] == "partial_timeout"
    assert finalized["workflow_steps"][-1]["safe_counts"] == {"resumes_returned": 1}
    trace_ref = str(finalized["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert trace["status"] == "partial"
    assert trace["stop_reason"] == "partial_timeout"
    assert trace["target_resumes"] == 2
    assert trace["max_cards"] == 1
    assert trace["cards_seen"] == 1
    assert trace["resumes_returned"] == 1


def test_capture_liepin_detail_resume_preserves_detail_source_url(tmp_path: Path) -> None:
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
        "&index=5&position=5&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad\n"
                "王** 40岁 工作14年 硕士 上海\n"
                "当前职位：数据开发专家\n"
                "负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): detail_url,
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    captured = runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is True
    collected = json.loads((tmp_path / "protected" / "pi-detail" / "run-1" / "collected-resumes.json").read_text())
    assert collected["resumes"][0]["detail_payload"]["sourceUrl"] == detail_url


def test_capture_liepin_detail_resume_rejects_blank_page_state(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: about:blank url: about:blank title: viewport: 1512x707 --- interactive: 0 | iframes: 0"
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
        }
    )

    captured = _runner(commands, lease_dir=tmp_path).capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is False
    assert captured.safe_reason_code == "liepin_opencli_detail_not_opened"
    assert not (tmp_path / "protected" / "pi-detail" / "run-1" / "collected-resumes.json").exists()


def test_generic_click_still_rejects_liepin_detail_targets() -> None:
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(FakeCommands()).click(target="查看完整简历")

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"


def test_card_state_classification_still_rejects_detail_url() -> None:
    assert (
        classify_liepin_state(url="https://h.liepin.com/resume/detail?id=1", text="完整简历")
        == "liepin_opencli_unknown_modal"
    )


def test_search_liepin_cards_applies_native_filters_before_reading_cards(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = (
        "[41]<button><span>城市</span></button>\n"
        "[42]<button><span>工作经验</span></button>\n"
        "[43]<button><span>年龄</span></button>\n"
        "[90]<div>王** 男 34岁 工作5年 硕士 上海</div>"
    )
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>\n[45]<label>北京</label>"
    state_after_city = "已选 上海\n[42]<button><span>工作经验</span></button>\n[43]<button><span>年龄</span></button>"
    state_experience_menu = "已选 上海\n[42]<button><span>工作经验</span></button>\n[45]<label>3-5年</label>"
    state_after_experience = "已选 上海 3-5年\n[43]<button><span>年龄</span></button>"
    state_age_menu = "已选 上海 3-5年\n[43]<button><span>年龄</span></button>\n[46]<label>35岁以下</label>"
    state_after_filters = (
        "已选 上海 3-5年 35岁以下\n"
        "王** 男 34岁 工作5年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_after_city,
                state_experience_menu,
                state_after_experience,
                state_age_menu,
                state_after_filters,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "工作经验"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "45"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "年龄"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "46"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={
            "city": "上海",
            "experience": {"minYears": 3, "maxYears": 5},
            "age": {"max": 35},
        },
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "ok": True,
    } in trace["events"]
    click_search_index = commands.calls.index(
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    )
    assert click_search_index < len(commands.calls)
    filter_events = [
        (event.get("filter"), event.get("section"), event.get("value"))
        for event in trace["events"]
        if event.get("action_kind") == "apply_native_filter"
    ]
    assert filter_events == [
        ("city", "legacy", "上海"),
        ("experience", "legacy", "3-5年"),
        ("age", "legacy", "35岁以下"),
    ]


def test_search_liepin_cards_clicks_filters_in_named_sections(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[10]<label>目前城市：</label>
[11]<label>北京</label>
[20]<label>期望城市：</label>
[21]<label>北京</label>
[30]<label>教育经历：</label>
[31]<label>本科</label>
[40]<label>统招要求：</label>
[126]<div />
  [122]<div />
    [121]<span title=统招/非统招（不限）>统招/非统招（不限）</span>
[50]<label>院校要求：</label>
[51]<label>211</label>
[52]<label>985</label>
"""
    state_recruitment_menu = """
[0]<div>已选 期望城市北京 本科</div>
[10]<label>目前城市：</label>
[11]<label>北京</label>
[20]<label>期望城市：</label>
[21]<label>北京</label>
[30]<label>教育经历：</label>
[31]<label>本科</label>
[40]<label>统招要求：</label>
[126]<div />
  [122]<div />
    [121]<span title=统招/非统招（不限）>统招/非统招（不限）</span>
[42]<label>统招本科</label>
[50]<label>院校要求：</label>
[51]<label>211</label>
[52]<label>985</label>
"""
    state_after_expected_city = f"已选 期望城市北京\n{state_after_search}"
    state_after_degree = f"已选 期望城市北京 本科\n{state_after_search}"
    state_after_recruitment = f"已选 期望城市北京 本科 统招\n{state_after_search}"
    state_after_school_211 = f"已选 期望城市北京 本科 统招 211\n{state_after_search}"
    state_after_filters = (
        "已选 期望城市北京 本科 统招 211 985\n"
        "王** 男 34岁 工作5年 本科 北京\n"
        "求职期望：北京 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_after_expected_city,
                state_after_degree,
                state_recruitment_menu,
                state_after_recruitment,
                state_after_school_211,
                state_after_filters,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发 ETL"): '{"filled":true}',
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "31"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "121"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "统招要求"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "42"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "51"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "52"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="source-1",
        query="数据开发 ETL",
        max_pages=1,
        max_cards=10,
        native_filters={
            "city": {"section": "expected", "label": "北京"},
            "degree": {"section": "education", "label": "本科"},
            "recruitmentType": {"section": "recruitment_type", "label": "统招本科"},
            "schoolTypes": [
                {"section": "school_type", "label": "211"},
                {"section": "school_type", "label": "985"},
            ],
        },
    )

    assert result["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "source-1" / "action-trace.json").read_text())
    filter_events = [
        (event.get("filter"), event.get("section"), event.get("value"))
        for event in trace["events"]
        if event.get("action_kind") == "apply_native_filter"
    ]
    assert filter_events == [
        ("city", "expected", "北京"),
        ("degree", "education", "本科"),
        ("recruitmentType", "recruitment_type", "统招本科"),
        ("schoolTypes", "school_type", "211"),
        ("schoolTypes", "school_type", "985"),
    ]
    assert any(
        event.get("action_kind") == "open_native_filter_menu"
        and event.get("filter") == "recruitmentType"
        and event.get("section") == "recruitment_type"
        for event in trace["events"]
    )


def test_search_liepin_cards_blocks_when_required_native_filter_click_fails(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
                state_city_menu,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(1, ["opencli"]),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
    assert envelope["cards"] == []
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "ok": False,
        "safe_reason_code": "liepin_opencli_filter_unapplied",
        "blocking": True,
    } in trace["events"]


def test_search_liepin_cards_accepts_selected_filter_chip_state(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
王** 男 34岁 工作5年 硕士 北京
"""
    state_after_expected_city = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[50]<label title=期望城市 />
  <span>北京</span>
  [51]<span role=img tabindex=-1 />
王** 男 34岁 工作5年 硕士 北京
求职期望：北京 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "北京"}},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "expected",
        "value": "北京",
        "ok": True,
    } in trace["events"]


def test_search_liepin_cards_blocks_when_filter_click_does_not_apply_selection(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_bad_click = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 北京"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_after_bad_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"


def test_search_liepin_cards_retries_unconfirmed_filter_before_blocking(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
王** 男 34岁 工作5年 硕士 北京
"""
    state_after_bad_click = """
[20]<label>期望城市：</label>
王** 男 34岁 工作5年 硕士 上海
"""
    state_after_delayed_chip = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[50]<label title=期望城市 />
  <span>北京</span>
王** 男 34岁 工作5年 硕士 北京
求职期望：北京 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_after_search,
                state_after_bad_click,
                state_after_delayed_chip,
                state_after_search,
                state_after_delayed_chip,
                state_after_delayed_chip,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "北京"}},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert any(
        event.get("action_kind") == "apply_native_filter_retry"
        and event.get("safe_reason_code") == "liepin_opencli_filter_unapplied"
        for event in trace["events"]
    )
    assert any(
        event.get("action_kind") == "verify_native_filter"
        and event.get("already_applied") is True
        and event.get("ok") is True
        for event in trace["events"]
    )


def test_search_liepin_cards_skips_optional_filter_after_retries(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = (
        "[30]<label>教育经历：</label>\n"
        "[31]<label>本科</label>\n"
        "王** 男 34岁 工作5年 硕士 北京\n"
        "求职期望：北京 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    state_after_bad_click = (
        "[30]<label>教育经历：</label>\n"
        "[31]<label>本科</label>\n"
        "王** 男 34岁 工作5年 硕士 北京\n"
        "求职期望：北京 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "31"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={
            "degree": {"section": "education", "label": "本科"},
            "optionalFilterNames": ["degree"],
            "sourceTarget": {"phase": "balanced", "batchNo": 1, "requestedCount": 10},
        },
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "degree",
        "section": "education",
        "value": "本科",
        "ok": False,
        "safe_reason_code": "liepin_opencli_filter_unapplied",
        "blocking": False,
    } in trace["events"]
    assert {
        "action_kind": "skip_native_filter",
        "filter": "degree",
        "ok": True,
        "safe_reason_code": "liepin_opencli_filter_unapplied",
    } in trace["events"]


def test_search_liepin_cards_retries_transient_native_filter_status(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_filter = "已选 上海\n王** 男 34岁 工作5年 硕士 上海\n求职期望：上海 数据开发专家"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
                state_after_filter,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): [
                subprocess.CalledProcessError(1, ["opencli"]),
                '{"clicked":true}',
            ],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter_retry",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "safe_reason_code": "liepin_opencli_status_unavailable",
    } in trace["events"]
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "ok": True,
    } in trace["events"]


def test_extract_liepin_card_summaries_strips_opencli_accessibility_markup() -> None:
    text = (
        "[247]<span title=智能排序>智能排序</span>\n"
        "[251]<span />\n"
        "[250]<span role=img aria-label=down />\n"
        "[249]<svg /> <div /> table 今天活跃周**25岁工作4年本科常州\n"
        "求职期望：杭州 数据分析师\n"
        "中创新航技术研究院(江苏)有限公司 · 大数据开发工程师2022.08-至今(3年9个月)\n"
        "沈阳工业大学 · 本科"
    )

    cards = extract_liepin_card_summaries(text, max_cards=10)

    assert len(cards) == 1
    summary = cards[0]
    normalized = str(summary["normalized_card_text"])
    assert "<" not in normalized
    assert "role=" not in normalized
    assert "aria-label" not in normalized
    assert {"span", "svg", "div", "table"}.isdisjoint(set(summary["skill_tags"]))


def test_search_liepin_cards_returns_blocked_envelope_when_state_is_terminal(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): "安全验证 请完成验证码",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_risk_page"
    assert envelope["cards"] == []
    assert (tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").is_file()


def test_search_liepin_cards_closes_known_add_candidate_modal_before_search(tmp_path: Path) -> None:
    modal_state = "URL: https://h.liepin.com/search/getConditionItem#session\n[1]<a>X</a>\n<div>新增人才</div>"
    search_state = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [modal_state, search_state, result_state],
            ("opencli", "browser", "seektalent-liepin", "click", "1"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "1") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家") in commands.calls


def test_search_liepin_cards_retries_stale_search_input_ref(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    retry_state = search_state.replace("[26]", "[41]")
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, retry_state, result_state],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): [
                subprocess.CalledProcessError(1, ["opencli"], stderr="stale ref"),
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {"action_kind": "fill_search_retry", "route_kind": "search", "chars": 6} in trace["events"]


def test_search_liepin_cards_retries_structured_stale_search_input_ref(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    retry_state = search_state.replace("[26]", "[41]")
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, retry_state, result_state],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): [
                subprocess.CalledProcessError(
                    1,
                    ["opencli"],
                    output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
                    stderr="",
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "fill_search_retry",
        "route_kind": "search",
        "chars": 6,
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_search_liepin_cards_retries_stale_search_button_ref(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_state,
                search_state,
                search_state,
                result_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): [
                subprocess.CalledProcessError(
                    1,
                    ["opencli"],
                    output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
                    stderr="",
                ),
                '{"clicked":true}',
            ],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert (
        commands.calls.count(
            ("opencli", "browser", "seektalent-liepin", "click", "29")
        )
        == 2
    )
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "click_search_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_search_liepin_cards_retries_repeated_transient_fill_status(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_state,
                search_state,
                search_state,
                result_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): [
                subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable"),
                subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable"),
                '{"filled":true}',
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家")) == 3
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert [event["action_kind"] for event in trace["events"]].count("fill_search_retry") == 2


def test_search_liepin_cards_rechecks_transient_unready_state(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): ["安全验证 请稍候", search_state, result_state],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert any(event["action_kind"] == "observe_retry_after_unready" for event in trace["events"])


def test_classify_liepin_state_does_not_treat_doris_as_risk_page() -> None:
    text = "求职期望：深圳大数据开发 Python SQL DorisKafka Spark Hadoop Hive"

    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text=text) is None


def test_cli_rejects_unknown_action(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "network"])
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    rc = opencli_browser_cli.main()

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["safeReasonCode"] == "liepin_opencli_forbidden_command"


def test_cli_state_returns_pi_observation(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "state"])
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    monkeypatch.setattr(opencli_browser_cli, "_runner_from_env", lambda: _runner(commands))

    rc = opencli_browser_cli.main()

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["observation"]["text"] == "搜索职位、公司 [ref=16]"


def test_cli_exposes_cleanup_orphaned_tabs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands = FakeCommands(outputs={("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]"})
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "cleanup_orphaned_tabs"])
    monkeypatch.setattr("sys.stdin", io.StringIO('{"force":true}'))
    monkeypatch.setattr(
        opencli_browser_cli,
        "_runner_from_env",
        lambda: _runner(commands, lease_dir=tmp_path, close_blank_window=False),
    )

    rc = opencli_browser_cli.main()

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "cleanup_orphaned_tabs"
    assert payload["counts"] == {"blankWindows": 0, "closedTabs": 0, "leases": 0, "skipped": 0}


def test_cli_search_cards_prints_strict_envelope(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "search_cards"])
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"sourceRunId":"run-1","query":"数据开发专家","maxPages":1,"maxCards":10}'),
    )
    monkeypatch.setattr(
        opencli_browser_cli,
        "_runner_from_env",
        lambda: _runner(FakeCommands(fail=True), lease_dir=tmp_path),
    )

    rc = opencli_browser_cli.main()

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "seektalent.pi_liepin_cards.v1"
    assert payload["status"] == "blocked"
    assert payload["safe_reason_code"] == "liepin_opencli_timeout"


def test_cli_runner_uses_shell_safe_command_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", '"/tmp/open cli" --profile "qa user"')

    runner = opencli_browser_cli._runner_from_env()

    assert runner._browser_config.command == ("/tmp/open cli", "--profile", "qa user")


def test_cli_runner_reads_state_derived_click_refs_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_CLICK_REFS_JSON", '["16","next"]')

    runner = opencli_browser_cli._runner_from_env()

    assert runner._site_config.allowed_click_refs == ("16", "next")


def test_cli_runner_reads_idle_cleanup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR", str(tmp_path))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS", "3")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW", "false")

    runner = opencli_browser_cli._runner_from_env()

    assert runner._site_config.lease_dir == tmp_path
    assert runner._site_config.idle_close_seconds == 3
    assert runner._site_config.close_blank_window is False
