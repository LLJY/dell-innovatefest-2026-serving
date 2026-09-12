#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

PROFILE=omnilion-container
OVERRIDE_FILE="$REPO_ROOT/compose.omnilion-container.yml"
action=${1:-status}

container_compose() {
  docker compose \
    -f "$COMPOSE_FILE" \
    -f "$OVERRIDE_FILE" \
    --env-file "$ENV_FILE" \
    --profile "$PROFILE" \
    "$@"
}

require_command docker
load_env_file

case "$action" in
  config)
    container_compose config --quiet
    printf 'OmniLion container profile configuration is valid.\n'
    ;;
  up)
    require_command systemctl
    if systemctl --user is-active --quiet omnilion-vllm.service; then
      die "host omnilion-vllm.service is active; stop it explicitly before starting the container profile"
    fi
    container_compose config --quiet
    container_compose up -d --build omnilion-vllm omnilion-adapter litellm
    ;;
  status)
    container_compose ps omnilion-vllm omnilion-adapter litellm
    ;;
  down)
    container_compose stop omnilion-adapter omnilion-vllm
    printf 'Containerized OmniLion is stopped. Run scripts/luna-up.sh to restore the host-native route.\n'
    ;;
  *)
    die "usage: $0 {config|up|status|down}"
    ;;
esac
