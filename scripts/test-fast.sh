#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 0 ]; then
  uv run --group dev python -m pytest -q "$@"
else
  uv run --group dev python -m pytest --tach -q
fi
