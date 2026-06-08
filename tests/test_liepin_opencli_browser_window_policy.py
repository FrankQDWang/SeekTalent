from __future__ import annotations

import json
import subprocess
from pathlib import Path

from seektalent.opencli_browser.runtime import SubprocessCurrentChromeTabOpener
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_TAB_REUSE_FRAGMENTS
from seektalent.providers.liepin.liepin_site_adapter import classify_liepin_state
from tests.test_liepin_opencli_browser import FakeCommands, FakeCurrentChromeTabOpener, FakeWindowCounter, _runner


def test_subprocess_current_chrome_tab_opener_accepts_canonical_return_url(monkeypatch) -> None:
    requested_url = "https://h.liepin.com/search/getConditionItem#session"
    opened_url = "https://h.liepin.com/search/getConditionItem?from=redirect#session"

    def fake_run(argv, *, check, capture_output, text, timeout):
        del check, capture_output, text
        assert argv[-2:] == (requested_url, "h.liepin.com/search/getConditionItem")
        assert timeout == 5
        return subprocess.CompletedProcess(argv, 0, stdout=f"{opened_url}\n")

    monkeypatch.setattr("seektalent.opencli_browser.runtime.subprocess.run", fake_run)

    opener = SubprocessCurrentChromeTabOpener(reuse_url_fragments=LIEPIN_RECRUITER_SEARCH_TAB_REUSE_FRAGMENTS)

    assert opener.open_tab(requested_url) is True


def test_subprocess_current_chrome_tab_opener_rejects_missing_chrome_window(monkeypatch) -> None:
    def fake_run(argv, *, check, capture_output, text, timeout):
        del check, capture_output, text, timeout
        return subprocess.CompletedProcess(argv, 0, stdout="no-window\n")

    monkeypatch.setattr("seektalent.opencli_browser.runtime.subprocess.run", fake_run)

    opener = SubprocessCurrentChromeTabOpener(reuse_url_fragments=LIEPIN_RECRUITER_SEARCH_TAB_REUSE_FRAGMENTS)

    assert opener.open_tab("https://h.liepin.com/search/getConditionItem#session") is False


def test_extract_visible_liepin_cards_accepts_english_education_labels(tmp_path: Path) -> None:
    state_text = "<div id=resultList>共3000+位人选</div>"
    result_card = {
        "entries": [
            {
                "ref": "333",
                "text": "N**35岁工作10年MasterShanghai求职期望：ShanghaiData Analyst"
                "Python 数据分析 SQL Alibaba Taotian Group · Data Analysis2024.09-2025.03 "
                "ByteDance E-commerce China Data Science Team Data Science · Data Science2021.09-2024.08 "
                "Risk Control Platform Growth Analytics Recommendation Search Ads Revenue Operations "
                "Data Warehouse Streaming Batch Modeling Stakeholder Delivery Reliability",
            }
        ]
    }
    commands = FakeCommands(
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
            ): json.dumps(result_card, ensure_ascii=False),
        }
    )

    result = _runner(commands, lease_dir=tmp_path).extract_visible_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["card_count"] == 1
    card = payload["cards"][0]
    assert card["ref"] == "333"
    assert card["education_level"] == "硕士"
    assert card["work_years"] == 10
    assert "Data Analyst" in card["visible_text"]

def test_open_liepin_tab_opens_current_window_tab_before_binding(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-current-window-search"
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    owned_page = owned_pages["page-current-window-search"]
    assert owned_page["session"] == "seektalent-liepin"
    assert owned_page["page_id"] == "page-current-window-search"
    assert owned_page["url"] == liepin_url
    assert isinstance(owned_page["opened_at"], int | float)
    assert "runtime_run_id" in owned_page
    assert "source_lane_run_id" in owned_page
    assert isinstance(owned_page["owner_nonce"], str) and owned_page["owner_nonce"]
    assert lease["owner_nonce"] == owned_page["owner_nonce"]


def test_open_liepin_tab_rebinds_when_first_bind_points_at_workbench(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    workbench_url = "http://127.0.0.1:8123/sessions/session-a55bc2b8e6fe4165"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [workbench_url, liepin_url],
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url, liepin_url]
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-current-window-search"


def test_open_liepin_tab_retries_after_transient_get_url_failure(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    get_url_failure = subprocess.CalledProcessError(
        1,
        ["opencli", "browser", "seektalent-liepin", "get", "url"],
        output="",
        stderr='{"error":{"code":"target_unavailable"}}',
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [get_url_failure, liepin_url],
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url, liepin_url]


def test_open_liepin_tab_unbinds_stale_workbench_tab_before_binding_liepin(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    owner_nonce = "nonce-stale"
    blocked_select = subprocess.CalledProcessError(
        1,
        ["opencli", "browser", "seektalent-liepin", "tab", "select", "page-stale-search"],
        output='{"error":{"code":"bound_tab_mutation_blocked"}}',
        stderr='Session "seektalent-liepin" is bound to a user tab.\n',
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-stale-search"): blocked_select,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                '[{"page":"page-local-ui","url":"http://127.0.0.1:8123/sessions/session-bad","active":true}]',
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]',
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]',
            ],
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener)
    runner._write_owned_page_marker(
        page_id="page-stale-search",
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
                "page_id": "page-stale-search",
                "url": liepin_url,
                "last_activity_at": 9_999_999_999,
                "owner_nonce": owner_nonce,
            }
        ),
        encoding="utf-8",
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-stale-search"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-current-window-search"


