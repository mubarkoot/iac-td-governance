output "service_fqdn" {
  value = "${var.dns_record_name}.${var.dns_zone}"
}

output "load_balancer_ip" {
  value = split("/", var.lb_ip)[0]
}

output "managed_vm_ids" {
  description = "VM IDs owned by this blueprint; used by the unmanaged-resource scan."
  value = concat(
    [proxmox_virtual_environment_vm.lb.vm_id, proxmox_virtual_environment_vm.db.vm_id],
    [for vm in proxmox_virtual_environment_vm.app : vm.vm_id],
  )
}
