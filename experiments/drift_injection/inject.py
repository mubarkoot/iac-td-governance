"""Out-of-band change injectors used by the drift-injection experiment.

Every injector returns an *undo* callable that restores the previous state.
Injectors talk to Proxmox directly (bypassing Terraform and the pipeline),
which is exactly the behavior the framework is meant to detect.
"""

from __future__ import annotations

import time
from typing import Callable

from tdgov.proxmox import ProxmoxClient, ProxmoxError

Undo = Callable[[], None]


def resolve_vm(client: ProxmoxClient, name: str) -> dict:
    matches = [v for v in client.list_vms() if v["name"] == name]
    if len(matches) != 1:
        raise ProxmoxError(f"expected exactly one VM named {name!r}, found {len(matches)}")
    return matches[0]


def _restore_config(client, node, vmid, original: dict, keys: list[str]) -> None:
    set_params = {k: original[k] for k in keys if k in original}
    missing = [k for k in keys if k not in original]
    if missing:
        set_params["delete"] = ",".join(missing)
    if set_params:
        client.update_vm_config(node, vmid, **set_params)


def inject_config(client: ProxmoxClient, vm: dict, params: dict) -> Undo:
    original = client.vm_config(vm["node"], vm["vmid"])
    client.update_vm_config(vm["node"], vm["vmid"], **params)
    return lambda: _restore_config(client, vm["node"], vm["vmid"], original, list(params))


def _set_nic_field(nic: str, field: str, value: str) -> str:
    parts = nic.split(",")
    for i, p in enumerate(parts):
        if p.startswith(f"{field}="):
            parts[i] = f"{field}={value}"
            break
    else:
        parts.append(f"{field}={value}")
    return ",".join(parts)


def inject_nic(client: ProxmoxClient, vm: dict, params: dict) -> Undo:
    nic_key = params.get("nic", "net0")
    original = client.vm_config(vm["node"], vm["vmid"])
    nic = original[nic_key]
    new = nic
    for field in ("bridge", "firewall", "tag", "link_down"):
        if field in params:
            new = _set_nic_field(new, field, str(params[field]))
    client.update_vm_config(vm["node"], vm["vmid"], **{nic_key: new})
    return lambda: client.update_vm_config(vm["node"], vm["vmid"], **{nic_key: nic})


def inject_add_disk(client: ProxmoxClient, vm: dict, params: dict) -> Undo:
    slot = params.get("slot", "scsi1")
    node, vmid = vm["node"], vm["vmid"]
    client.update_vm_config(node, vmid, **{slot: f"{params['datastore']}:{params.get('size_gb', 1)}"})
    volume = client.vm_config(node, vmid)[slot].split(",")[0]

    def undo():
        client.update_vm_config(node, vmid, delete=slot)
        # A detached disk becomes unusedN; delete it to remove the volume.
        for k, v in client.vm_config(node, vmid).items():
            if k.startswith("unused") and v.split(",")[0] == volume:
                client.update_vm_config(node, vmid, delete=k)
    return undo


def inject_unmanaged_vm(client: ProxmoxClient, node: str, params: dict) -> Undo:
    vmid = int(params["vmid"])
    upid = client.create_vm(node, vmid, params.get("name", "shadow-vm"), tags=params.get("tags", ""))
    if upid:
        client.wait_task(node, upid)

    def undo():
        u = client.delete_vm(node, vmid)
        if u:
            client.wait_task(node, u)
    return undo


def inject_guest_exec(client: ProxmoxClient, vm: dict, params: dict) -> Undo:
    client.agent_exec(vm["node"], vm["vmid"], params["apply"])
    time.sleep(2)
    return lambda: client.agent_exec(vm["node"], vm["vmid"], params["revert"])


def inject(client: ProxmoxClient, scenario: dict, default_node: str) -> Undo:
    kind = scenario["kind"]
    params = scenario.get("params", {})
    if kind == "control":
        return lambda: None
    if kind == "unmanaged_vm":
        return inject_unmanaged_vm(client, params.get("node", default_node), params)
    vm = resolve_vm(client, scenario["target"])
    if kind == "config":
        return inject_config(client, vm, params)
    if kind == "nic":
        return inject_nic(client, vm, params)
    if kind == "add_disk":
        return inject_add_disk(client, vm, params)
    if kind == "guest_exec":
        return inject_guest_exec(client, vm, params)
    raise ValueError(f"unknown scenario kind: {kind}")
