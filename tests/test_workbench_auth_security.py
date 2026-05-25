from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from pathlib import Path

from fastapi import APIRouter
from fastapi.testclient import TestClient

from seektalent_ui.server import create_app
from tests.settings_factory import make_settings


SESSION_COOKIE_NAME = "seektalent_workbench_session"
CSRF_COOKIE_NAME = "seektalent_workbench_csrf"


def _app(tmp_path: Path):
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    return create_app(settings=settings)


def _client(tmp_path: Path, *, base_url: str = "http://localhost") -> TestClient:
    return TestClient(_app(tmp_path), base_url=base_url, client=("127.0.0.1", 50000))


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / ".seektalent" / "workbench.sqlite3"


def _bootstrap_admin(client: TestClient, *, email: str = "admin@example.com", password: str = "correct horse") -> dict:
    response = client.post(
        "/api/auth/bootstrap",
        json={"email": email, "password": password, "displayName": "Admin User"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, *, email: str = "admin@example.com", password: str = "correct horse"):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 204, response.text
    return response


def _create_session(client: TestClient, csrf_token: str):
    response = client.post(
        "/api/workbench/sessions",
        headers={"X-CSRF-Token": csrf_token},
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )
    assert response.status_code == 201, response.text
    return response


def _session_cookie_value(response) -> str:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie[SESSION_COOKIE_NAME].value


def _csrf_token(client: TestClient) -> str:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return token


def _active_session_digest(tmp_path: Path) -> str:
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        row = conn.execute("SELECT session_id FROM user_sessions WHERE revoked_at IS NULL").fetchone()
    assert row is not None
    return row[0]


def test_workbench_auth_routes_are_exposed_by_router_module(tmp_path: Path) -> None:
    from seektalent_ui import workbench_routes

    assert isinstance(workbench_routes.router, APIRouter)

    client = _client(tmp_path)
    paths = {route.path for route in client.app.routes}
    assert "/api/auth/bootstrap" in paths
    assert "/api/auth/login" in paths
    assert "/api/auth/logout" in paths
    assert "/api/auth/me" in paths


def test_bootstrap_creates_first_admin_once(tmp_path: Path) -> None:
    client = _client(tmp_path)

    payload = _bootstrap_admin(client)

    assert payload["user"]["email"] == "admin@example.com"
    assert payload["user"]["role"] == "admin"
    assert payload["workspace"]["id"] == "default"

    second = client.post(
        "/api/auth/bootstrap",
        json={"email": "second@example.com", "password": "second passphrase", "displayName": "Second"},
    )
    assert second.status_code == 409


def test_passwords_are_stored_as_salted_hashes_only(tmp_path: Path) -> None:
    client = _client(tmp_path)
    plaintext = "not persisted password"

    _bootstrap_admin(client, password=plaintext)

    with sqlite3.connect(_db_path(tmp_path)) as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE email = ?", ("admin@example.com",)).fetchone()
        assert row is not None
        stored = row[0]
        assert plaintext not in stored
        assert stored.startswith("pbkdf2_sha256$")
        assert len(stored.split("$")) == 4

        raw_db = _db_path(tmp_path).read_bytes()
        assert plaintext.encode("utf-8") not in raw_db


def test_login_sets_scoped_httponly_cookie_and_rotates_session_ids(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)

    first = _login(client)
    first_cookie = _session_cookie_value(first)
    second = _login(client)
    second_cookie = _session_cookie_value(second)

    assert first_cookie != second_cookie
    set_cookie_headers = second.headers.get_list("set-cookie")
    assert len(set_cookie_headers) == 2
    set_cookie = next(header for header in set_cookie_headers if header.startswith(f"{SESSION_COOKIE_NAME}="))
    csrf_cookie = next(header for header in set_cookie_headers if header.startswith(f"{CSRF_COOKIE_NAME}="))
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Path=/api" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" not in set_cookie
    assert "correct horse" not in set_cookie

    with sqlite3.connect(_db_path(tmp_path)) as conn:
        rows = conn.execute("SELECT session_id, revoked_at FROM user_sessions ORDER BY issued_at").fetchall()
    assert len(rows) == 2
    assert rows[0][0] != first_cookie
    assert rows[1][0] != second_cookie
    assert rows[0][1] is not None
    assert rows[1][1] is None


def test_session_cookie_secure_policy_tracks_request_context(tmp_path: Path) -> None:
    local_client = _client(tmp_path, base_url="http://localhost")
    _bootstrap_admin(local_client)

    local_login = _login(local_client)
    assert "Secure" not in local_login.headers["set-cookie"]

    https_client = TestClient(_app(tmp_path), base_url="https://localhost", client=("127.0.0.1", 50000))
    https_login = _login(https_client)
    assert "Secure" in https_login.headers["set-cookie"]

    remote_http_client = TestClient(
        _app(tmp_path),
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )
    remote_login = _login(remote_http_client)
    assert "Secure" not in remote_login.headers["set-cookie"]
    me = remote_http_client.get("/api/auth/me")
    assert me.status_code == 200


def test_user_sessions_store_only_session_token_digest(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)

    login_response = _login(client)
    raw_token = _session_cookie_value(login_response)
    raw_csrf = login_response.headers["x-csrf-token"]

    with sqlite3.connect(_db_path(tmp_path)) as conn:
        row = conn.execute("SELECT session_id, csrf_token_digest FROM user_sessions WHERE revoked_at IS NULL").fetchone()
    assert row is not None
    assert row[0] != raw_token
    assert row[1] != raw_csrf
    db_bytes = _db_path(tmp_path).read_bytes()
    assert raw_token.encode("utf-8") not in db_bytes
    assert raw_csrf.encode("utf-8") not in db_bytes

    me = client.get("/api/auth/me")
    assert me.status_code == 200


def test_login_exposes_csrf_header_for_configured_workbench_origin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)

    login_response = client.post(
        "/api/auth/login",
        headers={"Origin": "http://localhost"},
        json={"email": "admin@example.com", "password": "correct horse"},
    )
    csrf_token = login_response.headers.get("x-csrf-token")
    assert login_response.status_code == 204
    assert csrf_token
    assert login_response.headers["access-control-allow-origin"] == "http://localhost"
    assert login_response.headers["access-control-expose-headers"] == "X-CSRF-Token"

    _create_session(client, csrf_token)


