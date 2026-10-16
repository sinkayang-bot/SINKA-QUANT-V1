#!/usr/bin/env bash
# Authorized deployment only. No trade routes, secret payload reads or OKX scan.
set -Eeuo pipefail
umask 077
[[ "${1:-}" == "--approved" && "${2:-}" =~ ^[A-Za-z0-9_-]{1,128}$ && $# == 2 ]] || {
  echo 'Usage: bash firebase/deploy-okx-status.sh --approved OWNER_FIREBASE_UID' >&2; exit 2;
}
owner_uid="$2"
project=sinka-quant
region=asia-east1
repository=sinka-okx-readonly
service=sinka-okx-readonly-status
sa="${service}@${project}.iam.gserviceaccount.com"
role="projects/${project}/roles/sinkaFirebaseRevocationReader"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for executable in gcloud python3 mktemp; do command -v "$executable" >/dev/null; done
ctx="$(mktemp -d)"
trap 'rm -rf "$ctx"' EXIT

gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com identitytoolkit.googleapis.com --project "$project"
gcloud artifacts repositories describe "$repository" --project "$project" --location "$region" >/dev/null
# Metadata only. Pin the enabled versions for this revision; never access payloads.
secret_refs=""
for secret in OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE; do
  version="$(gcloud secrets versions describe latest --secret "$secret" --project "$project" --format='value(name)')"
  [[ "${version##*/}" =~ ^[0-9]+$ ]] || { echo 'Invalid secret version metadata' >&2; exit 1; }
  state="$(gcloud secrets versions describe "${version##*/}" --secret "$secret" --project "$project" --format='value(state)')"
  [[ "$state" == ENABLED && "${version##*/}" =~ ^[0-9]+$ ]] || { echo 'Secret version unavailable' >&2; exit 1; }
  secret_refs+="${secret}=${secret}:${version##*/},"
done
if ! gcloud iam service-accounts describe "$sa" --project "$project" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$service" --project "$project" --display-name='SINKA demo read-only status'
fi
if gcloud iam roles describe sinkaFirebaseRevocationReader --project "$project" >/dev/null 2>&1; then
  gcloud iam roles update sinkaFirebaseRevocationReader --project "$project" \
    --permissions=firebaseauth.users.get --stage=GA >/dev/null
else
  gcloud iam roles create sinkaFirebaseRevocationReader --project "$project" \
    --title='SINKA Firebase revoked-session reader' --permissions=firebaseauth.users.get --stage=GA >/dev/null
fi
gcloud projects add-iam-policy-binding "$project" --member="serviceAccount:$sa" \
  --role="$role" --condition=None --quiet >/dev/null
for secret in OKX_API_KEY OKX_API_SECRET OKX_API_PASSPHRASE; do
  gcloud secrets add-iam-policy-binding "$secret" --project "$project" --member="serviceAccount:$sa" \
    --role=roles/secretmanager.secretAccessor --condition=None --quiet >/dev/null
done
cp "$root/backend/Dockerfile.okx-status" "$ctx/Dockerfile"
for file in firebase_auth.py okx_readonly.py okx_orders.py okx_pending_orders.py okx_smoke.py okx_status_app.py; do
  cp "$root/backend/$file" "$ctx/"
done
image="${region}-docker.pkg.dev/${project}/${repository}/okx-status:readonly"
digest="$(gcloud builds submit "$ctx" --project "$project" --region "$region" --tag "$image" \
  --suppress-logs --format='value(results.images[0].digest)')"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Build did not return an immutable digest' >&2; exit 1; }
image="${image%:*}@${digest}"
gcloud run deploy "$service" --project "$project" --region "$region" --image "$image" \
  --service-account "$sa" --set-secrets="${secret_refs%,}" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$project,SINKA_OWNER_UID=$owner_uid,SINKA_OKX_DEMO=true,SINKA_OKX_READONLY_API=true" \
  --cpu=1 --memory=512Mi --min-instances=0 --max-instances=1 --concurrency=4 \
  --timeout=60s --cpu-throttling --no-cpu-boost --ingress=all --allow-unauthenticated --quiet
url="$(gcloud run services describe "$service" --project "$project" --region "$region" --format='value(status.url)')"
# Only public process health and access denial. Neither request calls OKX.
python3 - "$url" "$image" <<'PY'
import json,sys,urllib.request,urllib.error,urllib.parse
url,image=sys.argv[1:]
p=urllib.parse.urlparse(url)
assert p.scheme=='https' and p.hostname.endswith('.run.app') and p.path in ('','/') and not p.query and not p.fragment
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
opener=urllib.request.build_opener(NoRedirect)
with opener.open(url+'/health',timeout=90) as r:
    health=json.load(r)
assert health.get('status')=='ok' and health.get('trading_enabled') is False and health.get('okx_connection_verified') is False
for headers in ({},{'Authorization':'Bearer invalid-deployment-probe'}):
    try:
        opener.open(urllib.request.Request(url+'/api/okx/status',headers=headers),timeout=90)
        raise RuntimeError('Unauthenticated diagnostics unexpectedly accessible')
    except urllib.error.HTTPError as e:
        assert e.code==401, 'Authentication probe did not return 401'
print(json.dumps({'service_url':url,'image':image,'public_health_verified':True,'missing_token_denied':True,
                  'invalid_token_denied':True,'okx_connection_verified':False,'trading_enabled':False}))
PY
