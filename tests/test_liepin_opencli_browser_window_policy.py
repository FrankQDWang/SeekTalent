from __future__ import annotations

import json
import subprocess
from pathlib import Path

from seektalent.opencli_browser.runtime import SubprocessCurrentChromeTabOpener
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_TAB_REUSE_FRAGMENTS
from seektalent.providers.liepin.liepin_site_adapter import classify_liepin_state
from tests.test_liepin_opencli_browser import (
    ANY_STRUCTURED_CARD_PROBE,
    FakeCommands,
    FakeCurrentChromeTabOpener,
    RefEvalCommands,
    _runner,
)


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
    result_card = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [
                {
                    "provider_rank": 1,
                    "ref": "333",
                    "masked_name": True,
                    "gender": None,
                    "age": 35,
                    "work_years": 10,
                    "city": "Shanghai",
                    "expected_city": "Shanghai",
                    "education_level": "硕士",
                    "current_or_recent_company": "Alibaba Taotian Group",
                    "current_or_recent_title": "Data Analysis",
                    "job_intention": "Data Analyst",
                    "skill_tags": ["Python", "数据分析", "SQL"],
                }
            ],
        },
        ensure_ascii=False,
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={ANY_STRUCTURED_CARD_PROBE: result_card},
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        },
    )

    result = _runner(commands, lease_dir=tmp_path).extract_structured_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["card_count"] == 1
    card = payload["cards"][0]
    assert card["ref"] == "333"
    assert card["education_level"] == "硕士"
    assert card["work_years"] == 10
    assert card["job_intention"] == "Data Analyst"
    assert "visible_text" not in card
    assert "normalized_card_text" not in card


def test_open_liepin_tab_creates_background_opencli_tab_without_current_window_opener(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): ["[]", "[]"],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "unbind") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "bind") not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    owned_page = owned_pages["page-background-search"]
    assert owned_page["session"] == "seektalent-liepin"
    assert owned_page["page_id"] == "page-background-search"
    assert owned_page["url"] == liepin_url
    assert isinstance(owned_page["opened_at"], int | float)
    assert "runtime_run_id" in owned_page
    assert "source_lane_run_id" in owned_page
    assert isinstance(owned_page["owner_nonce"], str) and owned_page["owner_nonce"]
    assert lease["owner_nonce"] == owned_page["owner_nonce"]


def test_open_liepin_tab_unbinds_and_retries_when_initial_background_tab_new_is_policy_blocked(
    tmp_path: Path,
) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    blocked_tab_new = subprocess.CalledProcessError(
        1,
        ["opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url],
        output='{"error":{"code":"bound_tab_mutation_blocked"}}',
        stderr='Session "seektalent-liepin" is bound to a user tab.\n',
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): [
                blocked_tab_new,
                json.dumps({"page": "page-background-search", "url": liepin_url}),
            ],
            ("opencli", "browser", "seektalent-liepin", "unbind"): "",
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"


def test_open_liepin_tab_ignores_active_user_tab_and_opens_background_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"


def test_open_liepin_tab_does_not_read_current_url_before_background_open(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "get", "url") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls


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
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-stale-search"): blocked_select,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                '[{"page":"page-local-ui","url":"http://127.0.0.1:8123/sessions/session-bad","active":true}]',
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
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
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "bind") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "unbind") not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"


def test_open_liepin_tab_accepts_canonical_background_search_url(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    canonical_url = "https://h.liepin.com/search/getConditionItem?from=redirect#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": canonical_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"


def test_open_liepin_detail_accepts_canonical_background_detail_url(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc"
    canonical_detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&index=0"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): (
                json.dumps({"page": "page-background-detail", "url": canonical_detail_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener)

    page_id = runner._open_new_liepin_tab(url=detail_url, source_run_id="source-1")

    assert page_id == "page-background-detail"
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "open", detail_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url) in commands.calls

def test_open_liepin_tab_reopens_background_tab_when_owned_lease_is_policy_blocked(tmp_path: Path) -> None:
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
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
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
        ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"


def test_open_liepin_tab_recovers_from_lease_select_blocked_on_workbench_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
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
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-bound-search"): blocked_select,
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
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
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "bind") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "unbind") not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"


def test_open_liepin_tab_does_not_reuse_active_user_search_tab_without_marker(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    update_notice = "\n\n  Update available: v1.8.0 -> v1.8.1\n  Run: npm install -g @jackwener/opencli\n"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-user-search","url":"{liepin_url}","active":true}}]{update_notice}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "open", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "bind") not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"

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
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-search-fresh","url":"{liepin_url}","active":true}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
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
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "bind") not in commands.calls

def test_open_liepin_tab_uses_background_tab_without_overwriting_active_non_liepin_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()

    result = _runner(commands, lease_dir=tmp_path, current_tab_opener=current_tab_opener).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert ("opencli", "browser", "seektalent-liepin", "bind") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) in commands.calls
    assert all(call[3] != "open" for call in commands.calls)

def test_open_liepin_tab_does_not_count_or_bind_foreground_windows(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(
        commands,
        lease_dir=tmp_path,
        current_tab_opener=current_tab_opener,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url),
    ]

def test_open_liepin_tab_writes_lease_for_background_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (
                f'[{{"page":"page-current-window-search","url":"{liepin_url}","active":true}}]'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                json.dumps({"page": "page-background-search", "url": liepin_url})
            ),
        }
    )
    current_tab_opener = FakeCurrentChromeTabOpener()
    runner = _runner(
        commands,
        lease_dir=tmp_path,
        current_tab_opener=current_tab_opener,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert current_tab_opener.calls == []
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-background-search"

def test_state_classifier_does_not_block_search_page_for_generic_risk_text() -> None:
    text = "找简历\n<input id=riskControl type=hidden />\n<script>window.risk = window.riskScore = 0</script>"

    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text=text) is None
