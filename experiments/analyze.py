"""Summarize experiment CSVs into tables for the paper.

    python -m experiments.analyze results/drift_injection.csv
    python -m experiments.analyze --scalability results/scalability.csv

Drift injection: per-scenario detection rate, latency (mean / median / p95),
audit duration and CPU, false-positive rate on control trials, and recovery
rate after revert. Uses only the standard library.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict


def _f(rows, key):
    return [float(r[key]) for r in rows if r.get(key) not in ("", None)]


def p95(xs):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def fmt(x, nd=1):
    return "–" if x is None or x != x else f"{x:.{nd}f}"


def drift_table(path: str) -> str:
    rows = list(csv.DictReader(open(path)))
    by = defaultdict(list)
    for r in rows:
        by[r["scenario"]].append(r)
    out = ["| Scenario | Expected | Trials | Detected / correct | Latency mean (s) | median | p95 | "
           "Audit duration (s) | Audit CPU (s) | Recovered |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    tot_pos = tot_det = tot_neg = tot_fp = 0
    for sc in sorted(by):
        rs = [r for r in by[sc] if r["outcome"] not in ("error", "skipped-precondition")]
        if not rs:
            out.append(f"| {sc} | {by[sc][0]['expected']} | 0 | – | – | – | – | – | – | – |")
            continue
        exp = rs[0]["expected"]
        if exp == "none":
            ok = sum(r["outcome"] == "true-negative" for r in rs)
            tot_neg += len(rs)
            tot_fp += len(rs) - ok
        else:
            ok = sum(r["outcome"] == "detected" for r in rs)
            tot_pos += len(rs)
            tot_det += ok
        lat = _f(rs, "latency_s")
        out.append(
            f"| {sc} | {exp} | {len(rs)} | {ok}/{len(rs)} ({ok / len(rs) * 100:.0f}%) | "
            f"{fmt(statistics.mean(lat)) if lat else '–'} | {fmt(statistics.median(lat)) if lat else '–'} | "
            f"{fmt(p95(lat)) if lat else '–'} | {fmt(statistics.mean(_f(rs, 'audit_duration_s')))} | "
            f"{fmt(statistics.mean(_f(rs, 'audit_cpu_s')), 2)} | "
            f"{sum(r['recovered'] == 'True' for r in rs)}/{len(rs)} |")
    skipped = sum(r["outcome"] in ("error", "skipped-precondition") for r in rows)
    out.append("")
    if tot_pos:
        out.append(f"Overall detection rate (detectable scenarios): {tot_det}/{tot_pos} = {tot_det / tot_pos * 100:.1f}%")
    if tot_neg:
        out.append(f"False-positive rate (control and blind-spot scenarios): {tot_fp}/{tot_neg} = {tot_fp / tot_neg * 100:.1f}%")
    out.append(f"Trials excluded (errors or failed precondition): {skipped}")
    return "\n".join(out)


def scalability_table(path: str) -> str:
    rows = list(csv.DictReader(open(path)))
    by = defaultdict(list)
    for r in rows:
        by[int(r["resources"])].append(r)
    out = ["| Resources | Runs | Refresh-only plan (s) | Full plan (s) | Total audit (s) | CPU (s) | Bundle size (KiB) |",
           "|---|---|---|---|---|---|---|"]
    for n in sorted(by):
        rs = by[n]
        out.append(f"| {n} | {len(rs)} | {fmt(statistics.mean(_f(rs, 'refresh_s')), 2)} | "
                   f"{fmt(statistics.mean(_f(rs, 'plan_s')), 2)} | {fmt(statistics.mean(_f(rs, 'total_s')), 2)} | "
                   f"{fmt(statistics.mean(_f(rs, 'cpu_s')), 2)} | {fmt(statistics.mean(_f(rs, 'bundle_bytes')) / 1024, 1)} |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--scalability", action="store_true")
    a = ap.parse_args()
    print(scalability_table(a.csv) if a.scalability else drift_table(a.csv))


if __name__ == "__main__":
    main()
