from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "build_offline_macos_intel.py"
CONSTRAINTS_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "offline"
    / "constraints-0.7.46-macos-intel.txt"
)
SPEC = importlib.util.spec_from_file_location("build_offline_macos_intel", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _wheel(directory: Path, name: str) -> None:
    (directory / name).write_bytes(b"wheel")


def test_constraints_pin_the_current_release_and_accepted_native_dependencies() -> None:
    constraints = CONSTRAINTS_PATH.read_text(encoding="utf-8").splitlines()

    assert "seektalent==0.7.46" in constraints
    assert "cryptography==48.0.0" in constraints
    assert "pydantic-core==2.46.4" in constraints
    assert "tiktoken==0.13.0" in constraints
    assert all("==" in line for line in constraints if line and not line.startswith("#"))


def test_validate_wheelhouse_accepts_pure_intel_and_universal2_wheels(tmp_path: Path) -> None:
    _wheel(tmp_path, "seektalent-0.7.46-py3-none-any.whl")
    _wheel(tmp_path, "pydantic_core-2.46.4-cp313-cp313-macosx_10_13_x86_64.whl")
    _wheel(tmp_path, "cryptography-49.0.0-cp311-abi3-macosx_10_9_universal2.whl")

    native = MODULE.validate_wheelhouse(tmp_path)

    assert [wheel.name for wheel in native] == [
        "cryptography-49.0.0-cp311-abi3-macosx_10_9_universal2.whl",
        "pydantic_core-2.46.4-cp313-cp313-macosx_10_13_x86_64.whl",
    ]


@pytest.mark.parametrize(
    "wheel_name",
    [
        "pydantic_core-2.46.4-cp313-cp313-macosx_11_0_arm64.whl",
        "pydantic_core-2.46.4-cp313-cp313-manylinux_2_17_x86_64.whl",
    ],
)
def test_validate_wheelhouse_rejects_wrong_platform_wheels(tmp_path: Path, wheel_name: str) -> None:
    _wheel(tmp_path, "seektalent-0.7.46-py3-none-any.whl")
    _wheel(tmp_path, wheel_name)

    with pytest.raises(RuntimeError):
        MODULE.validate_wheelhouse(tmp_path)
