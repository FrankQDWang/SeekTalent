from __future__ import annotations

import json
from pathlib import Path

from seektalent.dev_mode import build_dev_mode_env_diagnostics


def _write_opencli_binary(root: Path) -> Path:
    opencli_bin = root / "apps" / "web-svelte" / "node_modules" / ".bin" / "opencli"
    opencli_bin.parent.mkdir(parents=True, exist_ok=True)
    opencli_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    opencli_bin.chmod(0o755)
    return opencli_bin


def test_env_diagnostics_reports_configured_opencli_without_legacy_mcp(tmp_path: Path) -> None:
    opencli_bin = _write_opencli_binary(tmp_path)

    status = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_TEXT_LLM_API_KEY": "sk-test",
            "SEEKTALENT_CTS_TENANT_KEY": "tenant-key",
            "SEEKTALENT_CTS_TENANT_SECRET": "tenant-secret",
            "SEEKTALENT_LIEPIN_WORKER_MODE": "opencli",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": str(opencli_bin),
        },
        workspace_root=tmp_path,
    )

    public = status.model_dump(mode="json")
    raw = json.dumps(public, sort_keys=True)
    components = {item["name"]: item for item in public["components"]}
    assert status.overallStatus in {"ready", "warning"}
    assert components["liepin_opencli_browser"]["status"] == "configured"
    assert "dokobot_mcp" not in raw
    assert "liepin_" + "pi" not in raw
    assert str(tmp_path) not in raw


def test_dev_launcher_uses_liepin_opencli_helper_without_legacy_mcp_adapter() -> None:
    script = Path("scripts/start-dev-workbench.sh").read_text(encoding="utf-8")

    assert "seektalent.providers.liepin.opencli_browser_cli cleanup_orphaned_tabs" in script
    assert ".".join(("seektalent", "providers", "pi" + "_agent", "opencli_browser_cli")) not in script
    assert "node_modules/pi-mcp-adapter/index.ts" not in script
    assert "SEEKTALENT_LIEPIN_" + "DOKOBOT_MCP_COMMAND" not in script
    assert "DOKOBOT_MCP_COMMAND" not in script
