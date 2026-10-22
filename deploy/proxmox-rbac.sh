#!/usr/bin/env bash
# Role-based access control for the provisioning API (DR6, Step 7).
#
# Run on a Proxmox node as root. Creates:
#   * tdgov-pipeline@pve  - the ONLY identity with write access to the
#                           blueprint's resource pool (used by `tdgov deploy`)
#   * tdgov-auditor@pve   - read-only identity (used by `tdgov audit`)
#   * group devops        - read-only access for engineers; changes go through
#                           change requests or the break-glass procedure
#
# Without these restrictions the framework still DETECTS out-of-band
# changes, but cannot PREVENT them (see Section 3 of the paper).
set -euo pipefail

POOL="${POOL:-tdgov-reference-web-stack}"
STORAGE="${STORAGE:-zfs-encrypted}"

pvesh create /pools --poolid "$POOL" --comment "Managed by tdgov; do not modify manually" 2>/dev/null || true
pvesh set "/pools/$POOL" --storage "$STORAGE" 2>/dev/null || true

# Minimal privilege set for the bpg/proxmox provider to manage VMs.
pveum role add TDGovPipeline --privs "VM.Allocate VM.Audit VM.Clone VM.Config.CDROM VM.Config.CPU VM.Config.Cloudinit VM.Config.Disk VM.Config.HWType VM.Config.Memory VM.Config.Network VM.Config.Options VM.Migrate VM.PowerMgmt VM.GuestAgent.Audit Datastore.Allocate Datastore.AllocateSpace Datastore.AllocateTemplate Datastore.Audit Pool.Audit SDN.Use Sys.Audit Sys.Modify" 2>/dev/null || \
  pveum role modify TDGovPipeline --privs "VM.Allocate VM.Audit VM.Clone VM.Config.CDROM VM.Config.CPU VM.Config.Cloudinit VM.Config.Disk VM.Config.HWType VM.Config.Memory VM.Config.Network VM.Config.Options VM.Migrate VM.PowerMgmt VM.GuestAgent.Audit Datastore.Allocate Datastore.AllocateSpace Datastore.AllocateTemplate Datastore.Audit Pool.Audit SDN.Use Sys.Audit Sys.Modify"
pveum role add TDGovAuditor --privs "VM.Audit Datastore.Audit Pool.Audit Sys.Audit" 2>/dev/null || true

pveum user add tdgov-pipeline@pve --comment "tdgov deployment pipeline" 2>/dev/null || true
pveum user add tdgov-auditor@pve --comment "tdgov auditor (read-only)" 2>/dev/null || true
pveum group add devops --comment "Engineers: read-only on governed resources" 2>/dev/null || true

pveum acl modify "/pool/$POOL" --users tdgov-pipeline@pve --roles TDGovPipeline
pveum acl modify "/storage/$STORAGE" --users tdgov-pipeline@pve --roles TDGovPipeline
pveum acl modify "/storage/local" --users tdgov-pipeline@pve --roles TDGovPipeline
pveum acl modify "/sdn/zones" --users tdgov-pipeline@pve --roles TDGovPipeline
pveum acl modify "/" --users tdgov-auditor@pve --roles TDGovAuditor
pveum acl modify "/pool/$POOL" --groups devops --roles PVEAuditor

# API tokens with privilege separation disabled so they inherit the user's ACLs.
echo "Create tokens and store them in your secrets manager:"
echo "  pveum user token add tdgov-pipeline@pve deploy --privsep 0"
echo "  pveum user token add tdgov-auditor@pve audit --privsep 0"
echo "Add the blueprint's VMs to pool '$POOL' (pool_id argument or 'pvesh set /pools/$POOL --vms <ids>')."
