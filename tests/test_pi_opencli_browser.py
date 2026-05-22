from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from seektalent.providers.pi_agent import opencli_browser_cli
from seektalent.providers.pi_agent.opencli_browser import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserRunner,
    bucket_text,
    classify_liepin_state,
    default_liepin_opencli_policy,
    extract_allowed_click_refs,
    extract_known_modal_close_ref,
    extract_liepin_card_summaries,
    extract_liepin_search_input_ref,
)


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

    def run(self, argv: Sequence[str], *, timeout: int) -> str:
        del timeout
        call = tuple(argv)
        self.calls.append(call)
        if self.fail:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=1)
        output = self.outputs.get(call, "{}")
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

    def close_blank_window(self) -> bool:
        self.calls += 1
        return True


def _runner(
    commands: FakeCommands,
    *,
    allowed_click_refs: tuple[str, ...] = (),
    lease_dir: Path | None = None,
    idle_close_seconds: int = 120,
    close_blank_window: bool = True,
    blank_window_closer: FakeBlankWindowCloser | None = None,
) -> OpenCliBrowserRunner:
    return OpenCliBrowserRunner(
        config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-liepin",
            timeout_seconds=10,
            policy=default_liepin_opencli_policy(
                allowed_hosts=("www.liepin.com", "h.liepin.com"),
                allowed_start_urls=("https://h.liepin.com/search/getConditionItem#session",),
            ),
            allowed_click_refs=allowed_click_refs,
            lease_dir=lease_dir,
            artifact_root=lease_dir,
            idle_close_seconds=idle_close_seconds,
            close_blank_window=close_blank_window,
            cleanup_worker_enabled=False,
        ),
        commands=commands,
        window_counter=FakeWindowCounter(),
        blank_window_closer=blank_window_closer,
    )


def test_status_maps_opencli_doctor_success() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): (
                "Daemon: running (PID 123)\n"
                "Version: 1.8.0\n"
                "Extension: connected (v1.8.0)\n"
                "Profiles: default v1.8.0\n"
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
    assert result.safe_reason_code == "liepin_opencli_extension_disconnected"
    assert commands.calls == [("opencli", "daemon", "status")]
    assert all("doctor" not in call for call in commands.calls for call in call)


def test_status_blocks_when_extension_is_disconnected() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): (
                "Daemon: running (PID 123)\n"
                "Version: 1.8.0\n"
                "Extension: disconnected\n"
            )
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


def test_open_liepin_tab_creates_lease_tab_then_selects_it(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(
        "https://h.liepin.com/search/getConditionItem#session"
    )

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-1"
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    assert owned_pages["page-1"]["session"] == "seektalent-liepin"
    assert owned_pages["page-1"]["page_id"] == "page-1"
    assert owned_pages["page-1"]["url"] == "https://h.liepin.com/search/getConditionItem#session"
    assert isinstance(owned_pages["page-1"]["opened_at"], int | float)
    assert "runtime_run_id" in owned_pages["page-1"]
    assert "source_lane_run_id" in owned_pages["page-1"]
    assert isinstance(owned_pages["page-1"]["owner_nonce"], str) and owned_pages["page-1"]["owner_nonce"]


def test_open_liepin_tab_closes_existing_owned_lease_before_opening_next_tab(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-0"): '{"closed":"page-0"}',
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
        }
    )
    (tmp_path / "seektalent-liepin.json").write_text(
        json.dumps({"page_id": "page-0", "last_activity_at": 9_999_999_999}),
        encoding="utf-8",
    )
    blank_window_closer = FakeBlankWindowCloser()

    result = _runner(commands, lease_dir=tmp_path, blank_window_closer=blank_window_closer).open_liepin_tab(
        "https://h.liepin.com/search/getConditionItem#session"
    )

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-0"),
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"),
    ]
    assert blank_window_closer.calls == 1
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-1"


