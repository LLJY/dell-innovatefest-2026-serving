#!/usr/bin/env bash

set -euo pipefail
source "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/luna-common.sh"

require_command docker
load_env_file

compose_status=0
model_status=0
compose down --remove-orphans || compose_status=$?
"$SCRIPT_DIR/omnilion-runtime.sh" down || model_status=$?

if (( compose_status != 0 || model_status != 0 )); then
  die "shutdown incomplete (compose=$compose_status, omnilion=$model_status)"
fi
printf 'Luna containers and native OmniLion are stopped; named data volumes were preserved.\n'
