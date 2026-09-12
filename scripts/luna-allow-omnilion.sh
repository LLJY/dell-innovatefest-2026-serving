#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

require_command env
require_command python3
load_env_file
read_luna_key

{
  printf '%s\n' "$LITELLM_MASTER_KEY"
  printf '%s\n' "$LUNA_KEY"
} | env -u LITELLM_MASTER_KEY -u LUNA_KEY python3 "$SCRIPT_DIR/luna-allow-omnilion.py" \
  --base-url "$LUNA_URL"