def test_open_liepin_tab_does_not_bind_or_overwrite_user_tab(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(
        "https://h.liepin.com/search/getConditionItem#session"
    )

    assert result.ok is True
    assert all(call[3] not in {"bind", "open"} for call in commands.calls)


def test_open_liepin_tab_allows_owned_window_creation_for_lease(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
        }
    )
    runner = OpenCliBrowserRunner(
        config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-liepin",
            timeout_seconds=10,
            policy=default_liepin_opencli_policy(
                allowed_hosts=("www.liepin.com", "h.liepin.com"),
                allowed_start_urls=("https://h.liepin.com/search/getConditionItem#session",),
            ),
            lease_dir=tmp_path,
            cleanup_worker_enabled=False,
        ),
        commands=commands,
        window_counter=FakeWindowCounter((1, 1, 1, 2, 2, 2)),
    )

    result = runner.open_liepin_tab("https://h.liepin.com/search/getConditionItem#session")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"),
    ]


def test_open_liepin_tab_allows_opencli_owned_window_for_idle_cleanup(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
        }
    )
    runner = OpenCliBrowserRunner(
        config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-liepin",
            timeout_seconds=10,
            policy=default_liepin_opencli_policy(
                allowed_hosts=("www.liepin.com", "h.liepin.com"),
                allowed_start_urls=("https://h.liepin.com/search/getConditionItem#session",),
            ),
            lease_dir=tmp_path,
            cleanup_worker_enabled=False,
        ),
        commands=commands,
        window_counter=FakeWindowCounter((1, 1, 1, 2, 2, 3)),
    )

    result = runner.open_liepin_tab("https://h.liepin.com/search/getConditionItem#session")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"),
    ]


def test_open_liepin_tab_rejects_malformed_page_id(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"bad/page"}'
            ),
        }
    )

    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands, lease_dir=tmp_path).open_liepin_tab("https://h.liepin.com/search/getConditionItem#session")

    assert error.value.safe_reason_code == "liepin_opencli_malformed_state"


def test_cleanup_idle_lease_closes_target_and_blank_window(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-1"): '{"closed":"page-1"}',
        }
    )
    blank_window_closer = FakeBlankWindowCloser()
    lease_path = tmp_path / "seektalent-liepin.json"
    lease_path.write_text(
        json.dumps({"page_id": "page-1", "last_activity_at": 1}),
        encoding="utf-8",
    )

    result = _runner(commands, lease_dir=tmp_path, blank_window_closer=blank_window_closer).cleanup_idle_lease(force=True)

    assert result.ok is True
    assert result.counts == {"leases": 1, "closed": 1, "blankWindows": 1}
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "tab", "close", "page-1")]
    assert blank_window_closer.calls == 1
    assert not lease_path.exists()


def test_cleanup_idle_lease_forgets_owned_page_marker(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-1"): '{"closed":"page-1"}',
        }
    )
    runner = _runner(commands, lease_dir=tmp_path, close_blank_window=False)
    runner._write_owned_page_marker(
        page_id="page-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-owned-1",
        opened_at=9_999_999_999.0,
    )
    (tmp_path / "seektalent-liepin.json").write_text(
        json.dumps({"page_id": "page-1", "last_activity_at": 1}),
        encoding="utf-8",
    )

    result = runner.cleanup_idle_lease(force=True)

    assert result.ok is True
    assert not (tmp_path / "seektalent-liepin-owned-pages.json").exists()


def test_cleanup_orphaned_owned_tabs_closes_liepin_tabs_without_lease(tmp_path: Path) -> None:
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
    assert result.counts == {"leases": 0, "closedTabs": 1, "blankWindows": 0}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-owned-1") in commands.calls
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
    assert result.counts == {"leases": 0, "closedTabs": 0, "blankWindows": 0}
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

    assert error.value.safe_reason_code == "liepin_opencli_malformed_state"
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-owned-1") not in commands.calls


def test_open_liepin_tab_quarantines_malformed_owned_marker_and_writes_fresh_marker(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-2"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-2"): "{}",
        }
    )
    marker_path = tmp_path / "seektalent-liepin-owned-pages.json"
    marker_path.write_text("{not-json", encoding="utf-8")

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(
        "https://h.liepin.com/search/getConditionItem#session"
    )

    assert result.ok is True
    owned_pages = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(owned_pages) == {"page-2"}
    assert owned_pages["page-2"]["url"] == "https://h.liepin.com/search/getConditionItem#session"
    assert list(tmp_path.glob("seektalent-liepin-owned-pages.json.malformed-*"))


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
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}'
        }
    )

    result = _runner(commands).click(target="搜索")

    assert result.ok is True
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")]


def test_click_allows_state_derived_ref_target() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}'
        }
    )

    result = _runner(commands, allowed_click_refs=("16",)).click(target="16")

    assert result.ok is True
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")]


