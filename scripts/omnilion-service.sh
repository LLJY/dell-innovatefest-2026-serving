#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

load_env_file
require_command curl
require_command python3
require_command systemctl
require_command systemd-run

action=${1:-status}

required=(
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

[[ "$OMNILION_PORT" =~ ^[0-9]+$ && "$OMNILION_PORT" -ge 1 && "$OMNILION_PORT" -le 65535 ]] \
  || die "OMNILION_PORT must be an integer from 1 to 65535"
[[ "$OMNILION_MAX_MODEL_LEN" =~ ^[0-9]+$ && "$OMNILION_MAX_MODEL_LEN" -gt 0 ]] \
  || die "OMNILION_MAX_MODEL_LEN must be a positive integer"
[[ "$OMNILION_VIDEO_NUM_FRAMES" =~ ^[0-9]+$ && "$OMNILION_VIDEO_NUM_FRAMES" -gt 0 ]] \
  || die "OMNILION_VIDEO_NUM_FRAMES must be a positive integer"
[[ -x "$OMNILION_VENV/bin/vllm" ]] || die "vLLM is not executable at $OMNILION_VENV/bin/vllm"

unit=${OMNILION_SERVICE_UNIT:-omnilion-vllm}
base_url="http://${OMNILION_BIND_HOST}:${OMNILION_PORT}"
startup_timeout=${OMNILION_STARTUP_TIMEOUT:-900}
[[ "$startup_timeout" =~ ^[0-9]+$ && "$startup_timeout" -gt 0 ]] \
  || die "OMNILION_STARTUP_TIMEOUT must be a positive integer"
python3 - "$OMNILION_GPU_MEMORY_UTILIZATION" <<'PY'
import sys
try:
    value = float(sys.argv[1])
except ValueError as exc:
    raise SystemExit("OMNILION_GPU_MEMORY_UTILIZATION must be numeric") from exc
if not 0 < value < 1:
    raise SystemExit("OMNILION_GPU_MEMORY_UTILIZATION must be greater than 0 and less than 1")
PY

if [[ "$action" == start || "$action" == restart ]]; then
  require_command docker
  bridge_host=$(docker network inspect bridge --format '{{with (index .IPAM.Config 0)}}{{.Gateway}}{{end}}' 2>/dev/null) \
    || die "cannot inspect Docker's default bridge gateway"
  [[ -n "$bridge_host" ]] || die "Docker's default bridge has no IPv4 gateway"
  [[ "$OMNILION_BIND_HOST" == "$bridge_host" ]] \
    || die "OMNILION_BIND_HOST must equal Docker's default bridge gateway ($bridge_host)"
  expected_api_base="http://host.docker.internal:${OMNILION_PORT}/v1"
  [[ "${OMNILION_API_BASE%/}" == "$expected_api_base" ]] \
    || die "OMNILION_API_BASE must be $expected_api_base"
fi

check_model() {
  local response
  response=$(curl -fsS \
    -H "Authorization: Bearer $OMNILION_API_KEY" \
    "$base_url/v1/models" 2>/dev/null) || return 1
  python3 -c '
import json, sys
body = json.load(sys.stdin)
if "OmniLion" not in {row.get("id") for row in body.get("data", [])}:
    raise SystemExit(1)
' <<<"$response"
}

show_status() {
  local active substate main_pid
  active=$(systemctl --user is-active "$unit" 2>/dev/null || true)
  substate=$(systemctl --user show "$unit" --property=SubState --value 2>/dev/null || true)
  main_pid=$(systemctl --user show "$unit" --property=MainPID --value 2>/dev/null || true)
  printf 'unit=%s active=%s substate=%s main_pid=%s\n' \
    "$unit" "${active:-unknown}" "${substate:-unknown}" "${main_pid:-unknown}"
  if [[ "$active" == active ]] && check_model; then
    printf 'OmniLion API is ready at %s/v1 (bridge-only).\n' "$base_url"
    return 0
  fi
  return 1
}

case "$action" in
  start)
    if systemctl --user is-active --quiet "$unit"; then
      show_status
      exit $?
    fi
    systemctl --user reset-failed "$unit" >/dev/null 2>&1 || true
    command=(
      "$OMNILION_VENV/bin/vllm" serve "$OMNILION_MODEL"
      --revision "$OMNILION_REVISION"
      --served-model-name OmniLion
      --host "$OMNILION_BIND_HOST"
      --port "$OMNILION_PORT"
      --dtype bfloat16
      --max-model-len "$OMNILION_MAX_MODEL_LEN"
      --max-num-seqs 1
      --gpu-memory-utilization "$OMNILION_GPU_MEMORY_UTILIZATION"
      --limit-mm-per-prompt '{"image":1,"video":1,"audio":1}'
      --media-io-kwargs "{\"video\":{\"num_frames\":${OMNILION_VIDEO_NUM_FRAMES}}}"
      --chat-template-content-format string
      --enforce-eager
    )
    systemd-run --user \
      --unit "$unit" \
      --collect \
      --property Restart=on-failure \
      --property RestartSec=5 \
      --property="EnvironmentFile=$ENV_FILE" \
      --setenv VLLM_PLUGINS=omnilion \
      --setenv PYTHONNOUSERSITE=1 \
      --setenv PYTHONUNBUFFERED=1 \
      --setenv "PATH=$OMNILION_VENV/bin:$PATH" \
      /bin/sh -c 'export VLLM_API_KEY="$OMNILION_API_KEY"; exec "$@"' sh \
      "${command[@]}" >/dev/null
    started=$SECONDS
    until check_model; do
      if ! systemctl --user is-active --quiet "$unit"; then
        journalctl --user -u "$unit" --no-pager -n 120 >&2 || true
        die "OmniLion service exited during startup"
      fi
      (( SECONDS - started < startup_timeout )) || {
        journalctl --user -u "$unit" --no-pager -n 120 >&2 || true
        systemctl --user stop "$unit" >/dev/null 2>&1 || true
        die "OmniLion did not become ready within ${startup_timeout}s; unit stopped"
      }
      sleep 2
    done
    printf 'OmniLion is healthy at %s/v1 (bridge-only).\n' "$base_url"
    ;;
  restart)
    systemctl --user stop "$unit" >/dev/null 2>&1 || true
    exec "$0" start
    ;;
  stop)
    systemctl --user stop "$unit" >/dev/null 2>&1 || true
    printf 'Stopped %s (or it was already inactive).\n' "$unit"
    ;;
  status)
    show_status
    ;;
  logs)
    journalctl --user -u "$unit" --no-pager -n "${OMNILION_LOG_LINES:-200}"
    ;;
  *)
    die "usage: $0 {start|restart|stop|status|logs}"
    ;;
esac
