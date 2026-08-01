#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

scripts/verify-local-quality.sh
(cd apps/web-react && pnpm check && pnpm test -- --runInBand && pnpm build)
