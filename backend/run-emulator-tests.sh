#!/usr/bin/env bash
set -euo pipefail
: "${FIRESTORE_EMULATOR_HOST:?Start a Firestore emulator first}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-sinka-emulator}"
cd "$(dirname "$0")"
python -m pytest -q tests/test_lease_emulator.py
