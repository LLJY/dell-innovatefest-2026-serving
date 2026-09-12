#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

load_env_file
require_command curl
require_command docker
require_command systemctl

container=${OMNILION_LITELLM_CONTAINER:-omnilion-litellm}
image=${OMNILION_LITELLM_IMAGE:-ghcr.io/berriai/litellm:main-v1.83.3-stable}
action=${1:-up}

required=(
  LITELLM_MASTER_KEY
  LITELLM_HOST_PORT
  OMNILION_MODEL
  OMNILION_REVISION
  OMNILION_VENV
  OMNILION_API_KEY
  OMNILION_API_BASE
  OMNILION_BIND_HOST
  OMNILION_PORT
  OMNILION_MAX_MODEL_LEN
  OMNILION_GPU_MEMORY_UTILIZATION
  OMNILION_VIDEO_NUM_FRAMES
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || die "$name is missing from $ENV_FILE"
  [[ "${!name}" != *replace-* ]] || die "$name still contains an example placeholder"
done
[[ "$LITELLM_MASTER_KEY" == sk-* ]] || die "LITELLM_MASTER_KEY must start with sk-"
[[ "$LITELLM_HOST_PORT" =~ ^[0-9]+$ && "$LITELLM_HOST_PORT" -ge 1 && "$LITELLM_HOST_PORT" -le 65535 ]] \
  || die "LITELLM_HOST_PORT must be an integer from 1 to 65535"

pull_and_install() {
  local python snapshot wheel
  python="$OMNILION_VENV/bin/python"
  [[ -x "$python" ]] || die "Python is not executable at $python"
  printf 'Pulling OmniLion %s@%s...\n' "$OMNILION_MODEL" "$OMNILION_REVISION"
  snapshot=$(
    "$python" - "$OMNILION_MODEL" "$OMNILION_REVISION" <<'PY'
from huggingface_hub import snapshot_download
import sys

print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2]))
PY
  ) || die "failed to download OmniLion from Hugging Face"
  wheel="$snapshot/omnilion_vllm_plugin-0.1.0-py3-none-any.whl"
  [[ -f "$snapshot/release-manifest.json" ]] || die "downloaded snapshot has no release manifest"
  [[ -f "$wheel" ]] || die "downloaded snapshot has no plugin wheel"
  "$python" -m pip install --no-deps --force-reinstall "$wheel"
}

case "$action" in
  up)
    native_was_active=false
    if systemctl --user is-active --quiet "${OMNILION_SERVICE_UNIT:-omnilion-vllm}"; then
      native_was_active=true
    fi
    pull_and_install
    "$SCRIPT_DIR/omnilion-service.sh" start
    docker rm -f "$container" >/dev/null 2>&1 || true
    if ! docker run -d \
      --name "$container" \
      --restart unless-stopped \
      --add-host host.docker.internal:host-gateway \
      -p "127.0.0.1:${LITELLM_HOST_PORT}:4000" \
      -e LITELLM_MASTER_KEY \
      -e OMNILION_API_BASE \
      -e OMNILION_API_KEY \
      -v "$REPO_ROOT/litellm-omnilion.yaml:/app/config.yaml:ro" \
      "$image" --config /app/config.yaml --port 4000 >/dev/null; then
      [[ "$native_was_active" == true ]] || "$SCRIPT_DIR/omnilion-service.sh" stop >/dev/null 2>&1 || true
      die "LiteLLM failed to start; newly started OmniLion was stopped"
    fi
    if ! wait_for_http "$LUNA_URL/health/liveliness" 300; then
      docker logs --tail 120 "$container" >&2 || true
      docker rm -f "$container" >/dev/null 2>&1 || true
      [[ "$native_was_active" == true ]] || "$SCRIPT_DIR/omnilion-service.sh" stop >/dev/null 2>&1 || true
      die "LiteLLM did not become healthy; standalone container removed"
    fi
    printf 'OmniLion is ready through LiteLLM at %s (model: OmniLion).\n' "$LUNA_URL"
    ;;
  down)
    docker rm -f "$container" >/dev/null 2>&1 || true
    "$SCRIPT_DIR/omnilion-service.sh" stop
    ;;
  status)
    "$SCRIPT_DIR/omnilion-service.sh" status || true
    if docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null; then
      printf 'LiteLLM container: %s\n' "$container"
    else
      printf 'LiteLLM container %s is absent.\n' "$container"
      exit 1
    fi
    ;;
  *)
    die "usage: $0 {up|down|status}"
    ;;
esac
