"""Audit overhead versus blueprint size (offline benchmark).

Generates blueprints with N ``terraform_data`` resources (built into
Terraform, no cloud provider needed), applies them to a local state, and
measures the cost of the audit operations: canonical bundling, ledger
storage, refresh-only plan and full plan. This isolates the framework's own
overhead from provider API latency, which must be added for real platforms
(measure that with the drift-injection experiment).

    python -m experiments.scalability.run_scalability --sizes 10 50 100 200 400 --reps 3

Output: CSV with one row per (size, repetition).
"""

from __future__ import annotations

import argparse
import csv
import resource
import shutil
import tempfile
import time
from pathlib import Path

from tdgov.canonical import bundle
from tdgov.ledger import FileLedger
from tdgov.terraform import Terraform


def generate(n: int, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    blocks = ['terraform {\n  required_version = ">= 1.6.0"\n}\n']
    for i in range(n):
        blocks.append(
            f'resource "terraform_data" "r{i}" {{\n'
            f'  input = {{\n    name = "res-{i}"\n    tier = "{"db" if i % 10 == 0 else "app"}"\n'
            f'    size = {i % 8 + 1}\n  }}\n}}\n')
    (path / "main.tf").write_text("\n".join(blocks))


def cpu_now() -> float:
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 100, 200, 400])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--terraform", default="terraform")
    ap.add_argument("--out", default="results/scalability.csv")
    a = ap.parse_args()

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["resources", "rep", "bundle_bytes", "bundle_s", "ledger_put_s", "refresh_s", "plan_s", "total_s", "cpu_s"]
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for n in a.sizes:
            work = Path(tempfile.mkdtemp(prefix=f"tdgov-scale-{n}-"))
            try:
                generate(n, work)
                tf = Terraform(work, a.terraform)
                tf.init()
                tf.run("apply", "-auto-approve", "-input=false", "-no-color")
                ledger = FileLedger(work / "ledger.jsonl")
                for rep in range(1, a.reps + 1):
                    c0, t0 = cpu_now(), time.perf_counter()
                    b = bundle(work)
                    t1 = time.perf_counter()
                    ledger.put(f"blob:{b.digest}:{rep}", b.data)
                    t2 = time.perf_counter()
                    tf.plan(refresh_only=True)
                    t3 = time.perf_counter()
                    tf.plan()
                    t4 = time.perf_counter()
                    w.writerow({"resources": n, "rep": rep, "bundle_bytes": b.size,
                                "bundle_s": round(t1 - t0, 4), "ledger_put_s": round(t2 - t1, 4),
                                "refresh_s": round(t3 - t2, 3), "plan_s": round(t4 - t3, 3),
                                "total_s": round(t4 - t0, 3), "cpu_s": round(cpu_now() - c0, 3)})
                    fh.flush()
                    print(f"n={n} rep={rep} total={t4 - t0:.2f}s", flush=True)
            finally:
                shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
