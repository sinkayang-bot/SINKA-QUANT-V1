#!/usr/bin/env bash
# SINKA QUANT: explicitly deploy a ONE-SHOT, DEMO-ONLY, GET-only Cloud Run Job.
# Never executes the job and never obtains or prints secret values.
set -Eeuo pipefail
umask 077

PROJECT_ID="${PROJECT_ID:-sinka-quant}"
REGION="${REGION:-asia-east1}"
REPOSITORY="${REPOSITORY:-sinka-okx-readonly}"
JOB="${JOB:-sinka-okx-readonly-smoke}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-sinka-okx-readonly@${PROJECT_ID}.iam.gserviceaccount.com}"

if [[ "${PROJECT_ID}" != "sinka-quant" ]]; then
  echo "BLOCKED: unexpected Google Cloud project" >&2; exit 1
fi
for command in gcloud git mktemp; do
  command -v "$command" >/dev/null || { echo "Missing: $command" >&2; exit 1; }
done
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for f in Dockerfile.okx-smoke okx_readonly.py okx_orders.py okx_pending_orders.py okx_smoke.py; do
  [[ -f "$ROOT/backend/$f" ]] || { echo "Missing backend/$f" >&2; exit 1; }
done
echo "Target: $PROJECT_ID / $REGION; one-shot demo-only read-only job: $JOB"
echo "This deploys cloud resources that may incur charges, but WILL NOT execute the job."
read -r -p "Type DEPLOY to proceed: " confirmation
[[ "$confirmation" == "DEPLOY" ]] || { echo "Cancelled"; exit 0; }

gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts describe "$SERVICE_ACCOUNT" --project "$PROJECT_ID" >/dev/null
for secret in OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE; do
  gcloud secrets describe "$secret" --project "$PROJECT_ID" >/dev/null
done
if ! gcloud artifacts repositories describe "$REPOSITORY" --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" --project "$PROJECT_ID" --location "$REGION" \
    --repository-format=docker --description="SINKA QUANT demo-only read-only diagnostics"
fi
ctx="$(mktemp -d)"
trap 'rm -rf "$ctx"' EXIT
cp "$ROOT/backend/Dockerfile.okx-smoke" "$ctx/Dockerfile"
cp "$ROOT/backend/okx_readonly.py" "$ROOT/backend/okx_orders.py" \
   "$ROOT/backend/okx_pending_orders.py" "$ROOT/backend/okx_smoke.py" "$ctx/"
image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/okx-smoke:initial"
gcloud builds submit "$ctx" --project "$PROJECT_ID" --region "$REGION" --tag "$image"

# The worker is never deployed here. This job has NO web endpoint and NO trade permission.
# Secrets are only mapped at execution, not baked into the image or printed.
gcloud run jobs deploy "$JOB" \
  --project "$PROJECT_ID" --region "$REGION" --image "$image" \
  --service-account "$SERVICE_ACCOUNT" \
  --set-secrets="OKX_API_KEY=OKX_API_KEY:latest,OKX_API_SECRET=OKX_API_SECRET:latest,OKX_API_PASSPHRASE=OKX_API_PASSPHRASE:latest" \
  --set-env-vars="SINKA_OKX_DEMO=true,SINKA_OKX_READONLY_SMOKE=true" \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=120s \
  --cpu=1 --memory=512Mi
echo "CREATED: $JOB (NOT executed). Do not schedule or run until reviewing permissions and costs."
