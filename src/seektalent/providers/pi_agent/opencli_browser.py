from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse


ALLOWED_BROWSER_COMMANDS = frozenset(
    {"bind", "unbind", "open", "state", "get", "find", "click", "fill", "scroll", "wait", "tab"}
)
FORBIDDEN_BROWSER_COMMANDS = frozenset({"eval", "network", "upload", "console", "dialog", "drag", "select"})
LIEPIN_ALLOWED_HOSTS = frozenset({"www.liepin.com", "h.liepin.com", "c.liepin.com", "lpt.liepin.com"})
LIEPIN_RECRUITER_SEARCH_URL = "https://h.liepin.com/search/getConditionItem#session"
OWNED_PAGE_MARKER_TTL_SECONDS = 24 * 60 * 60
FORBIDDEN_LIEPIN_PATH_FRAGMENTS = frozenset(
    {
        "resume",
        "detail",
        "contact",
        "chat",
        "download",
        "payment",
        "pay",
    }
)
FORBIDDEN_ACTION_TARGET_FRAGMENTS = frozenset(
    {
        "查看完整简历",
        "完整简历",
        "简历详情",
        "查看简历",
        "打开简历",
        "下载简历",
        "联系",
        "聊天",
        "沟通",
        "下载",
        "付费",
        "购买",
        "电话",
        "手机",
        "邮箱",
        "消息",
        "账号",
        "账户",
        "设置",
        "resume detail",
        "detail",
        "contact",
        "chat",
        "download",
        "payment",
        "phone",
        "email",
        "message",
        "account",
        "settings",
    }
)
ACCESSIBILITY_NOISE_TOKENS = frozenset(
    {
        "a",
        "aria-label",
        "button",
        "combobox",
        "div",
        "down",
        "img",
        "input",
        "role",
        "span",
        "svg",
        "tabindex",
        "table",
        "title",
    }
)
ALLOWED_CLICK_TARGET_FRAGMENTS = frozenset(
    {
        "搜索",
        "搜 索",
        "查询",
        "下一页",
        "下页",
        "next",
    }
)


class OpenCliCommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: int) -> str: ...


class ChromeWindowCounter(Protocol):
    def count(self) -> int | None: ...


class BlankChromeWindowCloser(Protocol):
    def close_blank_window(self) -> bool: ...


