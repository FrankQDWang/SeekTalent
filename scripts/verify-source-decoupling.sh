#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

uv run python tools/check_source_boundaries.py

uv run pytest \
  tests/test_source_boundaries.py \
  tests/test_source_registry_contract.py \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_runtime_source_lanes.py \
  tests/test_provider_registry.py \
  tests/test_cts_query_builder.py \
  tests/test_cts_source_compiler.py \
  tests/test_filter_projection.py \
  tests/test_cts_provider_adapter.py \
  tests/test_liepin_provider_adapter.py \
  -q
