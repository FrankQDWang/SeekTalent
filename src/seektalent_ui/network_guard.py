from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Iterable
from urllib.parse import urlparse


WORKBENCH_GUARDED_PREFIXES = ("/api/auth", "/api/workbench")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
LOCAL_DEV_FRONTEND_ORIGINS = {"http://localhost:5176", "http://127.0.0.1:5176"}


@dataclass(frozen=True)
class NetworkGuard:
    bind_host: str
    port: int
    lan_enabled: bool
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]


def build_network_guard(
    *,
    bind_host: str,
    port: int,
    lan_enabled: bool,
    allowed_hosts: Iterable[str] | None = None,
    allowed_origins: Iterable[str] | None = None,
) -> NetworkGuard:
    hosts = {_normalize_host(host) for host in (allowed_hosts or []) if host.strip()}
    origins = {_normalize_origin(origin) for origin in (allowed_origins or []) if origin.strip()}
    hosts.update(LOCAL_HOSTS)
    normalized_bind = _normalize_host(bind_host)
    if normalized_bind and normalized_bind not in {"0.0.0.0", "::", "*"}:
        hosts.add(normalized_bind)
    if _is_loopback_host(bind_host):
        origins.update(LOCAL_DEV_FRONTEND_ORIGINS)
    return NetworkGuard(
        bind_host=bind_host,
        port=port,
        lan_enabled=lan_enabled,
        allowed_hosts=frozenset(hosts),
        allowed_origins=frozenset(origins),
    )


def require_allowed_bind(bind_host: str, *, lan_flag: bool) -> None:
    if _is_loopback_host(bind_host):
        return
    if lan_flag or os.environ.get("SEEKTALENT_UI_LAN") == "1":
        return
    raise ValueError("Non-loopback UI bind requires --lan or SEEKTALENT_UI_LAN=1.")


def host_allowed(host_header: str | None, guard: NetworkGuard | None) -> bool:
    if guard is None:
        return True
    host = _normalize_host(host_header or "")
    return host in guard.allowed_hosts


def origin_allowed(origin: str | None, host_header: str | None, scheme: str, guard: NetworkGuard | None) -> bool:
    if origin is None:
        return True
    normalized_origin = _normalize_origin(origin)
    if normalized_origin == _same_origin(host_header, scheme):
        return True
    if guard is None:
        return False
    return normalized_origin in guard.allowed_origins


def is_workbench_path(path: str) -> bool:
    return path.startswith(WORKBENCH_GUARDED_PREFIXES)


def is_packaged_frontend_path(path: str) -> bool:
    return path != "/api" and not path.startswith("/api/")


def is_guarded_workbench_path(path: str, *, serve_frontend: bool = False) -> bool:
    return is_workbench_path(path) or (serve_frontend and is_packaged_frontend_path(path))


def render_startup_diagnostics(guard: NetworkGuard) -> str:
    allowed_hosts = ", ".join(sorted(guard.allowed_hosts))
    allowed_origins = ", ".join(sorted(guard.allowed_origins)) or "same-origin only"
    scheme = "http"
    return "\n".join(
        [
            f"SeekTalent UI bind: {guard.bind_host}:{guard.port}",
            f"SeekTalent UI URL: {scheme}://{guard.bind_host}:{guard.port}",
            f"Allowed Host headers: {allowed_hosts}",
            f"Allowed Origins: {allowed_origins}",
            "Cookie posture: HTTP cookies are not Secure; HTTPS requests set Secure cookies.",
            "Network posture: trusted proxy headers ignored by default.",
        ]
    )


def _normalize_host(host: str) -> str:
    value = host.strip().lower()
    if not value:
        return value
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[1:end]
    if ":" in value and value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def _normalize_origin(origin: str) -> str:
    parsed = urlparse(origin.strip())
    if not parsed.scheme or not parsed.netloc:
        return origin.strip().lower().rstrip("/")
    scheme = parsed.scheme.lower()
    return f"{scheme}://{parsed.netloc.lower()}"


def _same_origin(host_header: str | None, scheme: str) -> str:
    return f"{scheme.lower()}://{(host_header or '').strip().lower()}"


def _is_loopback_host(host: str) -> bool:
    normalized = _normalize_host(host)
    if normalized in {"localhost", "testserver"}:
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False
