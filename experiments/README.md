# Experiments

## 1. Drift injection (detection effectiveness and latency)

Addresses the evaluation gap noted in Section 6.3 of the paper: it measures, rather than asks experts about,
whether and how quickly out-of-band changes are detected.

**Prerequisites:** a dedicated lab Proxmox cluster; the reference blueprint approved and deployed (`tdgov deploy`);
a Proxmox token that can modify the blueprint's VMs (the injector deliberately bypasses the pipeline);
`scan_unmanaged: true` with `unmanaged_vmid_range` covering VM ID 9150.

```bash
python -m experiments.drift_injection.run_experiment \
    --config tdgov.yaml --node pve01 --reps 5 --interval 120 --max-audits 3 \
    --out results/drift_injection.csv
python -m experiments.analyze results/drift_injection.csv
```

**Procedure for each trial** (scenarios run in randomized order in every repetition):

1. pre-audit: the trial is skipped unless the environment is compliant;
2. wait a uniformly random offset within the audit interval, then inject the change (see `scenarios.yaml`);
3. run audits on the interval schedule until the change is detected or `--max-audits` audits have passed;
4. revert the change and run a post-audit to confirm recovery.

**Metrics:** detection rate for detectable scenarios, false-positive rate (control and blind-spot scenarios),
detection latency (mean, median, p95), audit duration, child CPU time and peak memory of Terraform, ledger growth,
and recovery rate.

Scenario S8 (a change inside the guest) is *expected not to be detected*. It is included to measure and report
the documented blind spot honestly, not to inflate the detection rate.

## 2. Scalability (audit overhead versus blueprint size)

```bash
python -m experiments.scalability.run_scalability --sizes 10 50 100 200 400 --reps 3
python -m experiments.analyze --scalability results/scalability.csv
```

This uses Terraform's built-in `terraform_data` resource, so no cloud provider is needed. It isolates the
framework's own cost (canonical bundling, ledger write, refresh-only plan, plan) from provider API latency.
Pass `--terraform tofu` to benchmark OpenTofu.

Example run (OpenTofu 1.9.0, file ledger, 2 vCPU container; illustrative, not a result for the paper):

| Resources | Refresh-only plan (s) | Full plan (s) | Total audit (s) | Bundle (KiB) |
|---|---|---|---|---|
| 10 | 0.05 | 0.05 | 0.10 | 0.4 |
| 50 | 0.10 | 0.12 | 0.21 | 0.6 |
| 100 | 0.13 | 0.17 | 0.30 | 0.9 |
| 200 | 0.24 | 0.31 | 0.55 | 1.5 |

## Reporting

`analyze.py` prints Markdown tables that can be adapted for the paper. Report the hardware, Proxmox version,
Terraform/OpenTofu and provider versions, audit interval, number of repetitions and the random seed alongside
the results.
