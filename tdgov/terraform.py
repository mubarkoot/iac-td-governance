"""Thin wrapper around the Terraform (or OpenTofu) CLI.

Drift detection relies on Terraform's machine-readable UI (``-json``):

* ``plan -refresh-only`` emits ``resource_drift`` messages for managed
  resources whose real-world attributes differ from the recorded state
  (e.g. a VM resized through the Proxmox UI).
* a normal ``plan`` emits ``planned_change`` messages when the approved
  configuration and the state diverge.

``-detailed-exitcode`` returns 0 (no changes), 1 (error) or 2 (changes).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class TerraformError(Exception):
    def __init__(self, message: str, stderr: str = "", diagnostics: list | None = None):
        super().__init__(message)
        self.stderr = stderr
        self.diagnostics = diagnostics or []


@dataclass
class PlanResult:
    exit_code: int
    drift: list[dict] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return self.exit_code == 2


def parse_json_stream(stdout: str) -> PlanResult:
    """Parse the JSON-lines output of ``terraform plan -json``."""
    res = PlanResult(exit_code=0)
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = msg.get("type")
        change = msg.get("change") or {}
        resource = change.get("resource") or {}
        if kind == "resource_drift":
            res.drift.append({"address": resource.get("addr"), "action": change.get("action"),
                              "resource_type": resource.get("resource_type")})
        elif kind == "planned_change":
            res.changes.append({"address": resource.get("addr"), "action": change.get("action"),
                                "resource_type": resource.get("resource_type"),
                                "reason": change.get("reason")})
        elif kind == "change_summary":
            res.summary = msg.get("changes") or {}
        elif kind == "diagnostic":
            d = msg.get("diagnostic") or {}
            res.diagnostics.append({"severity": d.get("severity"), "summary": d.get("summary"),
                                    "detail": d.get("detail")})
    return res


class Terraform:
    def __init__(self, workdir: str | Path, binary: str = "terraform", env: dict | None = None):
        self.workdir = Path(workdir)
        self.binary = binary
        self.env = {**os.environ, "TF_IN_AUTOMATION": "1", "TF_INPUT": "0", **(env or {})}

    def run(self, *args: str, check: bool = True, ok_codes=(0,)) -> subprocess.CompletedProcess:
        proc = subprocess.run([self.binary, *args], cwd=self.workdir, env=self.env,
                              capture_output=True, text=True)
        if check and proc.returncode not in ok_codes:
            raise TerraformError(f"terraform {' '.join(args[:1])} failed (exit {proc.returncode})",
                                 stderr=proc.stderr[-4000:],
                                 diagnostics=parse_json_stream(proc.stdout).diagnostics)
        return proc

    def init(self, backend_config: dict | None = None, backend: bool = True) -> None:
        args = ["init", "-input=false", "-no-color"]
        if not backend:
            args.append("-backend=false")
        for k, v in (backend_config or {}).items():
            args.append(f"-backend-config={k}={v}")
        self.run(*args)

    def fmt_check(self) -> list[str]:
        proc = self.run("fmt", "-check", "-recursive", "-list=true", check=False)
        return [line for line in proc.stdout.splitlines() if line.strip()] if proc.returncode != 0 else []

    def validate(self) -> dict:
        proc = self.run("validate", "-json", "-no-color", check=False)
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise TerraformError("terraform validate produced no JSON", stderr=proc.stderr) from exc
        return out

    def plan(self, out: str | None = None, var_file: str | None = None, refresh_only: bool = False,
             extra: tuple[str, ...] = ()) -> PlanResult:
        args = ["plan", "-input=false", "-no-color", "-json", "-detailed-exitcode", "-lock-timeout=60s"]
        if refresh_only:
            args.append("-refresh-only")
        if out:
            args.append(f"-out={out}")
        if var_file:
            args.append(f"-var-file={var_file}")
        args.extend(extra)
        proc = self.run(*args, ok_codes=(0, 2))
        res = parse_json_stream(proc.stdout)
        res.exit_code = proc.returncode
        return res

    def show_json(self, planfile: str | None = None) -> dict:
        args = ["show", "-json", "-no-color"] + ([planfile] if planfile else [])
        return json.loads(self.run(*args).stdout or "{}")

    def apply(self, planfile: str) -> str:
        return self.run("apply", "-input=false", "-no-color", "-auto-approve", planfile).stdout


def managed_vm_ids(state_json: dict) -> set[int]:
    """VM IDs of Proxmox VMs recorded in the Terraform state."""
    ids: set[int] = set()

    def walk(module: dict):
        for r in module.get("resources", []):
            if r.get("type") == "proxmox_virtual_environment_vm":
                vm_id = (r.get("values") or {}).get("vm_id")
                if vm_id is not None:
                    ids.add(int(vm_id))
        for child in module.get("child_modules", []):
            walk(child)

    walk((state_json.get("values") or {}).get("root_module") or {})
    return ids
