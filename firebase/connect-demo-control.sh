#!/usr/bin/env bash
# One-time bootstrap in the existing authorized Cloud Shell. Never starts a trade.
set -Eeuo pipefail
umask 077
if [[ "${1:-}" != '--approved' || $# != 1 ]]; then
  echo 'Explicit --approved required: deploys usage-billed control resources; no trading starts.' >&2
  exit 2
fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="$HOME/sinka-demo-venv/bin/python"
command -v gcloud >/dev/null
command -v node >/dev/null
[[ -x "$python" && -f "$HOME/sinka-demo-trial/account.json" && -f "$HOME/sinka-demo-trial/ledger.sqlite" ]] || {
  echo 'Existing demo venv, account binding and ledger required; nothing reset.' >&2
  exit 2
}
"$python" -m pip install --quiet 'google-cloud-firestore>=2.20,<3'
python3 "$root/firebase/deploy-demo-control.py" --approved
echo 'Control API deployed. Connecting runner for one hour; no trade starts until an owner command is received.'
exec "$python" "$root/backend/okx_demo_control_runner.py" --connect --seconds 3600
