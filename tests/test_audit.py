"""Audit logic with a fake Terraform binary (no cloud access needed)."""

import json

from tdgov import canonical
from tdgov.audit import run_audit
from tdgov.proxmox import find_unmanaged
from tdgov.terraform import PlanResult, managed_vm_ids, parse_json_stream

DRIFT_STREAM = "\n".join(json.dumps(m) for m in [
    {"@level": "info", "type": "version", "terraform": "1.14.7"},
    {"type": "resource_drift", "change": {"resource": {"addr": 'proxmox_virtual_environment_vm.app["app1"]',
                                                       "resource_type": "proxmox_virtual_environment_vm"},
                                          "action": "update"}},
    {"type": "planned_change", "change": {"resource": {"addr": "proxmox_virtual_environment_vm.db",
                                                       "resource_type": "proxmox_virtual_environment_vm"},
                                          "action": "update", "reason": None}},
    {"type": "change_summary", "changes": {"add": 0, "change": 1, "remove": 0}},
    {"type": "diagnostic", "diagnostic": {"severity": "warning", "summary": "w"}},
])


def test_parse_json_stream():
    r = parse_json_stream(DRIFT_STREAM)
    assert r.drift == [{"address": 'proxmox_virtual_environment_vm.app["app1"]', "action": "update",
                        "resource_type": "proxmox_virtual_environment_vm"}]
    assert r.changes[0]["address"] == "proxmox_virtual_environment_vm.db"
    assert r.summary == {"add": 0, "change": 1, "remove": 0}
    assert r.diagnostics[0]["severity"] == "warning"


def test_managed_ids_and_unmanaged_scan():
    state = {"values": {"root_module": {"resources": [
        {"type": "proxmox_virtual_environment_vm", "values": {"vm_id": 9100}},
        {"type": "dns_a_record_set", "values": {}},
    ], "child_modules": [{"resources": [{"type": "proxmox_virtual_environment_vm", "values": {"vm_id": 9110}}]}]}}}
    ids = managed_vm_ids(state)
    assert ids == {9100, 9110}
    cluster = [
        {"vmid": 9100, "name": "lb", "tags": []},
        {"vmid": 9150, "name": "shadow", "tags": []},                      # in range
        {"vmid": 500, "name": "tagged", "tags": ["blueprint-x"]},           # tagged
        {"vmid": 501, "name": "other", "tags": []},                         # out of scope
        {"vmid": 9160, "name": "ignored", "tags": ["tdgov-ignore"]},        # explicitly ignored
        {"vmid": 9170, "name": "tpl", "tags": [], "template": True},        # template
    ]
    found = {v["vmid"] for v in find_unmanaged(cluster, ids, "blueprint-x", (9100, 9199))}
    assert found == {9150, 500}


class FakeTerraform:
    """Stands in for the CLI; behavior is configured per test via class attributes."""
    drift: list = []
    changes: list = []

    def __init__(self, workdir, binary="terraform", env=None):
        self.workdir = workdir
        self.env = env or {}

    def init(self, backend_config=None, backend=True):
        assert (self.workdir / "main.tf").exists()

    def plan(self, refresh_only=False, **kw):
        if refresh_only:
            return PlanResult(exit_code=2 if self.drift else 0, drift=list(self.drift))
        return PlanResult(exit_code=2 if self.changes else 0, changes=list(self.changes))

    def show_json(self, planfile=None):
        return {}


def _approve(gov, blueprint_dir):
    cr = gov.submit(canonical.bundle(blueprint_dir), author="dave", message="init")
    gov.approve(cr["cr_id"], "alice")
    gov.approve(cr["cr_id"], "bob")


def test_audit_without_baseline_is_error(cfg, gov):
    res = run_audit(cfg, gov, terraform_cls=FakeTerraform)
    assert res.status == "error" and "no approved baseline" in res.error


def test_audit_compliant_and_drift(cfg, gov, blueprint_dir, monkeypatch):
    monkeypatch.setenv("TDGOV_DB_ADMIN_PASSWORD", "s3cret")
    cfg.secrets.mapping = {"db_admin_password": "TDGOV_DB_ADMIN_PASSWORD"}
    _approve(gov, blueprint_dir)

    FakeTerraform.drift, FakeTerraform.changes = [], []
    res = run_audit(cfg, gov, terraform_cls=FakeTerraform)
    assert res.status == "compliant" and res.baseline_version == 1

    FakeTerraform.drift = [{"address": "proxmox_virtual_environment_vm.lb", "action": "update"}]
    res = run_audit(cfg, gov, terraform_cls=FakeTerraform)
    assert res.status == "drift" and res.classification == "unexpected" and res.findings == 1

    gov.breakglass_open("oncall", "incident 42", 30)
    res = run_audit(cfg, gov, terraform_cls=FakeTerraform)
    assert res.classification == "expected"

    audits = gov.events("audit")
    assert [a["status"] for a in audits] == ["compliant", "drift", "drift"]


def test_audit_uses_ledger_copy_not_working_copy(cfg, gov, blueprint_dir, monkeypatch):
    monkeypatch.setenv("TDGOV_DB_ADMIN_PASSWORD", "s3cret")
    cfg.secrets.mapping = {"db_admin_password": "TDGOV_DB_ADMIN_PASSWORD"}
    _approve(gov, blueprint_dir)
    original = (blueprint_dir / "main.tf").read_text()
    (blueprint_dir / "main.tf").write_text("# unapproved local edit\n")
    seen = {}

    class Spy(FakeTerraform):
        def init(self, backend_config=None, backend=True):
            seen["main"] = (self.workdir / "main.tf").read_text()

    FakeTerraform.drift, FakeTerraform.changes = [], []
    run_audit(cfg, gov, terraform_cls=Spy)
    assert seen["main"] == original


def test_secrets_redacted_from_errors(cfg, gov, blueprint_dir, monkeypatch):
    monkeypatch.setenv("TDGOV_DB_ADMIN_PASSWORD", "s3cret-value")
    cfg.secrets.mapping = {"db_admin_password": "TDGOV_DB_ADMIN_PASSWORD"}
    _approve(gov, blueprint_dir)

    class Boom(FakeTerraform):
        def plan(self, **kw):
            raise RuntimeError("provider said: password s3cret-value rejected")

    res = run_audit(cfg, gov, terraform_cls=Boom)
    assert res.status == "error" and "s3cret-value" not in res.error and "***" in res.error
    assert "s3cret-value" not in json.dumps(gov.events("audit"))
