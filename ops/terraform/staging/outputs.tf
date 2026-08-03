output "staging_ipv4" {
  value       = hcloud_server.staging.ipv4_address
  description = "Public IPv4 — point the staging DNS A record here (§7.3)."
}

output "staging_id" {
  value       = hcloud_server.staging.id
  description = "Hetzner server id."
}
