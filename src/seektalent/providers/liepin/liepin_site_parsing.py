from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult
from seektalent.providers.liepin.opencli_card_text import (
    ACCESSIBILITY_NOISE_TOKENS,
    clean_liepin_result_card_text,
    clean_state_lines,
    education_from_block,
    looks_like_liepin_card,
    looks_like_liepin_card_start,
)

FIXED_READONLY_EVAL_PROBES = frozenset({"liepin_detail_url_for_card", "liepin_detail_resume_payload"})
LIEPIN_ALLOWED_HOSTS = frozenset({"www.liepin.com", "h.liepin.com", "c.liepin.com", "lpt.liepin.com"})
LIEPIN_RISK_HOSTS = frozenset({"safe.liepin.com"})
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

@dataclass(frozen=True)
class _LiepinDetailTarget:
    rank: int
    ref: str
    block_text: str
    score: int
    source: str = "detail_link"


def bucket_text(text: str) -> dict[str, int]:
    return {"chars": len(text)}


def build_observation(text: str, *, max_chars: int = 12_000) -> dict[str, object]:
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


def extract_liepin_search_button_ref(text: str) -> str | None:
    for line in text.splitlines():
        normalized = " ".join(line.strip().split())
        lowered = normalized.lower()
        if "<button" not in lowered and "role=button" not in lowered:
            continue
        if any(fragment in lowered for fragment in FORBIDDEN_ACTION_TARGET_FRAGMENTS):
            continue
        if "搜 索" not in normalized and "搜索" not in normalized and "查询" not in normalized:
            continue
        refs = _extract_refs_from_line(line)
        if refs:
            return refs[0]
    return None


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
    if host in LIEPIN_RISK_HOSTS:
        return "liepin_opencli_risk_page"
    if host not in LIEPIN_ALLOWED_HOSTS:
        return "liepin_opencli_host_blocked"
    if _is_forbidden_liepin_url(url):
        return "liepin_opencli_unknown_modal"
    if host == "lpt.liepin.com" and ("身份" in text or "请选择" in text):
        return "liepin_opencli_identity_intercept"
    if _looks_like_login_required(text):
        return "liepin_opencli_login_required"
    if "验证码" in text or "安全验证" in text or "风险提示" in text or re.search(r"\bcaptcha\b", lowered):
        return "liepin_opencli_risk_page"
    if any(marker in text for marker in ("联系候选人", "查看联系方式", "聊天弹窗", "下载简历", "付费查看", "购买套餐")):
        return "liepin_opencli_unknown_modal"
    return None


def extract_liepin_card_summaries(text: str, *, max_cards: int) -> tuple[dict[str, object], ...]:
    lines = clean_state_lines(text)
    cards: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not looks_like_liepin_card_start(line):
            continue
        block_lines = lines[index : index + 12]
        block = "\n".join(block_lines)
        if not looks_like_liepin_card(block):
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
        clean_block = _card_block_around_detail_line(lines, index)
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


def _card_block_around_detail_line(lines: Sequence[str], index: int) -> str:
    previous_detail = None
    for cursor in range(index - 1, -1, -1):
        if _line_has_detail_open_label(lines[cursor]):
            previous_detail = cursor
            break
    next_detail = None
    for cursor in range(index + 1, len(lines)):
        if _line_has_detail_open_label(lines[cursor]):
            next_detail = cursor
            break

    forward_end = min(next_detail if next_detail is not None else len(lines), index + 12)
    forward = "\n".join(clean_state_lines("\n".join(lines[index:forward_end])))
    if looks_like_liepin_card(forward):
        return forward

    backward_start = max((previous_detail + 1) if previous_detail is not None else 0, index - 8)
    backward = "\n".join(clean_state_lines("\n".join(lines[backward_start : index + 1])))
    if looks_like_liepin_card(backward):
        return backward

    return backward


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
        clean_block = clean_liepin_result_card_text(text)
        if not clean_block or not looks_like_liepin_card(clean_block):
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
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme in {"http", "https"}
        and (hostname == "liepin.com" or hostname.endswith(".liepin.com"))
        and (parsed.path or "").startswith("/resume/showresumedetail")
    )


def _is_blank_tab_url(url: str) -> bool:
    normalized = url.strip().casefold()
    return normalized in {"", "about:blank"} or normalized.startswith("about:blank?")


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