def test_me_refreshes_exposed_csrf_token_for_existing_session(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)
    login_response = _login(client)
    login_csrf_token = login_response.headers["x-csrf-token"]

    me = client.get("/api/auth/me", headers={"Origin": "http://localhost"})
    refreshed_csrf_token = me.headers.get("x-csrf-token")

    assert me.status_code == 200
    assert refreshed_csrf_token
    assert refreshed_csrf_token != login_csrf_token
    assert me.headers["access-control-expose-headers"] == "X-CSRF-Token"
    assert refreshed_csrf_token.encode("utf-8") not in _db_path(tmp_path).read_bytes()
    _create_session(client, refreshed_csrf_token)


def test_bootstrap_is_loopback_only_but_login_allows_remote_clients(tmp_path: Path) -> None:
    app = _app(tmp_path)
    remote_client = TestClient(app, base_url="http://recruiting.internal", client=("203.0.113.10", 50000))

    blocked = remote_client.post(
        "/api/auth/bootstrap",
        json={"email": "admin@example.com", "password": "correct horse", "displayName": "Admin User"},
    )
    assert blocked.status_code == 403

    local_client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    _bootstrap_admin(local_client)

    login = remote_client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"})
    assert login.status_code == 204


def test_me_rejects_missing_expired_and_revoked_sessions(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)

    missing = client.get("/api/auth/me")
    assert missing.status_code == 401

    login_response = _login(client)
    session_id = _active_session_digest(tmp_path)
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "admin@example.com"

    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.execute("UPDATE user_sessions SET expires_at = ? WHERE session_id = ?", (expired_at, session_id))
    expired = client.get("/api/auth/me")
    assert expired.status_code == 401

    login_response = _login(client)
    logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": _csrf_token(client)})
    assert logout.status_code == 204
    assert "Max-Age=0" in logout.headers["set-cookie"]
    client.cookies.set(SESSION_COOKIE_NAME, _session_cookie_value(login_response), path="/api")
    revoked = client.get("/api/auth/me")
    assert revoked.status_code == 401


