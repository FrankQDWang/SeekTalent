from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from experiments.claude_code_baseline import CLAUDE_CODE_MODEL_ALIAS
from seektalent.config import AppSettings


def read_env_file(path: str | Path) -> dict[str, str]:
    env: dict[str, str] = {}
    file_path = Path(path)
    if not file_path.exists():
        return env
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        env[key.strip()] = value
    return env


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def controller_model_name(settings: AppSettings) -> str:
    provider, model_name = settings.controller_model.split(":", 1)
    if provider != "openai-chat":
        raise ValueError(f"Claude Code baseline requires openai-chat controller model, got {settings.controller_model!r}.")
    return model_name


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def isolated_process_env(*, env_file: str | Path, home_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(read_env_file(env_file))
    env["HOME"] = str(home_dir)
    env["CI"] = "true"
    env["FORCE_COLOR"] = "0"
    return env


def write_router_config(*, home_dir: Path, settings: AppSettings, env_file: str | Path, port: int, api_key: str) -> Path:
    env = read_env_file(env_file)
    openai_base_url = env.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not openai_base_url:
        raise ValueError("Claude Code baseline requires OPENAI_BASE_URL in the env file.")
    if not (env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        raise ValueError("Claude Code baseline requires OPENAI_API_KEY in the env file.")
    model_name = controller_model_name(settings)
    config = {
        "HOST": "127.0.0.1",
        "PORT": port,
        "APIKEY": api_key,
        "LOG": False,
        "API_TIMEOUT_MS": 600000,
        "NON_INTERACTIVE_MODE": True,
        "Providers": [
            {
                "name": "bailian",
                "api_base_url": chat_completions_url(openai_base_url),
                "api_key": "$OPENAI_API_KEY",
                "models": [model_name],
                "transformer": {"use": ["deepseek"], model_name: {"use": ["tooluse"]}},
            }
        ],
        "Router": {
            "default": f"bailian,{model_name}",
            "background": f"bailian,{model_name}",
            "think": f"bailian,{model_name}",
            "longContext": f"bailian,{model_name}",
            "longContextThreshold": 60000,
            "webSearch": f"bailian,{model_name}",
        },
    }
    config_path = home_dir / ".claude-code-router" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def write_claude_settings(*, path: Path, port: int, api_key: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        "env": {
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_API_KEY": api_key,
            "NO_PROXY": "127.0.0.1,localhost",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_COST_WARNINGS": "1",
        },
        "model": CLAUDE_CODE_MODEL_ALIAS,
        "permissions": {"defaultMode": "bypassPermissions"},
    }
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
