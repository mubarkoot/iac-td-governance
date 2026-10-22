# Reference infrastructure blueprint (Section 4.1 of the paper).
# Provider versions are pinned so that the approved baseline is reproducible
# (DR1). The backend is configured at init time by tdgov (partial
# configuration), so the same blueprint can be materialized from the ledger.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.116"
    }
    dns = {
      source  = "hashicorp/dns"
      version = "~> 3.4"
    }
  }

  backend "local" {}
}

# Credentials are never written into the blueprint (design rule 2, Section 3).
# They are injected at deployment time through environment variables:
#   PROXMOX_VE_ENDPOINT, PROXMOX_VE_API_TOKEN
#   DNS_UPDATE_SERVER, DNS_UPDATE_KEYNAME, DNS_UPDATE_KEYALGORITHM, DNS_UPDATE_KEYSECRET
provider "proxmox" {
  insecure = var.proxmox_insecure_tls

  ssh {
    agent    = true
    username = var.proxmox_ssh_user
  }
}

provider "dns" {}
