"""Stakeholder reporting (Step 5).

The paper lists three triggers for reports: (i) a new baseline release,
(ii) a stakeholder enquiry, and (iii) periodic reporting under the corporate
audit policy. ``deploy`` covers (i); ``tdgov report`` covers (ii); scheduling
``tdgov report --days 7`` with cron covers (iii).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .governance import Governance


def build_report(gov: Governance, days: int = 7) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = gov.approved_baseline()
    audits = gov.events("audit", since=since)
    deviating = [a for a in audits if a.get("findings")]
    errors = [a for a in audits if a.get("status") == "error"]
    open_crs = gov.list_crs("open")
    bg = gov.events("breakglass-opened", since=since)
    deployed = gov.events("deployed", since=since)

    lines = [f"# Compliance report: {gov.cfg.blueprint_name}",
             f"Period: last {days} days (generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)", ""]
    if base:
        lines += ["## Approved baseline",
                  f"- Version {base['version']}, digest `{base['digest'][:16]}`",
                  f"- Change request {base['cr_id']} by {base['author']}, approved by {', '.join(base['approvers'])} "
                  f"at {base['approved_at']}", ""]
    else:
        lines += ["## Approved baseline", "- none", ""]

    completed = len(audits) - len(errors)
    compliant = completed - len(deviating)
    lines += ["## Audits",
              f"- Audits run: {len(audits)}; compliant: {compliant}; "
              f"with deviations: {len(deviating)}; failed: {len(errors)}",
              f"- Compliance rate (completed audits): {compliant / completed * 100:.1f}%" if completed
              else "- Compliance rate: n/a", ""]
    if deviating:
        lines.append("### Deviations")
        for a in deviating[-20:]:
            items = [d["address"] for d in a.get("drift", []) + a.get("divergence", [])]
            items += [f"VM {u['vmid']} (unmanaged)" for u in a.get("unmanaged", [])]
            lines.append(f"- {a['started_at']} [{a.get('classification')}]: {', '.join(items)}")
        lines.append("")

    lines += ["## Change management",
              f"- Open change requests: {len(open_crs)}"]
    lines += [f"  - {c['cr_id']} by {c['author']}: {c['message']}" for c in open_crs]
    lines += [f"- Deployments: {len(deployed)}", f"- Break-glass windows opened: {len(bg)}"]
    lines += [f"  - {b['opened_at']} by {b['actor']}: {b['reason']}" for b in bg]
    return "\n".join(lines) + "\n"
