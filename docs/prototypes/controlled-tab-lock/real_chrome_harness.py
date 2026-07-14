from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_CAPABILITIES = {
    "control-fence.v1",
    "tab.close-verified.v1",
    "tab.create-in-existing-window.v1",
    "tab.find.v1",
    "tab.idle-deadline.v1",
}


class PrototypeFailure(RuntimeError):
    pass


def _run(argv: list[str], *, env: dict[str, str] | None = None, timeout: int = 30) -> str:
    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "command failed").strip()
        raise PrototypeFailure(message)
    return completed.stdout.strip()


def _json_output(output: str) -> object:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:  # noqa: S112 - scan past CLI notices to the final JSON value.
            continue
        if not output[index + end :].strip():
            return value
    raise PrototypeFailure("OpenCLI did not return JSON")


def _daemon_status() -> dict[str, object] | None:
    request = urllib.request.Request(
        "http://127.0.0.1:19825/status",
        headers={"X-OpenCLI": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            value = json.load(response)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _wait_for_paired_bridge(main_js: Path, node: str) -> dict[str, object]:
    _run([node, str(main_js), "daemon", "restart"], timeout=20)
    deadline = time.monotonic() + 12
    status = None
    while time.monotonic() < deadline:
        status = _daemon_status()
        if status and status.get("extensionConnected") is True:
            break
        time.sleep(0.25)
    if not status:
        raise PrototypeFailure("OpenCLI fork daemon did not start")
    if status.get("implementation") != "seektalent-opencli":
        raise PrototypeFailure("connected daemon is not the SeekTalent OpenCLI fork")
    if status.get("extensionImplementation") != "seektalent-opencli":
        raise PrototypeFailure(
            "connected extension is not the SeekTalent fork; load and reload /Users/frankqdwang/Agents/OpenCLI/extension"
        )
    if status.get("bridgeBuildId") != status.get("extensionBridgeBuildId"):
        raise PrototypeFailure("OpenCLI fork daemon and extension build IDs do not match")
    capabilities = set(status.get("extensionCapabilities") or [])
    missing = REQUIRED_CAPABILITIES - capabilities
    if missing:
        raise PrototypeFailure(f"OpenCLI fork extension is missing capabilities: {', '.join(sorted(missing))}")
    return status


def _browser_command(
    node: str,
    main_js: Path,
    session: str,
    args: list[str],
    *,
    control_key: str | None = None,
    fence_token: int | None = None,
    idle_seconds: int = 60,
    timeout: int = 30,
) -> str:
    env = os.environ.copy()
    env["OPENCLI_BROWSER_IDLE_TIMEOUT"] = str(idle_seconds)
    env["OPENCLI_WINDOW"] = "background"
    if control_key is not None and fence_token is not None:
        env["OPENCLI_CONTROL_KEY"] = control_key
        env["OPENCLI_FENCE_TOKEN"] = str(fence_token)
    return _run(
        [node, str(main_js), "browser", session, *args],
        env=env,
        timeout=timeout,
    )


def _wait_for_url_prefix(
    node: str,
    main_js: Path,
    session: str,
    page: str,
    url_prefix: str,
    *,
    control_key: str,
    fence_token: int,
    idle_seconds: int,
) -> str:
    deadline = time.monotonic() + 10
    current_url = ""
    while time.monotonic() < deadline:
        current_url = _browser_command(
            node,
            main_js,
            session,
            ["get", "url", "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=idle_seconds,
        )
        if current_url.startswith(url_prefix):
            return current_url
        time.sleep(0.25)
    return current_url


def _pick_prototype_host(matches: object) -> dict[str, object]:
    if not isinstance(matches, list):
        raise PrototypeFailure("host discovery returned an invalid result")
    safe_matches = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        parsed = urlparse(str(item.get("url") or ""))
        if parsed.hostname != "h.liepin.com" or parsed.path.startswith("/resume/showresumedetail"):
            continue
        safe_matches.append(item)
    if len(safe_matches) == 1:
        return safe_matches[0]
    active = [item for item in safe_matches if item.get("active") is True and item.get("windowFocused") is True]
    if len(active) == 1:
        return active[0]
    focused = [item for item in safe_matches if item.get("windowFocused") is True]
    if len(focused) == 1:
        return focused[0]
    if not safe_matches:
        raise PrototypeFailure("no existing logged-in h.liepin.com tab was found")
    raise PrototypeFailure("multiple eligible h.liepin.com host tabs were found; host selection belongs to issue #291")


def _eval(
    node: str,
    main_js: Path,
    session: str,
    page: str,
    script: str,
    *,
    control_key: str,
    fence_token: int,
    idle_seconds: int,
) -> object:
    output = _browser_command(
        node,
        main_js,
        session,
        ["eval", script, "--tab", page],
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )
    return _json_output(output)


def _set_automation_active(
    node: str,
    main_js: Path,
    session: str,
    page: str,
    active: bool,
    *,
    control_key: str,
    fence_token: int,
    idle_seconds: int,
) -> None:
    active_js = "true" if active else "false"
    deadline_update = "" if active else f"api.updateDeadline(Date.now() + {idle_seconds * 1000});"
    _eval(
        node,
        main_js,
        session,
        page,
        "(() => { const api = window.__seektalentControlledTabLockV1; "
        "if (!api) return {installed:false}; "
        f"api.setAutomationActive({active_js}); {deadline_update} return api.snapshot(); }})()",
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )


def _automation_command(
    node: str,
    main_js: Path,
    session: str,
    page: str,
    args: list[str],
    *,
    control_key: str,
    fence_token: int,
    idle_seconds: int,
) -> str:
    _set_automation_active(
        node,
        main_js,
        session,
        page,
        True,
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )
    try:
        return _browser_command(
            node,
            main_js,
            session,
            [*args, "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=idle_seconds,
        )
    finally:
        try:
            _set_automation_active(
                node,
                main_js,
                session,
                page,
                False,
                control_key=control_key,
                fence_token=fence_token,
                idle_seconds=idle_seconds,
            )
        except PrototypeFailure as exc:
            print(f"prototype diagnostic: overlay relock failed: {exc}", file=sys.stderr)


def _install_overlay(
    overlay_source: str,
    node: str,
    main_js: Path,
    session: str,
    page: str,
    *,
    control_key: str,
    fence_token: int,
    idle_seconds: int,
) -> object:
    script = (
        f"window.__seektalentControlledTabLockDeadlineAt = Date.now() + {idle_seconds * 1000};\n"
        + overlay_source
    )
    return _eval(
        node,
        main_js,
        session,
        page,
        script,
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )


def _fixture_snapshot(
    node: str,
    main_js: Path,
    session: str,
    page: str,
    *,
    control_key: str,
    fence_token: int,
    idle_seconds: int,
) -> dict[str, object]:
    result = _eval(
        node,
        main_js,
        session,
        page,
        "window.__controlledTabFixture.snapshot()",
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )
    if not isinstance(result, dict):
        raise PrototypeFailure("fixture snapshot returned an invalid result")
    return result


def _start_fixture_server(prototype_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(prototype_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/fixture.html"


def main() -> int:
    parser = argparse.ArgumentParser(description="PROTOTYPE — real Chrome controlled-tab lock proof")
    parser.add_argument("--verify-idle-close", action="store_true")
    parser.add_argument("--idle-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.idle_seconds < 1:
        raise SystemExit("--idle-seconds must be positive")

    prototype_dir = Path(__file__).resolve().parent
    repo_root = prototype_dir.parents[2]
    opencli_root = repo_root.parent / "OpenCLI"
    main_js = opencli_root / "dist" / "src" / "main.js"
    overlay_source = (prototype_dir / "controlled_tab_lock.js").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None or not main_js.is_file():
        raise SystemExit("Build /Users/frankqdwang/Agents/OpenCLI before running this prototype")

    server, fixture_url = _start_fixture_server(prototype_dir)
    page = None
    tab_session = f"seektalent-prototype-tab-{uuid.uuid4().hex}"
    probe_session = f"seektalent-prototype-probe-{uuid.uuid4().hex}"
    control_session = f"seektalent-prototype-control-{uuid.uuid4().hex}"
    control_key = f"seektalent-prototype-lane-{uuid.uuid4().hex}"
    evidence_dir = Path(tempfile.mkdtemp(prefix="seektalent-controlled-tab-lock-"))
    report: dict[str, object] = {"evidenceDir": str(evidence_dir)}

    try:
        status = _wait_for_paired_bridge(main_js, node)
        report["bridgeBuildId"] = status.get("bridgeBuildId")

        activated = _json_output(
            _browser_command(
                node,
                main_js,
                control_session,
                ["control", "activate", control_key],
                idle_seconds=args.idle_seconds,
            )
        )
        if not isinstance(activated, dict) or not isinstance(activated.get("fenceToken"), int):
            raise PrototypeFailure("control activation returned no fence token")
        fence_token = int(activated["fenceToken"])

        found = _json_output(
            _browser_command(
                node,
                main_js,
                probe_session,
                ["tab", "find", "https://h.liepin.com/"],
                control_key=control_key,
                fence_token=fence_token,
                idle_seconds=args.idle_seconds,
            )
        )
        host = _pick_prototype_host(found)
        host_page = str(host.get("page") or "")
        host_url = str(host.get("url") or "")
        if not host_page or not host_url:
            raise PrototypeFailure("selected host tab is missing identity")

        created = _json_output(
            _browser_command(
                node,
                main_js,
                tab_session,
                ["tab", "new", "https://h.liepin.com/", "--host-page", host_page],
                control_key=control_key,
                fence_token=fence_token,
                idle_seconds=args.idle_seconds,
            )
        )
        if not isinstance(created, dict):
            raise PrototypeFailure("controlled tab creation returned an invalid result")
        page = str(created.get("page") or "")
        if not page or created.get("active") is not False or created.get("placement") != "borrowed-host-window":
            raise PrototypeFailure("controlled tab was not created inactive in the borrowed host window")
        report["inactiveBorrowedHostTab"] = True

        current_url = _wait_for_url_prefix(
            node,
            main_js,
            tab_session,
            page,
            "https://h.liepin.com/",
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        if not current_url.startswith("https://h.liepin.com/"):
            raise PrototypeFailure("the owned tab did not inherit the existing Liepin login context")
        report["liepinLoginContext"] = True

        _browser_command(
            node,
            main_js,
            tab_session,
            ["open", fixture_url, "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        installed = _install_overlay(
            overlay_source,
            node,
            main_js,
            tab_session,
            page,
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        if not isinstance(installed, dict) or installed.get("installed") is not True:
            raise PrototypeFailure("overlay did not install")

        visible_path = evidence_dir / "overlay-visible.png"
        _browser_command(
            node,
            main_js,
            tab_session,
            ["screenshot", str(visible_path), "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )

        _browser_command(
            node,
            main_js,
            tab_session,
            ["click", "#action-button", "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        if _fixture_snapshot(
            node,
            main_js,
            tab_session,
            page,
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        ).get("count") != 0:
            raise PrototypeFailure("locked overlay did not block pointer input")
        report["userPointerBlocked"] = True

        _automation_command(
            node,
            main_js,
            tab_session,
            page,
            ["click", "#action-button"],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        _automation_command(
            node,
            main_js,
            tab_session,
            page,
            ["fill", "#prototype-input", "opencli works"],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        _automation_command(
            node,
            main_js,
            tab_session,
            page,
            ["scroll", "down", "--amount", "500"],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        after_scroll = _fixture_snapshot(
            node,
            main_js,
            tab_session,
            page,
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        _automation_command(
            node,
            main_js,
            tab_session,
            page,
            ["click", "#spa-button"],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        snapshot = _fixture_snapshot(
            node,
            main_js,
            tab_session,
            page,
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        if snapshot.get("count") != 1 or snapshot.get("inputValue") != "opencli works":
            raise PrototypeFailure("automation click or fill did not reach the page under the overlay")
        if int(after_scroll.get("scrollY") or 0) <= 0 or snapshot.get("spaVersion") != 1:
            raise PrototypeFailure(
                "automation scroll or SPA update did not complete: "
                f"scroll_after_scroll={after_scroll.get('scrollY')!r}, "
                f"scroll_after_spa={snapshot.get('scrollY')!r}, spa={snapshot.get('spaVersion')!r}"
            )
        if snapshot.get("overlayConnected") is not True:
            raise PrototypeFailure("SPA update removed the overlay")
        report["automationThroughOverlay"] = True
        report["spaOverlaySurvives"] = True

        state = _browser_command(
            node,
            main_js,
            tab_session,
            ["state", "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        if "seektalent-controlled-tab-lock" in state or "60s" in state:
            raise PrototypeFailure("overlay polluted OpenCLI state output")
        report["stateCaptureClean"] = True

        _eval(
            node,
            main_js,
            tab_session,
            page,
            "window.__seektalentControlledTabLockV1.setCaptureMode(true)",
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        clean_path = evidence_dir / "capture-clean.png"
        _browser_command(
            node,
            main_js,
            tab_session,
            ["screenshot", str(clean_path), "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        _eval(
            node,
            main_js,
            tab_session,
            page,
            "window.__seektalentControlledTabLockV1.setCaptureMode(false)",
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        report["screenshotCapturePrepared"] = True

        try:
            _eval(
                node,
                main_js,
                tab_session,
                page,
                "window.__seektalentControlledTabLockV1.destroy(); throw new Error('prototype overlay failure')",
                control_key=control_key,
                fence_token=fence_token,
                idle_seconds=args.idle_seconds,
            )
        except PrototypeFailure:
            report["overlayFailureObserved"] = True
        failure_snapshot = _fixture_snapshot(
            node,
            main_js,
            tab_session,
            page,
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        if failure_snapshot.get("count") != 1:
            raise PrototypeFailure("overlay failure changed the business result")
        report["overlayFailureNonFatal"] = True
        _install_overlay(
            overlay_source,
            node,
            main_js,
            tab_session,
            page,
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )

        _browser_command(
            node,
            main_js,
            tab_session,
            ["open", f"{fixture_url}?navigation=1", "--tab", page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        reinstalled = _install_overlay(
            overlay_source,
            node,
            main_js,
            tab_session,
            page,
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=args.idle_seconds,
        )
        if not isinstance(reinstalled, dict) or reinstalled.get("installed") is not True:
            raise PrototypeFailure("overlay did not reinstall after navigation")
        report["navigationReinjects"] = True

        found_after = _json_output(
            _browser_command(
                node,
                main_js,
                probe_session,
                ["tab", "find", "https://h.liepin.com/"],
                control_key=control_key,
                fence_token=fence_token,
                idle_seconds=args.idle_seconds,
            )
        )
        host_after = next(
            (
                item
                for item in found_after
                if isinstance(item, dict) and item.get("page") == host_page and item.get("url") == host_url
            ),
            None,
        ) if isinstance(found_after, list) else None
        if host_after is None:
            raise PrototypeFailure("the original user Liepin tab changed or disappeared")
        report["userHostUntouched"] = True

        if args.verify_idle_close:
            time.sleep(args.idle_seconds + 2)
            close_result = _json_output(
                _browser_command(
                    node,
                    main_js,
                    tab_session,
                    ["tab", "close", page],
                    idle_seconds=args.idle_seconds,
                )
            )
            page = None
            if not isinstance(close_result, dict) or close_result.get("outcome") != "already_missing":
                raise PrototypeFailure("extension idle alarm did not close the controlled tab on time")
            report["idleCloseVerified"] = True
        else:
            close_result = _json_output(
                _browser_command(
                    node,
                    main_js,
                    tab_session,
                    ["tab", "close", page],
                    idle_seconds=args.idle_seconds,
                )
            )
            page = None
            if not isinstance(close_result, dict) or close_result.get("outcome") not in {"closed", "already_missing"}:
                raise PrototypeFailure("verified close did not reclaim the controlled tab")
            report["verifiedClose"] = True

        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except PrototypeFailure as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        if page:
            try:
                _browser_command(
                    node,
                    main_js,
                    tab_session,
                    ["tab", "close", page],
                    idle_seconds=args.idle_seconds,
                )
            except PrototypeFailure as exc:
                print(f"prototype diagnostic: final close failed: {exc}", file=sys.stderr)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
