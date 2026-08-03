# Security

The living home for threat modeling, the security checklist, and secret-handling
procedure. Filled progressively; the S21 hardening sprint drives it to
completion. Constitution §17 (secrets) and §45.7/§45.12 (never trade
security/integrity/tenancy for speed) govern this directory.

## Secret handling (S0.1)
- Secrets are managed with **SOPS + age** (TDR §24). `.sops.yaml` maps encrypted
  env files to age recipients.
- Private keys **never** enter the repo, CI variables (unencrypted), logs,
  frontend bundles, ADRs, docs, or tickets (Constitution §17.3).
- The CI age private key lives only in GitHub Actions secrets; recovery copies
  are kept offline (Constitution §17.4).
- A leaked secret is rotated immediately, no exceptions. gitleaks runs in
  pre-commit now and in CI from S0.2.

## STRIDE worksheet (placeholder — grown per surface)
| Surface | Spoofing | Tampering | Repudiation | Info disclosure | DoS | Elevation |
|---|---|---|---|---|---|---|
| _(to be filled as auth (S10), API (S11), WS (S12) land)_ | | | | | | |

## Security checklist
The per-PR security checklist is the "Governance checklist" in
`.github/PULL_REQUEST_TEMPLATE.md`. The signed release security checklist is an
S10/S21 deliverable and will be linked here.
