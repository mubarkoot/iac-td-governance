"""Minimal Proxmox VE REST API client.

Used for the unmanaged-resource scan during audits (extending detection
beyond Terraform's state model, Section 4.1) and by the drift-injection
experiment harness. Authentication uses an API token
(``user@realm!tokenid=secret``), ideally with read-only permissions for
auditing.
"""

from __future__ import annotations

import os
import time

import requests


class ProxmoxError(Exception):
    pass


class ProxmoxClient:
    def __init__(self, endpoint: str, token: str, verify_tls: bool = True, timeout: int = 20):
        self.base = endpoint.rstrip("/") + "/api2/json"
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"PVEAPIToken={token}"
        self.session.verify = verify_tls
        self.timeout = timeout

    @classmethod
    def from_config(cls, cfg) -> "ProxmoxClient":
        pc = cfg.proxmox
        endpoint, token = os.environ.get(pc.endpoint_env), os.environ.get(pc.token_env)
        if not endpoint or not token:
            raise ProxmoxError(f"set {pc.endpoint_env} and {pc.token_env}")
        return cls(endpoint, token, verify_tls=pc.verify_tls)

    def _req(self, method: str, path: str, **kw):
        resp = self.session.request(method, self.base + path, timeout=self.timeout, **kw)
        if resp.status_code >= 400:
            raise ProxmoxError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json().get("data")

    def get(self, path: str, **params):
        return self._req("GET", path, params=params)

    def put(self, path: str, **data):
        return self._req("PUT", path, data=data)

    def post(self, path: str, **data):
        return self._req("POST", path, data=data)

    def delete(self, path: str, **params):
        return self._req("DELETE", path, params=params)

    # ------------------------------------------------------------- helpers
    def list_vms(self) -> list[dict]:
        out = []
        for r in self.get("/cluster/resources", type="vm") or []:
            if r.get("type") != "qemu":
                continue
            tags = [t for t in (r.get("tags") or "").replace(",", ";").split(";") if t]
            out.append({"vmid": int(r["vmid"]), "name": r.get("name"), "node": r.get("node"),
                        "status": r.get("status"), "tags": tags, "template": bool(r.get("template"))})
        return out

    def vm_config(self, node: str, vmid: int) -> dict:
        return self.get(f"/nodes/{node}/qemu/{vmid}/config")

    def update_vm_config(self, node: str, vmid: int, **params) -> None:
        self.put(f"/nodes/{node}/qemu/{vmid}/config", **params)

    def create_vm(self, node: str, vmid: int, name: str, tags: str = "", memory: int = 512) -> str:
        return self.post(f"/nodes/{node}/qemu", vmid=vmid, name=name, tags=tags, memory=memory)

    def delete_vm(self, node: str, vmid: int) -> str:
        return self.delete(f"/nodes/{node}/qemu/{vmid}", purge=1)

    def agent_exec(self, node: str, vmid: int, command: list[str]) -> dict:
        data = [("command", c) for c in command]
        resp = self.session.post(f"{self.base}/nodes/{node}/qemu/{vmid}/agent/exec", data=data, timeout=self.timeout)
        if resp.status_code >= 400:
            raise ProxmoxError(f"agent exec failed: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json().get("data")

    def wait_task(self, node: str, upid: str, timeout: int = 300) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.get(f"/nodes/{node}/tasks/{upid}/status")
            if st.get("status") == "stopped":
                if st.get("exitstatus") not in ("OK", None):
                    raise ProxmoxError(f"task {upid} failed: {st.get('exitstatus')}")
                return st
            time.sleep(2)
        raise ProxmoxError(f"task {upid} timed out")


def find_unmanaged(cluster_vms: list[dict], managed_ids: set[int], blueprint_tag: str,
                   vmid_range: tuple[int, int] | None, ignore_tag: str = "tdgov-ignore") -> list[dict]:
    """VMs inside the blueprint's scope that are not recorded in Terraform state.

    A VM is in scope if it carries the blueprint tag or its VM ID lies in the
    blueprint's reserved range. VMs tagged with ``ignore_tag`` and templates
    are excluded.
    """
    out = []
    for vm in cluster_vms:
        if vm["vmid"] in managed_ids or vm.get("template") or ignore_tag in vm.get("tags", []):
            continue
        in_range = vmid_range is not None and vmid_range[0] <= vm["vmid"] <= vmid_range[1]
        if blueprint_tag in vm.get("tags", []) or in_range:
            out.append(vm)
    return out
