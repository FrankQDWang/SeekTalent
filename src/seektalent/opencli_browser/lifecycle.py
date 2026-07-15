from __future__ import annotations

import hashlib


OPENCLI_OWNED_TAB_IDLE_SECONDS = 60


def browser_control_key(
    *,
    source_kind: str,
    browser_profile_id: str,
    provider_account_hash: str,
) -> str:
    parts = (source_kind.strip(), browser_profile_id.strip(), provider_account_hash.strip())
    if not all(parts):
        raise ValueError("browser control lane identity must be complete")
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"seektalent-browser-control-{digest}"
