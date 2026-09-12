#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

audio_file=${1:-${OMNILION_SMOKE_AUDIO:-}}
[[ -n "$audio_file" ]] || die "usage: $0 /path/to/audio.wav"
[[ -r "$audio_file" ]] || die "audio file is not readable: $audio_file"
require_command curl
require_command env
require_command gio
require_command python3
read_luna_key

response=$(mktemp "${TMPDIR:-/tmp}/omnilion-curl-smoke.XXXXXX")
chmod 600 "$response"
trap 'gio trash "$response" >/dev/null 2>&1 || true' EXIT

# The quotes inside curl's form expression preserve whitespace and separators
# in the local path; the outer shell quotes keep the expression one argument.
file_form="file=@\"$audio_file\""
printf 'Authorization: Bearer %s\n' "$LUNA_KEY" \
  | env -u LUNA_KEY curl --header @- --fail-with-body --silent --show-error \
      --user-agent 'Mozilla/5.0 OmniLionCurlSmoke/1.0' \
      --form 'model=omnilion' \
      --form 'response_format=json' \
      --form "$file_form" \
      --output "$response" \
      "$LUNA_URL/v1/audio/transcriptions"

env -u LUNA_KEY python3 - "$response" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    body = json.load(stream)
text = body.get("text")
if not isinstance(text, str) or not text.strip():
    raise SystemExit("OmniLion returned an empty transcription")
print(f"PASS: authenticated omnilion curl transcription returned {len(text)} characters")
PY
