#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

audio_file=${1:-${OMNILION_SMOKE_AUDIO:-}}
[[ -n "$audio_file" ]] || die "usage: $0 /path/to/audio.wav"
[[ -r "$audio_file" ]] || die "audio file is not readable: $audio_file"
require_command env
require_command python3
read_luna_key

printf '%s\n' "$LUNA_KEY" | env -u LUNA_KEY python3 "$SCRIPT_DIR/omnilion-smoke.py" \
  --base-url "$LUNA_URL" \
  --audio "$audio_file"
