variable "hcloud_token" {
  type        = string
  sensitive   = true
  description = "Hetzner Cloud API token (from the deploy environment secret)."
}

variable "admin_ssh_public_key" {
  type        = string
  description = "Admin SSH public key installed on the host."
}

variable "admin_ip_cidrs" {
  type        = list(string)
  description = "CIDRs permitted inbound SSH (admin IPs only)."
}

variable "server_type" {
  type        = string
  default     = "ccx13" # dedicated-vCPU class (TDR §18)
  description = "Hetzner server type."
}

variable "location" {
  type        = string
  default     = "nbg1" # Nuremberg, EU (GDPR + Binance geo, TDR)
  description = "Hetzner datacenter location."
}
