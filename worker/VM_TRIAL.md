# Bounded Google Cloud paper-worker trial — startup verified

Prepared for `sinka-quant`, `asia-east1-b`. The owner executed the approved
pinned deployment and supplied a successful startup-verification screenshot
on 2026-10-17. The original cost/resource plan below is retained for review.
This trial does not replace
or modify the existing Cloud Run status service or either OKX read-only job.
The agent cannot currently access Cloud Console and has no authenticated gcloud
installation; the final invocation must run in the owner's authenticated Cloud
Shell. No password or API key should be entered into chat or the public website.

## Concrete resources and bounds

- One `sinka-paper-trial` e2-micro VM, Debian 12, 10 GiB `pd-standard` boot disk.
- Dedicated VPC/subnet and a priority-zero IPv4 ingress DENY rule. No inbound
  listener or SSH access is enabled. SSH is disabled by the startup script too.
- No service account, OAuth scopes, secret mount, public web endpoint or IAM grant.
- An ephemeral external IPv4 address provides outbound public-market/package
  downloads. This address and VM compute usage incur charges while running.
- Provider `maxRunDuration=1800s`, termination action STOP, automatic restart off.
  Guest shutdown at 25 minutes is a separate backup. Paper process is limited
  to 900 seconds per successful run; failed runs may restart after 35 seconds
  only within the VM's provider runtime limit.
- **The boot disk is retained after STOP and after VM deletion**, to preserve
  the SQLite account. Its provisioned 10 GiB continues billing until the owner
  explicitly removes the disk. Nothing automatically deletes the ledger.

The 30-minute trial is not an always-on deployment. It must pass cloud health
and an actual stop/start persistence check before any longer runtime is proposed.
A new start incurs new usage and is not performed by these scripts.

## Cost approval proposal

For review, reserve **US$1 for the one-time bounded trial's incremental
compute/network usage**, and **US$1/month for keeping its 10 GiB disk**. These
are conservative planning allowances, **not regional SKU quotes, a billing
budget or an automatically enforced dollar cap**. Actual charges depend on the
project's region-specific price sheet, currency, taxes and usage; its billing
account has not been inspected. This does not authorize a 24-hour worker or
indefinite compute runtime. Existing project charges are outside this estimate.

Official references checked 2026-10-17:

- [Compute pricing](https://cloud.google.com/products/compute/pricing/general-purpose):
  VM rate depends on region and machine type. The dynamic Taiwan-specific quote
  was not available in this session; do not present a US-region rate as Taiwan's.
- [External IPv4 pricing](https://cloud.google.com/vpc/network-pricing):
  standard-VM in-use external IPv4 pricing lists US$0.005/hour.
- [Disk pricing](https://cloud.google.com/compute/disks-image-pricing):
  provisioned capacity is charged until relinquished; stopping compute does not
  remove a retained disk's charge.
- [Provider runtime limits](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime):
  a duration plus STOP termination action bounds the VM runtime; it is not a
  dollar-denominated project budget.

## Commands, only after cost approval

From a reviewed, pinned repository checkout in Cloud Shell:

```sh
bash firebase/deploy-paper-vm.sh --plan
bash firebase/deploy-paper-vm.sh --approved-cost
```

The default `--plan` makes no gcloud calls. `--render-startup FILE` creates a
local inspection artifact only. Only literal `--approved-cost` can enable the
Compute API and create the four named resources. It refuses existing names and
never adopts/updates another VM, disk or network. Partial failures leave created
resources for review; the script does not silently delete resources or data.

Source is bundled from an explicit checksum manifest. The startup metadata
contains only the reviewed paper worker source, validates its bundle checksum,
builds the isolated Docker image and runs the no-network container smoke test.
The real container runs non-root, without capabilities or an inbound port, with
a read-only root filesystem and the retained database bind-mounted at `/data`.

## Acceptance, separate from creation

After bootstrapping, run:

```sh
bash firebase/deploy-paper-vm.sh --verify
```

Verification is read-only: instance description plus serial-port log parsing.
It checks the 30-minute STOP configuration, disabled automatic restart, no
service account, retained disk and isolated-network identity. A malformed or
missing runtime bound fails and the creation command requests immediate STOP.

A VM creation response is **not** accepted as worker health. A RUNNING instance
and a recent active worker event with a fresh public quote are required for
`cloud_acceptance_complete=true`. Serial may be empty during initial boot;
that means not yet verified. Logs include a sanitized 30-second heartbeat;
no amounts, credentials, raw errors or trade contents are included in reports.
After automatic STOP, acceptance correctly reports not currently active even
if a recent heartbeat remains in the serial log. The data stays on disk.

Do not re-run creation on the same names. Use read-only verification and review
any partial resources first. Deleting the retained disk is intentionally outside
this script, since that destroys the test ledger and requires an explicit choice.

## Tested scope

Local Node tests cover the no-op default, strict approval flag, checksummed
startup rendering, resource-name collisions and all mocked gcloud creation
arguments. Python tests distinguish boot/configuration from current worker
health and sanitize log payloads. Cloud resource creation and active/public-quote startup were verified in the
owner screenshot. Actual provider STOP and stop/start persistence behavior
remain **unverified** pending the next Cloud Shell acceptance.

## One stop/start persistence acceptance on the existing VM

`firebase/verify-paper-vm-restart.sh` defaults to a no-op plan. After authorizing
this persistence check under the same trial budget, invoke it with
`--run-authorized-trial` from authenticated Cloud Shell. It never creates,
deletes, updates metadata, or changes scheduling on any resource.

It captures the existing VM ID, retained boot-disk ID/creation timestamp and
last persisted worker revision/trade count before stopping. If no valid
checkpoint evidence exists it refuses to restart. It stops the existing VM
if needed and starts that same VM once. A new start incurs compute/network
usage and starts a new 30-minute provider runtime limit; it is not an extension
to indefinite runtime or a new paid resource. The retained disk keeps billing.

Acceptance requires the same VM/disk, the same bounded configuration, a new
boot event, a checkpoint revision that did not reset, no reduction in closed
trade count, and a fresh post-restart public quote. Old serial events do not
prove a new boot. If an open position was restored, recovery may intentionally
paper-flatten it and stop new entries; that is reported separately from an
active waiting worker. No exchange orders are involved.

This is evidence of checkpoint/disk continuity, not proof that the strategy is
profitable. If the account has zero closed trades, it does not establish cloud
recovery of a completed trade; that remains covered only by the synthetic/local
ledger tests until paper trades occur in the cloud.

The script waits up to five minutes for the new bootstrap, then stops the VM
on failed/timed-out acceptance, retaining data. Output has
`restart_acceptance_complete: true` only when all required evidence is present.
The actual cloud stop/start test is pending operator execution; mocked gcloud
and deterministic comparison tests are not represented as cloud acceptance.


## Observed cloud startup evidence

Owner screenshot received 2026-10-17 17:01 Asia/Taipei showed all configuration
checks true, `vm_status=RUNNING`, an active `waiting_signal` worker with a fresh
public quote, no open paper position and zero closed trades. The event time
was `2026-10-17T09:00:41.585000+00:00`; `cloud_acceptance_complete=true` and
`trading_enabled=false`. This proves bounded cloud startup, not 24-hour uptime,
completed trades, profitability or successful restart recovery. No private OKX
account values, identifiers or screenshot contents are copied to this report.
