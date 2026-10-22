#!/usr/bin/env bash
# Preparation is free/local. Cloud mutations require the explicit cost option.
set -Eeuo pipefail
umask 077
mode="${1:---plan}"
[[ $# -le 1 && ( "$mode" == --plan || "$mode" == --approved-cost ) ]] || exit 2
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project=sinka-quant
region=asia-east1
zone=asia-east1-b
vm=sinka-demo-runner
network=sinka-demo-runner-net
subnet=sinka-demo-runner-subnet
firewall=sinka-demo-runner-iap
sa=sinka-demo-runner
identity="$sa@$project.iam.gserviceaccount.com"
if [[ "$mode" == --plan ]]; then
  cat <<'PLAN'
{"project":"sinka-quant","zone":"asia-east1-b","vm":"sinka-demo-runner","machine":"e2-small","boot_disk_gib":10,"data_disk_gib":10,"disk_type":"pd-standard","disk_auto_delete":false,"public_ssh":false,"iap_ssh":true,"service_account":"sinka-demo-runner","controller_restart":"systemd","strategy_round_seconds":900,"controller_session_seconds":14400,"source_fenced":true,"state":"original_sqlite_backup_and_binding","live_trading_enabled":false,"starts_new_trade":false,"new_billable_resources":true,"executed":false}
PLAN
  exit 0
fi
command -v gcloud >/dev/null
python="$HOME/sinka-demo-venv/bin/python"
[[ -x "$python" ]] || { echo 'Original demo Python environment required.' >&2; exit 2; }
[[ -n "$(gcloud auth list --filter=status:ACTIVE --format='value(account)')" ]] || exit 2
gcloud projects describe "$project" --format='value(projectId)' >/dev/null
# Only read existing resources first. No reuse, overwrite, deletion or fallback.
for spec in 'instances sinka-demo-runner' 'networks sinka-demo-runner-net' 'networks subnets sinka-demo-runner-subnet' 'firewall-rules sinka-demo-runner-iap' 'disks sinka-demo-runner' 'disks sinka-demo-data' 'addresses sinka-demo-runner-ip'; do
  read -ra fields <<< "$spec"
  name="${fields[-1]}"; unset 'fields[-1]'
  found="$(gcloud compute "${fields[@]}" list --project "$project" --filter="name=$name" --format='value(name)')"
  [[ -z "$found" ]] || { echo "Resource collision: $name; no resources changed." >&2; exit 2; }
done
[[ -z "$(gcloud iam service-accounts list --project "$project" --filter="email=$identity" --format='value(email)')" ]] || {
  echo 'Service account already exists; refusing reuse.' >&2; exit 2;
}
work="$(mktemp -d "$HOME/sinka-migration-XXXXXX")"
# Keep this local recovery bundle on failure. Never print its private contents.
echo 'Preparing original ledger backup; all old workers must already be stopped.'
"$python" "$root/backend/okx_demo_migration.py" --export "$work/handoff"
install -d -m 0700 "$work/backend" "$work/strategy" "$work/migration"
cp "$root"/backend/*.py "$work/backend/"
cp "$root/backend/requirements.txt" "$work/backend/"
cp "$root"/strategy/*.mjs "$work/strategy/"
cp "$root/migration/Dockerfile" "$work/migration/"
cp "$root/migration/install-vm.sh" "$work/migration/"
(cd "$work" && find backend strategy migration handoff -type f -print0 | sort -z | xargs -0 sha256sum > payload.sha256)
tar -czf "$work/payload.tgz" -C "$work" backend strategy migration handoff payload.sha256
# Chargeable resources and IAM changes begin only in --approved-cost mode.
gcloud services enable compute.googleapis.com iap.googleapis.com --project "$project" --quiet
gcloud iam service-accounts create "$sa" --project "$project" --display-name='SINKA demo controller only' --quiet
gcloud projects add-iam-policy-binding "$project" --member="serviceAccount:$identity" --role=roles/datastore.user --condition=None --quiet >/dev/null
for secret in OKX_DEMO_TRADE_API_KEY OKX_DEMO_TRADE_API_SECRET OKX_DEMO_TRADE_API_PASSPHRASE; do
  # Existing credentials() verifies ENABLED version metadata before payload access.
  gcloud secrets add-iam-policy-binding "$secret" --project "$project" --member="serviceAccount:$identity" --role=roles/secretmanager.viewer --condition=None --quiet >/dev/null
  gcloud secrets add-iam-policy-binding "$secret" --project "$project" --member="serviceAccount:$identity" --role=roles/secretmanager.secretAccessor --condition=None --quiet >/dev/null
done
gcloud compute networks create "$network" --project "$project" --subnet-mode=custom --quiet
gcloud compute networks subnets create "$subnet" --project "$project" --region "$region" --network "$network" --range=172.31.241.0/28 --quiet
gcloud compute firewall-rules create "$firewall" --project "$project" --network "$network" --direction=INGRESS --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-service-accounts="$identity" --quiet
gcloud compute addresses create sinka-demo-runner-ip --project "$project" --region "$region" --network-tier=STANDARD --quiet
ip="$(gcloud compute addresses describe sinka-demo-runner-ip --project "$project" --region "$region" --format='value(address)')"
gcloud compute disks create sinka-demo-data --project "$project" --zone "$zone" --size=10GB --type=pd-standard --quiet
gcloud compute instances create "$vm" --project "$project" --zone "$zone" --machine-type=e2-small \
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=10GB --boot-disk-type=pd-standard --no-boot-disk-auto-delete \
  --disk=name=sinka-demo-data,device-name=sinka-demo-data,mode=rw,boot=no,auto-delete=no \
  --network="$network" --subnet="$subnet" --address="$ip" --network-tier=STANDARD \
  --service-account="$identity" --scopes=cloud-platform \
  --metadata=enable-oslogin=true,block-project-ssh-keys=true \
  --labels=sinka-purpose=demo-controller --quiet
echo 'VM provisioned. Transferring checksummed original state over IAP; no keys are uploaded.'
ssh_ready=false
for attempt in {1..12}; do
  if gcloud compute ssh "$vm" --project "$project" --zone "$zone" --tunnel-through-iap --quiet --command=true; then
    ssh_ready=true; break
  fi
  sleep 5
done
[[ "$ssh_ready" == true ]] || { echo 'VM SSH not ready; source remains fenced and backup retained.' >&2; exit 2; }
gcloud compute scp "$work/payload.tgz" "$vm:/tmp/sinka-demo-migration.tgz" --project "$project" --zone "$zone" --tunnel-through-iap --quiet
gcloud compute scp "$root/migration/install-vm.sh" "$vm:/tmp/sinka-demo-install.sh" --project "$project" --zone "$zone" --tunnel-through-iap --quiet
gcloud compute ssh "$vm" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command='sudo systemd-run --unit=sinka-demo-migration-install /bin/bash /tmp/sinka-demo-install.sh /tmp/sinka-demo-migration.tgz'
# Installation is owned by VM systemd, independent of this SSH/Cloud Shell connection.
installed=false
for attempt in {1..60}; do
  status="$(gcloud compute ssh "$vm" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
    --command='if sudo test -f /var/lib/sinka-demo-migration.ready; then echo ready; elif sudo systemctl is-failed --quiet sinka-demo-migration-install; then echo failed; else echo pending; fi')"
  if [[ "$status" == ready ]]; then installed=true; break; fi
  if [[ "$status" == failed ]]; then
    echo 'Installation needs review; source is fenced. Read sinka-demo-migration-install journal on VM.' >&2; exit 2
  fi
  sleep 10
done
[[ "$installed" == true ]] || { echo 'VM installation still pending; do not rerun migration or old workers.' >&2; exit 2; }
gcloud compute ssh "$vm" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command='sudo systemctl is-active sinka-demo-runner.service && sudo journalctl -u sinka-demo-runner.service -n 8 --no-pager'
echo 'Controller migrated; refresh web status. Account recovery is required before another strategy start.'
echo 'Original Cloud Shell state is fenced and retained. Do not restart old copies or remove the source fence.'
echo 'VM and disks are usage billed. The retained disks and reserved IP remain billable after VM shutdown.'
