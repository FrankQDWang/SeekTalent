#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${SEEKTALENT_VERIFY_SKIP_PYTHON_PREFLIGHT:-0}" != "1" ]]; then
  uv run --group dev python -m pytest \
    tests/test_dev_mode_readiness.py \
    tests/test_workbench_local_actor.py \
    tests/test_workbench_api.py \
    tests/test_workbench_semantic_guardrails.py \
    tests/test_workbench_dual_source_dev_mode.py \
    tests/test_runtime_source_lanes.py \
    tests/test_liepin_runtime_source_lane.py \
    tests/test_liepin_config.py \
    tests/test_agent_workbench_contract.py \
    tests/test_react_workbench_cutover_gate.py \
    tests/test_workbench_contract_ci_optimization.py \
    -q

  uv run --group dev python -m ruff check \
    src/seektalent/dev_mode.py \
    src/seektalent_ui/final_top_candidates.py \
    src/seektalent_ui/event_routes.py \
    src/seektalent_ui/models.py \
    src/seektalent_ui/workbench_response.py \
    src/seektalent_ui/workbench_routes.py \
    src/seektalent_ui/agent_route_deps.py \
    src/seektalent_ui/agent_routes.py \
    src/seektalent_ui/agent_workbench_models.py \
    src/seektalent_ui/agent_workbench_projection.py \
    src/seektalent_ui/agent_workbench_response.py \
    src/seektalent_ui/agent_workbench_routes.py \
    src/seektalent_ui/agent_workbench_stream.py \
    src/seektalent_ui/agent_workbench_stream_projection.py \
    src/seektalent_ui/agent_workbench_stream_store.py \
    src/seektalent_ui/agent_workbench_transcript.py \
    src/seektalent_ui/server.py \
    src/seektalent_ui/workbench_actor_store.py \
    src/seektalent_ui/workbench_local_actor.py \
    src/seektalent_ui/workbench_store.py \
    tests/test_dev_mode_readiness.py \
    tests/test_workbench_local_actor.py \
    tests/test_agent_workbench_contract.py \
    tests/test_workbench_api.py \
    tests/test_workbench_semantic_guardrails.py \
    tests/test_workbench_dual_source_dev_mode.py \
    tests/test_react_workbench_cutover_gate.py \
    tests/test_workbench_contract_ci_optimization.py \
    tools/check_react_workbench_cutover.py \
    tools/check_react_workbench_design_acceptance.py

  uv run python tools/check_react_workbench_cutover.py
  uv run python tools/check_react_workbench_design_acceptance.py
fi

if [[ "${SEEKTALENT_VERIFY_PYTHON_ONLY:-0}" == "1" ]]; then
  echo "SEEKTALENT_VERIFY_PYTHON_ONLY=1; skipped React verification" >&2
  exit 0
fi

PNPM_CMD=()
if command -v corepack >/dev/null 2>&1; then
  PNPM_CMD=(corepack pnpm)
elif command -v pnpm >/dev/null 2>&1; then
  PNPM_CMD=(pnpm)
else
  echo "pnpm not found; rerun with SEEKTALENT_VERIFY_PYTHON_ONLY=1 only for Python-only local checks" >&2
  exit 1
fi

tmp_root="$(mktemp -d)"
api_pid=""
cleanup() {
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_root"
}
trap cleanup EXIT

api_port="${SEEKTALENT_VERIFY_API_PORT:-}"
if [[ -z "$api_port" ]]; then
  api_port="$(
    uv run python - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
  )"
fi
api_base_url="http://127.0.0.1:$api_port"
env SEEKTALENT_WORKSPACE_ROOT="$tmp_root" SEEKTALENT_WORKBENCH_ENABLED=true uv run seektalent-ui-api --host 127.0.0.1 --port "$api_port" &
api_pid=$!
for _ in {1..150}; do
  if curl -fsS "$api_base_url/openapi.json" >/dev/null; then
    break
  fi
  sleep 0.2
done
curl -fsS "$api_base_url/openapi.json" >/dev/null

schema_path="apps/web-react/src/lib/api/schema.d.ts"
schema_before="$(shasum "$schema_path" | awk '{print $1}')"

(
  cd apps/web-react
  SEEKTALENT_OPENAPI_URL="$api_base_url/openapi.json" "${PNPM_CMD[@]}" api:gen
)

