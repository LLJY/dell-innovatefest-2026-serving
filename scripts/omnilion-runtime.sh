#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

action=${1:-up}
unit=omnilion-vllm.service

require_omnilion_env() {
  local required name
  required=(OMNILION_MODEL OMNILION_REVISION OMNILION_VENV OMNILION_API_KEY OMNILION_API_BASE OMNILION_BIND_HOST OMNILION_PORT HF_HOME)
  for name in "${required[@]}"; do
    [[ -n "${!name:-}" ]] || die "$name is missing from $ENV_FILE"
    [[ "${!name}" != *replace-* ]] || die "$name still contains an example placeholder"
  done
  [[ "$OMNILION_REVISION" =~ ^[0-9a-f]{40}$ ]] || die "OMNILION_REVISION must be a full 40-character commit"
  [[ "$OMNILION_API_KEY" =~ ^[A-Za-z0-9._-]+$ ]] || die "OMNILION_API_KEY contains unsupported characters"
  [[ "$OMNILION_PORT" =~ ^[0-9]+$ && "$OMNILION_PORT" -ge 1 && "$OMNILION_PORT" -le 65535 ]] || die "OMNILION_PORT must be an integer from 1 to 65535"
  [[ "$OMNILION_VENV" = /* && "$OMNILION_VENV" != *[[:space:]]* && -x "$OMNILION_VENV/bin/python" && -x "$OMNILION_VENV/bin/vllm" ]] || die "OMNILION_VENV must be an absolute vLLM virtualenv without whitespace"
  [[ "$HF_HOME" = /* ]] || die "HF_HOME must be absolute"
  reject_repo_path "$HF_HOME" "Hugging Face cache"
}

download_and_prepare() {
  local snapshot manifest actual_manifest plugin
  mkdir -p "$HF_HOME"
  unset HF_TOKEN HUGGING_FACE_HUB_TOKEN
  export HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HOME
  printf 'Downloading/verifying OmniLion %s at %s anonymously...\n' "$OMNILION_MODEL" "$OMNILION_REVISION"
  snapshot=$("$OMNILION_VENV/bin/python" - "$OMNILION_MODEL" "$OMNILION_REVISION" <<'PY'
import sys
from huggingface_hub import snapshot_download

print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], token=False))
PY
)
  [[ -d "$snapshot" ]] || die "snapshot_download did not return a directory"
  manifest="$snapshot/release-manifest.json"
  plugin="$snapshot/omnilion_vllm_plugin-0.1.0-py3-none-any.whl"
  [[ -f "$manifest" && -f "$plugin" ]] || die "pinned snapshot is missing its release manifest or plugin wheel"
  actual_manifest=$(sha256sum "$manifest" | awk '{print $1}')
  [[ "$actual_manifest" == "${OMNILION_MANIFEST_SHA256:-b69cec09f3e6dfbf713485eb637c2909ed43704791f4ecb208b0b69bb0e7d8e0}" ]] || die "OmniLion release-manifest checksum mismatch"
  "$OMNILION_VENV/bin/python" -m pip install --disable-pip-version-check --no-deps --force-reinstall "$plugin"
  "$OMNILION_VENV/bin/python" - <<'PY' || die "OmniLion runtime lacks audio dependencies; install av, scipy, soundfile, and soxr in OMNILION_VENV"
import av, scipy, soundfile, soxr
PY
  OMNILION_SNAPSHOT=$snapshot
  export OMNILION_SNAPSHOT
}

start_runtime() {
  local bridge_host expected_api_base state_root runtime_env fingerprint desired current health_url
  require_command curl
  require_command docker
  require_command sha256sum
  require_command systemctl
  require_command systemd-run

  bridge_host=$(docker network inspect bridge --format '{{with (index .IPAM.Config 0)}}{{.Gateway}}{{end}}' 2>/dev/null) \
    || die "cannot inspect Docker's default bridge gateway"
  [[ -n "$bridge_host" ]] || die "Docker's default bridge has no IPv4 gateway"
  [[ "$OMNILION_BIND_HOST" == "$bridge_host" ]] \
    || die "OMNILION_BIND_HOST must equal Docker's default bridge gateway ($bridge_host)"
  expected_api_base="http://host.docker.internal:${OMNILION_PORT}/v1"
  [[ "${OMNILION_API_BASE%/}" == "$expected_api_base" ]] \
    || die "OMNILION_API_BASE must be $expected_api_base"

  download_and_prepare

  state_root=${OMNILION_STATE_DIR:-"${XDG_STATE_HOME:-$HOME/.local/state}/gb10-serving/omnilion"}
  [[ "$state_root" = /* ]] || die "OMNILION_STATE_DIR must be absolute"
  reject_repo_path "$state_root" "OmniLion state directory"
  mkdir -p "$state_root"
  chmod 700 "$state_root"
  runtime_env="$state_root/runtime.env"
  umask 077
  {
    printf 'VLLM_API_KEY=%s\n' "$OMNILION_API_KEY"
    printf 'VLLM_PLUGINS=omnilion\n'
    printf 'PYTHONNOUSERSITE=1\n'
    printf 'HF_HOME=%s\n' "$HF_HOME"
    printf 'HF_HUB_DISABLE_IMPLICIT_TOKEN=1\n'
    printf 'PATH=%s/bin:%s\n' "$OMNILION_VENV" "$PATH"
  } > "$runtime_env.tmp"
  mv -f "$runtime_env.tmp" "$runtime_env"
  chmod 600 "$runtime_env"

  fingerprint="$state_root/config.sha256"
  desired=$(printf '%s\n' "$OMNILION_SNAPSHOT" "$OMNILION_VENV" "$OMNILION_BIND_HOST" "$OMNILION_PORT" "${OMNILION_MAX_MODEL_LEN:-8192}" "${OMNILION_GPU_MEMORY_UTILIZATION:-0.65}" "${OMNILION_VIDEO_NUM_FRAMES:-30}" "$(printf '%s' "$OMNILION_API_KEY" | sha256sum | awk '{print $1}')" | sha256sum | awk '{print $1}')
  current=$(cat "$fingerprint" 2>/dev/null || true)
  health_url="http://$OMNILION_BIND_HOST:$OMNILION_PORT/health"
  if [[ "$current" == "$desired" ]] && systemctl --user is-active --quiet "$unit" && curl -fsS "$health_url" >/dev/null 2>&1; then
    printf 'OmniLion native vLLM is already healthy at %s.\n' "$health_url"
    return
  fi

  systemctl --user stop "$unit" >/dev/null 2>&1 || true
  printf 'Starting private OmniLion native vLLM...\n'
  systemd-run --user --unit="$unit" --collect \
    --property=Restart=on-failure \
    --property=RestartSec=5 \
    --property="EnvironmentFile=$runtime_env" \
    "$OMNILION_VENV/bin/vllm" serve "$OMNILION_SNAPSHOT" \
      --served-model-name OmniLion \
      --host "$OMNILION_BIND_HOST" \
      --port "$OMNILION_PORT" \
      --dtype bfloat16 \
      --max-model-len "${OMNILION_MAX_MODEL_LEN:-8192}" \
      --max-num-seqs 1 \
      --gpu-memory-utilization "${OMNILION_GPU_MEMORY_UTILIZATION:-0.65}" \
      --limit-mm-per-prompt '{"image":1,"video":1,"audio":1}' \
      --media-io-kwargs "{\"video\":{\"num_frames\":${OMNILION_VIDEO_NUM_FRAMES:-30}}}" \
      --chat-template-content-format string \
      --enforce-eager >/dev/null

  if ! wait_for_http "$health_url" "${OMNILION_STARTUP_TIMEOUT:-900}"; then
    systemctl --user --no-pager --full status "$unit" >&2 || true
    die "OmniLion did not become healthy; inspect with: journalctl --user -u $unit"
  fi
  printf '%s\n' "$desired" > "$fingerprint"
  chmod 600 "$fingerprint"
  printf 'OmniLion native vLLM is healthy at %s.\n' "$health_url"
}

load_env_file
case "$action" in
  up|start)
    require_omnilion_env
    start_runtime
    ;;
  down|stop)
    require_command systemctl
    systemctl --user stop "$unit" >/dev/null 2>&1 || true
    printf 'Stopped %s (or it was already inactive).\n' "$unit"
    ;;
  status)
    require_command systemctl
    active=$(systemctl --user is-active "$unit" 2>/dev/null || true)
    substate=$(systemctl --user show "$unit" --property=SubState --value 2>/dev/null || true)
    main_pid=$(systemctl --user show "$unit" --property=MainPID --value 2>/dev/null || true)
    printf 'unit=%s active=%s substate=%s main_pid=%s\n' \
      "$unit" "${active:-unknown}" "${substate:-unknown}" "${main_pid:-unknown}"
    [[ "$active" == active ]]
    ;;
  logs)
    require_command journalctl
    journalctl --user -u "$unit" --no-pager -n "${OMNILION_LOG_LINES:-200}"
    ;;
  *)
    die "usage: $0 {up|down|status|logs}"
    ;;
esac
