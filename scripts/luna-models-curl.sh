#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

require_command curl
require_command env
require_command gio
require_command python3
read_luna_key

response=$(mktemp "${TMPDIR:-/tmp}/luna-models-curl.XXXXXX")
chmod 600 "$response"
trap 'gio trash "$response" >/dev/null 2>&1 || true' EXIT

printf 'Authorization: Bearer %s\n' "$LUNA_KEY" \
  | env -u LUNA_KEY curl --header @- --fail --silent --show-error \
      --user-agent 'Mozilla/5.0 LunaCurlSmoke/1.0' \
      --output "$response" \
      "$LUNA_URL/v1/models"

env -u LUNA_KEY python3 - "$response" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    body = json.load(stream)
models = sorted(
    row.get("id") for row in body.get("data", []) if isinstance(row.get("id"), str)
)
required = {"gpt-5.6-luna", "omnilion"}
missing = required - set(models)
if missing:
    raise SystemExit(f"deployment key is missing model aliases: {sorted(missing)}")
print("PASS: authenticated model aliases: " + ", ".join(models))
PY
