#!/usr/bin/env bash
set -euo pipefail

workers="${SEEKTEST_PYTEST_WORKERS:-auto}"
dist="${SEEKTEST_PYTEST_DIST:-loadfile}"
pytest_args=(-q)

if [ "$workers" != "0" ] && [ "$workers" != "false" ]; then
  pytest_args+=(-n "$workers" --dist="$dist")
fi

if [ "${1:-}" = "--all" ]; then
  shift
  uv run --group dev python -m pytest "${pytest_args[@]}" "$@"
elif [ "$#" -gt 0 ]; then
  uv run --group dev python -m pytest "${pytest_args[@]}" "$@"
else
  uv run --group dev python -m pytest --tach -q
fi
