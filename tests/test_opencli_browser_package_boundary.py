from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPENCLI_BROWSER_ROOT = ROOT / "src" / "seektalent" / "opencli_browser"


def _python_files(path: Path) -> list[Path]:
    return sorted(item for item in path.rglob("*.py") if item.is_file())


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_generic_opencli_browser_package_exists() -> None:
    expected = {
        "__init__.py",
        "contracts.py",
        "reason_codes.py",
        "runtime.py",
        "automation.py",
    }

    assert expected <= {path.name for path in OPENCLI_BROWSER_ROOT.glob("*.py")}


def test_generic_opencli_browser_does_not_import_product_layers() -> None:
    forbidden_prefixes = (
        "seektalent.providers",
        "seektalent.sources",
        "seektalent.runtime",
        "seektalent.source_adapters",
        "seektalent_ui",
    )
    offenders: list[str] = []
    for path in _python_files(OPENCLI_BROWSER_ROOT):
        tree = ast.parse(_text(path), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(forbidden_prefixes):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.module}")

    assert offenders == []


def test_generic_opencli_browser_has_no_liepin_literals() -> None:
    offenders: list[str] = []
    forbidden = ("liepin_", "Liepin", "LIEPIN", "h.liepin.com", "www.liepin.com", "搜索", "下一页")
    for path in _python_files(OPENCLI_BROWSER_ROOT):
        text = _text(path)
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {marker!r}")

    assert offenders == []


def test_generic_opencli_browser_contracts_do_not_expose_site_config_fields() -> None:
    text = _text(OPENCLI_BROWSER_ROOT / "contracts.py")
    forbidden = (
        "OpenCliBrowserPolicy",
        "to_pi_tool_payload",
        "artifact_root",
        "detail_open_timeout_seconds",
        "allowed_click_refs",
        "cleanup_" + "worker_enabled",
        "idle_" + "close_seconds",
        "close_" + "blank_window",
    )

    assert all(item not in text for item in forbidden)


def test_generic_opencli_browser_automation_does_not_launch_provider_cleanup_worker() -> None:
    text = _text(OPENCLI_BROWSER_ROOT / "automation.py")
    forbidden = (
        "launch_idle_cleanup_worker",
        "watch_" + "idle_lease",
        "subprocess.Popen",
        "SEEKTALENT_LIEPIN_OPENCLI_",
    )

    assert all(item not in text for item in forbidden)


def test_opencli_browser_automation_uses_generic_reason_codes() -> None:
    from seektalent.opencli_browser.reason_codes import (
        OPENCLI_COMMAND_MISSING,
        OPENCLI_STALE_REF,
        OPENCLI_TIMEOUT,
    )

    assert OPENCLI_COMMAND_MISSING == "opencli_command_missing"
    assert OPENCLI_TIMEOUT == "opencli_timeout"
    assert OPENCLI_STALE_REF == "opencli_stale_ref"
