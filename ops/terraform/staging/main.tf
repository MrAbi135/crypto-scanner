# Staging host provisioning (S0.3 §7.1). One dedicated-vCPU Hetzner node,
# private network, firewall (80/443 + SSH from admin IPs only — no DB/Redis/
# process ports exposed), docker+compose via cloud-init, hardened SSH.
#
# State is local + encrypted backup (solo-dev appropriate); remote state is an
# S21 scale trigger. Run from ops/terraform/staging/: `terraform init && apply`.

terraform {
  required_version = ">= 1.6"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.48"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}

resource "hcloud_ssh_key" "admin" {
  name       = "scanner-staging-admin"
  public_key = var.admin_ssh_public_key
}

resource "hcloud_network" "private" {
  name     = "scanner-staging"
  ip_range = "10.10.0.0/16"
}

resource "hcloud_network_subnet" "private" {
  network_id   = hcloud_network.private.id
  type         = "cloud"
  network_zone = "eu-central"
  ip_range     = "10.10.1.0/24"
}

resource "hcloud_firewall" "edge" {
  name = "scanner-staging-edge"

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_ip_cidrs # SSH from admin IPs only (TAD §23)
  }
  # No DB/Redis/process ports: staging is not a dev box (S0.3 §7.1).
}

resource "hcloud_server" "staging" {
  name         = "scanner-staging"
  image        = "debian-12"
  server_type  = var.server_type # dedicated-vCPU class (TDR §18)
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.admin.id]
  firewall_ids = [hcloud_firewall.edge.id]
  user_data    = file("${path.module}/cloud-init.yaml")

  network {
    network_id = hcloud_network.private.id
    ip         = "10.10.1.10"
  }

  labels = {
    project = "scanner"
    env     = "staging"
  }
}
