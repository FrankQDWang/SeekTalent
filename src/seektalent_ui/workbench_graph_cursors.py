from __future__ import annotations

import base64
import binascii
import hashlib
import hmac


def encode_graph_candidate_cursor(offset: int, *, session_id: str, node_id: str, secret: str) -> str:
    offset_bytes = offset.to_bytes(8, byteorder="big", signed=False)
    pad = _cursor_pad(secret=secret, session_id=session_id, node_id=node_id)
    masked_offset = bytes(left ^ right for left, right in zip(offset_bytes, pad, strict=True))
    signature = hmac.new(
        secret.encode("utf-8"),
        b"cursor-v1:" + session_id.encode("utf-8") + b":" + node_id.encode("utf-8") + b":" + masked_offset,
        hashlib.sha256,
    ).digest()[:16]
    return "cur_" + base64.urlsafe_b64encode(masked_offset + signature).decode("ascii").rstrip("=")


def decode_graph_candidate_cursor(cursor: str, *, session_id: str, node_id: str, secret: str) -> int | None:
    if not cursor.startswith("cur_"):
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor[4:] + "=" * (-len(cursor[4:]) % 4))
    except (ValueError, binascii.Error):
        return None
    if len(raw) != 24:
        return None
    masked_offset = raw[:8]
    signature = raw[8:]
    expected = hmac.new(
        secret.encode("utf-8"),
        b"cursor-v1:" + session_id.encode("utf-8") + b":" + node_id.encode("utf-8") + b":" + masked_offset,
        hashlib.sha256,
    ).digest()[:16]
    if not hmac.compare_digest(signature, expected):
        return None
    pad = _cursor_pad(secret=secret, session_id=session_id, node_id=node_id)
    offset_bytes = bytes(left ^ right for left, right in zip(masked_offset, pad, strict=True))
    return int.from_bytes(offset_bytes, byteorder="big", signed=False)


def _cursor_pad(*, secret: str, session_id: str, node_id: str) -> bytes:
    return hmac.new(
        secret.encode("utf-8"),
        b"cursor-pad-v1:" + session_id.encode("utf-8") + b":" + node_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()[:8]
