"""Sidecar-private durable locator cache for Liepin detail URLs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from seektalent.strict_json import strict_json_object_loads


def remember_liepin_detail_locator(
    root: Path,
    *,
    provider_candidate_key_hash: str,
    detail_url: str,
    card_ref: str,
    rank: int,
) -> None:
    if len(provider_candidate_key_hash) != 64:
        raise ValueError("liepin_details_locator_hash_invalid")
    path = _locator_path(root, provider_candidate_key_hash)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(
        {
            "provider_candidate_key_hash": provider_candidate_key_hash,
            "detail_url": detail_url,
            "card_ref": card_ref,
            "rank": rank,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    tmp = path.with_suffix(".tmp")
    descriptor = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_liepin_detail_locator(
    root: Path,
    provider_candidate_key_hash: str,
) -> dict[str, object] | None:
    path = _locator_path(root, provider_candidate_key_hash)
    if not path.is_file():
        return None
    raw = strict_json_object_loads(path.read_bytes())
    detail_url = raw.get("detail_url")
    if not isinstance(detail_url, str) or not detail_url.strip():
        return None
    return raw


def _locator_path(root: Path, provider_candidate_key_hash: str) -> Path:
    return root.resolve(strict=False) / f"{provider_candidate_key_hash}.json"


__all__ = [
    "load_liepin_detail_locator",
    "remember_liepin_detail_locator",
]
