#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

uv run pytest \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_audit.py \
  tests/test_runtime_source_lanes.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_workbench_runtime_graph.py \
  tests/test_workbench_runtime_owned_execution.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_worker_client.py \
  tests/test_cts_provider_adapter.py \
  tests/test_provider_registry.py \
  -q

uv run python tools/check_arch_imports.py
uv run python tools/check_tach_baseline.py

command -v bun >/dev/null 2>&1 || {
  echo "bun not found; red-zone Liepin worker verification requires Bun" >&2
  exit 1
}
(
  cd apps/liepin-worker
  bun install --frozen-lockfile
  bun run boundary-check
  bun run typecheck
  bun run test
)

git diff --check
