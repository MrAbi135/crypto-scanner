# Runbook — Provision the Staging Host (S0.3 §7)

## Purpose
Stand up (or rebuild) the staging host from code and land the first deploy.

## Preconditions
- Hetzner Cloud API token; admin SSH keypair; admin IP CIDR(s).
- `terraform` ≥ 1.6, `sops` + `age` installed; the CI `age` key available.
- GitHub `staging` environment created with secrets: `STAGING_HOST`,
  `STAGING_USER`, `STAGING_SSH_KEY`, `SOPS_AGE_KEY`.

## Steps
1. **Provision**
   ```bash
   cd ops/terraform/staging
   export TF_VAR_hcloud_token=... TF_VAR_admin_ssh_public_key="$(cat ~/.ssh/id_ed25519.pub)"
   export TF_VAR_admin_ip_cidrs='["203.0.113.4/32"]'
   terraform init && terraform apply
   ```
   Record `staging_ipv4` from the outputs. Back up `terraform.tfstate` (encrypted).
2. **DNS** — point `staging.<domain>` (and `app.<domain>`) A records at `staging_ipv4`.
   Set `SCANNER_PUBLIC_HOST` in `ops/env/staging.enc.env` to the real host.
3. **Populate secrets** — `sops ops/env/staging.enc.env`: real DSNs (compose-internal
   hosts), a generated DB password (`openssl rand -base64 24`), `SCANNER_ENV=staging`,
   `SCANNER_SENTRY_DSN` (staging project), `SCANNER_LOG_LEVEL=INFO`.
4. **First deploy** — merge to `main`; `ci.yml` then `deploy-staging.yml` runs:
   pulls sha images, `sops -d` the env, `compose up -d`, and the readiness gate
   waits for all four processes ≤120s.
5. **Verify** — run the [§9 deployment checklist](../setup.md#verification) (TLS,
   edge-403, Grafana heartbeats + release annotation, Loki lines, a Sentry test
   event, rollback drill, reboot resilience, secrets hygiene).

## Escalation
- Readiness gate fails → `ssh` in, `docker compose -f ops/compose/docker-compose.staging.yml logs`;
  a bad release rolls back by re-running the workflow on the previous sha (≤5 min).
- Never edit data or config on the host by hand (Constitution §33.2) — fix in
  git, redeploy.
