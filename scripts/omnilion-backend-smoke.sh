#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

audio_file=${1:-${OMNILION_SMOKE_AUDIO:-}}
[[ -n "$audio_file" ]] || die "usage: $0 /path/to/audio.wav"
[[ -r "$audio_file" ]] || die "audio file is not readable: $audio_file"
require_command env
require_command python3
load_env_file

base_url="http://$OMNILION_BIND_HOST:$OMNILION_PORT/v1"
printf '%s\n' "$OMNILION_API_KEY" | env -u OMNILION_API_KEY python3 \
  "$SCRIPT_DIR/omnilion-backend-smoke.py" \
  --base-url "$base_url" \
  --audio "$audio_file"
