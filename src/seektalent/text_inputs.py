from __future__ import annotations

from pathlib import Path


def read_required_inline_or_file_text(
    *,
    inline_value: str | None,
    file_value: str | None,
    label: str,
) -> str:
    if inline_value is not None and file_value is not None:
        raise ValueError(f"Use only one of --{label} or --{label}-file.")
    if file_value is not None:
        return Path(file_value).read_text(encoding="utf-8")
    if inline_value:
        return inline_value
    raise ValueError(f"{label} is required via --{label} or --{label}-file.")


def read_optional_inline_or_file_text(
    *,
    inline_value: str | None,
    file_value: str | None,
    label: str,
) -> str:
    if inline_value is not None and file_value is not None:
        raise ValueError(f"Use only one of --{label} or --{label}-file.")
    if file_value is not None:
        return Path(file_value).read_text(encoding="utf-8")
    return inline_value or ""
