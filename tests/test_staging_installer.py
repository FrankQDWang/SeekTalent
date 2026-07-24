from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.browser_bridge_bundle_fixtures import write_browser_bridge_bundle


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-seektalent-staging.sh"
BRIDGE_INSTALLER = ROOT / "scripts" / "install_staging_browser_bridge.py"


def test_staging_installer_has_valid_shell_syntax(tmp_path: Path) -> None:
    subprocess.run(("bash", "-n", str(INSTALLER)), check=True)
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    subprocess.run(
        ("python3", str(BRIDGE_INSTALLER), "--help"),
        cwd=tmp_path,
        env=clean_env,
        check=True,
        capture_output=True,
    )
    bundle = tmp_path / "wtscli-bundle"
    write_browser_bridge_bundle(bundle)
    completed = subprocess.run(
        (
            "python3",
            str(BRIDGE_INSTALLER),
            "--bundle-dir",
            str(bundle),
            "--staging-home",
            str(tmp_path / "staging-home"),
            "--node",
            sys.executable,
        ),
        cwd=tmp_path,
        env=clean_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"bridgeBuildId":' in completed.stdout


def test_staging_installer_uses_published_package_and_isolated_runtime() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'VERSION="${1:-0.7.49}"' in script
    assert '"seektalent==${VERSION}"' in script
    assert 'STAGING_ROOT="${SEEKTALENT_STAGING_ROOT:-${HOME}/.seektalent-staging}"' in script
    assert 'export HOME="$STAGING_ROOT/home"' in script
    assert "run_seektalent_staging.py" in script
    assert "install_staging_browser_bridge.py" in script
    assert "709622fc3fb3463f15551467fdf0d28571dfd049" in script
    assert 'WTSCLI_NPM_VERSION="10.9.2"' in script
    assert '(cd "${WTSCLI_ROOT}" && "${WTSCLI_NPM[@]}" ci --ignore-scripts)' in script
    assert 'npm --prefix "${WTSCLI_ROOT}" ci' not in script
    assert "Application Support/Domi" in script
    assert "staging refuses the Domi Node runtime" in script
    assert "DOMI_PYTHON" not in script
    assert "SEEKTALENT_DOMI_JWT" not in script
