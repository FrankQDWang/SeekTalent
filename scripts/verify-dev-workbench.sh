#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

uv run pytest \
  tests/test_dev_mode_readiness.py \
  tests/test_workbench_api.py \
  tests/test_workbench_semantic_guardrails.py \
  tests/test_workbench_dual_source_dev_mode.py \
  tests/test_runtime_source_lanes.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_liepin_config.py \
  -q

uv run ruff check \
  src/seektalent/dev_mode.py \
  src/seektalent_ui/final_top_candidates.py \
  src/seektalent_ui/models.py \
  src/seektalent_ui/workbench_routes.py \
  src/seektalent_ui/server.py \
  src/seektalent_ui/workbench_store.py \
  tests/test_dev_mode_readiness.py \
  tests/test_workbench_api.py \
  tests/test_workbench_semantic_guardrails.py \
  tests/test_workbench_dual_source_dev_mode.py

if [[ "${SEEKTALENT_VERIFY_PYTHON_ONLY:-0}" == "1" ]]; then
  echo "SEEKTALENT_VERIFY_PYTHON_ONLY=1; skipped Svelte verification" >&2
  exit 0
fi

command -v bun >/dev/null 2>&1 || {
  echo "bun not found; rerun with SEEKTALENT_VERIFY_PYTHON_ONLY=1 only for Python-only local checks" >&2
  exit 1
}

tmp_root="$(mktemp -d)"
cookie_jar="$tmp_root/cookies.txt"
api_pid=""
api_owned=0
cleanup() {
  if [[ "$api_owned" == "1" && -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_root"
}
trap cleanup EXIT

if curl -fsS http://127.0.0.1:8012/openapi.json >/dev/null 2>&1; then
  echo "Using existing backend on 127.0.0.1:8012 for OpenAPI generation." >&2
else
  env SEEKTALENT_WORKSPACE_ROOT="$tmp_root" SEEKTALENT_WORKBENCH_ENABLED=true uv run seektalent-ui-api --host 127.0.0.1 --port 8012 &
  api_pid=$!
  api_owned=1
fi
for _ in {1..150}; do
  if curl -fsS http://127.0.0.1:8012/openapi.json >/dev/null; then
    break
  fi
  sleep 0.2
done
curl -fsS http://127.0.0.1:8012/openapi.json >/dev/null

schema_path="apps/web-svelte/src/lib/api/schema.d.ts"
schema_before="$(shasum "$schema_path" | awk '{print $1}')"

(
  cd apps/web-svelte
  bun run api:gen
)

schema_after="$(shasum "$schema_path" | awk '{print $1}')"
if [[ "$schema_before" != "$schema_after" ]]; then
  echo "Generated OpenAPI schema changed; run bun run api:gen and review the result." >&2
  exit 1
fi

handwritten_svelte_paths=(
  "apps/web-svelte/src/routes"
  "apps/web-svelte/src/lib/components"
  "apps/web-svelte/src/lib/workbench"
  "apps/web-svelte/src/lib/api/workbench.ts"
)

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
  if rg -n -i "$forbidden" "${handwritten_svelte_paths[@]}"; then
    echo "Forbidden legacy Liepin browser fallback reference found in Svelte milestone wiring: $forbidden" >&2
    exit 1
  fi
done

for forbidden_copy in 'Svelte 5 Workbench Spike' 'Dev mode BYOK' 'data-root' 'data root' dataRoots 'readiness dashboard'; do
  if rg -n -i "$forbidden_copy" "${handwritten_svelte_paths[@]}"; then
    echo "Forbidden spike/dev-mode primary UI copy found in Svelte parity source: $forbidden_copy" >&2
    exit 1
  fi
done

(
  cd apps/web-svelte
  bun run check
  bun run lint
  bun run test
  bun run build
  bun run test:e2e -- workbench-parity.spec.ts
)

if [[ "$api_owned" == "1" ]]; then
  curl -fsS -c "$cookie_jar" -b "$cookie_jar" \
    -H 'Content-Type: application/json' \
    -X POST \
    --data '{"email":"admin@example.com","password":"correct horse","displayName":"Admin User"}' \
    http://127.0.0.1:8012/api/auth/bootstrap >/dev/null

  curl -fsS -c "$cookie_jar" -b "$cookie_jar" \
    -H 'Content-Type: application/json' \
    -X POST \
    --data '{"email":"admin@example.com","password":"correct horse"}' \
    http://127.0.0.1:8012/api/auth/login >/dev/null

  csrf_token="$(awk '$6 == "seektalent_workbench_csrf" { print $7 }' "$cookie_jar" | tail -1)"
  if [[ -z "$csrf_token" ]]; then
    echo "Could not read CSRF cookie from real-backend smoke login." >&2
    exit 1
  fi

  session_json="$tmp_root/session.json"
  curl -fsS -c "$cookie_jar" -b "$cookie_jar" \
    -H 'Content-Type: application/json' \
    -H "X-CSRF-Token: $csrf_token" \
    -X POST \
    --data '{"jobTitle":"Python Engineer","jdText":"Build Python agents and ranking systems.","notes":"Prefer retrieval experience.","sourceKinds":["cts","liepin"]}' \
    http://127.0.0.1:8012/api/workbench/sessions > "$session_json"

  session_id="$(
    SESSION_JSON="$session_json" uv run python - <<'PY'
import json
import os

with open(os.environ["SESSION_JSON"], encoding="utf-8") as handle:
    print(json.load(handle)["sessionId"])
PY
  )"

  curl -fsS -c "$cookie_jar" -b "$cookie_jar" http://127.0.0.1:8012/api/workbench/sessions >/dev/null
  curl -fsS -c "$cookie_jar" -b "$cookie_jar" "http://127.0.0.1:8012/api/workbench/sessions/$session_id" >/dev/null
  curl -fsS -c "$cookie_jar" -b "$cookie_jar" "http://127.0.0.1:8012/api/workbench/sessions/$session_id/final-top10" >/dev/null
  curl -fsS -c "$cookie_jar" -b "$cookie_jar" http://127.0.0.1:8012/api/workbench/source-connections >/dev/null
  curl -fsS -c "$cookie_jar" -b "$cookie_jar" \
    -H "X-CSRF-Token: $csrf_token" \
    -X POST \
    http://127.0.0.1:8012/api/workbench/source-connections/liepin >/dev/null
else
  echo "Skipped real-backend mutable smoke because 127.0.0.1:8012 was already owned by another process." >&2
fi

git diff --check
