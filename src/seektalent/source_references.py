from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def canonicalize_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("source reference URL must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("source reference URL must not contain userinfo")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("source reference URL has an invalid port") from exc
        if (scheme, port) in {("http", 80), ("https", 443)}:
            port = None
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        netloc = host if port is None else f"{host}:{port}"
        return urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))