def test_logout_requires_session_bound_csrf_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)
    _login(client)
    csrf_token = _csrf_token(client)

    missing = client.post("/api/auth/logout")
    assert missing.status_code == 403

    wrong = client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong-token"})
    assert wrong.status_code == 403

    valid = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert valid.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_disabled_users_cannot_login_or_keep_using_existing_sessions(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)
    _login(client)

    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE users SET disabled_at = ? WHERE email = ?",
            (datetime.now(UTC).isoformat(timespec="seconds"), "admin@example.com"),
        )

    existing_session = client.get("/api/auth/me")
    assert existing_session.status_code == 401

    login = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"})
    assert login.status_code == 401
    assert login.json() == {"detail": "Invalid email or password."}

    with sqlite3.connect(_db_path(tmp_path)) as conn:
        reason = conn.execute(
            "SELECT reason FROM login_attempts WHERE email = ? ORDER BY created_at DESC LIMIT 1",
            ("admin@example.com",),
        ).fetchone()
    assert reason == ("disabled_user",)


def test_missing_and_disabled_login_share_external_failure_shape(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE users SET disabled_at = ? WHERE email = ?",
            (datetime.now(UTC).isoformat(timespec="seconds"), "admin@example.com"),
        )

    disabled = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"})
    missing = client.post("/api/auth/login", json={"email": "missing@example.com", "password": "correct horse"})

    assert disabled.status_code == missing.status_code == 401
    assert disabled.json() == missing.json() == {"detail": "Invalid email or password."}


def test_repeated_failed_logins_temporarily_lock_account_ip_boundary(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)

    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "wrong password"},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid email or password."}

    locked = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"})
    assert locked.status_code == 401
    assert locked.json() == {"detail": "Invalid email or password."}

    with sqlite3.connect(_db_path(tmp_path)) as conn:
        reasons = [
            row[0]
            for row in conn.execute(
                "SELECT reason FROM login_attempts WHERE email = ? ORDER BY created_at",
                ("admin@example.com",),
            ).fetchall()
        ]
    assert reasons[-1] == "locked_out"


def test_failed_login_attempts_record_metadata_without_secrets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)

    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "wrong password"},
        headers={"User-Agent": "pytest-agent"},
    )

    assert response.status_code == 401
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM login_attempts").fetchone()
    assert row is not None
    assert row["email"] == "admin@example.com"
    assert row["success"] == 0
    assert row["reason"] == "invalid_credentials"
    assert row["user_agent"] == "pytest-agent"
    serialized = " ".join(str(value) for value in dict(row).values())
    assert "wrong password" not in serialized
    assert SESSION_COOKIE_NAME not in serialized
    assert "session" not in dict(row)


def test_failed_login_attempt_truncates_oversized_user_agent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_admin(client)
    huge_user_agent = "pytest-agent/" + ("x" * 100_000)
    wrong_password = "wrong password"

    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": wrong_password},
        headers={"User-Agent": huge_user_agent},
    )

    assert response.status_code == 401
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT email, reason, ip_address, user_agent FROM login_attempts").fetchone()
    assert row is not None
    assert row["email"] == "admin@example.com"
    assert row["reason"] == "invalid_credentials"
    assert row["ip_address"] == "127.0.0.1"
    assert row["user_agent"].startswith("pytest-agent/")
    assert len(row["user_agent"]) == 512
    serialized = " ".join(str(value) for value in dict(row).values())
    assert wrong_password not in serialized
    assert SESSION_COOKIE_NAME not in serialized


def test_auth_payloads_reject_oversized_public_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)

    huge_password = "x" * 1025
    huge_display_name = "x" * 129
    huge_email = "a" * 245 + "@example.com"

    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"email": huge_email, "password": huge_password, "displayName": huge_display_name},
    )
    assert bootstrap.status_code == 400

    _bootstrap_admin(client)
    login = client.post("/api/auth/login", json={"email": "admin@example.com", "password": huge_password})
    assert login.status_code == 400
