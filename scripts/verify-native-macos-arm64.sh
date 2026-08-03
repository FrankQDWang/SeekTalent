#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This verifier requires a native Apple Silicon macOS host." >&2
  exit 1
fi

for required_command in git npm uv; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Missing required command: $required_command" >&2
    exit 1
  fi
done

domi_python="${DOMI_PYTHON:-${SEEKTALENT_DOMI_PYTHON:-}}"
domi_node="${DOMI_NODE:-${SEEKTALENT_DOMI_NODE:-}}"
if [[ ! -x "$domi_python" || ! -x "$domi_node" ]]; then
  echo "Set DOMI_PYTHON and DOMI_NODE to the Domi-provided runtimes." >&2
  exit 1
fi

wtscli_fork_commit="66bf6aaab1751c10bfee0f091a0ad31efc2bb453"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/seektalent-native-arm64.XXXXXX")"
trap 'rm -rf "$temp_root"' EXIT
export PYINSTALLER_CONFIG_DIR="$temp_root/pyinstaller-cache"

wtscli_source="$temp_root/wtscli-fork"
wtscli_bundle="$temp_root/wtscli-browser-bridge"
native_evidence="$temp_root/native-launch-binding-evidence.json"
source_revision="$(git rev-parse HEAD)"
build_dir="$ROOT/dist/tmp/0.8.2-${source_revision:0:12}"
wheel_dir="$build_dir"
wheelhouse_dir="$build_dir/wheelhouse-macos-arm64"
delivery_dir="$build_dir"
release_wheel="${SEEKTALENT_RELEASE_WHEEL:-}"

git init -q "$wtscli_source"
git -C "$wtscli_source" remote add origin https://github.com/FrankQDWang/wtscli.git
git -C "$wtscli_source" fetch --quiet --depth 1 origin "$wtscli_fork_commit"
git -C "$wtscli_source" checkout --quiet --detach FETCH_HEAD

(
  cd "$wtscli_source"
  export PATH="$(dirname "$domi_node"):$PATH"
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

mkdir -p "$wheel_dir" "$wheelhouse_dir"
if [[ -n "$release_wheel" ]]; then
  if [[ ! -f "$release_wheel" || "$(basename "$release_wheel")" != "seektalent-0.8.2-py3-none-any.whl" ]]; then
    echo "SEEKTALENT_RELEASE_WHEEL must name the exact 0.8.2 wheel." >&2
    exit 1
  fi
  cp "$release_wheel" "$wheel_dir/seektalent-0.8.2-py3-none-any.whl"
  uv build --sdist --out-dir "$wheel_dir"
else
  uv build --out-dir "$wheel_dir"
fi
wheels=("$wheel_dir"/seektalent-*.whl)
if [[ "${#wheels[@]}" -ne 1 || ! -f "${wheels[0]}" ]]; then
  echo "Expected exactly one SeekTalent wheel in $wheel_dir." >&2
  exit 1
fi
"$domi_python" -m pip download --only-binary=:all: \
  --dest "$wheelhouse_dir" "${wheels[0]}"

uv run --group dev python scripts/build_domi_delivery_bundle.py \
  --output-dir "$delivery_dir" \
  --wtscli-bundle-dir "$wtscli_bundle" \
  --seektalent-wheel "${wheels[0]}" \
  --wheelhouse-dir "$wheelhouse_dir" \
  --domi-python "$domi_python" \
  --node "$domi_node" \
  --platform macos-arm64 \
  --source-revision "$source_revision"

SEEKTALENT_NATIVE_DELIVERY_ARCHIVE="$delivery_dir/seektalent-offline-0.8.2-macos-arm64-py313.zip" \
SEEKTALENT_NATIVE_DELIVERY_PLATFORM="macos-arm64" \
  uv run --group dev python -m pytest tests/test_build_domi_delivery_bundle.py -q

PYTHONNOUSERSITE=1 \
  uv run --group dev python -m pytest tests/test_packaged_sidecar_artifact.py -q

uv run --group dev python -m pytest tests/test_installed_slot_lease.py -q

echo "Native macOS arm64 delivery saved under: $build_dir"
