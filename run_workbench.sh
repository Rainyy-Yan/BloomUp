#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
TASK_PYTHON="${CHALLENGE_PYTHON:-python3}"
if [ "$#" -eq 0 ]; then set -- status; fi
exec "$TASK_PYTHON" -m challenge "$@"
