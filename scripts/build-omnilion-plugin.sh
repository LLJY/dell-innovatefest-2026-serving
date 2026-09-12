#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR=${1:-"$REPO_ROOT/artifacts/omnilion-vllm-plugin"}

if [[ -n "${PYTHON:-}" ]]; then
  python_bin=$PYTHON
elif [[ -n "${OMNILION_VENV:-}" && -x "$OMNILION_VENV/bin/python" ]]; then
  python_bin="$OMNILION_VENV/bin/python"
else
  python_bin=python3
fi

command -v "$python_bin" >/dev/null 2>&1 || {
  printf 'error: Python executable not found: %s\n' "$python_bin" >&2
  exit 1
}

mkdir -p "$OUTPUT_DIR"
if compgen -G "$OUTPUT_DIR/omnilion_vllm_plugin-*.whl" >/dev/null; then
  printf 'error: refusing ambiguous output directory containing an OmniLion wheel: %s\n' "$OUTPUT_DIR" >&2
  exit 1
fi

"$python_bin" -m pip wheel \
  --no-deps \
  --wheel-dir "$OUTPUT_DIR" \
  "$REPO_ROOT/omnilion-vllm-plugin"

"$python_bin" - "$OUTPUT_DIR" <<'PY'
from pathlib import Path
import hashlib
import json
import sys
import zipfile

root = Path(sys.argv[1]).resolve()
wheels = list(root.glob("omnilion_vllm_plugin-*.whl"))
if len(wheels) != 1:
    raise SystemExit(f"expected exactly one OmniLion wheel, found {len(wheels)}")
wheel = wheels[0]
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
if any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names):
    raise SystemExit("wheel contains bytecode or cache files")
print(json.dumps({
    "status": "PASS_OMNILION_PLUGIN_WHEEL",
    "wheel": str(wheel),
    "bytes": wheel.stat().st_size,
    "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    "archive_files": len(names),
}, indent=2))
PY
