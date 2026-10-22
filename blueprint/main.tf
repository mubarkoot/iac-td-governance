# Reference topology used in the paper (Fig. 3):
#   DNS record -> load balancer -> two application servers -> database in a
#   private subnet; all disks on encrypted storage.

locals {
  common_tags = ["managed-by-terraform", "blueprint-${var.blueprint_name}"]

  app_servers = {
    for idx, ip in var.app_ips : "app${idx + 1}" => {
      vm_id      = var.vm_id_base + 1 + idx
      public_ip  = ip
      private_ip = var.app_private_ips[idx]
    }
  }

  db_vm_id = var.vm_id_base + 10
  lb_vm_id = var.vm_id_base
}

resource "proxmox_download_file" "ubuntu" {
  content_type = "iso"
  datastore_id = var.image_datastore_id
  node_name    = var.proxmox_node
  url          = var.cloud_image_url
  file_name    = "tdgov-noble-cloudimg.img"
  overwrite    = false
}

# ---------------------------------------------------------------- cloud-init

resource "proxmox_virtual_environment_file" "app_user_data" {
  for_each     = local.app_servers
  content_type = "snippets"
  datastore_id = var.snippets_datastore_id
  node_name    = var.proxmox_node

  source_raw {
    file_name = "tdgov-${each.key}.yaml"
    data = templatefile("${path.module}/templates/app.yaml.tftpl", {
      hostname  = each.key
      ssh_key   = var.admin_ssh_public_key
      db_host   = split("/", var.db_ip)[0]
      db_port   = var.db_port
      blueprint = var.blueprint_name
    })
  }
}

resource "proxmox_virtual_environment_file" "lb_user_data" {
  content_type = "snippets"
  datastore_id = var.snippets_datastore_id
  node_name    = var.proxmox_node

  source_raw {
    file_name = "tdgov-lb.yaml"
    data = templatefile("${path.module}/templates/lb.yaml.tftpl", {
      ssh_key  = var.admin_ssh_public_key
      backends = [for s in values(local.app_servers) : split("/", s.public_ip)[0]]
    })
  }
}

resource "proxmox_virtual_environment_file" "db_user_data" {
  content_type = "snippets"
  datastore_id = var.snippets_datastore_id
  node_name    = var.proxmox_node

  source_raw {
    file_name = "tdgov-db.yaml"
    data = templatefile("${path.module}/templates/db.yaml.tftpl", {
      ssh_key          = var.admin_ssh_public_key
      postgres_version = var.postgres_version
      db_port          = var.db_port
      db_password      = var.db_admin_password
      app_cidrs        = var.app_private_ips
    })
  }
}

# ------------------------------------------------------------------- VMs

resource "proxmox_virtual_environment_vm" "app" {
  for_each  = local.app_servers
  name      = "tdgov-${each.key}"
  node_name = var.proxmox_node
  vm_id     = each.value.vm_id
  pool_id   = var.pool_id
  tags      = concat(local.common_tags, ["role-app"])

  agent {
    enabled = true
  }

  cpu {
    cores = var.app_cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = var.app_memory_mb
  }

  disk {
    datastore_id = var.encrypted_datastore_id
    file_id      = proxmox_download_file.ubuntu.id
    interface    = "scsi0"
    size         = var.disk_size_gb
    discard      = "on"
  }

  network_device {
    bridge   = var.public_bridge
    firewall = true
  }

  network_device {
    bridge   = var.private_bridge
    firewall = true
  }

  initialization {
    datastore_id = var.encrypted_datastore_id

    ip_config {
      ipv4 {
        address = each.value.public_ip
        gateway = var.public_gateway
      }
    }

    ip_config {
      ipv4 {
        address = each.value.private_ip
      }
    }

    user_data_file_id = proxmox_virtual_environment_file.app_user_data[each.key].id
  }
}

resource "proxmox_virtual_environment_vm" "lb" {
  name      = "tdgov-lb"
  node_name = var.proxmox_node
  vm_id     = local.lb_vm_id
  pool_id   = var.pool_id
  tags      = concat(local.common_tags, ["role-lb"])

  agent {
    enabled = true
  }

  cpu {
    cores = 1
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = 1024
  }

  disk {
    datastore_id = var.encrypted_datastore_id
    file_id      = proxmox_download_file.ubuntu.id
    interface    = "scsi0"
    size         = var.disk_size_gb
    discard      = "on"
  }

  network_device {
    bridge   = var.public_bridge
    firewall = true
  }

  initialization {
    datastore_id = var.encrypted_datastore_id

    ip_config {
      ipv4 {
        address = var.lb_ip
        gateway = var.public_gateway
      }
    }

    user_data_file_id = proxmox_virtual_environment_file.lb_user_data.id
  }
}

resource "proxmox_virtual_environment_vm" "db" {
  name      = "tdgov-db"
  node_name = var.proxmox_node
  vm_id     = local.db_vm_id
  pool_id   = var.pool_id
  tags      = concat(local.common_tags, ["role-db", "pg${var.postgres_version}"])

  agent {
    enabled = true
  }

  cpu {
    cores = var.db_cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = var.db_memory_mb
  }

  disk {
    datastore_id = var.encrypted_datastore_id
    file_id      = proxmox_download_file.ubuntu.id
    interface    = "scsi0"
    size         = var.disk_size_gb
    discard      = "on"
  }

  # Private subnet only (DR4 policy: db_private_network).
  network_device {
    bridge   = var.private_bridge
    firewall = true
  }

  initialization {
    datastore_id = var.encrypted_datastore_id

    ip_config {
      ipv4 {
        address = var.db_ip
        gateway = var.private_gateway
      }
    }

    user_data_file_id = proxmox_virtual_environment_file.db_user_data.id
  }
}

# ------------------------------------------------------------------- DNS

resource "dns_a_record_set" "service" {
  zone      = var.dns_zone
  name      = var.dns_record_name
  addresses = [split("/", var.lb_ip)[0]]
  ttl       = 300
}
