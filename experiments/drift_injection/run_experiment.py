"""Drift-injection experiment (proposed in Section 6.3 of the paper).

For every repetition and scenario (in randomized order):

1. confirm the environment is compliant with the approved baseline;
2. wait a random offset within the audit interval, then inject the change
   (so that detection latency reflects a real cron schedule);
3. run audits on the schedule until the change is detected or
   ``--max-audits`` audits have passed;
4. revert the change and confirm the environment is compliant again.

Outputs one CSV row per trial with detection outcome, detection latency,
audit duration, CPU time and ledger growth. Analyze with analyze.py.

Usage:
    python -m experiments.drift_injection.run_experiment \
        --config tdgov.yaml --reps 5 --interval 120 --out results/drift.csv

Requires an approved and deployed baseline and Proxmox credentials with
permission to modify the blueprint's VMs (use a dedicated lab cluster).
"""

from __future__ import annotations

import argparse
import csv
import random
import time
from pathlib import Path

import yaml

from tdgov.audit import run_audit
from tdgov.config import load_config
from tdgov.governance import Governance
from tdgov.ledger import open_ledger
from tdgov.proxmox import ProxmoxClient

from .inject import inject

FIELDS = ["rep", "scenario", "kind", "expected", "outcome", "detected", "false_positive",
          "latency_s", "audits_until_detection", "audit_duration_s", "audit_cpu_s", "audit_max_rss_kb",
          "finding_types", "recovered", "ledger_entries_before", "ledger_entries_after", "error"]


def matches(scenario: dict, res) -> bool:
    exp = scenario["expected"]
    if exp == "unmanaged":
        vmid = int(scenario["params"]["vmid"])
        return any(int(u["vmid"]) == vmid for u in res.unmanaged)
    if exp == "drift":
        addr = scenario.get("target_address")
        return any(d.get("address") == addr for d in res.drift + res.divergence)
    return False


def finding_types(res) -> str:
    t = []
    if res.drift:
        t.append("drift")
    if res.divergence:
        t.append("divergence")
    if res.unmanaged:
        t.append("unmanaged")
    return "+".join(t) or "none"


def wait_until(ts: float) -> None:
    delay = ts - time.time()
    if delay > 0:
        time.sleep(delay)


def run_trial(rep: int, sc: dict, cfg, gov, client, node: str, interval: float, max_audits: int) -> dict:
    row = {f: "" for f in FIELDS}
    row.update(rep=rep, scenario=sc["id"], kind=sc["kind"], expected=sc["expected"])
    stats = gov.ledger.stats()
    row["ledger_entries_before"] = stats.get("entries", stats.get("tx_id", ""))

    pre = run_audit(cfg, gov, proxmox=client)
    if pre.status != "compliant":
        row.update(outcome="skipped-precondition", error=pre.error or f"pre-audit found {pre.findings} findings")
        return row

    schedule_start = time.time()
    offset = random.uniform(0, interval)
    wait_until(schedule_start + offset)
    undo = None
    try:
        undo = inject(client, sc, node)
        t_inject = time.time()
        detected, res, n = False, None, 0
        for n in range(1, max_audits + 1):
            wait_until(schedule_start + n * interval)
            res = run_audit(cfg, gov, proxmox=client)
            if res.status == "error":
                row["error"] = res.error
                break
            if sc["expected"] in ("drift", "unmanaged") and matches(sc, res):
                detected = True
                break
            if sc["expected"] == "none":
                break  # one audit is enough for control / blind-spot scenarios
        row.update(
            audits_until_detection=n if detected else "",
            latency_s=round(time.time() - t_inject, 1) if detected else "",
            detected=detected,
            false_positive=bool(res and sc["expected"] == "none" and res.findings > 0),
            audit_duration_s=res.duration_s if res else "",
            audit_cpu_s=res.child_cpu_s if res else "",
            audit_max_rss_kb=res.child_max_rss_kb if res else "",
            finding_types=finding_types(res) if res else "",
        )
        if sc["expected"] == "none":
            row["outcome"] = "false-positive" if row["false_positive"] else "true-negative"
        else:
            row["outcome"] = "detected" if detected else "missed"
    finally:
        if undo:
            undo()
            time.sleep(5)
        post = run_audit(cfg, gov, proxmox=client)
        row["recovered"] = post.status == "compliant"
        stats = gov.ledger.stats()
        row["ledger_entries_after"] = stats.get("entries", stats.get("tx_id", ""))
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="tdgov.yaml")
    ap.add_argument("--scenarios", default=str(Path(__file__).with_name("scenarios.yaml")))
    ap.add_argument("--only", nargs="*", help="run only these scenario IDs")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--interval", type=float, default=120.0, help="audit interval in seconds (cron schedule)")
    ap.add_argument("--max-audits", type=int, default=3)
    ap.add_argument("--node", required=True, help="Proxmox node name")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="results/drift_injection.csv")
    args = ap.parse_args()

    random.seed(args.seed)
    cfg = load_config(args.config)
    gov = Governance(open_ledger(cfg), cfg)
    client = ProxmoxClient.from_config(cfg)
    scenarios = yaml.safe_load(Path(args.scenarios).read_text())["scenarios"]
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    with out.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for rep in range(1, args.reps + 1):
            order = scenarios[:]
            random.shuffle(order)
            for sc in order:
                print(f"[rep {rep}] {sc['id']} ...", flush=True)
                try:
                    row = run_trial(rep, sc, cfg, gov, client, args.node, args.interval, args.max_audits)
                except Exception as exc:
                    row = {f: "" for f in FIELDS}
                    row.update(rep=rep, scenario=sc["id"], kind=sc["kind"], expected=sc["expected"],
                               outcome="error", error=str(exc))
                w.writerow(row)
                fh.flush()
                print(f"    -> {row['outcome']} latency={row['latency_s']}s", flush=True)


if __name__ == "__main__":
    main()
