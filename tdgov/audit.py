"""Periodic compliance audit of the deployed infrastructure (Steps 4-5, DR5).

An audit:

1. reads the approved baseline from the ledger and verifies its digest;
2. materializes exactly that blueprint into a work directory (never the
   developer's working copy, so the comparison is against what was approved);
3. runs ``terraform plan -refresh-only`` to detect *drift* of managed
   resources (out-of-band changes), and a normal ``plan`` to detect
   *divergence* between the approved configuration and the recorded state;
4. optionally scans the Proxmox API for *unmanaged* VMs inside the
   blueprint's scope, which Terraform cannot see;
5. classifies findings (expected during an active break-glass window,
   otherwise unexpected), records the result in the ledger and notifies
   stakeholders.

Audits only report; they never modify infrastructure. Remediation goes
through the change-request process (Step 6) or a redeploy of the approved
baseline (Step 7).
"""

from __future__ import annotations

import json
import resource
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import secrets as secrets_mod
from .canonical import materialize
from .governance import Governance, now_iso
from .proxmox import ProxmoxClient, find_unmanaged
from .terraform import Terraform, TerraformError, managed_vm_ids


@dataclass
class AuditResult:
    started_at: str
    finished_at: str = ""
    duration_s: float = 0.0
    status: str = "error"  # compliant | drift | error
    baseline_version: int | None = None
    baseline_digest: str | None = None
    drift: list[dict] = field(default_factory=list)
    divergence: list[dict] = field(default_factory=list)
    unmanaged: list[dict] = field(default_factory=list)
    classification: str = "none"  # none | expected | unexpected
    breakglass: dict | None = None
    error: str | None = None
    child_cpu_s: float = 0.0
    child_max_rss_kb: int = 0

    @property
    def findings(self) -> int:
        return len(self.drift) + len(self.divergence) + len(self.unmanaged)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["findings"] = self.findings
        return d


def prepare_workdir(cfg, gov: Governance, digest: str, purpose: str) -> Path:
    """Materialize the approved bundle; keep the provider cache between runs."""
    wd = cfg.resolve(cfg.work_dir) / f"{purpose}-{digest[:12]}"
    marker = wd / ".tdgov-digest"
    if wd.exists() and marker.exists() and marker.read_text().strip() == digest:
        return wd
    if wd.exists():
        shutil.rmtree(wd)
    materialize(gov.load_bundle(digest), wd, expected_digest=digest)
    marker.write_text(digest)
    return wd


def _copy_tfvars(cfg, wd: Path) -> str | None:
    if not cfg.tfvars_file:
        return None
    src = cfg.resolve(cfg.tfvars_file)
    if not src.exists():
        return None
    dst = wd / "site.auto.tfvars"
    shutil.copyfile(src, dst)
    return dst.name


def run_audit(cfg, gov: Governance, notifier=None, proxmox: ProxmoxClient | None = None,
              terraform_cls=Terraform) -> AuditResult:
    res = AuditResult(started_at=now_iso())
    t0 = time.monotonic()
    ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    secrets: dict[str, str] = {}
    try:
        baseline = gov.approved_baseline()
        if baseline is None:
            raise RuntimeError("no approved baseline in the ledger; submit and approve a blueprint first")
        res.baseline_version, res.baseline_digest = baseline["version"], baseline["digest"]

        wd = prepare_workdir(cfg, gov, baseline["digest"], "audit")
        _copy_tfvars(cfg, wd)
        secrets = secrets_mod.resolve(cfg)
        tf = terraform_cls(wd, cfg.terraform_bin, env=secrets_mod.as_tf_env(secrets))
        tf.init(backend_config={k: str(cfg.resolve(v)) if k == "path" else v for k, v in cfg.backend_config.items()})

        refresh = tf.plan(refresh_only=True)
        res.drift = refresh.drift
        normal = tf.plan()
        res.divergence = normal.changes

        if cfg.scan_unmanaged:
            client = proxmox or ProxmoxClient.from_config(cfg)
            state = tf.show_json()
            rng = tuple(cfg.unmanaged_vmid_range) if cfg.unmanaged_vmid_range else None
            res.unmanaged = find_unmanaged(client.list_vms(), managed_vm_ids(state),
                                           f"blueprint-{cfg.blueprint_name}", rng, cfg.proxmox.ignore_tag)

        res.status = "drift" if res.findings else "compliant"
        bg = gov.breakglass_active()
        res.breakglass = bg
        if res.findings:
            res.classification = "expected" if bg else "unexpected"
    except TerraformError as exc:
        res.error = secrets_mod.redact(f"{exc}\n{exc.stderr}", secrets)
    except Exception as exc:  # record every failure; an audit must not fail silently
        res.error = secrets_mod.redact(str(exc), secrets)
    finally:
        ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        res.child_cpu_s = round((ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime), 3)
        res.child_max_rss_kb = int(ru1.ru_maxrss)
        res.duration_s = round(time.monotonic() - t0, 3)
        res.finished_at = now_iso()

    gov.record_event("audit", res.to_dict())
    if notifier is not None:
        _notify(notifier, cfg, res)
    return res


def format_findings(res: AuditResult) -> str:
    lines = [f"Baseline v{res.baseline_version} ({(res.baseline_digest or '')[:12]})"]
    for d in res.drift:
        lines.append(f"  DRIFT       {d['address']} ({d.get('action')})")
    for d in res.divergence:
        lines.append(f"  DIVERGENCE  {d['address']} ({d.get('action')})")
    for v in res.unmanaged:
        lines.append(f"  UNMANAGED   VM {v['vmid']} '{v.get('name')}' on {v.get('node')}")
    if res.breakglass:
        lines.append(f"  Break-glass window open by {res.breakglass['actor']} until {res.breakglass['expires_at']}: "
                     f"{res.breakglass['reason']}")
    return "\n".join(lines)


def _notify(notifier, cfg, res: AuditResult) -> None:
    if res.status == "error":
        notifier.send(f"{cfg.blueprint_name}: audit failed", res.error or "unknown error", "error", res.to_dict())
    elif res.findings:
        sev = "warning" if res.classification == "expected" else "critical"
        notifier.send(f"{cfg.blueprint_name}: {res.findings} deviation(s) from the approved baseline",
                      format_findings(res), sev, res.to_dict())


def result_json(res: AuditResult) -> str:
    return json.dumps(res.to_dict(), indent=2, default=str)
