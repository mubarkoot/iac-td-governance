# Architecture, trust model and detection scope

## Components

| Component (paper) | Module | Responsibility |
|---|---|---|
| Infrastructure blueprint | `blueprint/` | Declarative topology, policies embedded as typed variables and tags |
| Blueprint Repository | `tdgov/ledger.py`, `tdgov/governance.py` | Tamper-evident storage of canonical bundles, change requests, approvals, approved-baseline pointer, events |
| Compliance Auditing and Deployment Pipeline | `tdgov/pipeline.py`, `tdgov/policy.py` | Validation, policy evaluation, unidirectional deployment |
| Analytics and Reporting service | `tdgov/audit.py`, `tdgov/report.py`, `tdgov/notify.py` | Scheduled comparison of deployed state with the approved baseline; reports |
| Provisioning infrastructure | Proxmox VE + `deploy/proxmox-rbac.sh` | Executes deployments; RBAC limits writes to the pipeline identity |

## Ledger key space

All records of a blueprint live under `bp:<blueprint_name>:`.

| Key | Value |
|---|---|
| `blob:<sha256>` | Canonical `.tar.gz` bundle (content-addressed) |
| `manifest:<sha256>` | Per-file SHA-256 manifest of the bundle |
| `cr:<cr_id>` | Change request; each status change appends a new version |
| `approval:<cr_id>:<approver>` | Approval record bound to the CR's digest |
| `approved` | Current approved baseline; its history is the list of every approved baseline |
| `breakglass` | Current break-glass window |
| `event:<ns-timestamp>:<kind>` | Audit results, deployments, approvals, break-glass events |

Keys are never updated in place. immudb keeps every version and proves inclusion and consistency for each read
and write against a trusted state persisted on the client (`ledger.state_dir`). If the server's history is
rewritten, verification fails on the next operation.

## Why a ledger in addition to Git

A Git repository with protected branches and signed commits offers similar versioning. However, its history can
be rewritten by sufficiently privileged administrators of the hosting platform. The ledger is the
*authoritative record of approved baselines*: any stakeholder can verify independently that the blueprint being
deployed or audited is byte-for-byte the one that was approved. Git remains the authoring environment, and
`tdgov submit` can run from a CI job on merge.

## Trust model

* **Approvers**: identities and roles are defined in `tdgov.yaml`. The CLI takes the acting identity from
  `--approver` / `$TDGOV_ACTOR`. This suits evaluation and single-team use. In production, run `approve` behind
  the organization's identity provider (for example, a CI job triggered by an authenticated review) and protect
  the ledger credentials so that only that job can write approvals.
* **Pipeline**: only the pipeline's Proxmox token has write access to the governed pool. Engineers have
  read-only access and use change requests, or the break-glass procedure for incidents.
* **Auditor**: uses a read-only Proxmox token. Audits never modify infrastructure.
* **Secrets**: resolved at run time from the environment or Vault, passed to Terraform as `TF_VAR_*` variables of
  the child process only, redacted from recorded errors, and never stored in the ledger. Plan files, which can
  contain secrets, are written to a private temporary directory and deleted after use.

## Detection scope

| Change | Mechanism | Detected? |
|---|---|---|
| Attribute of a managed resource changed out of band (memory, CPU, tags, NIC, disks) | `plan -refresh-only` -> `resource_drift` | Yes, at the next audit |
| Managed resource deleted out of band | refresh-only plan / plan | Yes |
| State and approved configuration diverge (for example, an unapproved apply from a modified working copy) | normal `plan` -> `planned_change` | Yes |
| VM created outside Terraform inside the blueprint's tag or VM ID range | Proxmox API scan | Yes |
| VM created outside the blueprint's scope | – | No (out of scope by design) |
| Change inside the guest OS (packages, configuration files) | – | No; needs configuration management or host agents |

Detection latency is bounded by the audit interval plus the audit duration. The drift-injection experiment
measures both.

## Break-glass

`tdgov breakglass open -r <reason> --ttl <minutes>` records an attributed, time-limited window. Audits during the
window still report all deviations, classified as *expected* (warning) instead of *unexpected* (critical). After
the incident, the change is either reverted or incorporated into the blueprint through a change request, and the
window is closed with a resolution note.
