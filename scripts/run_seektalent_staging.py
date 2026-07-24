from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any


STAGING_ROOT_ENV = "SEEKTALENT_STAGING_ROOT"
STAGING_LLM_ENV_VARS = frozenset(
    {
        "SEEKTALENT_TEXT_LLM_API_KEY",
        "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY",
        "SEEKTALENT_TEXT_LLM_ENDPOINT_KIND",
        "SEEKTALENT_TEXT_LLM_ENDPOINT_REGION",
        "SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE",
        "SEEKTALENT_REQUIREMENTS_MODEL_ID",
        "SEEKTALENT_CONTROLLER_MODEL_ID",
        "SEEKTALENT_SCORING_MODEL_ID",
        "SEEKTALENT_FINALIZE_MODEL_ID",
        "SEEKTALENT_REFLECTION_MODEL_ID",
        "SEEKTALENT_STRUCTURED_REPAIR_MODEL_ID",
        "SEEKTALENT_JUDGE_MODEL_ID",
        "SEEKTALENT_TUI_SUMMARY_MODEL_ID",
        "SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID",
        "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID",
        "SEEKTALENT_WORKBENCH_NOTE_WRITER_MODEL_ID",
        "SEEKTALENT_WORKBENCH_CONVERSATION_MODEL_ID",
    }
)
DOMI_LLM_ENV_VARS = frozenset(
    {
        "SEEKTALENT_DOMI_JWT",
        "SEEKTALENT_DOMI_LLM_BASE_URL",
        "SEEKTALENT_DOMI_LLM_CHANNEL",
        "SEEKTALENT_DOMI_NODE",
        "DOMI_NODE",
    }
)


class StagingConfigurationError(RuntimeError):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        env, staging_root = build_staging_env(os.environ)
        _require_staging_port_ownership(staging_root)
        runtime = _ensure_browser_runtime(env, staging_root=staging_root)
    except StagingConfigurationError as exc:
        print(f"reason_code=seektalent_staging_config_invalid {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if exc.__class__.__name__ != "BootstrapError":
            raise
        print(f"reason_code=liepin_opencli_bootstrap_failed WTSCLI/Node 启动失败：{exc}", file=sys.stderr)
        return 1

    env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND"] = shlex.join(
        (str(runtime.node), str(runtime.opencli_main))
    )
    env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND_MANAGED"] = "1"

    if args.check:
        try:
            _verify_browser_bridge(runtime)
        except Exception as exc:
            safe_reason_code = getattr(exc, "safe_reason_code", None)
            if not isinstance(safe_reason_code, str):
                raise
            print(f"reason_code=liepin_{safe_reason_code} staging browser bridge is not ready", file=sys.stderr)
            return 1
        from seektalent.version import __version__

        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "staging",
                    "packageVersion": __version__,
                    "runtimeMode": "prod",
                    "provider": "bailian",
                    "browserBridge": "connected",
                    "stagingRoot": str(staging_root),
                    "python": sys.executable,
                    "node": str(runtime.node),
                    "opencliMain": str(runtime.opencli_main),
                },
                ensure_ascii=False,
            )
        )
        return 0

    command = _server_command(args)
    try:
        return subprocess.run(command, check=False, env=env).returncode
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError:
        print("reason_code=seektalent_staging_python_missing staging Python executable not found", file=sys.stderr)
        return 1


def build_staging_env(base_env: Mapping[str, str]) -> tuple[dict[str, str], Path]:
    staging_root = _staging_root(base_env)
    expected_home = (staging_root / "home").resolve()
    actual_home = Path(base_env.get("HOME") or "").expanduser().resolve()
    if actual_home != expected_home:
        raise StagingConfigurationError(
            f"HOME must be the isolated staging home {expected_home}, found {actual_home}"
        )

    source_env = dict(_read_staging_llm_config(staging_root / "config.env"))
    source_env.update(base_env)
    key = str(source_env.get("SEEKTALENT_TEXT_LLM_API_KEY") or "").strip()
    if not key:
        raise StagingConfigurationError("SEEKTALENT_TEXT_LLM_API_KEY is required")

    node = _resolve_node(source_env)
    from seektalent.product_env import build_workbench_command_env

    env = build_workbench_command_env(source_env)
    for name in STAGING_LLM_ENV_VARS:
        value = source_env.get(name)
        if value and value.strip():
            env[name] = value
    for name in DOMI_LLM_ENV_VARS:
        env.pop(name, None)

    env["HOME"] = str(expected_home)
    env[STAGING_ROOT_ENV] = str(staging_root)
    env["SEEKTALENT_PACKAGED"] = "1"
    env["SEEKTALENT_RUNTIME_MODE"] = "prod"
    env["SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE"] = "prod"
    env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] = "bailian"
    env["SEEKTALENT_TEXT_LLM_API_KEY"] = key
    env["SEEKTALENT_WTSCLI_NODE"] = str(node)
    return env, staging_root


