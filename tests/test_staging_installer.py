from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-seektalent-staging.sh"
BRIDGE_INSTALLER = ROOT / "scripts" / "install_staging_browser_bridge.py"


def test_staging_installer_has_valid_shell_syntax() -> None:
    subprocess.run(("bash", "-n", str(INSTALLER)), check=True)
    subprocess.run(("python3", str(BRIDGE_INSTALLER), "--help"), check=True, capture_output=True)


def test_staging_installer_uses_published_package_and_isolated_runtime() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'VERSION="${1:-0.7.49}"' in script
    assert '"seektalent==${VERSION}"' in script
    assert 'STAGING_ROOT="${SEEKTALENT_STAGING_ROOT:-${HOME}/.seektalent-staging}"' in script
    assert 'export HOME="$STAGING_ROOT/home"' in script
    assert "run_seektalent_staging.py" in script
    assert "install_staging_browser_bridge.py" in script
    assert "60ae80db9ed96a0813eea12d5e24aa8e5c6ec863" in script
    assert '(cd "${WTSCLI_ROOT}" && npm ci --ignore-scripts)' in script
    assert 'npm --prefix "${WTSCLI_ROOT}" ci' not in script
    assert "Application Support/Domi" in script
    assert "staging refuses the Domi Node runtime" in script
    assert "DOMI_PYTHON" not in script
    assert "SEEKTALENT_DOMI_JWT" not in script
