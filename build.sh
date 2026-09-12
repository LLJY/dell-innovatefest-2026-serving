#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$ROOT_DIR/scripts/luna-common.sh"

load_env_file

[[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]] || die "CLOUDFLARE_TUNNEL_TOKEN is missing from $ENV_FILE"
[[ "$CLOUDFLARE_TUNNEL_TOKEN" != *replace-* ]] || die "CLOUDFLARE_TUNNEL_TOKEN still contains an example placeholder"

"$ROOT_DIR/scripts/luna-up.sh"

printf '%s\n' 'Starting Cloudflare Tunnel...'
compose --profile edge up -d cloudflared

printf '%s\n' 'Luna translator, LiteLLM, and Cloudflare Tunnel are up.'
compose --profile edge ps
