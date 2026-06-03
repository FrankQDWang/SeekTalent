from __future__ import annotations

import re


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
ENGLISH_EDUCATION_LABELS = {
    "phd": "博士",
    "doctor": "博士",
    "master": "硕士",
    "msc": "硕士",
    "mba": "硕士",
    "bachelor": "本科",
    "bsc": "本科",
    "ba": "本科",
    "associate": "大专",
}
ENGLISH_EDUCATION_RE = re.compile(
    r"(?<![A-Za-z])(phd|doctor|master|msc|mba|bachelor|bsc|ba|associate)(?=\b|[A-Z\u4e00-\u9fa5])",
    re.IGNORECASE,
)


def looks_like_liepin_card_start(line: str) -> bool:
    return bool(re.search(r"\b\d{2}\s*岁\b|工作\s*\d+\s*年|\d+\s*年经验", line))


def clean_state_lines(text: str) -> list[str]:
    result: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\[[^\]]+\]", "", raw_line)
        line = re.sub(r"<[^>]*>", " ", line)
        line = re.sub(r"\b(?:aria-label|role|tabindex|title)\s*=\s*[^\s]+", " ", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+", " ", line).strip(" ·|")
        line = drop_accessibility_noise_tokens(line)
        if not line or len(line) > 240:
            continue
        if line in result[-2:]:
            continue
        result.append(line)
    return result


def clean_liepin_result_card_text(text: str) -> str:
    clean_block = "\n".join(clean_state_lines(text))
    if clean_block:
        return clean_block
    compact = re.sub(r"\[[^\]]+\]", "", text)
    compact = re.sub(r"<[^>]*>", " ", compact)
    compact = re.sub(r"\s+", " ", compact).strip(" ·|")
    compact = drop_accessibility_noise_tokens(compact)
    return compact[:1_200] if compact else ""


def looks_like_liepin_card(block: str) -> bool:
    if any(marker in block for marker in ("筛选", "搜索职位", "搜索公司", "高级搜索", "登录", "验证码")):
        return False
    has_profile_fact = bool(re.search(r"\b\d{2}\s*岁\b|工作\s*\d+\s*年|\d+\s*年经验", block))
    has_role = bool("求职期望" in block or "·" in block or re.search(r"\d{4}[./-]\d{2}", block))
    has_education = any(marker in block for marker in ("本科", "硕士", "博士", "大专", "统招")) or bool(
        ENGLISH_EDUCATION_RE.search(block)
    )
    return has_profile_fact and has_role and has_education


def education_from_block(block: str) -> str | None:
    for education in ("博士", "硕士", "本科", "大专"):
        if education in block:
            return education
    match = ENGLISH_EDUCATION_RE.search(block)
    if match:
        return ENGLISH_EDUCATION_LABELS[match.group(1).casefold()]
    return None


def drop_accessibility_noise_tokens(text: str) -> str:
    tokens = text.split()
    while tokens and tokens[0].lower() in ACCESSIBILITY_NOISE_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1].lower() in ACCESSIBILITY_NOISE_TOKENS:
        tokens.pop()
    return " ".join(tokens)
