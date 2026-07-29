"""Sidecar-private durable locator cache for Liepin detail URLs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from seektalent.providers.liepin.liepin_site_parsing import (
    stable_liepin_detail_candidate_key_hash,
)
from seektalent.strict_json import strict_json_object_loads

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_CARD_REF_CHARS = 96


@dataclass(frozen=True, slots=True)
class LiepinDetailLocator:
    provider_candidate_key_hash: str
    detail_url: str
    card_ref: str
    rank: int


def remember_liepin_detail_locator(
    root: Path,
    *,
    provider_candidate_key_hash: str,
    detail_url: str,
    card_ref: str,
    rank: int,
) -> None:
    locator = _validated_locator(
        provider_candidate_key_hash=provider_candidate_key_hash,
        detail_url=detail_url,
        card_ref=card_ref,
        rank=rank,
        requested_hash=provider_candidate_key_hash,
    )
    path = _locator_path(root, locator.provider_candidate_key_hash)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(
        {
            "provider_candidate_key_hash": locator.provider_candidate_key_hash,
            "detail_url": locator.detail_url,
            "card_ref": locator.card_ref,
            "rank": locator.rank,
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
) -> LiepinDetailLocator | None:
    """Return the stored locator, or None only when no locator file exists."""
    _require_hash(provider_candidate_key_hash)
    path = _locator_path(root, provider_candidate_key_hash)
    if not path.is_file():
        return None
    raw = strict_json_object_loads(path.read_bytes())
    stored_hash = raw.get("provider_candidate_key_hash")
    detail_url = raw.get("detail_url")
    card_ref = raw.get("card_ref")
    rank = raw.get("rank")
    if not isinstance(stored_hash, str):
        raise ValueError("liepin_details_locator_hash_invalid")
    if not isinstance(detail_url, str):
        raise ValueError("liepin_details_locator_detail_url_invalid")
    if not isinstance(card_ref, str):
        raise ValueError("liepin_details_locator_card_ref_invalid")
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise ValueError("liepin_details_locator_rank_invalid")
    return _validated_locator(
        provider_candidate_key_hash=stored_hash,
        detail_url=detail_url,
        card_ref=card_ref,
        rank=rank,
        requested_hash=provider_candidate_key_hash,
    )


def _validated_locator(
    *,
    provider_candidate_key_hash: str,
    detail_url: str,
    card_ref: str,
    rank: int,
    requested_hash: str,
) -> LiepinDetailLocator:
    _require_hash(provider_candidate_key_hash)
    _require_hash(requested_hash)
    if provider_candidate_key_hash != requested_hash:
        raise ValueError("liepin_details_locator_hash_mismatch")
    if stable_liepin_detail_candidate_key_hash(detail_url) != provider_candidate_key_hash:
        raise ValueError("liepin_details_locator_detail_url_mismatch")
    trimmed_ref = card_ref.strip()
    if not trimmed_ref or len(trimmed_ref) > _MAX_CARD_REF_CHARS:
        raise ValueError("liepin_details_locator_card_ref_invalid")
    if rank < 1 or rank > 100:
        raise ValueError("liepin_details_locator_rank_invalid")
    return LiepinDetailLocator(
        provider_candidate_key_hash=provider_candidate_key_hash,
        detail_url=detail_url,
        card_ref=trimmed_ref,
        rank=rank,
    )


def _require_hash(value: str) -> None:
    if _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("liepin_details_locator_hash_invalid")


def _locator_path(root: Path, provider_candidate_key_hash: str) -> Path:
    return root.resolve(strict=False) / f"{provider_candidate_key_hash}.json"


__all__ = [
    "LiepinDetailLocator",
    "load_liepin_detail_locator",
    "remember_liepin_detail_locator",
]
