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
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlparse


ALLOWED_BROWSER_COMMANDS = frozenset({"open", "state", "get", "find", "click", "fill", "scroll", "wait", "tab"})
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
    detail_open_timeout_seconds: int = 90
    idle_close_seconds: int = 120
    close_blank_window: bool = False
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


@dataclass(frozen=True)
class _LiepinDetailTarget:
    rank: int
    ref: str
    block_text: str
    score: int
    source: str = "detail_link"


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
    detail_targets = _rank_liepin_detail_targets(text, max_cards=20)
    if detail_targets:
        observation["detailTargets"] = _detail_targets_payload(detail_targets)
    return observation


def _detail_targets_payload(targets: Sequence[_LiepinDetailTarget]) -> tuple[dict[str, object], ...]:
    payloads: list[dict[str, object]] = []
    for index, target in enumerate(targets, start=1):
        payloads.append(
            {
                "rank": index,
                "ref": target.ref,
                "summary": target.block_text[:1_200],
                "score": target.score,
            }
        )
    return tuple(payloads)


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


def _rank_liepin_detail_targets(
    text: str,
    *,
    max_cards: int,
) -> tuple[_LiepinDetailTarget, ...]:
    lines = text.splitlines()
    targets: list[_LiepinDetailTarget] = []
    seen_refs: set[str] = set()
    for index, line in enumerate(lines):
        if not _line_has_detail_open_label(line):
            continue
        refs = _extract_refs_from_line(line)
        if not refs:
            continue
        block = "\n".join(lines[max(0, index - 8) : index + 1])
        clean_block = "\n".join(_clean_state_lines(block))
        if not clean_block:
            continue
        for ref in refs:
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            targets.append(_LiepinDetailTarget(rank=len(targets) + 1, ref=ref, block_text=clean_block, score=0))
            break
        if len(targets) >= max_cards:
            break
    return tuple(sorted(targets, key=lambda target: (-target.score, target.rank)))


def _rank_liepin_result_card_targets(
    output: str,
    *,
    max_cards: int,
) -> tuple[_LiepinDetailTarget, ...]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
    raw_entries: object
    if isinstance(parsed, dict):
        raw_entries = parsed.get("entries") or parsed.get("matches") or []
    elif isinstance(parsed, list):
        raw_entries = parsed
    else:
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    if not isinstance(raw_entries, list):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")

    targets: list[_LiepinDetailTarget] = []
    seen_refs: set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("visible") is False:
            continue
        ref = str(entry.get("ref") or entry.get("id") or "")
        if not _is_safe_page_id(ref) or ref in seen_refs:
            continue
        text = str(entry.get("text") or "")
        clean_block = "\n".join(_clean_state_lines(text))
        if not clean_block or not _looks_like_liepin_card(clean_block):
            continue
        seen_refs.add(ref)
        targets.append(
            _LiepinDetailTarget(
                rank=len(targets) + 1,
                ref=ref,
                block_text=clean_block,
                score=0,
                source="result_card",
            )
        )
        if len(targets) >= max_cards:
            break
    return tuple(sorted(targets, key=lambda target: (-target.score, target.rank)))


def _merge_liepin_detail_targets(
    *target_groups: Sequence[_LiepinDetailTarget],
    max_cards: int,
) -> tuple[_LiepinDetailTarget, ...]:
    merged: list[_LiepinDetailTarget] = []
    seen_refs: set[str] = set()
    for group in target_groups:
        for target in group:
            if target.ref in seen_refs:
                continue
            seen_refs.add(target.ref)
            merged.append(target)
    ordered = sorted(merged, key=lambda target: (-target.score, target.rank))[:max_cards]
    return tuple(
        _LiepinDetailTarget(
            rank=index,
            ref=target.ref,
            block_text=target.block_text,
            score=target.score,
            source=target.source,
        )
        for index, target in enumerate(ordered, start=1)
    )


def _line_has_detail_open_label(line: str) -> bool:
    return any(marker in line for marker in ("查看完整简历", "查看简历", "简历详情", "打开简历"))


def _looks_like_liepin_search_result_page(text: str) -> bool:
    return "id=resultList" in text or "detail-resume-card-wrap" in text or bool(re.search(r"\b\d+\s*位人选\b", text))


def _state_url(text: str) -> str | None:
    match = re.search(r"(?im)^\s*URL:\s*(\S+)", text)
    return match.group(1) if match else None


def _is_liepin_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname or "").endswith("liepin.com") and (parsed.path or "").startswith(
        "/resume/showresumedetail"
    )


def _looks_like_liepin_detail_resume_state(text: str) -> bool:
    url = _state_url(text)
    if url is not None and not _is_liepin_detail_url(url):
        return False
    if _looks_like_liepin_search_result_page(text):
        return False
    detail_markers = ("当前职位", "工作经历", "教育经历", "项目经历", "自我评价", "求职意向", "任职经历")
    if any(marker in text for marker in detail_markers):
        return True
    has_work_years = re.search(r"工作\s*\d+\s*年", text) is not None
    has_profile_context = any(marker in text for marker in ("公司", "大学", "本科", "硕士", "博士", "负责", "平台"))
    return has_work_years and has_profile_context


_CONTACT_TEXT_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|(?:\+?86[-\s]?)?1[3-9]\d{9}\b|"
    r"(?:手机|电话|邮箱|微信|weixin|wechat|wx[:：])",
    re.IGNORECASE,
)


