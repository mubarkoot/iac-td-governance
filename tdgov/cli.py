"""Command-line interface: ``tdgov <command>``.

    tdgov validate [--no-plan]            Step 2   validate working-copy blueprint
    tdgov submit -m MSG [--author A]      Step 3/6 open a change request
    tdgov approve CR_ID [--approver A]    Step 6   record an approval
    tdgov reject CR_ID -r REASON          Step 6   reject a change request
    tdgov status                                    baseline, open CRs, break-glass
    tdgov deploy [--yes]                  Step 7   deploy the approved baseline
    tdgov audit [--json] [--no-notify]    Step 4/5 compare deployed state with baseline
    tdgov report [--days N]               Step 5   stakeholder report
    tdgov breakglass open|close ...                 emergency change window
    tdgov ledger verify|stats|history               ledger integrity and statistics

Exit codes: 0 ok / compliant, 1 error, 2 deviations found, 3 validation failed.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from . import __version__
from .audit import format_findings, result_json, run_audit
from .config import ConfigError, load_config
from .governance import Governance, GovernanceError
from .ledger import LedgerError, open_ledger
from .notify import Notifier
from .pipeline import deploy, submit, validate
from .policy import PolicyError
from .report import build_report
from .secrets import SecretError
from .terraform import TerraformError


def _actor(args) -> str:
    return getattr(args, "actor", None) or os.environ.get("TDGOV_ACTOR") or getpass.getuser()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tdgov", description="Compliance-by-design governance for IaC blueprints")
    p.add_argument("--config", "-c", help="path to tdgov.yaml (default: $TDGOV_CONFIG or ./tdgov.yaml)")
    p.add_argument("--version", action="version", version=f"tdgov {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("validate", help="validate the working-copy blueprint")
    s.add_argument("--no-plan", action="store_true", help="skip plan + policy evaluation (no credentials needed)")
    s.add_argument("--dir", help="blueprint directory (default from config)")

    s = sub.add_parser("submit", help="validate and open a change request")
    s.add_argument("-m", "--message", required=True)
    s.add_argument("--author", dest="actor")
    s.add_argument("--no-plan", action="store_true")

    s = sub.add_parser("approve", help="approve a change request")
    s.add_argument("cr_id")
    s.add_argument("--approver", dest="actor")
    s.add_argument("--comment", default="")

    s = sub.add_parser("reject", help="reject a change request")
    s.add_argument("cr_id")
    s.add_argument("--approver", dest="actor")
    s.add_argument("-r", "--reason", required=True)

    sub.add_parser("status", help="show approved baseline, open change requests, break-glass state")

    s = sub.add_parser("deploy", help="deploy the approved baseline")
    s.add_argument("--yes", action="store_true", help="apply without interactive confirmation")
    s.add_argument("--actor", dest="actor")

    s = sub.add_parser("audit", help="audit deployed infrastructure against the approved baseline")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-notify", action="store_true")

    s = sub.add_parser("report", help="stakeholder compliance report")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--notify", action="store_true", help="also send the report through notification channels")

    s = sub.add_parser("breakglass", help="emergency change window")
    bs = s.add_subparsers(dest="bg_cmd", required=True)
    o = bs.add_parser("open")
    o.add_argument("-r", "--reason", required=True)
    o.add_argument("--ttl", type=int, default=60, help="minutes (default 60)")
    o.add_argument("--actor", dest="actor")
    c = bs.add_parser("close")
    c.add_argument("-r", "--resolution", required=True, help="e.g. 'reverted' or 'incorporated in cr-...'")
    c.add_argument("--actor", dest="actor")

    s = sub.add_parser("ledger", help="ledger integrity and statistics")
    ls = s.add_subparsers(dest="ledger_cmd", required=True)
    ls.add_parser("verify")
    ls.add_parser("stats")
    ls.add_parser("history", help="history of approved baselines")

    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.cmd == "validate":
            rep = validate(cfg, blueprint_dir=args.dir, plan=not args.no_plan)
            print(json.dumps(rep.to_dict(), indent=2))
            return 0 if rep.passed else 3

        ledger = open_ledger(cfg)
        gov = Governance(ledger, cfg)
        notifier = Notifier(cfg)
        try:
            return _dispatch(args, cfg, gov, notifier, ledger)
        finally:
            ledger.close()
    except (ConfigError, GovernanceError, LedgerError, SecretError, PolicyError) as exc:
        print(f"tdgov: {exc}", file=sys.stderr)
        return 1
    except TerraformError as exc:
        print(f"tdgov: {exc}\n{exc.stderr}", file=sys.stderr)
        return 1


def _dispatch(args, cfg, gov: Governance, notifier: Notifier, ledger) -> int:
    if args.cmd == "submit":
        try:
            cr = submit(cfg, gov, author=_actor(args), message=args.message, plan=not args.no_plan)
        except GovernanceError as exc:
            print(f"tdgov: {exc}", file=sys.stderr)
            return 3
        print(json.dumps({k: v for k, v in cr.items() if k != "validation"}, indent=2))
        notifier.send(f"{cfg.blueprint_name}: change request {cr['cr_id']} awaiting approval",
                      f"{cr['author']}: {cr['message']}", "info", {"cr_id": cr["cr_id"], "diff": cr.get("diff")})
        return 0

    if args.cmd == "approve":
        out = gov.approve(args.cr_id, _actor(args), args.comment)
        print(json.dumps(out, indent=2))
        if out["policy"]["satisfied"]:
            base = gov.approved_baseline()
            notifier.send(f"{cfg.blueprint_name}: baseline v{base['version']} approved",
                          f"{args.cr_id} approved by {', '.join(base['approvers'])}", "info", base)
        return 0

    if args.cmd == "reject":
        print(json.dumps(gov.reject(args.cr_id, _actor(args), args.reason), indent=2))
        return 0

    if args.cmd == "status":
        print(json.dumps({"approved_baseline": gov.approved_baseline(),
                          "open_change_requests": [{k: c[k] for k in ("cr_id", "author", "message", "created_at")}
                                                   for c in gov.list_crs("open")],
                          "breakglass": gov.breakglass_active()}, indent=2))
        return 0

    if args.cmd == "deploy":
        print(json.dumps(deploy(cfg, gov, _actor(args), auto_approve=args.yes, notifier=notifier), indent=2, default=str))
        return 0

    if args.cmd == "audit":
        res = run_audit(cfg, gov, notifier=None if args.no_notify else notifier)
        print(result_json(res) if args.json else (format_findings(res) if res.findings else
                                                  f"{res.status}: {res.error or 'no deviations'}"))
        return {"compliant": 0, "drift": 2}.get(res.status, 1)

    if args.cmd == "report":
        text = build_report(gov, args.days)
        print(text)
        if args.notify:
            notifier.send(f"{cfg.blueprint_name}: compliance report ({args.days} days)", text, "info")
        return 0

    if args.cmd == "breakglass":
        if args.bg_cmd == "open":
            rec = gov.breakglass_open(_actor(args), args.reason, args.ttl)
            notifier.send(f"{cfg.blueprint_name}: break-glass opened by {rec['actor']}",
                          f"{rec['reason']} (until {rec['expires_at']})", "warning", rec)
        else:
            rec = gov.breakglass_close(_actor(args), args.resolution)
            notifier.send(f"{cfg.blueprint_name}: break-glass closed", rec["resolution"], "info", rec)
        print(json.dumps(rec, indent=2))
        return 0

    if args.cmd == "ledger":
        if args.ledger_cmd == "verify":
            ledger.verify()
            print("ledger verified")
        elif args.ledger_cmd == "stats":
            print(json.dumps(ledger.stats(), indent=2))
        else:
            print(json.dumps(gov.baseline_history(), indent=2))
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
