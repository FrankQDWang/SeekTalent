from __future__ import annotations

from pathlib import Path


def test_memory_workspace_renders_bounded_diff(tmp_path: Path) -> None:
    from seektalent_agent_memory.workspace import MemoryWorkspace

    workspace = MemoryWorkspace(tmp_path / "memory_workspace")
    workspace.prepare()
    workspace.write_artifact("raw_memories.md", "old memory\n")
    workspace.reset_baseline()
    workspace.write_artifact("raw_memories.md", "new memory\n")

    diff = workspace.render_workspace_diff(max_bytes=4000)

    assert "# Memory Workspace Diff" in diff
    assert "-old memory" in diff or "- old memory" in diff
    assert "+new memory" in diff or "+ new memory" in diff
