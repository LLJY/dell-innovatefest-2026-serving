#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
COMPOSE_FILE="$REPO_ROOT/compose.luna.yml"
ENV_FILE=${ENV_FILE:-"$REPO_ROOT/.env"}
LUNA_URL=${LUNA_URL:-http://127.0.0.1:4000}
LUNA_KEY_FILE=${LUNA_KEY_FILE:-"${XDG_CONFIG_HOME:-$HOME/.config}/gb10-serving/luna-deployment-key"}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

resolve_omnilion_variant() {
  local variant=${OMNILION_VARIANT_OVERRIDE:-${OMNILION_VARIANT:-custom}}
  case "$variant" in
    bf16)
      OMNILION_MODEL=LLJYY/OmniLion
      OMNILION_REVISION=405ef8e1d21224edca0bdff6499389d4260c17e2
      ;;
    nvfp4)
      OMNILION_MODEL=LLJYY/OmniLion-NVFP4-W4A16
      OMNILION_REVISION=cc2628352cf7eb76c93b992c4f3079f4b3bc9498
      ;;
    custom)
      [[ -n "${OMNILION_MODEL:-}" ]] || die "OMNILION_MODEL is required for the custom variant"
      [[ -n "${OMNILION_REVISION:-}" ]] || die "OMNILION_REVISION is required for the custom variant"
      ;;
    *)
      die "unknown OmniLion variant: $variant (expected bf16, nvfp4, or custom)"
      ;;
  esac
  OMNILION_VARIANT=$variant
  export OMNILION_VARIANT OMNILION_MODEL OMNILION_REVISION
}

omnilion_command_matches() {
  local expected_model=$1 expected_revision=$2 model= revision=
  shift 2
  while (( $# )); do
    case "$1" in
      serve)
        shift
        model=${1:-}
        ;;
      --revision)
        shift
        revision=${1:-}
        ;;
    esac
    (( $# )) && shift
  done
  [[ "$model" == "$expected_model" && "$revision" == "$expected_revision" ]]
}

omnilion_active_service_matches() {
  local unit=$1 pid
  local -a command
  pid=$(systemctl --user show "$unit" --property=MainPID --value 2>/dev/null || true)
  [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/cmdline" ]] || return 1
  mapfile -d '' -t command < "/proc/$pid/cmdline"
  omnilion_command_matches "$OMNILION_MODEL" "$OMNILION_REVISION" "${command[@]}"
}

file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null || true
}

file_uid() {
  stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1" 2>/dev/null || true
}

file_gid() {
  stat -c '%g' "$1" 2>/dev/null || stat -f '%g' "$1" 2>/dev/null || true
}

canonical_existing_dir() {
  (CDPATH= cd -P -- "$1" && pwd)
}

reject_repo_path() {
  local path=$1 label=$2
  [[ "$path" != "$REPO_ROOT" && "$path" != "$REPO_ROOT/"* ]] || die "$label must be outside the repository: $path"
}

# Load plain KEY=VALUE entries without evaluating the file as shell code.
load_env_file() {
  local line key value
  [[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE; copy .env.example to .env and replace every placeholder"
  local env_mode
  env_mode=$(file_mode "$ENV_FILE")
  [[ "$env_mode" == 600 || "$env_mode" == 400 ]] || die "$ENV_FILE must have mode 600 or 400 (found ${env_mode:-unknown})"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || die "invalid line in $ENV_FILE: $line"
    key=${line%%=*}
    value=${line#*=}
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "invalid variable name in $ENV_FILE: $key"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value=${value:1:${#value}-2}
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value=${value:1:${#value}-2}
    fi
    export "$key=$value"
  done < "$ENV_FILE"
}

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

wait_for_http() {
  local url=$1 timeout_seconds=${2:-300} started=$SECONDS
  until curl -fsS "$url" >/dev/null 2>&1; do
    (( SECONDS - started < timeout_seconds )) || return 1
    sleep 2
  done
}

read_luna_key() {
  local key_dir key_mode
  key_dir=$(canonical_existing_dir "$(dirname -- "$LUNA_KEY_FILE")") || die "deployment key directory does not exist"
  reject_repo_path "$key_dir" "deployment key directory"
  LUNA_KEY_FILE="$key_dir/$(basename -- "$LUNA_KEY_FILE")"
  [[ -r "$LUNA_KEY_FILE" ]] || die "missing deployment key: $LUNA_KEY_FILE; run scripts/luna-create-key.sh"
  key_mode=$(file_mode "$LUNA_KEY_FILE")
  [[ "$key_mode" == 600 || "$key_mode" == 400 ]] || die "$LUNA_KEY_FILE must have mode 600 or 400 (found ${key_mode:-unknown})"
  LUNA_KEY=$(tr -d '\r\n' < "$LUNA_KEY_FILE")
  [[ -n "$LUNA_KEY" ]] || die "deployment key file is empty: $LUNA_KEY_FILE"
  export LUNA_KEY
}
