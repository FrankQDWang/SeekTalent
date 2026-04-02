from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LoadedPrompt:
    name: str
    path: Path
    content: str
    sha256: str


class PromptRegistry:
    def __init__(self, prompt_dir: Path) -> None:
        self.prompt_dir = prompt_dir
        self._prompts: dict[str, LoadedPrompt] = {}

    def load(self, name: str) -> LoadedPrompt:
        if name in self._prompts:
            return self._prompts[name]
        path = self.prompt_dir / f"{name}.md"
        content = path.read_text(encoding="utf-8")
        prompt = LoadedPrompt(
            name=name,
            path=path,
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        self._prompts[name] = prompt
        return prompt

    def load_many(self, names: list[str]) -> dict[str, LoadedPrompt]:
        return {name: self.load(name) for name in names}

    def prompt_hashes(self) -> dict[str, str]:
        return {name: prompt.sha256 for name, prompt in self._prompts.items()}

    def prompt_files(self) -> dict[str, str]:
        return {name: str(prompt.path) for name, prompt in self._prompts.items()}

    def loaded_prompts(self) -> dict[str, LoadedPrompt]:
        return dict(self._prompts)


def json_block(title: str, payload: Any) -> str:
    return f"{title}\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}\n```"