def write_staging_llm_config(source: Path, target: Path) -> None:
    values = _read_env_file(source)
    lines = [
        f"{key}={json.dumps(values[key], ensure_ascii=False)}"
        for key in sorted(STAGING_LLM_ENV_VARS - {"SEEKTALENT_TEXT_LLM_API_KEY"})
        if values.get(key)
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_staging_llm_config(path: Path) -> dict[str, str]:
    return {
        key: value
        for key, value in _read_env_file(path).items()
        if key in STAGING_LLM_ENV_VARS and key != "SEEKTALENT_TEXT_LLM_API_KEY"
    }


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _staging_root(env: Mapping[str, str]) -> Path:
    raw = str(env.get(STAGING_ROOT_ENV) or "").strip()
    if not raw:
        raise StagingConfigurationError(f"{STAGING_ROOT_ENV} is required")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise StagingConfigurationError(f"{STAGING_ROOT_ENV} must be an absolute path")
    return root.resolve()


def _resolve_node(env: Mapping[str, str]) -> Path:
    configured = str(env.get("SEEKTALENT_WTSCLI_NODE") or "").strip()
    candidate = configured or shutil.which("node", path=env.get("PATH"))
    if not candidate:
        raise StagingConfigurationError(
            "Node was not found; set SEEKTALENT_WTSCLI_NODE to a standalone Node executable"
        )
    node = Path(candidate).expanduser().resolve()
    if not node.is_file() or (sys.platform != "win32" and not os.access(node, os.X_OK)):
        raise StagingConfigurationError(f"Node is not executable: {node}")
    if "Application Support/Domi" in node.as_posix() or "/Domi.app/" in node.as_posix():
        raise StagingConfigurationError(f"staging refuses the Domi Node runtime: {node}")
    return node


def _ensure_browser_runtime(env: MutableMapping[str, str], *, staging_root: Path):
    from seektalent.opencli_launcher import ensure_opencli_runtime

    runtime_root = staging_root / "home" / ".seektalent" / "wtscli-runtime"
    return ensure_opencli_runtime(root=runtime_root, env=env)


def _require_staging_port_ownership(staging_root: Path) -> None:
    manifest_path = staging_root / "home" / ".seektalent" / "browser-bridge" / "bridge-manifest.json"
    from seektalent.opencli_browser.contracts import OpenCliBrowserError
    from seektalent.opencli_browser.daemon_transport import (
        OpenCliDaemonClient,
        load_bridge_requirement,
    )
    from seektalent.opencli_browser.reason_codes import (
        OPENCLI_DAEMON_NOT_RUNNING,
        OPENCLI_EXTENSION_DISCONNECTED,
    )

    try:
        client = OpenCliDaemonClient(requirement=load_bridge_requirement(manifest_path))
    except OpenCliBrowserError as exc:
        raise StagingConfigurationError(f"invalid staging browser bridge manifest: {manifest_path}") from exc
    try:
        client.verify_bridge(timeout_seconds=0.3)
    except OpenCliBrowserError as exc:
        reason = exc.safe_reason_code
        if reason in {OPENCLI_DAEMON_NOT_RUNNING, OPENCLI_EXTENSION_DISCONNECTED}:
            return
        raise StagingConfigurationError(
            "port 19826 did not prove ownership by this exact WTSCLI bundle"
        ) from exc
    finally:
        client.close()


def _verify_browser_bridge(runtime: Any) -> None:
    from seektalent.opencli_browser.daemon_process import connect_installed_opencli_daemon

    client = connect_installed_opencli_daemon(runtime)
    try:
        client.verify_bridge(timeout_seconds=2.0)
    finally:
        client.close()


def _server_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "seektalent_ui.server",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--runtime-mode",
        "prod",
        "--serve-frontend",
        "--liepin-worker-mode",
        "opencli",
        "--liepin-browser-action-backend",
        "opencli",
    ]
    if args.lan:
        command.append("--lan")
    for host in args.allowed_host:
        command.extend(("--allowed-host", host))
    for origin in args.allowed_origin:
        command.extend(("--allowed-origin", origin))
    return command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the downloaded SeekTalent production package in an isolated staging home."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--lan", action="store_true")
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--allowed-origin", action="append", default=[])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the package, WTSCLI runtime, daemon, and paired Chrome extension.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
