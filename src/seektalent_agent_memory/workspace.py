from __future__ import annotations

import difflib
import json
from contextlib import suppress
from pathlib import Path


class MemoryWorkspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.baseline_path = self.root / ".baseline.json"

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.baseline_path.exists():
            self.baseline_path.write_text("{}", encoding="utf-8")

    def write_artifact(self, relative_path: str, content: str) -> None:
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise ValueError("agent_memory_workspace_path_invalid")
        self.prepare()
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def prune_artifacts(self, allowed_relative_paths: set[str]) -> None:
        allowed = {_normalize_relative_path(path) for path in allowed_relative_paths}
        self.prepare()
        for path in sorted(self.root.rglob("*"), reverse=True):
            if not path.is_file() or path == self.baseline_path:
                continue
            relative_path = path.relative_to(self.root).as_posix()
            if relative_path not in allowed:
                path.unlink()
        for path in sorted((item for item in self.root.rglob("*") if item.is_dir()), reverse=True):
            with suppress(OSError):
                path.rmdir()

    def reset_baseline(self) -> None:
        self.prepare()
        self._remove_generated_diff()
        self.baseline_path.write_text(json.dumps(self._current_files(), ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def render_workspace_diff(self, *, max_bytes: int) -> str:
        self.prepare()
        self._remove_generated_diff()
        baseline = self._read_baseline()
        current = self._current_files()
        chunks = ["# Memory Workspace Diff\n"]
        for path in sorted(set(baseline) | set(current)):
            old = baseline.get(path, "").splitlines(keepends=True)
            new = current.get(path, "").splitlines(keepends=True)
            if old == new:
                continue
            chunks.extend(
                difflib.unified_diff(
                    old,
                    new,
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
            chunks.append("\n")
        rendered = "".join(chunks)
        encoded = rendered.encode("utf-8")
        if len(encoded) <= max_bytes:
            return rendered
        return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n[diff truncated]\n"

    def _current_files(self) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path == self.baseline_path or path.name == "phase2_workspace_diff.md":
                continue
            files[str(path.relative_to(self.root))] = path.read_text(encoding="utf-8")
        return files

    def _read_baseline(self) -> dict[str, str]:
        try:
            loaded = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {str(key): str(value) for key, value in loaded.items()}

    def _remove_generated_diff(self) -> None:
        diff_path = self.root / "phase2_workspace_diff.md"
        if diff_path.exists():
            diff_path.unlink()


def _normalize_relative_path(relative_path: str) -> str:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("agent_memory_workspace_path_invalid")
    return path.as_posix()