@dataclass(frozen=True)
class SubprocessOpenCliCommandRunner:
    def run(self, argv: Sequence[str], *, timeout: int) -> str:
        completed = subprocess.run(
            list(argv),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.stdout


@dataclass(frozen=True)
class SubprocessChromeWindowCounter:
    def count(self) -> int | None:
        try:
            completed = subprocess.run(
                ("osascript", "-e", 'tell application "Google Chrome" to get count of windows'),
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        try:
            return int(completed.stdout.strip())
        except ValueError:
            return None


@dataclass(frozen=True)
class SubprocessBlankChromeWindowCloser:
    def close_blank_window(self) -> bool:
        script = '''
tell application "Google Chrome"
  repeat with w in windows
    if (count of tabs of w) = 1 and (URL of active tab of w) is "about:blank" then
      close w
      return "closed"
    end if
  end repeat
  return "none"
end tell
'''
        try:
            completed = subprocess.run(
                ("osascript", "-e", script),
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
        return completed.stdout.strip() == "closed"


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
    idle_close_seconds: int = 120
    close_blank_window: bool = True
    cleanup_worker_enabled: bool = True


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


def bucket_text(text: str) -> dict[str, int]:
    return {"chars": len(text)}


def build_observation(text: str, *, max_chars: int = 12_000) -> dict[str, object]:
    if _looks_sensitive(text):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    observation: dict[str, object] = {
        "text": text[:max_chars],
        "chars": len(text),
        "truncated": len(text) > max_chars,
    }
    refs = extract_allowed_click_refs(text)
    if refs:
        observation["allowedClickRefs"] = refs
    return observation


def extract_allowed_click_refs(text: str) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        normalized = " ".join(line.strip().lower().split())
        if not normalized:
            continue
        lookahead = " ".join(lines[index + 1 : index + 3]).lower()
        candidate_text = f"{normalized} {lookahead}"
        if any(fragment in normalized for fragment in FORBIDDEN_ACTION_TARGET_FRAGMENTS):
            continue
        if not _has_allowed_click_label(candidate_text):
            continue
        for ref in _extract_refs_from_line(line):
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)
    return tuple(refs)


def extract_liepin_search_input_ref(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "包含全部关键词" not in line:
            continue
        for nearby in lines[index + 1 : index + 20]:
            if "role=combobox" not in nearby or "<input" not in nearby:
                continue
            refs = _extract_refs_from_line(nearby)
            if refs:
                return refs[0]
    for line in lines:
        if "role=combobox" not in line or "<input" not in line or "id=rc_select_1" not in line:
            continue
        refs = _extract_refs_from_line(line)
        if refs:
            return refs[0]
    return None


def extract_known_modal_close_ref(text: str) -> str | None:
    if "新增人才" not in text and "新增人选" not in text:
        return None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"\[\w+\]<a[^>]*>\s*X\s*</a>", line):
            continue
        nearby = "\n".join(lines[index : index + 12])
        if "新增人才" in nearby or "新增人选" in nearby:
            refs = _extract_refs_from_line(line)
            if refs:
                return refs[0]
    return None


def classify_liepin_state(*, url: str, text: str) -> str | None:
    host = urlparse(url).hostname or ""
    lowered = text.lower()
    if host not in LIEPIN_ALLOWED_HOSTS:
        return "liepin_opencli_host_blocked"
    if _is_forbidden_liepin_url(url):
        return "liepin_opencli_unknown_modal"
    if host == "lpt.liepin.com" and ("身份" in text or "请选择" in text):
        return "liepin_opencli_identity_intercept"
    if _looks_like_login_required(text):
        return "liepin_opencli_login_required"
    if "验证码" in text or "安全验证" in text or re.search(r"\b(?:risk|captcha)\b", lowered):
        return "liepin_opencli_risk_page"
    if any(marker in text for marker in ("联系候选人", "查看联系方式", "聊天弹窗", "下载简历", "付费查看", "购买套餐")):
        return "liepin_opencli_unknown_modal"
    return None


def extract_liepin_card_summaries(text: str, *, max_cards: int) -> tuple[dict[str, object], ...]:
    if _looks_sensitive(text):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    lines = _clean_state_lines(text)
    cards: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not _looks_like_liepin_card_start(line):
            continue
        block_lines = lines[index : index + 12]
        block = "\n".join(block_lines)
        if not _looks_like_liepin_card(block):
            continue
        summary = _safe_card_summary_from_block(block)
        normalized_card_text = str(summary["normalized_card_text"])
        fingerprint = hashlib.sha256(normalized_card_text.encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        cards.append(summary)
        if len(cards) >= max_cards:
            break
    return tuple(cards)


def _looks_like_liepin_card_start(line: str) -> bool:
    return bool(re.search(r"\b\d{2}\s*岁\b|工作\s*\d+\s*年|\d+\s*年经验", line))


def _clean_state_lines(text: str) -> list[str]:
    result: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\[[^\]]+\]", "", raw_line)
        line = re.sub(r"<[^>]*>", " ", line)
        line = re.sub(r"\b(?:aria-label|role|tabindex|title)\s*=\s*[^\s]+", " ", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+", " ", line).strip(" ·|")
        line = _drop_accessibility_noise_tokens(line)
        if not line or len(line) > 240:
            continue
        if line in result[-2:]:
            continue
        result.append(line)
    return result


def _looks_like_liepin_card(block: str) -> bool:
    if any(marker in block for marker in ("筛选", "搜索职位", "搜索公司", "高级搜索", "登录", "验证码")):
        return False
    has_profile_fact = bool(re.search(r"\b\d{2}\s*岁\b|工作\s*\d+\s*年|\d+\s*年经验", block))
    has_role = bool("求职期望" in block or "·" in block or re.search(r"\d{4}[./-]\d{2}", block))
    has_education = any(marker in block for marker in ("本科", "硕士", "博士", "大专", "统招"))
    return has_profile_fact and has_role and has_education


def _safe_card_summary_from_block(block: str) -> dict[str, object]:
    normalized_block = _bounded_public_text(block, max_chars=900)
    company, title = _company_title_from_block(block)
    job_intention = _job_intention_from_block(block)
    work_years = _int_match(block, r"工作\s*(\d+)\s*年|(\d+)\s*年经验")
    age = _int_match(block, r"(\d{2})\s*岁")
    city = _city_from_block(block)
    education = _education_from_block(block)
    school_names = _school_names_from_block(block)
    skill_tags = _skill_tags_from_block(block)
    display_title = title or job_intention or "Liepin candidate card"
    return {
        "display_name_masked": _has_masked_name(block),
        "display_title": display_title,
        "current_or_recent_company": company,
        "current_or_recent_title": title,
        "work_years": work_years,
        "age": age,
        "city": city,
        "expected_city": _expected_city_from_block(block) or city,
        "education_level": education,
        "school_names": school_names,
        "major_names": [],
        "skill_tags": skill_tags,
        "job_intention": job_intention,
        "recent_experience_text": _recent_experience_from_block(block),
        "normalized_card_text": normalized_block,
    }


def _company_title_from_block(block: str) -> tuple[str | None, str | None]:
    for line in block.splitlines():
        match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9()（）&·\-]{2,40})\s*·\s*([^·\n]{2,40})", line)
        if match:
            company = _bounded_public_text(match.group(1), max_chars=60)
            title = _bounded_public_text(re.split(r"\s+\d{4}[./-]", match.group(2))[0], max_chars=80)
            return company, title
    return None, None


def _job_intention_from_block(block: str) -> str | None:
    match = re.search(r"求职期望[:：]\s*([^\n]+)", block)
    if not match:
        return None
    text = match.group(1).strip()
    parts = re.split(r"\s+", text)
    if len(parts) >= 2:
        text = parts[-1]
    return _bounded_public_text(text, max_chars=80)


def _recent_experience_from_block(block: str) -> str | None:
    for line in block.splitlines():
        if "·" in line and re.search(r"\d{4}[./-]\d{2}", line):
            return _bounded_public_text(line, max_chars=180)
    return None


def _expected_city_from_block(block: str) -> str | None:
    match = re.search(r"求职期望[:：]\s*([\u4e00-\u9fa5]{2,8})", block)
    if match:
        return match.group(1)
    return None


def _city_from_block(block: str) -> str | None:
    for city in ("上海", "北京", "深圳", "广州", "杭州", "南京", "苏州", "成都", "武汉", "西安"):
        if city in block:
            return city
    return None


def _education_from_block(block: str) -> str | None:
    for education in ("博士", "硕士", "本科", "大专"):
        if education in block:
            return education
    return None


def _school_names_from_block(block: str) -> list[str]:
    schools: list[str] = []
    for match in re.finditer(r"([\u4e00-\u9fa5]{2,24}(?:大学|学院))", block):
        school = match.group(1)
        if school not in schools:
            schools.append(school)
    return schools[:3]


def _skill_tags_from_block(block: str) -> list[str]:
    tags: list[str] = []
    for token in re.findall(r"\b[A-Za-z][A-Za-z0-9+#./-]{1,20}\b", block):
        if token.lower() in {"staff", *ACCESSIBILITY_NOISE_TOKENS}:
            continue
        if token not in tags:
            tags.append(token)
    return tags[:12]


def _drop_accessibility_noise_tokens(text: str) -> str:
    tokens = text.split()
    while tokens and tokens[0].lower() in ACCESSIBILITY_NOISE_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1].lower() in ACCESSIBILITY_NOISE_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _has_masked_name(block: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z][*＊]{1,3}|[*＊][\u4e00-\u9fa5A-Za-z]", block))


