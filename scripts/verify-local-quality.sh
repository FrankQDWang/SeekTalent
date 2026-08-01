#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

base_ref="${1:-origin/main}"

uv run --group dev ruff check src tests experiments tools
uv run --group dev ty check src tests tools
uv run --group dev python tools/check_arch_imports.py
uv run --group dev python tools/check_workbench_schema_modes.py
python3 tools/check_privacy_gate.py --base "$base_ref"
python3 tools/check_agent_safety_gate.py --base "$base_ref"
