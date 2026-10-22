"""Compliance Auditing and Deployment Pipeline (Steps 2 and 7, DR4).

``validate``  checks a working-copy blueprint: formatting, ``terraform
              validate``, and (with ``plan=True``) a plan evaluated against
              the policy-as-code rules.
``submit``    validates, bundles canonically, stores the bundle in the ledger
              and opens a change request.
``deploy``    is unidirectional: it deploys only the approved baseline read
              from the ledger, re-checks policies on the resulting plan and
              applies exactly that plan.

Plan files and their JSON rendering can contain secret values in clear text,
so they are written to a private temporary directory and deleted after use.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import canonical
from . import secrets as secrets_mod
from .audit import _copy_tfvars, prepare_workdir
from .governance import Governance, GovernanceError
from .policy import evaluate_plan
from .terraform import Terraform, TerraformError


@dataclass
class ValidationReport:
    passed: bool = False
    fmt_issues: list[str] = field(default_factory=list)
    validate_errors: list[str] = field(default_factory=list)
    policy_failures: list[str] = field(default_factory=list)
    policy_warnings: list[str] = field(default_factory=list)
    planned_changes: list[dict] = field(default_factory=list)
    planned: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _backend(cfg) -> dict:
    return {k: str(cfg.resolve(v)) if k == "path" else v for k, v in cfg.backend_config.items()}


def _plan_and_check(cfg, tf: Terraform, report: ValidationReport) -> str:
    """Create a plan in a private temp dir, evaluate policies; return plan path."""
    tmp = Path(tempfile.mkdtemp(prefix="tdgov-plan-"))
    os.chmod(tmp, 0o700)
    planfile = tmp / "plan.tfplan"
    result = tf.plan(out=str(planfile))
    report.planned_changes = result.changes
    plan_json = tmp / "plan.json"
    import json

    plan_json.write_text(json.dumps(tf.show_json(str(planfile))))
    pol = evaluate_plan(plan_json, cfg.resolve(cfg.policy_dir), cfg.resolve(cfg.policy_data_dir), cfg.conftest_bin)
    plan_json.unlink(missing_ok=True)
    report.policy_failures, report.policy_warnings = pol.failures, pol.warnings
    report.planned = True
    return str(planfile)


def validate(cfg, blueprint_dir: str | None = None, plan: bool = True) -> ValidationReport:
    report = ValidationReport()
    src = Path(blueprint_dir) if blueprint_dir else cfg.resolve(cfg.blueprint_dir)
    secrets = secrets_mod.resolve(cfg) if plan else {}
    with tempfile.TemporaryDirectory(prefix="tdgov-validate-") as tmp:
        # Validate the canonical bundle, i.e. exactly what would be submitted.
        b = canonical.bundle(src)
        canonical.materialize(b.data, tmp, expected_digest=b.digest)
        _copy_tfvars(cfg, Path(tmp))
        tf = Terraform(tmp, cfg.terraform_bin, env=secrets_mod.as_tf_env(secrets))
        report.fmt_issues = tf.fmt_check()
        try:
            tf.init(backend=False)
            v = tf.validate()
            report.validate_errors = [f"{d.get('summary')}: {d.get('detail', '')}".strip()
                                      for d in v.get("diagnostics", []) if d.get("severity") == "error"]
            if not report.validate_errors and plan:
                tf.init(backend_config=_backend(cfg))
                planfile = _plan_and_check(cfg, tf, report)
                shutil.rmtree(Path(planfile).parent, ignore_errors=True)
        except TerraformError as exc:
            report.validate_errors.append(secrets_mod.redact(f"{exc}: {exc.stderr.strip()}", secrets))
    report.passed = not (report.fmt_issues or report.validate_errors or report.policy_failures)
    return report


def submit(cfg, gov: Governance, author: str, message: str, plan: bool = True) -> dict:
    report = validate(cfg, plan=plan)
    if not report.passed:
        raise GovernanceError("validation failed; fix the blueprint before submitting:\n" + _format(report))
    b = canonical.bundle(cfg.resolve(cfg.blueprint_dir))
    cr = gov.submit(b, author=author, message=message, validation=report.to_dict())
    base = gov.approved_baseline()
    if base:
        cr["diff"] = canonical.diff_manifests(gov.manifest(base["digest"]), b.manifest)
    return cr


def deploy(cfg, gov: Governance, actor: str, auto_approve: bool = False, notifier=None) -> dict:
    baseline = gov.approved_baseline()
    if baseline is None:
        raise GovernanceError("nothing to deploy: no approved baseline")
    wd = prepare_workdir(cfg, gov, baseline["digest"], "deploy")
    _copy_tfvars(cfg, wd)
    secrets = secrets_mod.resolve(cfg)
    tf = Terraform(wd, cfg.terraform_bin, env=secrets_mod.as_tf_env(secrets))
    report = ValidationReport()
    tf.init(backend_config=_backend(cfg))
    planfile = _plan_and_check(cfg, tf, report)
    try:
        if report.policy_failures:
            gov.record_event("deploy-blocked", {"actor": actor, "digest": baseline["digest"],
                                                "policy_failures": report.policy_failures})
            raise GovernanceError("policy check failed on the deployment plan:\n  " + "\n  ".join(report.policy_failures))
        if not report.planned_changes:
            gov.record_event("deploy-noop", {"actor": actor, "digest": baseline["digest"], "version": baseline["version"]})
            return {"status": "no-changes", "baseline": baseline}
        if not auto_approve:
            print(_format_changes(report.planned_changes))
            if input("Apply these changes? Type 'yes': ").strip() != "yes":
                return {"status": "cancelled", "baseline": baseline}
        tf.apply(planfile)
    except TerraformError as exc:
        gov.record_event("deploy-failed", {"actor": actor, "digest": baseline["digest"],
                                           "error": secrets_mod.redact(exc.stderr, secrets)})
        raise
    finally:
        shutil.rmtree(Path(planfile).parent, ignore_errors=True)
    event = {"actor": actor, "digest": baseline["digest"], "version": baseline["version"],
             "changes": report.planned_changes}
    gov.record_event("deployed", event)
    if notifier:
        notifier.send(f"{cfg.blueprint_name}: baseline v{baseline['version']} deployed",
                      _format_changes(report.planned_changes), "info", event)
    return {"status": "applied", "baseline": baseline, "changes": report.planned_changes}


def _format_changes(changes: list[dict]) -> str:
    return "\n".join(f"  {c.get('action'):>8}  {c.get('address')}" for c in changes) or "  (none)"


def _format(r: ValidationReport) -> str:
    out = []
    out += [f"  fmt: {f}" for f in r.fmt_issues]
    out += [f"  validate: {e}" for e in r.validate_errors]
    out += [f"  policy: {p}" for p in r.policy_failures]
    return "\n".join(out)
