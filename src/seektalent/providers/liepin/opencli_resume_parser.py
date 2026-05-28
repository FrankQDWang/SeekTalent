from __future__ import annotations

import re
from collections.abc import Sequence


_CONTACT_TEXT_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|(?:\+?86[-\s]?)?1[3-9]\d{9}\b|"
    r"(?:手机|电话|邮箱|微信|weixin|wechat|wx[:：])",
    re.IGNORECASE,
)

_DROP_LINE_MARKERS = (
    "首页",
    "搜索",
    "筛选",
    "推荐职位",
    "联系候选人",
    "查看联系方式",
    "聊天",
    "下载简历",
    "付费",
    "购买",
    "广告",
    "人才推荐",
)


def build_liepin_opencli_detail_payload(text: str) -> dict[str, object]:
    lines = _resume_lines(text)
    if not lines:
        raise ValueError("liepin_opencli_resume_text_empty")
    full_text = _bounded_public_text("\n".join(lines), max_chars=12_000)
    company, title = _company_title_from_text(full_text)
    title = title or _field_value(full_text, ("当前职位", "职位", "求职意向"))
    education_items = [
        {"school": school, "degree": _education_from_text(full_text), "speciality": None}
        for school in _school_names_from_text(full_text)
    ]
    return {
        "fullText": full_text,
        "currentTitle": title,
        "currentCompany": company,
        "workExperienceList": [
            {"company": company, "title": title, "summary": _recent_experience_from_text(full_text)}
        ],
        "educationList": education_items,
        "skills": _skill_tags_from_text(full_text),
        "locations": [city] if (city := _city_from_text(full_text)) else [],
    }


def _resume_lines(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = re.sub(r"\[[^\]]+\]", "", raw_line)
        line = re.sub(r"<[^>]*>", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if _CONTACT_TEXT_PATTERN.search(line):
            continue
        if any(marker in line for marker in _DROP_LINE_MARKERS):
            continue
        if line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result


def _bounded_public_text(text: str, *, max_chars: int) -> str:
    return text[:max_chars]


def _field_value(text: str, labels: Sequence[str]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}[:：]\s*([^\n]{{2,80}})", text)
        if match:
            return _bounded_public_text(match.group(1).strip(), max_chars=80)
    return None


def _company_title_from_text(text: str) -> tuple[str | None, str | None]:
    company = _field_value(text, ("当前公司", "公司"))
    title = _field_value(text, ("当前职位", "职位"))
    return company, title


def _recent_experience_from_text(text: str) -> str:
    match = re.search(r"(工作经历|项目经历)\n(?P<body>[\s\S]{1,800})", text)
    return _bounded_public_text((match.group("body") if match else text).strip(), max_chars=600)


def _education_from_text(text: str) -> str | None:
    for degree in ("博士", "硕士", "本科", "大专"):
        if degree in text:
            return degree
    return None


def _school_names_from_text(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"([\u4e00-\u9fa5A-Za-z0-9]{2,30}(?:大学|学院|University))", text)))


def _skill_tags_from_text(text: str) -> list[str]:
    tags = []
    for skill in ("Python", "SQL", "Flink", "Spark", "Kafka", "ClickHouse", "MySQL", "ETL"):
        if re.search(re.escape(skill), text, re.IGNORECASE):
            tags.append(skill)
    return tags


def _city_from_text(text: str) -> str | None:
    for city in ("北京", "上海", "深圳", "杭州", "广州", "成都"):
        if city in text:
            return city
    return None
