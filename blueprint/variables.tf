variable "blueprint_name" {
  description = "Logical name of the blueprint; used as the ledger namespace."
  type        = string
  default     = "reference-web-stack"
}

variable "proxmox_node" {
  description = "Proxmox node on which the VMs are provisioned."
  type        = string
}

variable "pool_id" {
  description = "Proxmox resource pool; write access is restricted to the pipeline identity (deploy/proxmox-rbac.sh)."
  type        = string
  default     = "tdgov-reference-web-stack"
}

variable "proxmox_insecure_tls" {
  description = "Skip TLS verification (lab environments with self-signed certificates only)."
  type        = bool
  default     = false
}

variable "proxmox_ssh_user" {
  description = "SSH user used by the provider to upload cloud-init snippets."
  type        = string
  default     = "root"
}

variable "image_datastore_id" {
  description = "Datastore that holds the downloaded cloud image (file storage, e.g. 'local')."
  type        = string
  default     = "local"
}

variable "snippets_datastore_id" {
  description = "Datastore with the 'snippets' content type enabled, for cloud-init user data."
  type        = string
  default     = "local"
}

variable "encrypted_datastore_id" {
  description = "Datastore backed by encrypted storage (e.g. ZFS native encryption or LUKS). Checked by policy."
  type        = string
}

variable "cloud_image_url" {
  description = "Ubuntu cloud image used for all VMs."
  type        = string
  default     = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

variable "public_bridge" {
  description = "Bridge for the front-end network (load balancer, application servers)."
  type        = string
  default     = "vmbr0"
}

variable "private_bridge" {
  description = "Bridge for the private subnet. The database must only be attached here."
  type        = string
  default     = "vmbr1"
}

variable "public_gateway" {
  type    = string
  default = "10.10.0.1"
}

variable "private_gateway" {
  type    = string
  default = "10.20.0.1"
}

variable "lb_ip" {
  description = "Load balancer address on the public network (CIDR)."
  type        = string
  default     = "10.10.0.10/24"
}

variable "app_ips" {
  description = "Public-network addresses of the application servers (CIDR)."
  type        = list(string)
  default     = ["10.10.0.21/24", "10.10.0.22/24"]
}

variable "app_private_ips" {
  description = "Private-network addresses of the application servers (CIDR), used to reach the database."
  type        = list(string)
  default     = ["10.20.0.21/24", "10.20.0.22/24"]
}

variable "db_ip" {
  description = "Database address on the private network (CIDR)."
  type        = string
  default     = "10.20.0.30/24"
}

variable "vm_id_base" {
  description = "First VM ID used by the blueprint; IDs are allocated consecutively."
  type        = number
  default     = 9100
}

variable "app_cores" {
  type    = number
  default = 2
}

variable "app_memory_mb" {
  type    = number
  default = 2048
}

variable "db_cores" {
  type    = number
  default = 2
}

variable "db_memory_mb" {
  type    = number
  default = 4096
}

variable "disk_size_gb" {
  type    = number
  default = 20
}

variable "postgres_version" {
  description = "PostgreSQL major version. Allowed versions are enforced by policy."
  type        = string
  default     = "16"
}

variable "db_port" {
  description = "PostgreSQL listen port. The default port 5432 is rejected by policy."
  type        = number
  default     = 6432
}

variable "db_admin_password" {
  description = "Database administrator password. Never set in the blueprint; injected from the secrets manager at deployment time."
  type        = string
  sensitive   = true
}

variable "admin_ssh_public_key" {
  description = "SSH public key for the 'ops' break-glass account."
  type        = string
}

variable "dns_zone" {
  description = "DNS zone (with trailing dot) in which the service record is created."
  type        = string
  default     = "lab.example.internal."
}

variable "dns_record_name" {
  type    = string
  default = "app"
}