def test_click_allows_state_derived_ref_marker() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}'
        }
    )

    result = _runner(commands, allowed_click_refs=("16",)).click(target="ref=16")

    assert result.ok is True
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")]


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


def test_restricted_command_shape_rejects_forbidden_click_target() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands)._run_browser_command("click", ("联系候选人",))

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_public_payload_does_not_include_raw_output() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )

    result = _runner(commands).state()

    payload = result.to_public_payload()
    assert payload == {"ok": True, "action": "state", "safeReasonCode": "configured", "counts": {}}
    assert "搜索职位" not in json.dumps(payload, ensure_ascii=False)


def test_state_rejects_sensitive_observation() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "document.cookie=secret",
        }
    )

    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).state()

    assert error.value.safe_reason_code == "liepin_opencli_malformed_state"


def test_state_classifier_blocks_login_and_risk_pages_before_next_action() -> None:
    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text="请登录后继续") == (
        "liepin_opencli_login_required"
    )
    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text="安全验证 请完成验证码") == (
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
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://www.liepin.com/resume/detail/123"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): "raw detail resume text",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_unknown_modal"
    assert result.to_pi_tool_payload()["observation"] == {
        "text": "",
        "chars": 0,
        "truncated": False,
        "terminal": True,
    }
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "get", "url")]


def test_state_returns_terminal_classification_to_pi_payload_only() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "请登录后继续 [ref=login]",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_login_required"
    pi_payload = result.to_pi_tool_payload()
    assert pi_payload["observation"]["terminal"] is True
    public_payload = result.to_public_payload()
    assert "请登录" not in json.dumps(public_payload, ensure_ascii=False)


def test_state_returns_bounded_observation_to_pi_only() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )

    result = _runner(commands).state()

    pi_payload = result.to_pi_tool_payload()
    public_payload = result.to_public_payload()
    assert pi_payload["observation"]["text"] == "搜索职位、公司 [ref=16]"
    assert pi_payload["observation"]["terminal"] is False
    assert "搜索职位" not in json.dumps(public_payload, ensure_ascii=False)


def test_state_exposes_only_safe_click_refs_to_pi() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
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
    assert result.to_pi_tool_payload()["observation"]["allowedClickRefs"] == ("16", "next", "29")
    assert "allowedClickRefs" not in result.to_public_payload()


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
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [state_before, state_after],
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

    assert envelope["schema_version"] == "seektalent.pi_liepin_cards.v1"
    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    assert envelope["cards"][0]["safe_card_summary"]["current_or_recent_company"] == "海光集成电路"
    assert envelope["cards"][0]["safe_card_summary"]["current_or_recent_title"] == "高级主管工程师"
    assert envelope["cards"][0]["safe_card_summary"]["work_years"] == 14
    assert envelope["cards"][0]["safe_card_summary_ref"].startswith("artifact://public-summary/pi-card/run-1/")
    assert (tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").is_file()
    assert (tmp_path / "public-summary" / "pi-card" / "run-1" / "1.json").is_file()
    assert commands.calls[:3] == [
        ("opencli", "browser", "seektalent-liepin", "unbind"),
        ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"),
    ]
    assert (
        "opencli",
        "browser",
        "seektalent-liepin",
        "fill",
        "26",
        "数据开发专家",
    ) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索") in commands.calls
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
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    )
    assert commands.calls[fill_index + 1 : click_index] == []


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
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
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
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
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
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
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


def test_search_liepin_cards_retries_repeated_transient_fill_status(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, search_state, search_state, result_state],
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
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
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
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
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
    assert payload["counts"] == {"blankWindows": 0, "closedTabs": 0, "leases": 0}


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

    assert runner._config.command == ("/tmp/open cli", "--profile", "qa user")


def test_cli_runner_reads_state_derived_click_refs_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_CLICK_REFS_JSON", '["16","next"]')

    runner = opencli_browser_cli._runner_from_env()

    assert runner._config.allowed_click_refs == ("16", "next")


def test_cli_runner_reads_idle_cleanup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR", str(tmp_path))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS", "3")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW", "false")

    runner = opencli_browser_cli._runner_from_env()

    assert runner._config.lease_dir == tmp_path
    assert runner._config.idle_close_seconds == 3
    assert runner._config.close_blank_window is False
