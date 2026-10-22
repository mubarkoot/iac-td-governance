import csv

from experiments.analyze import drift_table
from experiments.drift_injection.inject import _set_nic_field
from experiments.drift_injection.run_experiment import FIELDS, matches
from tdgov.audit import AuditResult


def test_set_nic_field():
    nic = "virtio=BC:24:11:AA:BB:CC,bridge=vmbr1,firewall=1"
    assert _set_nic_field(nic, "bridge", "vmbr0") == "virtio=BC:24:11:AA:BB:CC,bridge=vmbr0,firewall=1"
    assert _set_nic_field(nic, "tag", "20").endswith(",tag=20")


def test_matches():
    res = AuditResult(started_at="t", drift=[{"address": "proxmox_virtual_environment_vm.db"}],
                      unmanaged=[{"vmid": 9150}])
    assert matches({"expected": "drift", "target_address": "proxmox_virtual_environment_vm.db"}, res)
    assert not matches({"expected": "drift", "target_address": "proxmox_virtual_environment_vm.lb"}, res)
    assert matches({"expected": "unmanaged", "params": {"vmid": 9150}}, res)


def test_drift_table(tmp_path):
    path = tmp_path / "r.csv"
    rows = [
        {"scenario": "S1", "expected": "drift", "outcome": "detected", "latency_s": "70", "audit_duration_s": "8",
         "audit_cpu_s": "2", "recovered": "True"},
        {"scenario": "S1", "expected": "drift", "outcome": "missed", "latency_s": "", "audit_duration_s": "8",
         "audit_cpu_s": "2", "recovered": "True"},
        {"scenario": "C0", "expected": "none", "outcome": "true-negative", "latency_s": "", "audit_duration_s": "7",
         "audit_cpu_s": "2", "recovered": "True"},
    ]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**{f: "" for f in FIELDS}, **r})
    table = drift_table(str(path))
    assert "| S1 | drift | 2 | 1/2 (50%)" in table
    assert "Overall detection rate (detectable scenarios): 1/2 = 50.0%" in table
    assert "False-positive rate (control and blind-spot scenarios): 0/1 = 0.0%" in table
