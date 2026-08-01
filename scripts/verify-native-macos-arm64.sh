#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This verifier requires a native Apple Silicon macOS host." >&2
  exit 1
fi

for required_command in git node npm uv; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Missing required command: $required_command" >&2
    exit 1
  fi
done

wtscli_fork_commit="b05374d5d1834cda297701f7dc7a8caf756cac3c"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/seektalent-native-arm64.XXXXXX")"
trap 'rm -rf "$temp_root"' EXIT

wtscli_source="$temp_root/wtscli-fork"
wtscli_bundle="$temp_root/wtscli-browser-bridge"
native_evidence="$temp_root/native-launch-binding-evidence.json"
wheel_dir="$temp_root/wheel"
delivery_dir="$temp_root/delivery"

git init -q "$wtscli_source"
git -C "$wtscli_source" remote add origin https://github.com/FrankQDWang/wtscli.git
git -C "$wtscli_source" fetch --quiet --depth 1 origin "$wtscli_fork_commit"
git -C "$wtscli_source" checkout --quiet --detach FETCH_HEAD

(
  cd "$wtscli_source"
  npm ci --ignore-scripts
  npm --prefix extension ci --ignore-scripts
  npm run build:seektalent-bundle -- --out "$wtscli_bundle"
)

python3 tools/native_probes/launch_binding_probe.py --json > "$native_evidence"
uv sync --locked --group dev

SEEKTALENT_NATIVE_LAUNCH_BINDING_EVIDENCE="$native_evidence" \
  uv run --group dev python -m pytest tests/test_native_launch_binding_probe.py -q

SEEKTALENT_EXACT_WTSCLI_BUNDLE="$wtscli_bundle" \
  uv run --group dev python -m pytest \
    tests/test_wtscli_bundle_binding.py \
    tests/test_domi_delivery_admission.py \
    -q

uv run --group dev python -m pytest \
  tests/test_browser_bridge_environment.py \
  tests/test_wtscli_bundle_binding.py \
  -q

mkdir -p "$wheel_dir" "$delivery_dir"
uv build --wheel --out-dir "$wheel_dir"
wheels=("$wheel_dir"/seektalent-*.whl)
if [[ "${#wheels[@]}" -ne 1 || ! -f "${wheels[0]}" ]]; then
  echo "Expected exactly one SeekTalent wheel in $wheel_dir." >&2
  exit 1
fi

uv run --group dev python scripts/build_domi_delivery_bundle.py \
  --output-dir "$delivery_dir" \
  --wtscli-bundle-dir "$wtscli_bundle" \
  --seektalent-wheel "${wheels[0]}" \
  --node "$(command -v node)" \
  --platform macos-arm64

SEEKTALENT_NATIVE_DELIVERY_ARCHIVE="$delivery_dir/seektalent-domi-macos-arm64.zip" \
SEEKTALENT_NATIVE_DELIVERY_PLATFORM="macos-arm64" \
  uv run --group dev python -m pytest tests/test_build_domi_delivery_bundle.py -q

PYTHONNOUSERSITE=1 \
  uv run --group dev python -m pytest tests/test_packaged_sidecar_artifact.py -q

uv run --group dev python -m pytest tests/test_installed_slot_lease.py -q
