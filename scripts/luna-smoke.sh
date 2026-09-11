#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

require_command curl
require_command python3
read_luna_key

api="$LUNA_URL/v1/chat/completions"
auth=( -H "Authorization: Bearer $LUNA_KEY" -H 'Content-Type: application/json' )
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/luna-live-smoke.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT
chmod 700 "$tmp_dir"

printf '\n== Models ==\n'
curl -fsS "$LUNA_URL/v1/models" -H "Authorization: Bearer $LUNA_KEY" | python3 -m json.tool

printf '\n== Non-streaming text ==\n'
curl -fsS "$api" "${auth[@]}" --data-binary \
  '{"model":"gpt-5.6-sol","messages":[{"role":"user","content":"Reply with exactly: luna live"}]}' \
  | tee "$tmp_dir/text.json" | python3 -m json.tool
python3 - "$tmp_dir/text.json" <<'PY'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
text = body["choices"][0]["message"]["content"]
if not isinstance(text, str) or not text.strip():
    raise SystemExit("empty non-streaming assistant response")
PY

printf '\n== Streaming text ==\n'
curl -fsSN "$api" "${auth[@]}" --data-binary \
  '{"model":"gpt-5.6-sol","stream":true,"messages":[{"role":"user","content":"Stream a greeting in five words or fewer."}]}' \
  | tee "$tmp_dir/stream.sse"
grep -q '^data: \[DONE\]' "$tmp_dir/stream.sse" || die "stream did not terminate with [DONE]"

printf '\n== Forced tool call ==\n'
cat > "$tmp_dir/tool-request.json" <<'JSON'
{"model":"gpt-5.6-sol","messages":[{"role":"user","content":"Call get_server_time now."}],"tools":[{"type":"function","function":{"name":"get_server_time","description":"Return the current server time supplied by the caller.","parameters":{"type":"object","properties":{},"additionalProperties":false}}}],"tool_choice":{"type":"function","function":{"name":"get_server_time"}}}
JSON
curl -fsS "$api" "${auth[@]}" --data-binary @"$tmp_dir/tool-request.json" > "$tmp_dir/tool-response.json"
python3 -m json.tool < "$tmp_dir/tool-response.json"

python3 - "$tmp_dir/tool-request.json" "$tmp_dir/tool-response.json" "$tmp_dir/tool-result-request.json" <<'PY'
import datetime
import json
import sys

request = json.load(open(sys.argv[1], encoding="utf-8"))
response = json.load(open(sys.argv[2], encoding="utf-8"))
message = response["choices"][0]["message"]
calls = message.get("tool_calls") or []
if len(calls) != 1 or calls[0].get("function", {}).get("name") != "get_server_time":
    raise SystemExit("expected exactly one get_server_time tool call")
call_id = calls[0]["id"]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
body = {
    "model": request["model"],
    "messages": request["messages"] + [message, {
        "role": "tool", "tool_call_id": call_id,
        "content": json.dumps({"utc": now}),
    }],
    "tools": request["tools"],
    "tool_choice": "none",
}
json.dump(body, open(sys.argv[3], "w", encoding="utf-8"))
PY

printf '\n== Tool result returned to model ==\n'
curl -fsS "$api" "${auth[@]}" --data-binary @"$tmp_dir/tool-result-request.json" \
  | tee "$tmp_dir/final.json" | python3 -m json.tool
python3 - "$tmp_dir/final.json" <<'PY'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
text = body["choices"][0]["message"]["content"]
if not isinstance(text, str) or not text.strip():
    raise SystemExit("empty response after tool output")
PY

printf '\nPASS: live text, SSE, tool-call, and tool-output checks completed.\n'
