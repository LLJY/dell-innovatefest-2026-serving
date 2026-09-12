#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

require_command curl
require_command python3
load_env_file

[[ -n "${LITELLM_MASTER_KEY:-}" ]] || die "LITELLM_MASTER_KEY is missing from $ENV_FILE"
[[ ! -e "$LUNA_KEY_FILE" ]] || die "$LUNA_KEY_FILE already exists; revoke the old key before removing this file and creating another"
key_dir=$(dirname -- "$LUNA_KEY_FILE")
mkdir -p "$key_dir"
chmod 700 "$key_dir"
key_dir=$(canonical_existing_dir "$key_dir")
reject_repo_path "$key_dir" "deployment key directory"
LUNA_KEY_FILE="$key_dir/$(basename -- "$LUNA_KEY_FILE")"
wait_for_http "$LUNA_URL/health/liveliness" 30 || die "LiteLLM is not reachable at $LUNA_URL"

duration=${LUNA_KEY_DURATION:-24h}
response=$(mktemp "${TMPDIR:-/tmp}/luna-key-response.XXXXXX")
trap 'rm -f "$response"' EXIT
chmod 600 "$response"

http_code=$(curl -sS -o "$response" -w '%{http_code}' "$LUNA_URL/key/generate" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary "{\"models\":[\"gpt-5.6-sol\",\"OmniLion\"],\"key_alias\":\"luna-deployment\",\"user_id\":\"openshift\",\"duration\":\"$duration\"}")
[[ "$http_code" == 200 ]] || { cat "$response" >&2; die "key generation returned HTTP $http_code"; }

python3 - "$response" "$LUNA_KEY_FILE" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    key = json.load(source).get("key")
if not isinstance(key, str) or not key:
    raise SystemExit("LiteLLM response did not contain a key")
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as target:
    target.write(key + "\n")
PY

printf 'Saved the %s deployment key to %s (the key was not printed).\n' "$duration" "$LUNA_KEY_FILE"
printf 'Run scripts/luna-smoke.sh next.\n'
