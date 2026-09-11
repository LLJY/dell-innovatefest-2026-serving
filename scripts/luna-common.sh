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

file_mode() {
  stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null || true
}

file_uid() {
  stat -f '%u' "$1" 2>/dev/null || stat -c '%u' "$1" 2>/dev/null || true
}

file_gid() {
  stat -f '%g' "$1" 2>/dev/null || stat -c '%g' "$1" 2>/dev/null || true
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
