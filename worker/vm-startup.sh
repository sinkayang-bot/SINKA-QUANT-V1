#!/usr/bin/env bash
# Called by the generated startup header after extracting an integrity-checked source bundle.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
trap 'echo SINKA_PAPER_VM_BOOT_FAILED >&2' ERR
systemctl stop sinka-paper-trial.service 2>/dev/null || true
systemctl disable --now ssh.service ssh.socket 2>/dev/null || true
apt-get update -q
apt-get install -y -q --no-install-recommends docker.io ca-certificates
systemctl enable --now docker
install -d -m 0700 -o 1000 -g 1000 /var/lib/sinka-paper
# The provider-side 30 minute STOP limit is primary; this is an independent guest backup.
shutdown -h +25 'SINKA paper trial: bounded run complete'
docker build --network=default -f "$SINKA_RELEASE_DIR/worker/Dockerfile" -t sinka-paper-worker:trial "$SINKA_RELEASE_DIR"
docker run --rm --network none --entrypoint node sinka-paper-worker:trial worker/container-smoke.mjs
cat > /etc/systemd/system/sinka-paper-trial.service <<'UNIT'
[Unit]
Description=SINKA public-market paper strategy bounded trial
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Type=simple
Restart=on-failure
RestartSec=35
TimeoutStopSec=20
RuntimeMaxSec=1000
ExecStartPre=-/usr/bin/docker rm -f sinka-paper-trial-worker
ExecStart=/usr/bin/docker run --rm --name sinka-paper-trial-worker --read-only --tmpfs /tmp:rw,nosuid,nodev,size=32m --cap-drop=ALL --security-opt=no-new-privileges --memory=512m --cpus=0.5 --pids-limit=64 --log-driver=json-file --log-opt=max-size=5m --log-opt=max-file=2 --mount type=bind,source=/var/lib/sinka-paper,target=/data sinka-paper-worker:trial --symbol BTC-USDT-SWAP --max-seconds 900
ExecStop=/usr/bin/docker stop -t 15 sinka-paper-trial-worker
StandardOutput=journal+console
StandardError=journal+console
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now sinka-paper-trial.service
echo SINKA_PAPER_VM_BOOT_READY