def _safe_detail_payload_from_state(text: str) -> dict[str, object]:
    lines = [
        line
        for line in _clean_state_lines(text)
        if not _CONTACT_TEXT_PATTERN.search(line)
        and not any(marker in line for marker in ("联系候选人", "查看联系方式", "聊天", "下载简历", "付费", "购买"))
    ]
    if not lines:
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    full_text = _bounded_public_text("\n".join(lines), max_chars=12_000)
    company, title = _company_title_from_block(full_text)
    title = title or _current_title_from_detail(full_text)
    education_items = [
        {"school": school, "degree": _education_from_block(full_text), "speciality": None}
        for school in _school_names_from_block(full_text)
    ]
    work_items = [
        {"company": company, "title": title, "summary": _recent_experience_from_block(full_text) or full_text[:240]}
    ]
    return {
        "fullText": full_text,
        "currentTitle": title,
        "currentCompany": company,
        "workExperienceList": work_items,
        "educationList": education_items,
        "skills": _skill_tags_from_block(full_text),
        "locations": [city] if (city := _city_from_block(full_text)) else [],
    }


def _current_title_from_detail(text: str) -> str | None:
    match = re.search(r"当前职位[:：]\s*([^\n]{2,60})", text)
    if match:
        return _bounded_public_text(match.group(1), max_chars=80)
    return _job_intention_from_block(text)


