from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from seektalent.version import __version__


DOMI_NODE_ENV_KEYS = ("SEEKTALENT_DOMI_NODE", "DOMI_NODE")
DEFAULT_BIN_DIR = Path.home() / ".seektalent" / "bin"
WINDOWS_DEFAULT_NODE_RELATIVE = Path("Domi") / "runtime" / "node" / "node.exe"
MAC_DEFAULT_NODE_CANDIDATES = (
    Path("/Applications/Domi.app/Contents/Resources/extraResources/node/runtime/bin/node"),
    Path("/Applications/Domi.app/Contents/Resources/extraResources/node/bin/node"),
    Path("/Applications/Domi.app/Contents/Resources/extraResources/node/node"),
)


class DomiBootstrapError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class DomiBootstrapResult:
    bin_dir: Path
    command_name: str
    domi_python: Path
    domi_node: Path
    package_version: str
    python_paths: tuple[Path, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "binDir": str(self.bin_dir),
            "commandName": self.command_name,
            "domiPython": str(self.domi_python),
            "domiNode": str(self.domi_node),
            "packageVersion": self.package_version,
            "pythonPaths": [str(path) for path in self.python_paths],
        }


def bootstrap_domi_workbench(
    *,
    home: Path | None = None,
    platform: str | None = None,
    domi_python: Path | None = None,
    domi_node: Path | None = None,
    python_paths: Sequence[Path] = (),
    package_version: str = __version__,
    bin_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> DomiBootstrapResult:
    current_platform = platform or sys.platform
    root = (home or Path.home()).expanduser()
    target_bin_dir = (bin_dir or root / ".seektalent" / "bin").expanduser()
    target_bin_dir.mkdir(parents=True, exist_ok=True)

    resolved_python = (domi_python or Path(sys.executable)).expanduser()
    _require_executable_runtime(
        resolved_python,
        platform=current_platform,
        reason_code="domi_python_missing",
        label="Domi Python",
    )

    resolved_node = (domi_node or resolve_domi_node(env=env, platform=current_platform, home=root)).expanduser()
    _require_executable_runtime(
        resolved_node,
        platform=current_platform,
        reason_code="domi_node_missing",
        label="Domi Node",
    )

    resolved_python_paths = tuple(path.expanduser() for path in python_paths if str(path).strip())
    if current_platform == "win32":
        _write_windows_shims(
            bin_dir=target_bin_dir,
            domi_python=resolved_python,
            domi_node=resolved_node,
            python_paths=resolved_python_paths,
        )
        _write_windows_root_compat_shims(root / ".seektalent", target_bin_dir)
    else:
        _write_posix_shim(
            bin_dir=target_bin_dir,
            domi_python=resolved_python,
            domi_node=resolved_node,
            python_paths=resolved_python_paths,
        )

    return DomiBootstrapResult(
        bin_dir=target_bin_dir,
        command_name="seektalent",
        domi_python=resolved_python,
        domi_node=resolved_node,
        package_version=package_version,
        python_paths=resolved_python_paths,
    )


def resolve_domi_node(*, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None) -> Path:
    source_env = os.environ if env is None else env
    current_platform = platform or sys.platform
    for key in DOMI_NODE_ENV_KEYS:
        raw = source_env.get(key)
        if raw and raw.strip():
            return _resolve_node_path(raw.strip(), platform=current_platform)

    candidates = list(_default_node_candidates(env=source_env, platform=current_platform, home=home or Path.home()))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if candidates:
        return candidates[0]
    return Path("node.exe" if current_platform == "win32" else "node")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install SeekTalent's Domi Workbench command shim.")
    parser.add_argument("--domi-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--domi-node", type=Path)
    parser.add_argument("--python-path", type=Path, action="append", default=[])
    parser.add_argument("--bin-dir", type=Path)
    parser.add_argument("--package-version", default=__version__)
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = bootstrap_domi_workbench(
            domi_python=args.domi_python,
            domi_node=args.domi_node,
            python_paths=tuple(args.python_path),
            bin_dir=args.bin_dir,
            package_version=args.package_version,
        )
    except DomiBootstrapError as exc:
        print(f"reason_code={exc.reason_code} {exc}", file=sys.stderr)
        return 1

    if args.print_json:
        print(json.dumps(result.to_public_dict(), ensure_ascii=False))
    else:
        print(f"SeekTalent Domi command ready: {result.bin_dir / result.command_name}")
    return 0


def _default_node_candidates(*, env: Mapping[str, str], platform: str, home: Path) -> tuple[Path, ...]:
    if platform == "win32":
        bases = []
        appdata = env.get("APPDATA")
        local_appdata = env.get("LOCALAPPDATA")
        if appdata:
            bases.append(Path(appdata))
        if local_appdata:
            bases.append(Path(local_appdata))
        return tuple(base / WINDOWS_DEFAULT_NODE_RELATIVE for base in bases)

    home_candidates = (
        home / "Library" / "Application Support" / "Domi" / "runtime" / "node" / "node",
        home / "Library" / "Application Support" / "Domi" / "runtime" / "node" / "bin" / "node",
        home / ".domi" / "runtime" / "node" / "node",
        home / ".domi" / "runtime" / "node" / "bin" / "node",
    )
    return (*MAC_DEFAULT_NODE_CANDIDATES, *home_candidates)


def _resolve_node_path(raw: str, *, platform: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_dir():
        return path / ("node.exe" if platform == "win32" else "node")
    return path


def _require_executable_runtime(path: Path, *, platform: str, reason_code: str, label: str) -> None:
    if not path.is_file() or (platform != "win32" and not os.access(path, os.X_OK)):
        raise DomiBootstrapError(reason_code, f"{label} is not an executable file: {path}")


def _write_windows_shims(
    *,
    bin_dir: Path,
    domi_python: Path,
    domi_node: Path,
    python_paths: tuple[Path, ...],
) -> None:
    ps1 = bin_dir / "seektalent.ps1"
    cmd = bin_dir / "seektalent.cmd"
    python_path_lines = "\n".join(f'$PythonPathEntries += "{_escape_powershell(path)}"' for path in python_paths)
    ps1.write_text(
        f"""$ErrorActionPreference = "Stop"
$DomiPython = "{_escape_powershell(domi_python)}"
$DomiNode = "{_escape_powershell(domi_node)}"
$PythonPathEntries = @()
{python_path_lines}
if ($PythonPathEntries.Count -gt 0) {{
  $env:PYTHONPATH = if ($env:PYTHONPATH) {{ ($PythonPathEntries + @($env:PYTHONPATH)) -join ";" }} else {{ $PythonPathEntries -join ";" }}
}}
$env:PATH = "{_escape_powershell(domi_python.parent)};{_escape_powershell(domi_node.parent)};$env:PATH"
$env:SEEKTALENT_DOMI_NODE = $DomiNode
$env:DOMI_NODE = $DomiNode
if ($args.Count -ge 1 -and $args[0] -eq "workbench") {{
  $remaining = @()
  if ($args.Count -gt 1) {{ $remaining = $args[1..($args.Count - 1)] }}
  & $DomiPython -m seektalent.domi_workbench @remaining
  exit $LASTEXITCODE
}}
& $DomiPython -m seektalent @args
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    cmd.write_text(
        """@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0seektalent.ps1" %*
exit /b %ERRORLEVEL%
""",
        encoding="utf-8",
    )


def _write_windows_root_compat_shims(root_dir: Path, bin_dir: Path) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("seektalent.ps1", "seektalent.cmd"):
        source = bin_dir / filename
        target = root_dir / filename
        if source == target:
            continue
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _write_posix_shim(
    *,
    bin_dir: Path,
    domi_python: Path,
    domi_node: Path,
    python_paths: tuple[Path, ...],
) -> None:
    shim = bin_dir / "seektalent"
    pythonpath = ":".join(_shell_quote(path) for path in python_paths)
    pythonpath_block = ""
    if pythonpath:
        pythonpath_block = f"PYTHONPATH={pythonpath}${{PYTHONPATH:+:$PYTHONPATH}}\nexport PYTHONPATH\n"
    shim.write_text(
        f"""#!/bin/sh
set -eu
DOMI_PYTHON={_shell_quote(domi_python)}
DOMI_NODE={_shell_quote(domi_node)}
{pythonpath_block}PATH={_shell_quote(domi_python.parent)}:{_shell_quote(domi_node.parent)}:$PATH
SEEKTALENT_DOMI_NODE="$DOMI_NODE"
DOMI_NODE="$DOMI_NODE"
export PATH SEEKTALENT_DOMI_NODE DOMI_NODE
if [ "${{1:-}}" = "workbench" ]; then
  shift
  exec "$DOMI_PYTHON" -m seektalent.domi_workbench "$@"
fi
exec "$DOMI_PYTHON" -m seektalent "$@"
""",
        encoding="utf-8",
    )
    current_mode = shim.stat().st_mode
    shim.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _escape_powershell(value: Path | str) -> str:
    return str(value).replace("`", "``").replace('"', '`"')


def _shell_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