schema_after="$(shasum "$schema_path" | awk '{print $1}')"
if [[ "$schema_before" != "$schema_after" ]]; then
  echo "Generated OpenAPI schema changed; run pnpm api:gen in apps/web-react and review the result." >&2
  exit 1
fi

handwritten_react_paths=(
  "apps/web-react/src/routes"
  "apps/web-react/src/components"
  "apps/web-react/src/lib/api/agentWorkbench.ts"
  "apps/web-react/src/lib/api/client.ts"
  "apps/web-react/src/lib/query"
  "apps/web-react/src/lib/strategy-graph"
  "apps/web-react/src/lib/stream"
)

grep_react_source() {
  git grep --untracked -n -i -F -e "$1" -- "${handwritten_react_paths[@]}"
}

for forbidden in \
  login-relay \
  'login/snapshot' \
  'login/frame' \
  server_managed_browser \
  managed_local \
  external_http \
  pi_runner.py \
  'browser fallback' \
  'fallback browser' \
  'managed browser login' \
  'direct browser fallback'; do
  if grep_react_source "$forbidden"; then
    echo "Forbidden legacy Liepin browser fallback reference found in React workbench wiring: $forbidden" >&2
    exit 1
  fi
done

for forbidden_copy in 'Workbench Spike' 'Dev mode BYOK' 'data-root' 'data root' dataRoots 'readiness dashboard'; do
  if grep_react_source "$forbidden_copy"; then
    echo "Forbidden spike/dev-mode primary UI copy found in React workbench source: $forbidden_copy" >&2
    exit 1
  fi
done

(
  cd apps/web-react
  storybook_pid=""
  storybook_log="/tmp/seektalent-storybook-static-server.log"
  cleanup_storybook() {
    if [[ -n "$storybook_pid" ]]; then
      kill "$storybook_pid" 2>/dev/null || true
      wait "$storybook_pid" 2>/dev/null || true
    fi
  }
  trap cleanup_storybook EXIT

  "${PNPM_CMD[@]}" check
  "${PNPM_CMD[@]}" lint
  "${PNPM_CMD[@]}" test
  "${PNPM_CMD[@]}" build
  "${PNPM_CMD[@]}" storybook:build --test --quiet --disable-telemetry
  rm -f "$storybook_log"
  python3 -m http.server 6006 --bind 127.0.0.1 --directory storybook-static >"$storybook_log" 2>&1 &
  storybook_pid=$!
  for _ in {1..150}; do
    if ! kill -0 "$storybook_pid" 2>/dev/null; then
      cat "$storybook_log" >&2 || true
      echo "Static Storybook server exited before it became ready." >&2
      exit 1
    fi
    if curl -fsS "http://127.0.0.1:6006/iframe.html" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
  curl -fsS "http://127.0.0.1:6006/iframe.html" >/dev/null
  SEEKTALENT_STORYBOOK_EXTERNAL=1 "${PNPM_CMD[@]}" storybook:a11y
  SEEKTALENT_STORYBOOK_EXTERNAL=1 "${PNPM_CMD[@]}" storybook:interactions
  SEEKTALENT_STORYBOOK_EXTERNAL=1 "${PNPM_CMD[@]}" storybook:visual
  SEEKTALENT_DEV_BACKEND_HOST=127.0.0.1 \
    SEEKTALENT_DEV_BACKEND_PORT="$api_port" \
    "${PNPM_CMD[@]}" test:e2e
)

conversation_json="$tmp_root/conversation.json"
curl -fsS \
  -H 'Content-Type: application/json' \
  -X POST \
  --data '{"title":"Python Agent Engineer"}' \
  "$api_base_url/api/agent/conversations" > "$conversation_json"

conversation_id="$(
  CONVERSATION_JSON="$conversation_json" uv run python - <<'PY'
import json
import os

with open(os.environ["CONVERSATION_JSON"], encoding="utf-8") as handle:
    print(json.load(handle)["conversation"]["conversationId"])
PY
)"

curl -fsS "$api_base_url/api/agent/workbench/conversations" >/dev/null
curl -fsS "$api_base_url/api/agent/workbench/conversations/$conversation_id" >/dev/null
curl -fsS "$api_base_url/api/workbench/source-connections" >/dev/null
curl -fsS \
  -X POST \
  "$api_base_url/api/workbench/source-connections/liepin" >/dev/null

git diff --check
