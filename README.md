# tdgov: compliance-by-design governance for Infrastructure as Code

Reference implementation and replication package for the article

> Mubarkoot, M., Mellaoui, W., Altmann, J., & Egger, B. *Toward Sustainable Software Engineering: A Framework for Resilient Infrastructure Delivery and Technical Debt Prevention.* (under review)

The framework treats an approved Infrastructure as Code (IaC) blueprint as a governed, tamper-evident baseline.
Blueprints are validated against policy-as-code rules, stored in an immutable ledger, changed only through
multi-stakeholder approval, deployed through a unidirectional pipeline, and audited periodically so that
configuration drift is detected and reported before it accumulates as technical debt.

```
  stakeholders ──(1) design──► blueprint/ ──(2) validate──► policies/ (OPA/conftest)
        ▲                                                        │
        │ (5) report                                   (3) submit │ canonical bundle + digest
        │                                                        ▼
  tdgov audit ◄──(4) compare── Proxmox ◄──(7) deploy── ledger (immudb) ◄──(6) approve── approvers
```

## How the code maps to the paper

| Framework step (Fig. 1) | Design requirement (Table 3) | Implementation |
|---|---|---|
| 1. Design blueprint | DR1 single executable baseline | `blueprint/` (Terraform/HCL, pinned providers) |
| 2. Validate blueprint | DR4 automated validation | `tdgov validate`: `fmt`, `validate`, plan + `policies/main.rego` |
| 3. Store in immutable repository | DR3 tamper-evident versioned record | `tdgov/canonical.py`, `tdgov/ledger.py` (immudb with proof verification) |
| 4. Audit deployed state | DR5 detect deviations | `tdgov audit`: refresh-only plan, plan, unmanaged-VM scan |
| 5. Report mismatches | DR5 | `tdgov/notify.py` (stdout, webhook, e-mail), `tdgov report` |
| 6. Change request and approval | DR2 multi-stakeholder approval | `tdgov submit` / `approve` / `reject` (quorum, required roles, no self-approval) |
| 7. Deployment pipeline | DR4, DR6 restrict out-of-band changes | `tdgov deploy` (ledger copy only) + `deploy/proxmox-rbac.sh` |
| Break-glass procedure (Section 3) | DR6 | `tdgov breakglass open/close` |
| Secrets management rule (Section 3) | – | `tdgov/secrets.py` (env or Vault), policy TDGOV-007/008 |

Policy rules (`policies/main.rego`):

| ID | Rule |
|---|---|
| TDGOV-001 | VM disks and cloud-init drives on approved encrypted datastores |
| TDGOV-002 | Database attached to the private bridge only |
| TDGOV-003 | No default PostgreSQL port |
| TDGOV-004 | Only supported PostgreSQL major versions (prevents version debt) |
| TDGOV-005 | Ownership and role tags on every VM |
| TDGOV-006 | Firewall enabled on every NIC |
| TDGOV-007 | Secret variables marked sensitive and never defaulted |
| TDGOV-008 | No hard-coded secret literals |
| TDGOV-009 | Provider versions pinned |

## Repository layout

```
blueprint/        Reference topology from the paper: DNS -> load balancer -> 2 app servers -> DB (private subnet)
policies/         Rego rules, site data and unit tests
tdgov/            Governance toolkit (CLI: tdgov)
experiments/      Drift-injection experiment and scalability benchmark, plus analysis
deploy/           cron, systemd timer, Proxmox RBAC setup
config/           Example configuration
tests/            Unit tests (no cloud access required)
docs/             Architecture, trust model and detection scope
```

## Quick start

Requirements: Python 3.10+, Terraform 1.6+ (or OpenTofu), conftest, a Proxmox VE 8/9 cluster, and immudb
(`docker compose up -d immudb`).

```bash
pip install -e ".[immudb]"
cp config/tdgov.example.yaml tdgov.yaml               # adjust approvers, ledger, notifications
cp blueprint/terraform.tfvars.example blueprint/terraform.tfvars

export PROXMOX_VE_ENDPOINT=https://pve01:8006/ PROXMOX_VE_API_TOKEN='tdgov-pipeline@pve!deploy=...'
export IMMUDB_USER=tdgov IMMUDB_PASSWORD=...
export TDGOV_DB_ADMIN_PASSWORD=...                     # or configure the Vault provider
export DNS_UPDATE_SERVER=... DNS_UPDATE_KEYNAME=... DNS_UPDATE_KEYALGORITHM=hmac-sha256 DNS_UPDATE_KEYSECRET=...

tdgov validate                                        # Step 2
tdgov submit -m "initial baseline" --author dave      # Steps 3, 6
tdgov approve cr-... --approver alice                 # Step 6 (compliance)
tdgov approve cr-... --approver bob                   # Step 6 (infrastructure) -> baseline v1
tdgov deploy                                          # Step 7
tdgov audit                                           # Steps 4-5 (exit 2 if deviations found)
```

Schedule audits with `deploy/cron/tdgov.cron` or the systemd timer in `deploy/systemd/`. Detection latency is
bounded by the audit interval.

For evaluation without immudb, set `ledger.backend: file`. This uses a SHA-256 hash-chained ledger whose
`tdgov ledger verify` detects modified, deleted or reordered entries.

## Experiments

See [`experiments/README.md`](experiments/README.md).

* **Drift injection**: nine scenarios (memory, CPU, tags, database moved to a public bridge, firewall disabled,
  extra disk, unmanaged VM, guest-level change, control) injected out of band through the Proxmox API at random
  times relative to the audit schedule. Reports detection rate, detection latency, false-positive rate, audit
  overhead and recovery.
* **Scalability**: audit overhead versus blueprint size (10 to 400 resources) without a cloud provider.

## Detection scope and limitations

* Drift detection follows Terraform's state model. Changes inside guest operating systems are not visible
  (scenario S8 documents this blind spot). Resources created outside Terraform are covered by the
  unmanaged-VM scan only within the blueprint's tag or VM ID range.
* Prevention depends on access control. With `deploy/proxmox-rbac.sh` applied, only the pipeline identity
  can modify governed resources. Without it, the framework detects out-of-band changes but cannot prevent them.
* Approver identities come from configuration. Production use should bind them to an identity provider or
  signed commits (see `docs/ARCHITECTURE.md`).
* Secrets are injected at deployment time, but the rendered cloud-init snippet for the database is stored on the
  Proxmox snippets datastore and the value is recorded in Terraform state. Restrict access to both and encrypt
  the state backend.

## Tests

```bash
pip install -e ".[dev]"
pytest -q            # Python unit tests
opa test policies    # policy unit tests
```

## Citation

See `CITATION.cff`. Licensed under the MIT License.
