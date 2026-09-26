#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" -m challenge --root "${BLOOMUP_REPRO_ROOT:-.ci-state/reproduction}" reproduce