def test_open_liepin_tab_accepts_canonical_current_window_search_url(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    canonical_url = "https://h.liepin.com/search/getConditionItem?from=redirect#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): canonical_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{canonical_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-current-window-search"


def test_open_liepin_detail_accepts_canonical_current_window_detail_url(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc"
    canonical_detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&index=0"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): canonical_detail_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-detail","url":"{canonical_detail_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener)

    page_id = runner._open_new_liepin_tab(url=detail_url, source_run_id="source-1")

    assert page_id == "page-current-window-detail"
    assert current_tab_opener.calls == [detail_url]
    assert ("opencli", "browser", "seektalent-liepin", "open", detail_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url) not in commands.calls

def test_open_liepin_tab_reuses_bound_active_lease_without_tab_select(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    owner_nonce = "nonce-bound-active"
    blocked_select = subprocess.CalledProcessError(
        1,
        ["opencli", "browser", "seektalent-liepin", "tab", "select", "page-bound-search"],
        output='{"error":{"code":"bound_tab_mutation_blocked"}}',
        stderr='Session "seektalent-liepin" is bound to a user tab.\n',
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-bound-search","url":"{liepin_url}","active":true}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-bound-search"): blocked_select,
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "open", liepin_url): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-bound-search",
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
                "page_id": "page-bound-search",
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
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-bound-search"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "open", liepin_url),
    ]


def test_open_liepin_tab_recovers_from_lease_select_blocked_on_workbench_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    workbench_url = "http://127.0.0.1:8123/sessions/session_1c04e1640dce4c57"
    owner_nonce = "nonce-workbench-bound"
    blocked_select = subprocess.CalledProcessError(
        1,
        ["opencli", "browser", "seektalent-liepin", "tab", "select", "page-bound-search"],
        output='{"error":{"code":"bound_tab_mutation_blocked"}}',
        stderr='Session "seektalent-liepin" is bound to a user tab.\n',
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                f'[{{"page":"page-bound-search","url":"{liepin_url}","active":false}}]',
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]',
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-bound-search"): blocked_select,
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [workbench_url, liepin_url],
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener(commands=commands)
    runner = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener)
    runner._write_owned_page_marker(
        page_id="page-bound-search",
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
                "page_id": "page-bound-search",
                "url": liepin_url,
                "last_activity_at": 9_999_999_999,
                "owner_nonce": owner_nonce,
            }
        ),
        encoding="utf-8",
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-bound-search"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-search"


def test_open_liepin_tab_reuses_existing_current_window_search_tab_without_marker(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    update_notice = "\n\n  Update available: v1.8.0 -> v1.8.1\n  Run: npm install -g @jackwener/opencli\n"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): f"{liepin_url}{update_notice}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-user-search","url":"{liepin_url}","active":true}}]{update_notice}'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-user-search"

def test_get_url_strips_opencli_update_notice() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session\n\n"
                "  Update available: v1.8.0 -> v1.8.1\n"
                "  Run: npm install -g @jackwener/opencli\n"
            )
        }
    )

    result = _runner(commands).get_url()

    assert result.ok is True
    assert result.private_output == "https://h.liepin.com/search/getConditionItem#session"

def test_open_liepin_tab_does_not_walk_all_search_markers_when_canonical_is_stale(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-old"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): ["about:blank", liepin_url],
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-search-fresh","url":"{liepin_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener)
    runner._write_owned_page_marker(
        page_id="page-search-old",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-old",
        opened_at=9_999_999_900.0,
    )
    runner._write_owned_page_marker(
        page_id="page-search-newer",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:2",
        owner_nonce="nonce-newer",
        opened_at=9_999_999_999.0,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-newer") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    assert current_tab_opener.calls == [liepin_url]
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-old"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]

def test_open_liepin_tab_uses_current_window_opener_without_overwriting_active_non_liepin_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert ("opencli", "browser", "seektalent-liepin", "bind") in commands.calls
    assert all(call[3] != "open" for call in commands.calls)

def test_open_liepin_tab_does_not_create_unbound_owned_window(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
        }
    )
    window_counter = FakeWindowCounter((1, 1, 1, 2, 2, 2))
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(
        commands,
        lease_dir=tmp_path,
        window_counter=window_counter,
        current_tab_opener=current_tab_opener,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert window_counter.calls == 0
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls

def test_open_liepin_tab_writes_lease_for_current_window_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(
        commands,
        lease_dir=tmp_path,
        window_counter=FakeWindowCounter((1, 1, 1, 2, 2, 3)),
        current_tab_opener=current_tab_opener,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == [liepin_url]
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "bind"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-current-window-search"

def test_state_classifier_does_not_block_search_page_for_generic_risk_text() -> None:
    text = "找简历\n<input id=riskControl type=hidden />\n<script>window.risk = window.riskScore = 0</script>"

    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text=text) is None
