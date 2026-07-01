from __future__ import annotations

import pytest

from scripts import build_packaged_workbench


def test_packaged_workbench_build_prefers_corepack_pnpm(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in {"corepack", "pnpm"} else None

    monkeypatch.setattr(build_packaged_workbench.shutil, "which", fake_which)

    assert build_packaged_workbench._pnpm_command() == ["corepack", "pnpm"]


def test_packaged_workbench_build_falls_back_to_direct_pnpm(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/pnpm" if name == "pnpm" else None

    monkeypatch.setattr(build_packaged_workbench.shutil, "which", fake_which)

    assert build_packaged_workbench._pnpm_command() == ["pnpm"]


def test_packaged_workbench_build_requires_pnpm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_packaged_workbench.shutil, "which", lambda _name: None)

    with pytest.raises(SystemExit, match="pnpm is required"):
        build_packaged_workbench._pnpm_command()