def _int_match(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group)
    return int(match.group(1))


def _bounded_public_text(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if _looks_sensitive(cleaned):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    return cleaned[:max_chars]


def _safe_artifact_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return segment[:80] or "run"


def _is_forbidden_liepin_url(url: str) -> bool:
    parsed = urlparse(url)
    path = unquote(parsed.path or "").lower()
    return any(fragment in path for fragment in FORBIDDEN_LIEPIN_PATH_FRAGMENTS)


def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    forbidden = (
        "document.cookie",
        "localstorage",
        "sessionstorage",
        "authorization:",
        "bearer ",
        "storagestate",
        "<script",
        "<html",
    )
    return any(marker in lowered for marker in forbidden)


def _looks_like_login_required(text: str) -> bool:
    lowered = text.lower()
    if "login required" in lowered or "sign in required" in lowered:
        return True
    login_markers = (
        "请登录",
        "登录后继续",
        "登录后查看",
        "登录后使用",
        "未登录",
        "扫码登录",
        "密码登录",
        "账号登录",
        "立即登录",
        "登录/注册",
    )
    return any(marker in text for marker in login_markers)


def _has_allowed_click_label(text: str) -> bool:
    return any(fragment in text for fragment in ALLOWED_CLICK_TARGET_FRAGMENTS)


class OpenCliBrowserRunner:
    def __init__(
        self,
        *,
        config: OpenCliBrowserConfig,
        commands: OpenCliCommandRunner | None = None,
        window_counter: ChromeWindowCounter | None = None,
        blank_window_closer: BlankChromeWindowCloser | None = None,
    ) -> None:
        self._config = config
        self._commands = commands or SubprocessOpenCliCommandRunner()
        self._window_counter = window_counter or SubprocessChromeWindowCounter()
        self._blank_window_closer = blank_window_closer or SubprocessBlankChromeWindowCloser()

    def status(self) -> OpenCliBrowserResult:
        try:
            output = self._run(tuple(self._config.command) + ("daemon", "status"))
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="status", safe_reason_code=exc.safe_reason_code)
        if "Daemon: running" not in output or "Extension: connected" not in output:
            return OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_extension_disconnected",
                private_output=output,
            )
        return OpenCliBrowserResult(ok=True, action="status")

    def open_liepin_tab(self, url: str) -> OpenCliBrowserResult:
        self._validate_start_url(url)
        self.cleanup_idle_lease(force=True)
        self._unbind_current_session()
        output = self._run_browser_command("tab", ("new", url))
        page_id = _parse_page_id(output)
        self._run_browser_command("tab", ("select", page_id))
        self._write_lease(page_id=page_id, url=url)
        self._write_owned_page_marker(
            page_id=page_id,
            url=url,
            runtime_run_id=None,
            source_lane_run_id=None,
            owner_nonce=uuid.uuid4().hex,
        )
        self._launch_idle_cleanup_worker()
        return OpenCliBrowserResult(ok=True, action="open_liepin_tab", private_output=output)

    def state(self) -> OpenCliBrowserResult:
        current_url = self._current_url()
        url_terminal_reason = classify_liepin_state(url=current_url, text="")
        if url_terminal_reason:
            observation = build_observation("")
            observation["terminal"] = True
            return OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code=url_terminal_reason,
                observation=observation,
            )
        output = self._run_browser_command("state", ())
        observation = build_observation(output)
        terminal_reason = classify_liepin_state(url=current_url, text=output)
        observation["terminal"] = terminal_reason is not None
        if terminal_reason:
            return OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code=terminal_reason,
                observation=observation,
                private_output=output,
            )
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="state", observation=observation, private_output=output)

    def get_url(self) -> OpenCliBrowserResult:
        output = self._run_browser_command("get", ("url",))
        self._touch_lease()
        return OpenCliBrowserResult(
            ok=True,
            action="get_url",
            observation=build_observation(output),
            private_output=output,
        )

    def find(self, *, query: str) -> OpenCliBrowserResult:
        self._validate_keyword_text(query)
        output = self._run_browser_command("find", (query,))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="find", observation=build_observation(output), private_output=output)

    def fill(self, *, target: str, text: str) -> OpenCliBrowserResult:
        self._validate_action_target(target)
        self._validate_keyword_text(text)
        output = self._run_browser_command("fill", self._fill_args_for_target(target=target, text=text))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="fill", counts=bucket_text(text), private_output=output)

    def click(self, *, target: str) -> OpenCliBrowserResult:
        self._validate_click_target(target)
        output = self._run_browser_command("click", self._click_args_for_target(target))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="click", private_output=output)

    def scroll(self, *, direction: str) -> OpenCliBrowserResult:
        if direction not in {"up", "down"}:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        output = self._run_browser_command("scroll", (direction,))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="scroll", private_output=output)

    def wait_time(self, *, seconds: int) -> OpenCliBrowserResult:
        if seconds < 1 or seconds > 10:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        output = self._run_browser_command("wait", ("time", str(seconds)))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="wait_time", private_output=output)

    def search_liepin_cards(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
    ) -> dict[str, object]:
        safe_run_id = _safe_artifact_segment(source_run_id)
        events: list[dict[str, object]] = []
        pages_visited = 0
        try:
            self._validate_keyword_text(query)
            events.append({"action_kind": "open_search", "route_kind": "search"})
            opened = self.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)
            if not opened.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=opened.safe_reason_code,
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            pages_visited = 1
            events.append({"action_kind": "wait_search_ready", "route_kind": "search"})
            self.wait_time(seconds=3)
            first_state = self.state()
            events.append({"action_kind": "observe", "route_kind": "search", "ok": first_state.ok})
            if (
                not first_state.ok
                and first_state.safe_reason_code in {"liepin_opencli_risk_page", "liepin_opencli_status_unavailable"}
            ):
                events.append(
                    {
                        "action_kind": "observe_retry_after_unready",
                        "route_kind": "search",
                        "safe_reason_code": first_state.safe_reason_code,
                    }
                )
                self.wait_time(seconds=2)
                first_state = self.state()
                events.append({"action_kind": "observe_after_unready_retry", "route_kind": "search", "ok": first_state.ok})
            if not first_state.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=first_state.safe_reason_code,
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
            )
            first_state_text = first_state.private_output or str(first_state.observation.get("text") or "")
            modal_close_ref = extract_known_modal_close_ref(first_state_text)
            if modal_close_ref is not None:
                events.append({"action_kind": "close_known_modal", "route_kind": "search"})
                self._click_known_modal_close_ref(modal_close_ref)
                self.wait_time(seconds=1)
                first_state = self.state()
                events.append({"action_kind": "observe_after_modal_close", "route_kind": "search", "ok": first_state.ok})
                if not first_state.ok:
                    return self._blocked_cards_envelope(
                        source_run_id=source_run_id,
                        query=query,
                        safe_reason_code=first_state.safe_reason_code,
                        safe_run_id=safe_run_id,
                        pages_visited=pages_visited,
                        events=events,
                    )
                first_state_text = first_state.private_output or str(first_state.observation.get("text") or "")
            events.append({"action_kind": "fill_search", "route_kind": "search", "chars": len(query)})
            search_input_ref = extract_liepin_search_input_ref(first_state_text)
            fill_target = search_input_ref or "搜索"
            for attempt_index in range(3):
                try:
                    self.fill(target=fill_target, text=query)
                    break
                except OpenCliBrowserError as exc:
                    if exc.safe_reason_code != "liepin_opencli_status_unavailable" or attempt_index == 2:
                        raise
                    events.append({"action_kind": "fill_search_retry", "route_kind": "search", "chars": len(query)})
                    self.wait_time(seconds=2)
                    retry_state = self.state()
                    events.append(
                        {"action_kind": "observe_before_fill_retry", "route_kind": "search", "ok": retry_state.ok}
                    )
                    if not retry_state.ok:
                        return self._blocked_cards_envelope(
                            source_run_id=source_run_id,
                            query=query,
                            safe_reason_code=retry_state.safe_reason_code,
                            safe_run_id=safe_run_id,
                            pages_visited=pages_visited,
                            events=events,
                        )
                    retry_state_text = retry_state.private_output or str(retry_state.observation.get("text") or "")
                    modal_close_ref = extract_known_modal_close_ref(retry_state_text)
                    if modal_close_ref is not None:
                        events.append({"action_kind": "close_known_modal_before_fill_retry", "route_kind": "search"})
                        self._click_known_modal_close_ref(modal_close_ref)
                        self.wait_time(seconds=1)
                        retry_state = self.state()
                        events.append(
                            {
                                "action_kind": "observe_after_retry_modal_close",
                                "route_kind": "search",
                                "ok": retry_state.ok,
                            }
                        )
                        if not retry_state.ok:
                            return self._blocked_cards_envelope(
                                source_run_id=source_run_id,
                                query=query,
                                safe_reason_code=retry_state.safe_reason_code,
                                safe_run_id=safe_run_id,
                                pages_visited=pages_visited,
                                events=events,
                            )
                        retry_state_text = retry_state.private_output or str(retry_state.observation.get("text") or "")
                    retry_input_ref = extract_liepin_search_input_ref(retry_state_text)
                    fill_target = retry_input_ref or fill_target
            events.append({"action_kind": "click_search", "route_kind": "search"})
            self.click(target="搜索")
            self.wait_time(seconds=3)
            final_state = self.state()
            events.append({"action_kind": "observe_results", "route_kind": "search", "ok": final_state.ok})
            if not final_state.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=final_state.safe_reason_code,
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            state_text = final_state.private_output
            cards = extract_liepin_card_summaries(state_text, max_cards=max_cards)
            return self._cards_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_run_id=safe_run_id,
                pages_visited=pages_visited,
                events=events,
                state_text=state_text,
                cards=cards,
            )
        except OpenCliBrowserError as exc:
            return self._blocked_cards_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_reason_code=exc.safe_reason_code,
                safe_run_id=safe_run_id,
                pages_visited=pages_visited,
                events=events,
            )

    def _blocked_cards_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str,
        safe_run_id: str,
        pages_visited: int,
        events: list[dict[str, object]],
    ) -> dict[str, object]:
        action_trace_ref = self._write_pi_artifact(
            "protected",
            f"pi-trace/{safe_run_id}/action-trace.json",
            {
                "schema_version": "seektalent.opencli_action_trace.v1",
                "mode": "card",
                "source": "liepin",
                "safe_reason_code": safe_reason_code,
                "events": events,
            },
        )
        return {
            "schema_version": "seektalent.pi_liepin_cards.v1",
            "status": "blocked",
            "stop_reason": "blocked_backend_unavailable",
            "safe_reason_code": safe_reason_code,
            "source_run_id": source_run_id,
            "query": query,
            "cards_seen": 0,
            "cards_returned": 0,
            "pages_visited": pages_visited,
            "action_trace_ref": action_trace_ref,
            "safe_summary_refs": [],
            "protected_snapshot_refs": [],
            "cards": [],
        }

    def _cards_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_run_id: str,
        pages_visited: int,
        events: list[dict[str, object]],
        state_text: str,
        cards: tuple[dict[str, object], ...],
    ) -> dict[str, object]:
        action_trace_ref = self._write_pi_artifact(
            "protected",
            f"pi-trace/{safe_run_id}/action-trace.json",
            {
                "schema_version": "seektalent.opencli_action_trace.v1",
                "mode": "card",
                "source": "liepin",
                "events": events,
                "cards_seen": len(cards),
            },
        )
        page_snapshot_ref = self._write_pi_artifact(
            "protected",
            f"pi-page/{safe_run_id}/search-state.json",
            {"schema_version": "seektalent.opencli_state_snapshot.v1", "chars": len(state_text)},
        )
        envelope_cards: list[dict[str, object]] = []
        safe_summary_refs: list[str] = []
        protected_snapshot_refs: list[str] = [page_snapshot_ref]
        for rank, summary in enumerate(cards, start=1):
            digest = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            provider_material_ref = self._write_pi_artifact(
                "protected",
                f"pi-provider-key/{safe_run_id}/{rank}.txt",
                f"liepin-opencli:{safe_run_id}:{rank}:{digest}",
            )
            safe_summary_ref = self._write_pi_artifact(
                "public-summary",
                f"pi-card/{safe_run_id}/{rank}.json",
                summary,
            )
            protected_snapshot_ref = self._write_pi_artifact(
                "protected",
                f"pi-card/{safe_run_id}/{rank}.json",
                {"schema_version": "seektalent.opencli_card_snapshot.v1", "rank": rank, "summary": summary},
            )
            safe_summary_refs.append(safe_summary_ref)
            protected_snapshot_refs.append(protected_snapshot_ref)
            envelope_cards.append(
                {
                    "provider_rank": rank,
                    "provider_candidate_key_material_ref": provider_material_ref,
                    "candidate_resume_id": f"liepin-opencli-{safe_run_id}-{rank}-{digest}",
                    "display_name_masked": bool(summary.get("display_name_masked", True)),
                    "safe_card_summary": {
                        key: value for key, value in summary.items() if key != "display_name_masked"
                    },
                    "safe_card_summary_ref": safe_summary_ref,
                    "protected_snapshot_ref": protected_snapshot_ref,
                }
            )
        return {
            "schema_version": "seektalent.pi_liepin_cards.v1",
            "status": "succeeded",
            "stop_reason": "completed",
            "source_run_id": source_run_id,
            "query": query,
            "cards_seen": len(envelope_cards),
            "cards_returned": len(envelope_cards),
            "pages_visited": pages_visited,
            "action_trace_ref": action_trace_ref,
            "safe_summary_refs": safe_summary_refs,
            "protected_snapshot_refs": protected_snapshot_refs,
            "cards": envelope_cards,
        }

    def _write_pi_artifact(self, scope: str, relative_path: str, payload: object) -> str:
        env_root = os.environ.get("SEEKTALENT_PI_ARTIFACT_ROOT")
        root = self._config.artifact_root or (Path(env_root) if env_root else None)
        if root is None:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        if scope not in {"protected", "public-summary"}:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        target = (root / scope / relative).resolve()
        allowed_root = (root / scope).resolve()
        if allowed_root not in target.parents:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return f"artifact://{scope}/{relative.as_posix()}"

    def cleanup_idle_lease(self, *, force: bool = False) -> OpenCliBrowserResult:
        lease = self._read_lease()
        if lease is None:
            return OpenCliBrowserResult(ok=True, action="cleanup_idle_lease", counts={"leases": 0})
        if not force and not self._lease_is_idle(lease):
            return OpenCliBrowserResult(ok=True, action="cleanup_idle_lease", counts={"leases": 1, "closed": 0})
        page_id = str(lease.get("page_id") or "")
        if not _is_safe_page_id(page_id):
            self._delete_lease()
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        self._run_browser_command("tab", ("close", page_id))
        self._delete_lease()
        self._forget_owned_page_marker(page_id)
        blank_windows = 1 if self._close_blank_window_if_enabled() else 0
        return OpenCliBrowserResult(
            ok=True,
            action="cleanup_idle_lease",
            counts={"leases": 1, "closed": 1, "blankWindows": blank_windows},
        )

    def watch_idle_lease(self) -> OpenCliBrowserResult:
        while True:
            lease = self._read_lease()
            if lease is None:
                return OpenCliBrowserResult(ok=True, action="watch_idle_lease", counts={"leases": 0})
            remaining_seconds = self._lease_remaining_seconds(lease)
            if remaining_seconds <= 0:
                return self.cleanup_idle_lease(force=True)
            time.sleep(min(max(remaining_seconds, 1), 30))

    def cleanup_orphaned_tabs(self, *, force: bool = False) -> OpenCliBrowserResult:
        lease = self._read_lease()
        if lease is not None:
            return self.cleanup_idle_lease(force=force)
        if not force:
            return OpenCliBrowserResult(
                ok=True,
                action="cleanup_orphaned_tabs",
                counts={"leases": 0, "closedTabs": 0, "blankWindows": 0},
            )
        owned_pages = self._read_owned_page_markers()
        closed = 0
        for tab in self._list_tabs():
            page_id = str(tab.get("id") or tab.get("page_id") or "")
            tab_url = str(tab.get("url") or "")
            if not _is_safe_page_id(page_id):
                continue
            marker = owned_pages.get(page_id)
            if marker is None:
                continue
            opened_at = marker.get("opened_at")
            if not isinstance(opened_at, int | float) or time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
                self._forget_owned_page_marker(page_id)
                continue
            if marker.get("session") != self._config.session or marker.get("url") != tab_url:
                continue
            self._run_browser_command("tab", ("close", page_id))
            self._forget_owned_page_marker(page_id)
            closed += 1
        blank_windows = 1 if self._close_blank_window_if_enabled() else 0
        return OpenCliBrowserResult(
            ok=True,
            action="cleanup_orphaned_tabs",
            counts={"leases": 0, "closedTabs": closed, "blankWindows": blank_windows},
        )

    def _list_tabs(self) -> list[dict[str, object]]:
        output = self._run_browser_command("tab", ("list",))
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        if not isinstance(parsed, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [tab for tab in parsed if isinstance(tab, dict)]

    def _bind_current_window(self) -> None:
        self._run_browser_command("bind", ())

    def _unbind_current_session(self) -> None:
        self._run_browser_command("unbind", ())

    def _is_owned_liepin_tab(self, tab_url: str) -> bool:
        tab = urlparse(tab_url)
        if (tab.hostname or "") not in self._config.policy.allowed_hosts:
            return False
        return any(_url_matches_start_surface(tab_url, start_url) for start_url in self._config.policy.allowed_start_urls)

    def _current_url(self) -> str:
        return self._run_browser_command("get", ("url",)).strip()

    def _run_browser_command(self, command: str, args: tuple[str, ...]) -> str:
        if command not in ALLOWED_BROWSER_COMMANDS or command in FORBIDDEN_BROWSER_COMMANDS:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._validate_command_shape(command, args)
        argv = tuple(self._config.command) + ("browser", self._config.session, command, *args)
        output = self._run(argv)
        return output

    def _click_known_modal_close_ref(self, ref: str) -> None:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        argv = tuple(self._config.command) + ("browser", self._config.session, "click", ref)
        self._run(argv)
        self._touch_lease()

    def _fill_args_for_target(self, *, target: str, text: str) -> tuple[str, ...]:
        normalized = " ".join(target.strip().lower().split())
        ref = _target_ref(normalized)
        if ref is not None:
            return (ref, text)
        if "搜索" in target or "keyword" in normalized:
            return ("--role", "combobox", "--nth", "0", text)
        return (target, text)

    def _click_args_for_target(self, target: str) -> tuple[str, ...]:
        normalized = " ".join(target.strip().lower().split())
        ref = _target_ref(normalized)
        if ref is not None:
            if ref not in self._config.allowed_click_refs:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            return ("--role", "button", "--name", "搜 索")
        if "搜索" in target or "search" in normalized:
            return ("--role", "button", "--name", "搜 索")
        if "下一页" in target or "下页" in target or "next" in normalized:
            return ("--role", "button", "--name", "下一页")
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")

    def _lease_path(self) -> Path:
        directory = self._config.lease_dir or (Path(tempfile.gettempdir()) / "seektalent-opencli-leases")
        return directory / f"{_safe_filename(self._config.session)}.json"

    def _owned_pages_path(self) -> Path:
        directory = self._config.lease_dir or (Path(tempfile.gettempdir()) / "seektalent-opencli-leases")
        return directory / f"{_safe_filename(self._config.session)}-owned-pages.json"

    def _read_lease(self) -> dict[str, object] | None:
        try:
            loaded = json.loads(self._lease_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        if not isinstance(loaded, dict):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return loaded

    def _write_lease(self, *, page_id: str, url: str) -> None:
        if not _is_safe_page_id(page_id):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        now = time.time()
        path = self._lease_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "seektalent.opencli_lease.v1",
            "session": self._config.session,
            "page_id": page_id,
            "url": url,
            "created_at": now,
            "last_activity_at": now,
        }
        self._write_lease_payload(payload)

    def _touch_lease(self) -> None:
        lease = self._read_lease()
        if lease is None:
            return
        lease["last_activity_at"] = time.time()
        self._write_lease_payload(lease)

    def _write_lease_payload(self, payload: Mapping[str, object]) -> None:
        path = self._lease_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(payload), sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _delete_lease(self) -> None:
        try:
            self._lease_path().unlink()
        except FileNotFoundError:
            return

    def _read_owned_page_markers(self) -> dict[str, dict[str, object]]:
        try:
            loaded = json.loads(self._owned_pages_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        if not isinstance(loaded, dict):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        markers: dict[str, dict[str, object]] = {}
        for page_id, marker in loaded.items():
            if not _is_safe_page_id(str(page_id)) or not isinstance(marker, dict):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            if marker.get("schema_version") != "seektalent.opencli_owned_page.v1":
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            if marker.get("session") != self._config.session:
                continue
            if marker.get("page_id") != page_id:
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            markers[str(page_id)] = dict(marker)
        return markers

    def _read_owned_page_markers_for_write(self) -> dict[str, dict[str, object]]:
        try:
            return self._read_owned_page_markers()
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_malformed_state":
                raise
            self._quarantine_owned_page_marker_file()
            return {}

    def _quarantine_owned_page_marker_file(self) -> None:
        path = self._owned_pages_path()
        if not path.exists():
            return
        target = path.with_name(f"{path.name}.malformed-{int(time.time())}-{uuid.uuid4().hex[:8]}")
        try:
            path.replace(target)
        except OSError:
            path.unlink(missing_ok=True)

    def _write_owned_page_marker(
        self,
        *,
        page_id: str,
        url: str,
        runtime_run_id: str | None,
        source_lane_run_id: str | None,
        owner_nonce: str,
        opened_at: float | None = None,
    ) -> None:
        if not _is_safe_page_id(page_id) or not self._is_owned_liepin_tab(url):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        markers = self._read_owned_page_markers_for_write()
        markers[page_id] = {
            "schema_version": "seektalent.opencli_owned_page.v1",
            "session": self._config.session,
            "page_id": page_id,
            "url": url,
            "opened_at": opened_at or time.time(),
            "runtime_run_id": runtime_run_id,
            "source_lane_run_id": source_lane_run_id,
            "owner_nonce": owner_nonce,
        }
        path = self._owned_pages_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _forget_owned_page_marker(self, page_id: str) -> None:
        markers = self._read_owned_page_markers_for_write()
        if page_id not in markers:
            return
        markers.pop(page_id)
        path = self._owned_pages_path()
        if markers:
            path.write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def _lease_is_idle(self, lease: Mapping[str, object]) -> bool:
        return self._lease_remaining_seconds(lease) <= 0

    def _lease_remaining_seconds(self, lease: Mapping[str, object]) -> int:
        last_activity = lease.get("last_activity_at")
        if not isinstance(last_activity, int | float):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return int(last_activity + self._config.idle_close_seconds - time.time())

    def _close_blank_window_if_enabled(self) -> bool:
        if not self._config.close_blank_window:
            return False
        return self._blank_window_closer.close_blank_window()

    def _launch_idle_cleanup_worker(self) -> None:
        if not self._config.cleanup_worker_enabled:
            return
        env = os.environ.copy()
        if self._config.lease_dir is not None:
            env["SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR"] = str(self._config.lease_dir)
        env["SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS"] = str(self._config.idle_close_seconds)
        env["SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW"] = "true" if self._config.close_blank_window else "false"
        try:
            subprocess.Popen(
                (sys.executable, "-m", "seektalent.providers.pi_agent.opencli_browser_cli", "watch_idle_lease"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError:
            return

    def _validate_command_shape(self, command: str, args: tuple[str, ...]) -> None:
        valid = {
            "state": len(args) == 0,
            "get": args == ("url",),
            "open": len(args) == 1,
            "find": len(args) == 1,
            "click": len(args) == 1 or _is_role_button_command(args),
            "fill": len(args) == 2 or _is_role_fill_command(args),
            "scroll": args in {("up",), ("down",)},
            "wait": len(args) == 2 and args[0] in {"time", "text", "selector"},
            "bind": len(args) == 0,
            "unbind": len(args) == 0,
            "tab": (
                args == ("list",)
                or (len(args) == 2 and args[0] in {"new", "select", "close"} and bool(args[1].strip()))
            ),
        }.get(command, False)
        if not valid:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if command == "click":
            if len(args) == 1:
                self._validate_click_target(args[0])
        if command == "fill":
            if len(args) == 2:
                self._validate_action_target(args[0])
                self._validate_keyword_text(args[1])
            else:
                self._validate_keyword_text(args[-1])
        if command == "open":
            self._validate_start_url(args[0])
        if command == "tab" and args[0] == "new":
            self._validate_start_url(args[1])
        if command == "tab" and args[0] in {"select", "close"} and not _is_safe_page_id(args[1]):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")

    def _run(self, argv: tuple[str, ...]) -> str:
        try:
            return self._commands.run(argv, timeout=self._config.timeout_seconds)
        except FileNotFoundError as exc:
            raise OpenCliBrowserError("liepin_opencli_command_missing") from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenCliBrowserError("liepin_opencli_timeout") from exc
        except subprocess.CalledProcessError as exc:
            output = f"{exc.stdout or ''}\n{exc.stderr or ''}"
            if "Extension" in output and ("not connected" in output or "disconnected" in output):
                raise OpenCliBrowserError("liepin_opencli_extension_disconnected") from exc
            raise OpenCliBrowserError("liepin_opencli_status_unavailable") from exc

    def _validate_start_url(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self._config.policy.allowed_hosts:
            raise OpenCliBrowserError("liepin_opencli_host_blocked")
        if url not in self._config.policy.allowed_start_urls:
            raise OpenCliBrowserError("liepin_opencli_start_url_blocked")

    def _validate_keyword_text(self, text: str) -> None:
        if not text.strip() or len(text) > self._config.policy.max_keyword_chars:
            raise OpenCliBrowserError("liepin_opencli_forbidden_text")
        forbidden_fragments = ("cookie", "Authorization", "Bearer", "storageState", "\n", "\r", "\x00")
        if any(fragment in text for fragment in forbidden_fragments):
            raise OpenCliBrowserError("liepin_opencli_forbidden_text")

    def _validate_action_target(self, target: str) -> None:
        normalized = " ".join(target.strip().lower().split())
        if not normalized or len(normalized) > 120:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if any(fragment in normalized for fragment in FORBIDDEN_ACTION_TARGET_FRAGMENTS):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")

    def _validate_click_target(self, target: str) -> None:
        self._validate_action_target(target)
        normalized = " ".join(target.strip().lower().split())
        ref = _target_ref(normalized)
        if ref is not None:
            if ref in self._config.allowed_click_refs:
                return
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if not any(fragment in normalized for fragment in ALLOWED_CLICK_TARGET_FRAGMENTS):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")


def _url_matches_start_surface(url: str, start_url: str) -> bool:
    parsed = urlparse(url)
    start = urlparse(start_url)
    if parsed.hostname != start.hostname:
        return False
    path = parsed.path or "/"
    start_path = start.path or "/"
    if path.rstrip("/") == start_path.rstrip("/"):
        return True
    prefix = start_path if start_path.endswith("/") else f"{start_path}/"
    return path.startswith(prefix)


_REF_PATTERN = re.compile(r"(?:\[ref=|\[|\bref=)([A-Za-z0-9_-]{1,64})(?:\]|\b)")
_SAFE_PAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _extract_refs_from_line(line: str) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for match in _REF_PATTERN.finditer(line):
        ref = match.group(1)
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return tuple(refs)


def _target_ref(target: str) -> str | None:
    if target.isdigit():
        return target
    match = re.fullmatch(r"(?:\[ref=|ref=|\[)([A-Za-z0-9_-]{1,64})\]?", target)
    if match is None:
        return None
    return match.group(1)


def _parse_page_id(output: str) -> str:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
    if not isinstance(parsed, dict):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    page_id = parsed.get("page")
    if not isinstance(page_id, str) or not _is_safe_page_id(page_id):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    return page_id


def _is_role_button_command(args: tuple[str, ...]) -> bool:
    return len(args) == 4 and args[0] == "--role" and args[1] == "button" and args[2] in {"--name", "--text"} and bool(
        args[3].strip()
    )


def _is_role_fill_command(args: tuple[str, ...]) -> bool:
    if len(args) != 5 or args[0] != "--role" or args[2] != "--nth":
        return False
    if args[1] not in {"textbox", "combobox"}:
        return False
    try:
        nth = int(args[3])
    except ValueError:
        return False
    return 0 <= nth <= 20 and bool(args[4].strip())


def _is_safe_page_id(value: str) -> bool:
    return bool(_SAFE_PAGE_ID_PATTERN.fullmatch(value))


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:128] or "default"