def _safe_detail_payload_from_probe_output(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
    if not isinstance(payload, dict):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    if payload.get("ok") is False:
        raise OpenCliBrowserError(str(payload.get("safeReasonCode") or "liepin_opencli_detail_not_opened"))
    if not isinstance(payload.get("candidate_name"), str) or not payload["candidate_name"].strip():
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    return payload


def _detail_provider_key_material(*, safe_run_id: str, rank: int, payload: Mapping[str, object]) -> str:
    digest = hashlib.sha256(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return f"liepin-opencli-detail:{safe_run_id}:{rank}:{digest}"



def _safe_card_summary_from_block(block: str) -> dict[str, object]:
    normalized_block = _bounded_public_text(block, max_chars=900)
    company, title = _company_title_from_block(block)
    job_intention = _job_intention_from_block(block)
    work_years = _int_match(block, r"工作\s*(\d+)\s*年|(\d+)\s*年经验")
    age = _int_match(block, r"(\d{2})\s*岁")
    city = _city_from_block(block)
    education = education_from_block(block)
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


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default
    return default


def _bounded_public_text(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:max_chars]


def _safe_visible_card_text(text: str) -> str:
    return _bounded_public_text(clean_liepin_result_card_text(text), max_chars=1_200)


def _safe_artifact_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return segment[:80] or "run"


def _is_forbidden_liepin_url(url: str) -> bool:
    parsed = urlparse(url)
    path = unquote(parsed.path or "").lower()
    return any(fragment in path for fragment in FORBIDDEN_LIEPIN_PATH_FRAGMENTS)


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed > 0 else None


def _string_key_mapping_or_none(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


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


def _fixed_readonly_eval_probe_script(*, probe_name: str, ref: str) -> str:
    if not _is_safe_page_id(ref):
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    if probe_name == "liepin_detail_resume_payload":
        return _liepin_detail_resume_payload_probe_script()
    if probe_name != "liepin_detail_url_for_card":
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    return (
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


def _liepin_detail_resume_payload_probe_script() -> str:
    return r"""
(() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const text = (node) => clean(node && (node.innerText || node.textContent));
  const splitSep = (node) =>
    Array.from(node ? node.childNodes : [])
      .map((child) => clean(child.textContent))
      .filter(Boolean);
  const lines = (node) =>
    String((node && node.innerText) || "")
      .split(/\n+/)
      .map(clean)
      .filter(Boolean);
  const bounded = (value, max) => {
    const next = clean(value);
    return next ? next.slice(0, max) : null;
  };
  const intFrom = (value, pattern) => {
    const match = clean(value).match(pattern);
    return match ? Number(match[1]) : null;
  };
  const labelValue = (line) => {
    const match = clean(line).match(/^([^:：]{1,16})[:：]\s*(.+)$/);
    return match ? [match[1], match[2]] : [null, null];
  };
  const afterLabel = (item, label) => {
    const itemLines = lines(item);
    const index = itemLines.findIndex((line) => line === label || line === `${label}：` || line === `${label}:`);
    if (index < 0) return null;
    const values = [];
    for (const line of itemLines.slice(index + 1)) {
      if (/^(工作地点|职责业绩|项目职务|所在公司|项目描述|项目业绩|询问TA)[:：]?$/.test(line)) break;
      if (line !== "询问TA") values.push(line);
    }
    return bounded(values.join("\n"), 6000);
  };
  const basic = document.querySelector("#resume-detail-basic-info");
  if (!basic) {
    return JSON.stringify({ ok: false, safeReasonCode: "liepin_opencli_detail_not_opened" });
  }
  const nameNode = basic.querySelector(".name[title], h4[title], .name, h4");
  const candidateName = bounded(nameNode && (nameNode.getAttribute("title") || nameNode.textContent), 120);
  if (!candidateName) {
    return JSON.stringify({ ok: false, safeReasonCode: "liepin_opencli_malformed_state" });
  }
  const profileRows = Array.from(basic.querySelectorAll(".basic-cont > .sep-info")).map(splitSep);
  const profile = profileRows[0] || [];
  const current = profileRows[1] || [];
  const jobIntention = {};
  const jobSection = document.querySelector("#resume-detail-job-exp-info");
  const jobLines = lines(jobSection).filter((line) => line !== "求职意向" && !line.startsWith("查看全部"));
  if (jobLines[0]) jobIntention.expectedRole = bounded(jobLines[0], 160);
  if (jobLines[1]) jobIntention.expectedSalary = bounded(jobLines[1], 120);
  if (jobLines[2]) jobIntention.expectedCity = bounded(jobLines[2], 200);
  if (jobLines[3]) jobIntention.expectedIndustry = bounded(jobLines.slice(3).join("、"), 200);
  const timelineItems = (sectionSelector, itemSelector, kind) => {
    const section = document.querySelector(sectionSelector);
    return Array.from(section ? section.querySelectorAll(itemSelector) : []).slice(0, 8).map((item) => {
      const itemLines = lines(item);
      const payload = {};
      const companyNode = item.querySelector("h5[title], h5");
      const titleNode = item.querySelector("h6[title], h6");
      const durationLine = itemLines.find((line) => /\d{4}\.\d{2}\s*-/.test(line)) || null;
      if (kind === "work") {
        payload.company = bounded(companyNode && (companyNode.getAttribute("title") || companyNode.textContent), 160);
        payload.title = bounded(titleNode && (titleNode.getAttribute("title") || titleNode.textContent), 160);
        payload.industry = bounded(text(item.querySelector("i")), 120);
        payload.location = afterLabel(item, "工作地点");
        payload.summary = afterLabel(item, "职责业绩");
        payload.description = payload.summary;
      } else if (kind === "project") {
        payload.name = bounded(itemLines[0], 180);
        payload.role = afterLabel(item, "项目职务");
        payload.company = afterLabel(item, "所在公司");
        payload.summary = afterLabel(item, "项目描述") || afterLabel(item, "项目业绩");
        payload.description = payload.summary;
      } else if (kind === "education") {
        const schoolLine = text(item.querySelector(".edu-school-cont")) || itemLines[0] || "";
        const schoolParts = schoolLine.split("·").map(clean).filter(Boolean);
        payload.school = bounded(schoolParts[0], 160);
        payload.major = bounded(schoolParts[1], 160);
        payload.degree = bounded((schoolParts[2] || "").replace(/\d{4}\.\d{2}.*$/, ""), 80);
        payload.summary = bounded(
          [
            text(item.querySelector(".edu-school-tags")),
            text(item.querySelector(".edu-school-exp")),
          ].filter(Boolean).join("\n"),
          2000
        );
      }
      if (durationLine) {
        payload.duration = bounded(durationLine.replace(/[（）]/g, ""), 120);
        payload.dateRange = payload.duration;
      }
      return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== null && value !== undefined && value !== ""));
    }).filter((item) => Object.keys(item).length > 0);
  };
  const skills = Array.from(document.querySelectorAll("#resume-detail-skill-info .skill-tag"))
    .map((node) => text(node))
    .filter((item, index, items) => item && items.indexOf(item) === index);
  return JSON.stringify({
    candidate_name: candidateName,
    activeStatus: bounded(text(basic.querySelector(".res-online-desc")), 120),
    jobStatus: bounded(text(basic.querySelector(".user-status-tag")), 120),
    gender: profile.find((item) => item === "男" || item === "女") || null,
    age: intFrom(profile.join(" "), /(\d{1,2})岁/),
    city: profile.find((item) => !/^(男|女|\d{1,2}岁|本科|硕士|博士|大专|工作\d{1,2}年|群众|共青团员|党员)$/.test(item)) || null,
    education: profile.find((item) => /^(本科|硕士|博士|大专)$/.test(item)) || null,
    workYears: intFrom(profile.join(" "), /工作(\d{1,2})年/),
    currentTitle: bounded(current[0], 180),
    currentCompany: bounded(current[1], 180),
    jobIntention,
    workExperienceList: timelineItems("#resume-detail-work-info", ".rd-work-item-cont", "work"),
    projectExperienceList: timelineItems("#resume-detail-project-info", ".rd-project-item-cont, .rd-info-tpl-item", "project"),
    educationList: timelineItems("#resume-detail-edu-info", ".resume-edu-info-item-wrap", "education"),
    skills,
    fullText: bounded(text(document.querySelector("#resume-detail-single")), 20000)
  });
})()
"""

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


def _url_matches_start_or_detail_surface(url: str, requested_url: str) -> bool:
    if _is_liepin_detail_url(requested_url):
        return _is_liepin_detail_url(url)
    return _url_matches_start_surface(url, requested_url)


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

def _is_safe_page_id(value: str) -> bool:
    return bool(_SAFE_PAGE_ID_PATTERN.fullmatch(value))


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:128] or "default"
