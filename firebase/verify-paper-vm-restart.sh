#!/usr/bin/env bash
set -euo pipefail
# Reuses exactly one existing bounded paper VM and disk. Never creates a resource.
mode="${1:---plan}"
[[ $# -le 1 ]] || { echo 'Unexpected arguments' >&2; exit 2; }
if [[ "$mode" == --plan ]]; then
  echo '{"vm":"sinka-paper-trial","project":"sinka-quant","zone":"asia-east1-b","action":"stop_then_start_same_vm","new_resources":false,"new_start_limit_minutes":30,"trading_enabled":false,"executed":false}'
  exit 0
fi
[[ "$mode" == --run-authorized-trial ]] || { echo 'Use --plan or --run-authorized-trial' >&2; exit 2; }
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v gcloud >/dev/null || { echo 'Use the existing authenticated Cloud Shell.' >&2; exit 2; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
project=sinka-quant
zone=asia-east1-b
name=sinka-paper-trial
snapshot() {
  gcloud compute instances describe "$name" --project "$project" --zone "$zone" --format=json > "$work/instance.json"
  gcloud compute disks describe "$name" --project "$project" --zone "$zone" --format=json > "$work/disk.json"
  gcloud compute instances get-serial-port-output "$name" --project "$project" --zone "$zone" --port=1 > "$work/serial.txt" || : > "$work/serial.txt"
}
snapshot
python3 "$root/worker/vm-recovery.py" before "$work/instance.json" "$work/disk.json" "$work/serial.txt" "$work/before.json"
state="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["status"])' "$work/instance.json")"
if [[ "$state" == RUNNING ]]; then
  echo 'Stopping the existing paper VM; its disk and checkpoint are retained.'
  gcloud compute instances stop "$name" --project "$project" --zone "$zone" --quiet
fi
# Start does not change the approved 30-minute STOP policy. Validate it again after boot.
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo 'Starting the same VM for one bounded persistence check.'
gcloud compute instances start "$name" --project "$project" --zone "$zone" --quiet
for attempt in $(seq 1 30); do
  snapshot
  if python3 "$root/worker/vm-recovery.py" after "$work/before.json" "$work/instance.json" "$work/disk.json" "$work/serial.txt" "$started_at" > "$work/report.json"; then
    cat "$work/report.json"
    echo 'Restart evidence verified. The VM still has its 30-minute automatic STOP limit.'
    exit 0
  else
    code=$?
    if [[ "$code" != 3 ]]; then
      cat "$work/report.json"
      echo 'Recovery verification failed; stopping the trial VM without deleting data.' >&2
      gcloud compute instances stop "$name" --project "$project" --zone "$zone" --quiet
      exit 2
    fi
  fi
  if (( attempt % 3 == 1 )); then echo 'Waiting for new-boot checkpoint and fresh public quote…'; fi
  sleep 10
done
cat "$work/report.json"
echo 'No verified recovery within five minutes; stopping the trial and retaining its disk.' >&2
gcloud compute instances stop "$name" --project "$project" --zone "$zone" --quiet
exit 2
