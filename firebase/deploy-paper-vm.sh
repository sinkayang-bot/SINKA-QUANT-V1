#!/usr/bin/env bash
set -euo pipefail
# Default is a local, read-only plan. New billable resources require explicit approval.
mode="${1:---plan}"
if [[ "$mode" != --plan && "$mode" != --render-startup && "$mode" != --verify && "$mode" != --approved-cost ]]; then
  echo 'Usage: bash firebase/deploy-paper-vm.sh [--plan|--render-startup FILE|--verify|--approved-cost]' >&2
  exit 2
fi
if [[ "$mode" == --render-startup ]]; then
  [[ $# == 2 ]] || { echo 'Specify startup output path' >&2; exit 2; }
else
  [[ $# -le 1 ]] || { echo 'Unexpected arguments' >&2; exit 2; }
fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project=sinka-quant
zone=asia-east1-b
region=asia-east1
vm=sinka-paper-trial
network=sinka-paper-trial-net
subnet=sinka-paper-trial-subnet
firewall=sinka-paper-trial-deny-ingress
if [[ "$mode" == --plan ]]; then
  cat <<'PLAN'
{"project":"sinka-quant","zone":"asia-east1-b","vm":"sinka-paper-trial","machine":"e2-micro","disk_gib":10,"disk_type":"pd-standard","vm_limit_seconds":1800,"worker_limit_seconds":900,"termination_action":"STOP","keep_disk":true,"service_account":false,"secrets":false,"public_inbound":false,"mode":"local_paper","trading_enabled":false,"new_billable_resources":true,"executed":false}
PLAN
  exit 0
fi
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
prepare_startup() {
  (cd "$root" && sha256sum --check worker/vm-source.sha256 >/dev/null)
  mapfile -t sources < <(awk '{print $2}' "$root/worker/vm-source.sha256")
  for file in "${sources[@]}"; do
    [[ "$file" =~ ^(strategy|worker)/[a-zA-Z0-9_.-]+$ ]] || { echo 'Invalid source manifest' >&2; exit 2; }
  done
  tar -C "$root" --owner=0 --group=0 -czf "$work/source.tgz" "${sources[@]}"
  source_sha="$(sha256sum "$work/source.tgz" | cut -d ' ' -f 1)"
  {
    cat <<'HEADER'
#!/usr/bin/env bash
set -euo pipefail
install -d -m 0755 /opt/sinka-paper
HEADER
    echo "source_sha='$source_sha'"
    echo "base64 -d > /opt/sinka-paper/source.tgz <<'SINKA_SOURCE_B64'"
    base64 "$work/source.tgz"
    echo 'SINKA_SOURCE_B64'
    cat <<'HEADER'
echo "$source_sha  /opt/sinka-paper/source.tgz" | sha256sum --check --status
SINKA_RELEASE_DIR="$(mktemp -d /opt/sinka-paper/release.XXXXXX)"
export SINKA_RELEASE_DIR
tar -xzf /opt/sinka-paper/source.tgz -C "$SINKA_RELEASE_DIR"
HEADER
    cat "$root/worker/vm-startup.sh"
  } > "$work/startup.sh"
  [[ "$(wc -c < "$work/startup.sh")" -lt 200000 ]] || { echo 'Startup metadata exceeds conservative size limit' >&2; exit 2; }
  bash -n "$work/startup.sh"
}
if [[ "$mode" == --render-startup ]]; then
  prepare_startup
  cp "$work/startup.sh" "$2"
  echo 'Startup script rendered locally; no cloud resource was created.'
  exit 0
fi
command -v gcloud >/dev/null || { echo 'Run from authenticated Google Cloud Shell; gcloud is unavailable here.' >&2; exit 2; }
verify() {
  gcloud compute instances describe "$vm" --project "$project" --zone "$zone" --format=json > "$work/instance.json"
  gcloud compute instances get-serial-port-output "$vm" --project "$project" --zone "$zone" --port=1 > "$work/serial.txt" || : > "$work/serial.txt"
  python3 "$root/worker/vm-verify.py" "$work/instance.json" "$work/serial.txt"
}
if [[ "$mode" == --verify ]]; then verify; exit; fi
prepare_startup
[[ -n "$(gcloud auth list --filter=status:ACTIVE --format='value(account)')" ]] || { echo 'Google Cloud login required.' >&2; exit 2; }
# This executes only after --approved-cost; no budget setting is claimed as a hard billing cap.
if [[ -z "$(gcloud services list --enabled --project "$project" --filter='config.name=compute.googleapis.com' --format='value(config.name)')" ]]; then
  gcloud services enable compute.googleapis.com --project "$project" --quiet
fi
# Refuse all name collisions rather than reusing/updating any existing resource.
for spec in 'instances sinka-paper-trial' 'networks sinka-paper-trial-net' 'networks subnets sinka-paper-trial-subnet' 'firewall-rules sinka-paper-trial-deny-ingress' 'disks sinka-paper-trial'; do
  read -ra fields <<< "$spec"
  name="${fields[-1]}"
  unset 'fields[-1]'
  found="$(gcloud compute "${fields[@]}" list --project "$project" --filter="name=$name" --format='value(name)')"
  [[ -z "$found" ]] || { echo "BLOCKED: resource name already exists: $name" >&2; exit 2; }
done
gcloud compute networks create "$network" --project "$project" --subnet-mode=custom --bgp-routing-mode=regional --quiet
gcloud compute networks subnets create "$subnet" --project "$project" --region "$region" --network "$network" --range=172.31.240.0/28 --quiet
gcloud compute firewall-rules create "$firewall" --project "$project" --network "$network" --direction=INGRESS --priority=0 --action=DENY --rules=all --source-ranges=0.0.0.0/0 --quiet
gcloud compute instances create "$vm" --project "$project" --zone "$zone" --machine-type=e2-micro \
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=10GB --boot-disk-type=pd-standard --no-boot-disk-auto-delete \
  --network="$network" --subnet="$subnet" --network-tier=STANDARD --no-service-account --no-scopes \
  --max-run-duration=30m --instance-termination-action=STOP --no-restart-on-failure \
  --labels=sinka-purpose=paper-trial --metadata=block-project-ssh-keys=true,enable-oslogin=true \
  --metadata-from-file="startup-script=$work/startup.sh" --quiet
if ! verify; then
  echo 'Configuration verification failed; requesting immediate STOP of the new trial VM.' >&2
  gcloud compute instances stop "$vm" --project "$project" --zone "$zone" --quiet
  exit 2
fi
echo 'VM created with a 30-minute provider STOP limit; worker health must be verified after bootstrap.'
echo 'The retained 10GB disk continues billing after STOP. No existing Cloud Run service was modified.'
echo 'Run: bash firebase/deploy-paper-vm.sh --verify'
