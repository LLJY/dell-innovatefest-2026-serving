#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

require_command codex
require_command python3
load_env_file

[[ -n "${OPENAI_CREDENTIALS_HOST_DIR:-}" ]] || die "OPENAI_CREDENTIALS_HOST_DIR is missing from $ENV_FILE"
[[ "$OPENAI_CREDENTIALS_HOST_DIR" = /* ]] || die "OPENAI_CREDENTIALS_HOST_DIR must be an absolute path outside the repository"
[[ "${TRANSLATOR_UID:-}" =~ ^[0-9]+$ && "$TRANSLATOR_UID" -gt 0 ]] || die "TRANSLATOR_UID must be the non-root numeric UID returned by id -u"
[[ "${TRANSLATOR_GID:-}" =~ ^[0-9]+$ ]] || die "TRANSLATOR_GID must be the numeric GID returned by id -g"
[[ "$TRANSLATOR_UID" == "$(id -u)" && "$TRANSLATOR_GID" == "$(id -g)" ]] || die "run as TRANSLATOR_UID:TRANSLATOR_GID ($TRANSLATOR_UID:$TRANSLATOR_GID), currently $(id -u):$(id -g)"

credential_dir=$(python3 - "$OPENAI_CREDENTIALS_HOST_DIR" <<'PY'
import os, sys
print(os.path.realpath(sys.argv[1]))
PY
)
reject_repo_path "$credential_dir" "OAuth credential directory"

replace=false
case ${1:-} in
  "") ;;
  --replace) replace=true ;;
  *) die "usage: scripts/luna-oauth-login.sh [--replace]" ;;
esac

mkdir -p "$credential_dir"
chmod 700 "$credential_dir"
[[ $(file_mode "$credential_dir") == 700 ]] || die "$credential_dir must have mode 700"
[[ $(file_uid "$credential_dir") == "$TRANSLATOR_UID" && $(file_gid "$credential_dir") == "$TRANSLATOR_GID" ]] || die "$credential_dir must be owned by $TRANSLATOR_UID:$TRANSLATOR_GID"

refresh_file="$credential_dir/refresh-token"
account_file="$credential_dir/account-id"
if [[ "$replace" != true && ( -e "$refresh_file" || -e "$account_file" ) ]]; then
  die "OAuth credentials already exist; use --replace only when intentionally rotating them"
fi

login_root=$(mktemp -d "${TMPDIR:-/tmp}/luna-codex-login.XXXXXX")
chmod 700 "$login_root"
cleanup() {
  rm -rf "$login_root"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
export CODEX_HOME="$login_root/codex-home"
mkdir -p "$CODEX_HOME"
chmod 700 "$CODEX_HOME"

printf '%s\n' 'Starting official Codex device-code login.'
printf '%s\n' 'Open the URL shown below on another device and enter its one-time code.'
codex login --device-auth
codex login status

auth_file="$CODEX_HOME/auth.json"
[[ -r "$auth_file" ]] || die "Codex login succeeded but did not create $auth_file"

python3 - "$auth_file" "$refresh_file" "$account_file" <<'PY'
import json
import os
import sys
import tempfile

source, refresh_path, account_path = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    auth = json.load(handle)
tokens = auth.get("tokens")
if not isinstance(tokens, dict):
    raise SystemExit("Codex auth file contains no ChatGPT token set")
refresh = tokens.get("refresh_token")
account = tokens.get("account_id")
if not isinstance(refresh, str) or not refresh:
    raise SystemExit("Codex auth file contains no refresh token")
if not isinstance(account, str) or not account:
    raise SystemExit("Codex auth file contains no account ID")

def atomic_secret(path: str, value: str) -> None:
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".oauth-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise

atomic_secret(refresh_path, refresh)
atomic_secret(account_path, account)
PY

[[ $(file_mode "$refresh_file") == 600 ]] || die "refresh token was not stored with mode 600"
[[ $(file_mode "$account_file") == 600 ]] || die "account ID was not stored with mode 600"
printf 'OAuth credentials saved to %s (token values were not printed).\n' "$credential_dir"
printf '%s\n' 'The temporary Codex auth store will now be removed.'
printf '%s\n' 'Next: scripts/luna-up.sh'
