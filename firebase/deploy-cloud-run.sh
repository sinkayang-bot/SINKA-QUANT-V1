#!/usr/bin/env bash
set -euo pipefail

# This deployment entrypoint intentionally refuses the current SQLite prototype.
# Run it only after the Firestore migration and owner-auth tests are implemented.
if [[ ! -f backend/firestore_ready.marker ]]; then
  echo "BLOCKED: Cloud Run deployment requires Firestore transactions, owner auth, and failover tests." >&2
  echo "The existing backend uses ephemeral SQLite and is NOT safe for Cloud Run." >&2
  exit 1
fi

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
: "${GOOGLE_CLOUD_REGION:=asia-east1}"
: "${SINKA_SERVICE:=sinka-paper-engine}"

build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT
cp backend/requirements.txt backend/*.py "$build_dir/"
cp backend/Dockerfile.firestore "$build_dir/Dockerfile"

gcloud run deploy "$SINKA_SERVICE" \
  --source "$build_dir" \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$GOOGLE_CLOUD_REGION" \
  --no-allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --concurrency 1 \
  --cpu 1 \
  --memory 512Mi \
  --no-cpu-throttling \
  --timeout 3600 \
  --env-vars-file firebase/cloud-run.env.yaml

echo "Cloud Run deploy requested. Verify logs, health, and continuity before using."
