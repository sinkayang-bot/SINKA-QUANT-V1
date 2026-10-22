#!/usr/bin/env bash
# Called over authenticated IAP SSH after explicit paid-resource approval.
set -Eeuo pipefail
[[ $EUID == 0 && $# == 1 ]] || exit 2
package="$1"
[[ -f "$package" && ! -L "$package" ]] || exit 2
[[ ! -e /etc/systemd/system/sinka-demo-runner.service ]] || { echo 'Target service already exists; refusing overwrite.' >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends docker.io
systemctl enable --now docker
device=/dev/disk/by-id/google-sinka-demo-data
[[ -b "$device" ]] || { echo 'Dedicated persistent data disk missing.' >&2; exit 2; }
# This installer never formats a disk which already has any filesystem.
if blkid "$device" >/dev/null 2>&1; then
  echo 'Data disk already formatted; manual review required, no reset.' >&2; exit 2
fi
mkfs.ext4 -F "$device" >/dev/null
install -d -m 0700 /home/sinka
mount "$device" /home/sinka
uuid="$(blkid -s UUID -o value "$device")"
printf 'UUID=%s /home/sinka ext4 defaults,nofail 0 2\n' "$uuid" >> /etc/fstab
install -d -m 0700 /opt/sinka-demo-release
tar -xzf "$package" --no-same-owner -C /opt/sinka-demo-release
cd /opt/sinka-demo-release
sha256sum --check --status payload.sha256
docker build -f migration/Dockerfile -t sinka-demo-runner:migrated .
# No network/credentials available while validating and importing original state.
docker run --rm --network none --user root --entrypoint python \
  --mount type=bind,source=/home/sinka,target=/home/sinka \
  --mount type=bind,source=/opt/sinka-demo-release/handoff,target=/handoff,readonly \
  sinka-demo-runner:migrated backend/okx_demo_migration.py --restore /handoff
chown -R 10001:10001 /home/sinka
chmod 0700 /home/sinka
cat > /etc/systemd/system/sinka-demo-runner.service <<'UNIT'
[Unit]
Description=SINKA OKX demo owner-command controller
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=/home/sinka
[Service]
Type=simple
Restart=always
RestartSec=30
TimeoutStopSec=100
ExecStartPre=/usr/bin/mountpoint -q /home/sinka
ExecStartPre=-/usr/bin/docker rm sinka-demo-runner
ExecStart=/usr/bin/docker run --rm --name sinka-demo-runner --init --read-only --tmpfs /tmp:rw,nosuid,nodev,size=128m --cap-drop=ALL --security-opt=no-new-privileges --memory=1536m --cpus=1 --pids-limit=256 --log-driver=json-file --log-opt=max-size=5m --log-opt=max-file=2 --mount type=bind,source=/home/sinka,target=/home/sinka sinka-demo-runner:migrated
ExecStop=/usr/bin/docker stop -t 80 sinka-demo-runner
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
# Read-only account check first. Failure leaves the controller unstarted.
docker run --rm --init --read-only --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  --mount type=bind,source=/home/sinka,target=/home/sinka \
  --entrypoint python sinka-demo-runner:migrated backend/okx_demo_cloudshell.py --check
systemctl enable --now sinka-demo-runner.service
for attempt in {1..30}; do
  if journalctl -u sinka-demo-runner.service --no-pager | grep '"phase": "runner_connected"' >/dev/null; then
    touch /var/lib/sinka-demo-migration.ready
    echo '{"phase":"vm_controller_connected","live_trading_enabled":false,"new_trade_started":false}'
    exit 0
  fi
  sleep 2
done
echo 'Controller installed but online heartbeat not yet verified; check service logs.' >&2
exit 2