def _detail_provider_key_material(*, safe_run_id: str, rank: int, payload: Mapping[str, object]) -> str:
    digest = hashlib.sha256(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return f"liepin-opencli-detail:{safe_run_id}:{rank}:{digest}"


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


LIEPIN_FILTER_SECTION_LABELS = {
    "legacy": "",
    "current": "目前城市",
    "expected": "期望城市",
    "experience": "工作年限",
    "age": "年龄",
    "education": "教育经历",
    "recruitment_type": "统招要求",
    "school_type": "院校要求",
}


def _liepin_filter_actions(native_filters: Mapping[str, object]) -> tuple[tuple[str, str, str], ...]:
    actions: list[tuple[str, str, str]] = []
    city = native_filters.get("city")
    if isinstance(city, str) and city.strip():
        actions.append(("city", "legacy", city.strip()))
    elif isinstance(city, Mapping):
        action = _filter_action_from_option("city", cast(Mapping[str, object], city))
        if action is not None:
            actions.append(action)
    experience = native_filters.get("experience")
    if isinstance(experience, Mapping):
        action = _filter_action_from_option("experience", cast(Mapping[str, object], experience))
        if action is not None:
            actions.append(action)
        else:
            label = _experience_label(cast(Mapping[str, object], experience))
            if label is not None:
                actions.append(("experience", "legacy", label))
    age = native_filters.get("age")
    if isinstance(age, Mapping):
        action = _filter_action_from_option("age", cast(Mapping[str, object], age))
        if action is not None:
            actions.append(action)
        else:
            label = _age_label(cast(Mapping[str, object], age))
            if label is not None:
                actions.append(("age", "legacy", label))
    for key in ("degree", "recruitmentType"):
        option = native_filters.get(key)
        if isinstance(option, Mapping):
            action = _filter_action_from_option(key, cast(Mapping[str, object], option))
            if action is not None:
                actions.append(action)
    school_types = native_filters.get("schoolTypes")
    if isinstance(school_types, list):
        for option in school_types:
            if isinstance(option, Mapping):
                action = _filter_action_from_option("schoolTypes", cast(Mapping[str, object], option))
                if action is not None:
                    actions.append(action)
    return tuple(actions)


def _filter_action_from_option(filter_name: str, option: Mapping[str, object]) -> tuple[str, str, str] | None:
    section = str(option.get("section") or "").strip()
    label = str(option.get("label") or "").strip()
    if section and label:
        return filter_name, section, label
    return None


def _skipped_liepin_filter_names(native_filters: Mapping[str, object]) -> tuple[str, ...]:
    known = {
        "city",
        "experience",
        "age",
        "degree",
        "recruitmentType",
        "schoolTypes",
        "partialReasonCodes",
        "sourceTarget",
    }
    return tuple(sorted(str(key) for key in native_filters if str(key) not in known))


def _liepin_filter_menu_label(filter_name: str, section: str) -> str | None:
    if section != "legacy":
        section_label = LIEPIN_FILTER_SECTION_LABELS.get(section)
        if section_label:
            return section_label
    return {
        "city": "城市",
        "experience": "工作经验",
        "age": "年龄",
    }.get(filter_name)


def _experience_label(experience: Mapping[str, object]) -> str | None:
    min_years = experience.get("minYears")
    max_years = experience.get("maxYears")
    if isinstance(min_years, int) and isinstance(max_years, int):
        return f"{min_years}-{max_years}年"
    if isinstance(min_years, int):
        return f"{min_years}年以上"
    if isinstance(max_years, int):
        return f"{max_years}年以下"
    return None


def _age_label(age: Mapping[str, object]) -> str | None:
    min_age = age.get("min")
    max_age = age.get("max")
    if isinstance(min_age, int) and isinstance(max_age, int):
        return f"{min_age}-{max_age}岁"
    if isinstance(max_age, int):
        return f"{max_age}岁以下"
    if isinstance(min_age, int):
        return f"{min_age}岁以上"
    return None


def _validate_native_filter_label(label: str) -> None:
    normalized = label.strip()
    if not normalized or len(normalized) > 32:
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    lowered = normalized.lower()
    forbidden = ("cookie", "authorization", "bearer", "storage", "\n", "\r", "\x00")
    if any(fragment in lowered for fragment in forbidden):
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")


def _opencli_result_text(result: OpenCliBrowserResult) -> str:
    if result.private_output:
        return result.private_output
    observation = result.observation or {}
    return str(observation.get("text") or "")


def _native_filter_option_visible(state_text: str, label: str) -> bool:
    if _native_filter_option_ref(state_text, label) is not None:
        return True
    escaped_label = re.escape(label)
    return any(re.search(rf"(?:\bbutton\b|<button).*?{escaped_label}", line) for line in state_text.splitlines())


def _native_filter_option_ref(state_text: str, label: str) -> str | None:
    escaped_label = re.escape(label)
    pattern = re.compile(rf"\[([A-Za-z0-9_-]{{1,64}})\]<label[^>]*>\s*{escaped_label}\s*</label>")
    for line in state_text.splitlines():
        match = pattern.search(line)
        if match is not None:
            return match.group(1)
    return None


def _native_filter_option_ref_in_section(state_text: str, *, section: str, label: str) -> str | None:
    if section == "legacy":
        return _native_filter_option_ref(state_text, label)
    section_label = LIEPIN_FILTER_SECTION_LABELS.get(section)
    if section_label is None:
        return None
    in_section = False
    fallback_dropdown_ref: str | None = None
    for line in state_text.splitlines():
        if _line_starts_known_filter_section(line) and section_label not in line and in_section:
            return fallback_dropdown_ref
        if section_label in line:
            in_section = True
            continue
        if not in_section:
            continue
        match = re.search(rf"\[([A-Za-z0-9_-]{{1,64}})\]<label[^>]*>\s*{re.escape(label)}\s*</label>", line)
        if match is not None:
            return match.group(1)
    return None


def _native_filter_control_ref_in_section(state_text: str, *, section: str) -> str | None:
    if section == "legacy":
        return None
    section_label = LIEPIN_FILTER_SECTION_LABELS.get(section)
    if section_label is None:
        return None
    in_section = False
    fallback_dropdown_ref: str | None = None
    for line in state_text.splitlines():
        if _line_starts_known_filter_section(line) and section_label not in line and in_section:
            return None
        if section_label in line:
            in_section = True
            match = _line_ref_for_clickable_filter_control(line)
            if match is not None:
                return match
            continue
        if not in_section:
            continue
        match = _line_ref_for_clickable_filter_control(line)
        if match is not None:
            return match
        preferred_ref = _line_ref_for_filter_dropdown_value(line, section=section)
        if preferred_ref is not None:
            return preferred_ref
        fallback_ref = _line_ref_for_filter_dropdown_shell(line, section=section)
        if fallback_ref is not None and fallback_dropdown_ref is None:
            fallback_dropdown_ref = fallback_ref
    return fallback_dropdown_ref


def _line_ref_for_clickable_filter_control(line: str) -> str | None:
    if "button" not in line and "combobox" not in line:
        return None
    match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
    return match.group(1) if match is not None else None


def _line_ref_for_filter_dropdown_shell(line: str, *, section: str) -> str | None:
    if section != "recruitment_type" or "<div" not in line:
        return None
    match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
    return match.group(1) if match is not None else None


def _line_ref_for_filter_dropdown_value(line: str, *, section: str) -> str | None:
    if section != "recruitment_type" or "统招/非统招" not in line:
        return None
    match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
    return match.group(1) if match is not None else None


def _native_filter_option_visible_in_section(state_text: str, *, section: str, label: str) -> bool:
    if _native_filter_option_ref_in_section(state_text, section=section, label=label) is not None:
        return True
    if section == "legacy":
        return _native_filter_option_visible(state_text, label)
    return False


def _line_starts_known_filter_section(line: str) -> bool:
    return any(label and label in line for label in LIEPIN_FILTER_SECTION_LABELS.values())


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
        lease = self._read_lease()
        if lease is not None:
            if str(lease.get("url") or "") == url:
                page_id = self._verified_owned_lease_page_id(lease)
                if page_id is not None:
                    self._run_browser_command("tab", ("select", page_id))
                    self._touch_lease()
                    self._launch_idle_cleanup_worker()
                    return OpenCliBrowserResult(
                        ok=True,
                        action="open_liepin_tab",
                        counts={"reused": 1},
                    )
                self.cleanup_idle_lease(force=True)
            else:
                self._delete_lease()
        output = self._run_browser_command("tab", ("new", url))
        page_id = _parse_page_id(output)
        self._run_browser_command("tab", ("select", page_id))
        owner_nonce = uuid.uuid4().hex
        self._write_lease(page_id=page_id, url=url, owner_nonce=owner_nonce)
        self._write_owned_page_marker(
            page_id=page_id,
            url=url,
            runtime_run_id=None,
            source_lane_run_id=None,
            owner_nonce=owner_nonce,
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
        result_card_targets = self._find_liepin_result_card_detail_targets(
            state_text=output,
            max_cards=20,
        )
        if result_card_targets:
            existing_targets = _rank_liepin_detail_targets(
                output,
                max_cards=20,
            )
            observation["detailTargets"] = _detail_targets_payload(
                _merge_liepin_detail_targets(existing_targets, result_card_targets, max_cards=20)
            )
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

    def _click_native_filter_option(self, label: str, *, state_text: str, section: str = "legacy") -> None:
        _validate_native_filter_label(label)
        if section not in LIEPIN_FILTER_SECTION_LABELS:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        ref = _native_filter_option_ref_in_section(state_text, section=section, label=label)
        if ref is not None:
            self._click_native_filter_ref(ref)
            return
        if section != "legacy":
            raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
        self._run_browser_command("click", ("--role", "button", "--text", label))
        self._touch_lease()

    def _click_native_filter_ref(self, ref: str) -> None:
        argv = tuple(self._config.command) + ("browser", self._config.session, "click", ref)
        self._run(argv)
        self._touch_lease()

    def _click_native_filter_menu(self, filter_name: str, *, section: str = "legacy") -> None:
        menu_label = _liepin_filter_menu_label(filter_name, section)
        if menu_label is None:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._run_browser_command("click", ("--role", "button", "--name", menu_label))
        self._touch_lease()

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

    def apply_liepin_native_filters(
        self,
        *,
        source_run_id: str,
        native_filters: Mapping[str, object],
    ) -> OpenCliBrowserResult:
        events: list[dict[str, object]] = []
        try:
            current_state = self.state()
            if not current_state.ok:
                return current_state
            result = self._apply_liepin_native_filters(
                native_filters=native_filters,
                current_state=current_state,
                events=events,
            )
            for event in events:
                self._append_agent_event(source_run_id, event)
            return result
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="apply_liepin_filters", safe_reason_code=exc.safe_reason_code)

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100 or not _is_safe_page_id(ref):
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            if self._detail_ref_was_opened(source_run_id=source_run_id, ref=ref):
                return OpenCliBrowserResult(
                    ok=True,
                    action="open_liepin_detail",
                    counts={"rank": rank, "reused": 1},
                )
            state = self._state_with_liepin_detail_ref(ref)
            if state is None:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            if not state.ok:
                return state
            self._append_agent_event(
                source_run_id,
                {"action_kind": "open_detail", "route_kind": "detail", "ref": ref, "rank": rank},
            )
            if self._open_liepin_detail_ref_controlled(ref, source_run_id=source_run_id):
                return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})
            tabs_before_click = self._safe_list_tabs()
            safe_reason_code = "liepin_opencli_timeout"
            try:
                self._click_liepin_detail_ref(ref)
            except OpenCliBrowserError as exc:
                if exc.safe_reason_code != "liepin_opencli_timeout":
                    raise
                safe_reason_code = exc.safe_reason_code
            if not self._claim_liepin_tab_after_detail_click(tabs_before_click, source_run_id=source_run_id):
                self._append_agent_event(
                    source_run_id,
                    {
                        "action_kind": "open_detail_timeout",
                        "route_kind": "detail",
                        "ref": ref,
                        "rank": rank,
                        "safe_reason_code": safe_reason_code,
                    },
                )
                return OpenCliBrowserResult(
                    ok=False,
                    action="open_liepin_detail",
                    safe_reason_code=safe_reason_code,
                    counts={"rank": rank},
                )
            return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="open_liepin_detail", safe_reason_code=exc.safe_reason_code)

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            safe_run_id = _safe_artifact_segment(source_run_id)
            detail_text = self._detail_state_text_until_resume_ready()
            payload = _safe_detail_payload_from_state(detail_text)
            protected_snapshot_ref = self._write_pi_artifact(
                "protected",
                f"pi-detail/{safe_run_id}/{rank}.json",
                {
                    "schema_version": "seektalent.opencli_detail_snapshot.v1",
                    "rank": rank,
                    "payload": payload,
                },
            )
            provider_material_ref = self._write_pi_artifact(
                "protected",
                f"pi-provider-key/{safe_run_id}/{rank}.txt",
                _detail_provider_key_material(safe_run_id=safe_run_id, rank=rank, payload=payload),
            )
            resume: dict[str, object] = {
                "provider_rank": rank,
                "provider_candidate_key_material_ref": provider_material_ref,
                "candidate_resume_id": f"liepin-opencli-detail-{safe_run_id}-{rank}",
                "protected_snapshot_ref": protected_snapshot_ref,
                "detail_payload": payload,
                "normalized_text": str(payload["fullText"]),
            }
            resumes = [item for item in self._read_collected_resumes(safe_run_id) if item.get("provider_rank") != rank]
            resumes.append(resume)
            resumes.sort(key=lambda item: int(cast(Any, item.get("provider_rank") or 0)))
            self._write_collected_resumes(safe_run_id, resumes)
            self._append_agent_event(
                source_run_id,
                {"action_kind": "observe_detail", "route_kind": "detail", "ok": True, "rank": rank},
            )
            return OpenCliBrowserResult(
                ok=True,
                action="capture_liepin_detail_resume",
                counts={"resumes": len(resumes), "rank": rank},
            )
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code=exc.safe_reason_code,
            )

    def finalize_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        cards_seen: int | None = None,
        target_resumes: int | None = None,
    ) -> dict[str, object]:
        safe_run_id = _safe_artifact_segment(source_run_id)
        resumes = self._read_collected_resumes(safe_run_id)
        protected_snapshot_refs = [
            str(resume["protected_snapshot_ref"])
            for resume in resumes
            if isinstance(resume.get("protected_snapshot_ref"), str)
        ]
        envelope = self._resumes_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_run_id=safe_run_id,
            pages_visited=max(1, min(max_pages, max_pages or 1)),
            events=self._read_agent_events(safe_run_id),
            cards_seen=min(max_cards, max(cards_seen or len(resumes), len(resumes))),
            resumes=resumes,
            protected_snapshot_refs=protected_snapshot_refs,
            target_resumes=target_resumes,
        )
        try:
            self._close_owned_detail_tabs_for_source_run(source_run_id=source_run_id)
        except OpenCliBrowserError:
            return envelope
        return envelope

    def search_liepin_cards(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None = None,
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
            if native_filters:
                final_state = self._apply_liepin_native_filters(
                    native_filters=native_filters,
                    current_state=final_state,
                    events=events,
                )
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

    def _apply_liepin_native_filters(
        self,
        *,
        native_filters: Mapping[str, object],
        current_state: OpenCliBrowserResult,
        events: list[dict[str, object]],
    ) -> OpenCliBrowserResult:
        working_state = current_state
        for filter_name, section, label in _liepin_filter_actions(native_filters):
            try:
                working_state = self._select_liepin_native_filter(
                    filter_name=filter_name,
                    section=section,
                    label=label,
                    current_state=working_state,
                    events=events,
                )
                events.append(
                    {
                        "action_kind": "apply_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "ok": True,
                    }
                )
            except OpenCliBrowserError as exc:
                events.append(
                    {
                        "action_kind": "apply_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "ok": False,
                        "safe_reason_code": exc.safe_reason_code,
                    }
                )
        for filter_name in _skipped_liepin_filter_names(native_filters):
            events.append({"action_kind": "skip_native_filter", "filter": filter_name, "ok": True})
        events.append({"action_kind": "observe_after_native_filters", "route_kind": "search", "ok": working_state.ok})
        return working_state

    def _select_liepin_native_filter(
        self,
        *,
        filter_name: str,
        section: str,
        label: str,
        current_state: OpenCliBrowserResult,
        events: list[dict[str, object]],
    ) -> OpenCliBrowserResult:
        state = current_state
        for attempt_index in range(2):
            try:
                state_text = _opencli_result_text(state)
                if not _native_filter_option_visible_in_section(state_text, section=section, label=label):
                    control_ref = _native_filter_control_ref_in_section(state_text, section=section)
                    if control_ref is not None:
                        self._click_native_filter_ref(control_ref)
                    else:
                        self._click_native_filter_menu(filter_name, section=section)
                    events.append(
                        {
                            "action_kind": "open_native_filter_menu",
                            "filter": filter_name,
                            "section": section,
                            "value": label,
                            "ok": True,
                        }
                    )
                    self.wait_time(seconds=1)
                    state = self.state()
                    events.append(
                        {
                            "action_kind": "observe_native_filter_menu",
                            "filter": filter_name,
                            "section": section,
                            "ok": state.ok,
                        }
                    )
                    if not state.ok:
                        raise OpenCliBrowserError(state.safe_reason_code)
                    state_text = _opencli_result_text(state)
                self._click_native_filter_option(label, state_text=state_text, section=section)
                self.wait_time(seconds=1)
                state = self.state()
                events.append(
                    {
                        "action_kind": "observe_after_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "ok": state.ok,
                    }
                )
                if not state.ok:
                    raise OpenCliBrowserError(state.safe_reason_code)
                return state
            except OpenCliBrowserError as exc:
                if exc.safe_reason_code != "liepin_opencli_status_unavailable" or attempt_index == 1:
                    raise
                events.append(
                    {
                        "action_kind": "apply_native_filter_retry",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "safe_reason_code": exc.safe_reason_code,
                    }
                )
                self.wait_time(seconds=2)
                state = self.state()
                events.append(
                    {
                        "action_kind": "observe_before_native_filter_retry",
                        "filter": filter_name,
                        "section": section,
                        "ok": state.ok,
                    }
                )
                if not state.ok:
                    raise OpenCliBrowserError(state.safe_reason_code)
        return state

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

    def _resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_run_id: str,
        pages_visited: int,
        events: list[dict[str, object]],
        cards_seen: int,
        resumes: list[dict[str, object]],
        protected_snapshot_refs: list[str],
        target_resumes: int | None = None,
    ) -> dict[str, object]:
        returned_count = len(resumes)
        target_count = max(0, int(target_resumes or 0))
        status = "partial" if target_count and returned_count < target_count else "succeeded"
        stop_reason = "blocked_budget_exhausted" if status == "partial" else "completed"
        action_trace_ref = self._write_pi_artifact(
            "protected",
            f"pi-trace/{safe_run_id}/action-trace.json",
            {
                "schema_version": "seektalent.opencli_action_trace.v1",
                "mode": "detail_backed_resume_search",
                "source": "liepin",
                "events": events,
                "cards_seen": cards_seen,
                "resumes_returned": returned_count,
            },
        )
        return {
            "schema_version": "seektalent.pi_liepin_resumes.v2",
            "status": status,
            "stop_reason": stop_reason,
            "source_run_id": source_run_id,
            "query": query,
            "cards_seen": cards_seen,
            "cards_excluded": [],
            "resumes_returned": returned_count,
            "pages_visited": pages_visited,
            "detail_pages_opened": returned_count,
            "action_trace_ref": action_trace_ref,
            "protected_snapshot_refs": protected_snapshot_refs,
            "resumes": resumes,
        }

    def _write_pi_artifact(self, scope: str, relative_path: str, payload: object) -> str:
        target = self._pi_artifact_path(scope, relative_path)
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return f"artifact://{scope}/{Path(relative_path).as_posix()}"

    def _pi_artifact_path(self, scope: str, relative_path: str) -> Path:
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
        return target

    def _append_agent_event(self, source_run_id: str, event: Mapping[str, object]) -> None:
        safe_run_id = _safe_artifact_segment(source_run_id)
        events = self._read_agent_events(safe_run_id)
        events.append(dict(event))
        self._write_pi_artifact(
            "protected",
            f"pi-trace/{safe_run_id}/agent-events.json",
            {"schema_version": "seektalent.opencli_agent_events.v1", "events": events},
        )

    def _read_agent_events(self, safe_run_id: str) -> list[dict[str, object]]:
        path = self._pi_artifact_path("protected", f"pi-trace/{safe_run_id}/agent-events.json")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        raw_events = loaded.get("events") if isinstance(loaded, dict) else None
        if not isinstance(raw_events, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [dict(item) for item in raw_events if isinstance(item, dict)]

    def _detail_ref_was_opened(self, *, source_run_id: str, ref: str) -> bool:
        safe_run_id = _safe_artifact_segment(source_run_id)
        return any(
            event.get("action_kind") == "open_detail" and event.get("ref") == ref
            for event in self._read_agent_events(safe_run_id)
        )

    def _read_collected_resumes(self, safe_run_id: str) -> list[dict[str, object]]:
        path = self._pi_artifact_path("protected", f"pi-detail/{safe_run_id}/collected-resumes.json")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        raw_resumes = loaded.get("resumes") if isinstance(loaded, dict) else None
        if not isinstance(raw_resumes, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [dict(item) for item in raw_resumes if isinstance(item, dict)]

    def _write_collected_resumes(self, safe_run_id: str, resumes: Sequence[Mapping[str, object]]) -> None:
        self._write_pi_artifact(
            "protected",
            f"pi-detail/{safe_run_id}/collected-resumes.json",
            {"schema_version": "seektalent.opencli_collected_resumes.v1", "resumes": [dict(item) for item in resumes]},
        )

    def _find_liepin_result_card_detail_targets(
        self,
        *,
        state_text: str,
        max_cards: int,
    ) -> tuple[_LiepinDetailTarget, ...]:
        if max_cards < 1 or not _looks_like_liepin_search_result_page(state_text):
            return ()
        try:
            output = self._run(
                tuple(self._config.command)
                + (
                    "browser",
                    self._config.session,
                    "find",
                    "--css",
                    "#resultList .detail-resume-card-wrap",
                    "--limit",
                    str(min(max_cards, 100)),
                    "--text-max",
                    "1200",
                )
            )
            return _rank_liepin_result_card_targets(
                output,
                max_cards=max_cards,
            )
        except OpenCliBrowserError:
            return ()

    def cleanup_idle_lease(self, *, force: bool = False) -> OpenCliBrowserResult:
        lease = self._read_lease()
        if lease is None:
            return OpenCliBrowserResult(ok=True, action="cleanup_idle_lease", counts={"leases": 0})
        if not force and not self._lease_is_idle(lease):
            return OpenCliBrowserResult(ok=True, action="cleanup_idle_lease", counts={"leases": 1, "closed": 0})
        page_id = self._lease_page_id(lease)
        if self._verified_owned_lease_page_id(lease) is None:
            self._delete_lease()
            if page_id:
                self._forget_owned_page_marker(page_id)
            return OpenCliBrowserResult(
                ok=True,
                action="cleanup_idle_lease",
                counts={"leases": 1, "closed": 0, "skipped": 1},
            )
        closed = self._close_owned_marked_tabs()
        self._delete_lease()
        blank_windows = 1 if self._close_blank_window_if_enabled() else 0
        return OpenCliBrowserResult(
            ok=True,
            action="cleanup_idle_lease",
            counts={"leases": 1, "closed": closed, "blankWindows": blank_windows},
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
        skipped = self._forget_orphaned_owned_page_markers()
        return OpenCliBrowserResult(
            ok=True,
            action="cleanup_orphaned_tabs",
            counts={"leases": 0, "closedTabs": 0, "blankWindows": 0, "skipped": skipped},
        )

    def cleanup_liepin_detail_tabs(self, *, source_run_id: str) -> OpenCliBrowserResult:
        try:
            closed = self._close_owned_detail_tabs_for_source_run(source_run_id=source_run_id)
            return OpenCliBrowserResult(ok=True, action="cleanup_liepin_detail_tabs", counts={"closed": closed})
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(
                ok=False,
                action="cleanup_liepin_detail_tabs",
                safe_reason_code=exc.safe_reason_code,
            )

    def _close_owned_detail_tabs_for_source_run(self, *, source_run_id: str) -> int:
        owned_pages = self._read_owned_page_markers()
        closed = 0
        for tab in self._list_tabs():
            page_id = _tab_page_id(tab)
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
            if marker.get("session") != self._config.session or marker.get("page_id") != page_id:
                continue
            if marker.get("source_run_id") != source_run_id:
                continue
            if marker.get("url") != tab_url or not _is_liepin_detail_url(tab_url):
                continue
            if self._close_owned_page_id(page_id):
                self._forget_owned_page_marker(page_id)
                closed += 1
        return closed

    def _forget_orphaned_owned_page_markers(self) -> int:
        markers = self._read_owned_page_markers()
        for page_id in tuple(markers):
            self._forget_owned_page_marker(page_id)
        return len(markers)

    def _close_owned_marked_tabs(self) -> int:
        owned_pages = self._read_owned_page_markers()
        closed = 0
        for tab in self._list_tabs():
            page_id = _tab_page_id(tab)
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
            if self._close_owned_page_id(page_id):
                self._forget_owned_page_marker(page_id)
                closed += 1
        return closed

    def _close_owned_page_id(self, page_id: str) -> bool:
        try:
            self._run_browser_command("tab", ("close", page_id))
            return True
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_status_unavailable":
                raise
            return False

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
        if any(_url_matches_start_surface(tab_url, start_url) for start_url in self._config.policy.allowed_start_urls):
            return True
        path = tab.path or ""
        if path.startswith("/resume/showresumedetail"):
            return True
        return False

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

    def _click_liepin_detail_ref(self, ref: str) -> None:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        argv = tuple(self._config.command) + ("browser", self._config.session, "click", ref)
        self._run(argv)
        self._touch_lease()

    def _open_liepin_detail_ref_controlled(self, ref: str, *, source_run_id: str) -> bool:
        detail_url = self._liepin_detail_url_for_ref(ref)
        if detail_url is None:
            return False
        output = self._run_browser_command("tab", ("new", detail_url))
        page_id = _parse_page_id(output)
        self._select_and_mark_owned_liepin_tab(page_id=page_id, url=detail_url, source_run_id=source_run_id)
        self._touch_lease()
        return True

    def _liepin_detail_url_for_ref(self, ref: str) -> str | None:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        script = (
            "(() => {"
            f"const card = document.querySelector('[data-opencli-ref=\"{ref}\"]');"
            "const input = card && card.querySelector('input[name=\"res_id_encode\"]');"
            "const value = input && (input.getAttribute('value') || input.value || '');"
            "if (!/^[A-Za-z0-9]+$/.test(value || '')) return null;"
            "const cards = Array.from(document.querySelectorAll('.detail-resume-card-wrap'));"
            "const index = Math.max(0, cards.indexOf(card));"
            "return 'https://h.liepin.com/resume/showresumedetail/?res_id_encode='"
            "+ encodeURIComponent(value)"
            "+ '&index=' + index"
            "+ '&position=' + index"
            "+ '&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal';"
            "})()"
        )
        output = self._run_browser_eval(script).strip()
        if output == "null" or not output:
            return None
        if not _is_liepin_detail_url(output):
            return None
        return output

    def _run_browser_eval(self, script: str) -> str:
        argv = tuple(self._config.command) + ("browser", self._config.session, "eval", script)
        output = self._run(argv)
        if _looks_sensitive(output):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        self._touch_lease()
        return output

    def _safe_list_tabs(self) -> tuple[dict[str, object], ...]:
        try:
            return tuple(self._list_tabs())
        except OpenCliBrowserError:
            return ()

    def _claim_liepin_tab_after_detail_click(
        self,
        before_tabs: Sequence[Mapping[str, object]],
        *,
        source_run_id: str,
    ) -> bool:
        before_urls = _tab_urls_by_page_id(before_tabs)
        if not before_urls:
            return False
        attempts = max(1, int(self._config.detail_open_timeout_seconds))
        for attempt_index in range(attempts):
            candidate = self._liepin_tab_claim_candidate(before_urls=before_urls)
            if candidate is not None:
                page_id, url = candidate
                self._select_and_mark_owned_liepin_tab(page_id=page_id, url=url, source_run_id=source_run_id)
                return True
            if attempt_index < attempts - 1:
                time.sleep(1)
        return False

    def _liepin_tab_claim_candidate(self, *, before_urls: Mapping[str, str]) -> tuple[str, str] | None:
        try:
            after_tabs = self._list_tabs()
            markers = self._read_owned_page_markers()
        except OpenCliBrowserError:
            return None
        candidates: list[tuple[int, str, str]] = []
        for tab in after_tabs:
            page_id = _tab_page_id(tab)
            url = str(tab.get("url") or "")
            if not _is_safe_page_id(page_id) or not self._is_owned_liepin_tab(url):
                continue
            before_url = before_urls.get(page_id)
            marker = markers.get(page_id)
            is_new_tab = page_id not in before_urls
            is_owned_navigation = marker is not None and before_url is not None and before_url != url
            if not is_new_tab and not is_owned_navigation:
                continue
            score = 0
            if tab.get("active") is True:
                score += 100
            if _is_liepin_detail_url(url):
                score += 50
            if is_new_tab:
                score += 10
            candidates.append((score, page_id, url))
        if not candidates:
            return None
        _, page_id, url = max(candidates, key=lambda item: item[0])
        return page_id, url

    def _select_and_mark_owned_liepin_tab(self, *, page_id: str, url: str, source_run_id: str | None = None) -> None:
        self._run_browser_command("tab", ("select", page_id))
        owner_nonce = self._owned_page_marker_nonce(page_id) or uuid.uuid4().hex
        self._write_lease(page_id=page_id, url=url, owner_nonce=owner_nonce)
        self._write_owned_page_marker(
            page_id=page_id,
            url=url,
            source_run_id=source_run_id,
            runtime_run_id=None,
            source_lane_run_id=None,
            owner_nonce=owner_nonce,
        )

    def _owned_page_marker_nonce(self, page_id: str) -> str | None:
        try:
            marker = self._read_owned_page_markers().get(page_id)
        except OpenCliBrowserError:
            return None
        if marker is None:
            return None
        owner_nonce = marker.get("owner_nonce")
        if isinstance(owner_nonce, str) and owner_nonce:
            return owner_nonce
        return None

    def _current_tab_page_id(self, current_url: str) -> str | None:
        tabs = self._list_tabs()
        for tab in tabs:
            page_id = _tab_page_id(tab)
            if tab.get("active") is True and _is_safe_page_id(page_id) and str(tab.get("url") or "") == current_url:
                return page_id
        for tab in tabs:
            page_id = _tab_page_id(tab)
            if _is_safe_page_id(page_id) and str(tab.get("url") or "") == current_url:
                return page_id
        return None

    def _state_has_liepin_detail_ref(self, state: OpenCliBrowserResult, ref: str) -> bool:
        if not state.ok:
            return False
        state_text = state.private_output or str(state.observation.get("text") or "")
        targets = _merge_liepin_detail_targets(
            _rank_liepin_detail_targets(state_text, max_cards=100),
            self._find_liepin_result_card_detail_targets(
                state_text=state_text,
                max_cards=100,
            ),
            max_cards=100,
        )
        return ref in {target.ref for target in targets}

    def _state_with_liepin_detail_ref(self, ref: str) -> OpenCliBrowserResult | None:
        first_state = self.state()
        if self._state_has_liepin_detail_ref(first_state, ref):
            return first_state
        for page_id in self._owned_liepin_search_page_ids():
            if not self._select_owned_liepin_search_page(page_id):
                continue
            restored_state = self.state()
            if self._state_has_liepin_detail_ref(restored_state, ref):
                return restored_state
        if not first_state.ok:
            return first_state
        return None

    def _is_liepin_search_context_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if (parsed.hostname or "") not in self._config.policy.allowed_hosts:
            return False
        path = parsed.path or ""
        if path.startswith("/resume/showresumedetail"):
            return False
        return "resume" not in path.lower() and "detail" not in path.lower()

    def _owned_liepin_search_page_id(self) -> str | None:
        page_ids = self._owned_liepin_search_page_ids()
        if page_ids:
            return page_ids[0]
        return None

    def _owned_liepin_search_page_ids(self) -> tuple[str, ...]:
        page_ids: list[str] = []
        seen: set[str] = set()
        lease = self._read_lease()
        if lease is not None and self._is_liepin_search_context_url(str(lease.get("url") or "")):
            page_id = self._verified_owned_lease_page_id(lease)
            if page_id is not None:
                page_ids.append(page_id)
                seen.add(page_id)
        try:
            markers = self._read_owned_page_markers()
        except OpenCliBrowserError:
            markers = {}
        for page_id, marker in sorted(
            markers.items(),
            key=lambda item: float(item[1].get("opened_at") or 0),
            reverse=True,
        ):
            if page_id in seen:
                continue
            opened_at = marker.get("opened_at")
            if not isinstance(opened_at, int | float) or time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
                continue
            marker_url = str(marker.get("url") or "")
            if marker.get("session") != self._config.session or marker.get("page_id") != page_id:
                continue
            if not self._is_liepin_search_context_url(marker_url):
                continue
            page_ids.append(page_id)
            seen.add(page_id)
        if page_ids:
            return tuple(page_ids)
        try:
            current_url = self._current_url()
        except OpenCliBrowserError:
            return ()
        if not self._is_liepin_search_context_url(current_url):
            return ()
        try:
            current_page_id = self._current_tab_page_id(current_url)
        except OpenCliBrowserError:
            return ()
        if current_page_id is None:
            return ()
        return (current_page_id,)

    def _select_owned_liepin_search_page(self, page_id: str) -> bool:
        if not _is_safe_page_id(page_id):
            return False
        try:
            marker = self._read_owned_page_markers().get(page_id)
        except OpenCliBrowserError:
            return False
        if marker is None:
            return False
        opened_at = marker.get("opened_at")
        if not isinstance(opened_at, int | float) or time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
            return False
        search_url = str(marker.get("url") or "")
        if marker.get("session") != self._config.session or marker.get("page_id") != page_id:
            return False
        if not self._is_liepin_search_context_url(search_url):
            return False
        owner_nonce = marker.get("owner_nonce")
        if not isinstance(owner_nonce, str) or not owner_nonce:
            return False
        try:
            self._run_browser_command("tab", ("select", page_id))
            self._write_lease(page_id=page_id, url=search_url, owner_nonce=owner_nonce)
        except OpenCliBrowserError:
            return False
        return True

    def _restore_liepin_search_results_state(self, page_id: str) -> OpenCliBrowserResult | None:
        if not self._select_owned_liepin_search_page(page_id):
            return None
        state = self.state()
        if not state.ok:
            return None
        state_text = state.private_output or str(state.observation.get("text") or "")
        if not _rank_liepin_detail_targets(state_text, max_cards=1):
            return None
        return state

    def _detail_state_text(self) -> str:
        output = self._run_browser_command("state", ())
        if _looks_sensitive(output):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        self._touch_lease()
        return output

    def _detail_state_text_until_resume_ready(self) -> str:
        attempts = max(4, min(30, int(self._config.detail_open_timeout_seconds) // 2))
        for attempt_index in range(attempts):
            output = self._detail_state_text()
            if _looks_like_liepin_detail_resume_state(output):
                return output
            if attempt_index < attempts - 1:
                self.wait_time(seconds=2)
        raise OpenCliBrowserError("liepin_opencli_detail_not_opened")

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

    def _write_lease(self, *, page_id: str, url: str, owner_nonce: str | None = None) -> None:
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
            "owner_nonce": owner_nonce,
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
        source_run_id: str | None = None,
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
            "source_run_id": source_run_id,
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

    def _lease_page_id(self, lease: Mapping[str, object]) -> str:
        page_id = str(lease.get("page_id") or "")
        if not _is_safe_page_id(page_id):
            self._delete_lease()
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return page_id

    def _verified_owned_lease_page_id(self, lease: Mapping[str, object]) -> str | None:
        if lease.get("session") not in {None, self._config.session}:
            return None
        page_id = self._lease_page_id(lease)
        lease_url = str(lease.get("url") or "")
        if not self._is_owned_liepin_tab(lease_url):
            return None
        try:
            marker = self._read_owned_page_markers().get(page_id)
        except OpenCliBrowserError:
            return None
        if marker is None:
            return None
        opened_at = marker.get("opened_at")
        if not isinstance(opened_at, int | float):
            return None
        if time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
            return None
        if marker.get("session") != self._config.session or marker.get("page_id") != page_id:
            return None
        if marker.get("url") != lease_url:
            return None
        lease_nonce = lease.get("owner_nonce")
        marker_nonce = marker.get("owner_nonce")
        if isinstance(lease_nonce, str) and lease_nonce and marker_nonce != lease_nonce:
            return None
        try:
            tabs = self._list_tabs()
        except OpenCliBrowserError:
            return None
        for tab in tabs:
            tab_id = _tab_page_id(tab)
            if tab_id != page_id:
                continue
            if str(tab.get("url") or "") == lease_url:
                return page_id
            return None
        return None

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
            self._validate_tab_new_url(args[1])
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

    def _validate_tab_new_url(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self._config.policy.allowed_hosts:
            raise OpenCliBrowserError("liepin_opencli_host_blocked")
        if url in self._config.policy.allowed_start_urls or _is_liepin_detail_url(url):
            return
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


def _tab_page_id(tab: Mapping[str, object]) -> str:
    return str(tab.get("id") or tab.get("page_id") or tab.get("page") or "")


def _tab_urls_by_page_id(tabs: Sequence[Mapping[str, object]]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for tab in tabs:
        page_id = _tab_page_id(tab)
        if _is_safe_page_id(page_id):
            urls[page_id] = str(tab.get("url") or "")
    return urls


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
