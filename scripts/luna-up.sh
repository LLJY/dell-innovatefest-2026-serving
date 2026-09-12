#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

require_command docker
require_command curl
load_env_file
resolve_omnilion_variant

required=(POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD LITELLM_MASTER_KEY LITELLM_SALT_KEY TRANSLATOR_SERVICE_KEY OPENAI_CREDENTIALS_HOST_DIR TRANSLATOR_UID TRANSLATOR_GID LITELLM_HOST_PORT OMNILION_MODEL OMNILION_REVISION OMNILION_VENV OMNILION_API_KEY OMNILION_API_BASE OMNILION_BIND_HOST OMNILION_PORT OMNILION_MAX_MODEL_LEN OMNILION_GPU_MEMORY_UTILIZATION OMNILION_VIDEO_NUM_FRAMES)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || die "$name is missing from $ENV_FILE"
  [[ "${!name}" != *replace-* ]] || die "$name still contains an example placeholder"
done
[[ "$LITELLM_MASTER_KEY" == sk-* ]] || die "LITELLM_MASTER_KEY must start with sk-"
[[ "$TRANSLATOR_UID" =~ ^[0-9]+$ && "$TRANSLATOR_UID" -gt 0 ]] || die "TRANSLATOR_UID must be a non-root numeric UID"
[[ "$TRANSLATOR_GID" =~ ^[0-9]+$ ]] || die "TRANSLATOR_GID must be a numeric GID"

credential_dir=$OPENAI_CREDENTIALS_HOST_DIR
[[ "$credential_dir" = /* ]] || credential_dir="$REPO_ROOT/$credential_dir"
[[ -d "$credential_dir" ]] || die "missing credential directory: $credential_dir"
credential_dir=$(canonical_existing_dir "$credential_dir")
reject_repo_path "$credential_dir" "OAuth credential directory"
dir_mode=$(file_mode "$credential_dir")
[[ "$dir_mode" == 700 ]] || die "$credential_dir must have mode 700 (found ${dir_mode:-unknown})"
[[ $(file_uid "$credential_dir") == "$TRANSLATOR_UID" && $(file_gid "$credential_dir") == "$TRANSLATOR_GID" ]] || die "$credential_dir must be owned by translator UID:GID $TRANSLATOR_UID:$TRANSLATOR_GID"
refresh_file="$credential_dir/refresh-token"
account_file="$credential_dir/account-id"
[[ -r "$refresh_file" ]] || die "missing readable OAuth refresh token: $refresh_file"
[[ -r "$account_file" ]] || die "missing readable OAuth account ID: $account_file"
[[ -w "$credential_dir" ]] || die "credential directory must be writable for atomic token rotation: $credential_dir"

mode=$(file_mode "$refresh_file")
[[ "$mode" == 600 ]] || die "$refresh_file must have mode 600 (found ${mode:-unknown})"
[[ $(file_uid "$refresh_file") == "$TRANSLATOR_UID" && $(file_gid "$refresh_file") == "$TRANSLATOR_GID" ]] || die "$refresh_file must be owned by translator UID:GID $TRANSLATOR_UID:$TRANSLATOR_GID"
mode=$(file_mode "$account_file")
[[ "$mode" == 600 ]] || die "$account_file must have mode 600 (found ${mode:-unknown})"
[[ $(file_uid "$account_file") == "$TRANSLATOR_UID" && $(file_gid "$account_file") == "$TRANSLATOR_GID" ]] || die "$account_file must be owned by translator UID:GID $TRANSLATOR_UID:$TRANSLATOR_GID"

printf 'Validating Compose configuration...\n'
compose config --quiet
printf 'Building the ARM64-compatible translator...\n'
compose build translator
printf 'Starting PostgreSQL and translator...\n'
if ! compose up -d postgres translator; then
  compose down --remove-orphans >/dev/null 2>&1 || true
  die "PostgreSQL or translator startup failed; partial Compose stack removed"
fi
printf 'Starting native OmniLion on the Docker host gateway...\n'
if ! "$SCRIPT_DIR/omnilion-service.sh" start; then
  compose down --remove-orphans >/dev/null 2>&1 || true
  die "OmniLion startup failed; Compose stack removed"
fi
printf 'Starting LiteLLM...\n'
if ! compose up -d litellm; then
  "$SCRIPT_DIR/omnilion-service.sh" stop >/dev/null 2>&1 || true
  compose down --remove-orphans >/dev/null 2>&1 || true
  die "LiteLLM startup failed; OmniLion and Compose stack stopped"
fi

if ! wait_for_http "$LUNA_URL/health/liveliness" 300; then
  compose ps >&2 || true
  "$SCRIPT_DIR/omnilion-service.sh" stop >/dev/null 2>&1 || true
  compose down --remove-orphans >/dev/null 2>&1 || true
  die "LiteLLM did not become healthy within 300 seconds; OmniLion and Compose stack stopped"
fi

compose ps
printf 'Luna core is healthy at %s\n' "$LUNA_URL"
printf 'Next: scripts/luna-create-key.sh && scripts/luna-smoke.sh\n'
