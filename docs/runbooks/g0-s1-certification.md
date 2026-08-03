# Runbook — Gate G0 & Sprint S1 Certification (Execution Plan)

Purpose: execute the infrastructure-dependent work that certifies **Gate G0**
and closes **Sprint S1** items 6–8 (B1–B5). No application code changes.
Execute steps in order; each ends by writing evidence under `docs/evidence/`.

## Conventions
- `$REPO` = the `crypto-scanner/` monorepo root. All paths are relative to it unless noted.
- Shell = **POSIX bash**. On Windows use **WSL2** (or Git Bash for read-only checks); Docker Desktop provides the daemon.
- `uv` on `PATH` (installer puts it at `~/.local/bin`); Node ≥20 + `corepack`; `docker` + `compose v2`.
- `DEV_DSN_HOST` = `postgresql+asyncpg://scanner:scanner-dev-only@localhost:5432/scanner` (host tooling).
- `DEV_DSN_CONTAINER` = `postgresql+asyncpg://scanner:scanner-dev-only@db:5432/scanner` (compose services).

## ⚠️ Known operational gotchas (engineered into the steps below)
1. **DB host differs by caller.** Compose services must reach `db:5432` (compose DNS); host-run `alembic`/CLI must reach `localhost:5432` (published port). `dev.env` (compose `env_file`) uses `db`; host tooling overrides to `localhost`.
2. **DB password is `scanner-dev-only`** (compose `POSTGRES_PASSWORD`), not `scanner`. Verify `ops/env/.env.example` before copying to `dev.env`; correct in `dev.env` (gitignored, operator-owned — not a code change).
3. **Redis URL** likewise: `redis://redis:6379/0` (containers) vs `redis://localhost:6379/0` (host).
4. **Terraform `cloud-init.yaml` disables root SSH but installs the key on `root`** → lockout. Before `terraform apply`, add a `deploy` user (key + sudo) to cloud-init and set `STAGING_USER=deploy`. (Infra-config fix at execution time — flagged in Step 12.)
5. **`ops/env/staging.enc.env` + `.sops.yaml` recipients are placeholders.** Generate a real age keypair and re-encrypt before any staging deploy (Steps 5/12/13).
6. **`deploy-prod.yml` is intentionally inert** (no `production` environment until S22) — leave it.
7. **testcontainers pulls `timescale/timescaledb:2.15.2-pg16`** — needs Docker + network on first run.

---

## Step 1 — Docker Desktop verification
- **Objective:** a reachable Docker daemon + Compose v2.
- **Prerequisites:** Docker Desktop installed; on Windows, WSL2 integration enabled.
- **Exact commands:**
  ```bash
  docker version
  docker info --format '{{.ServerVersion}}'
  docker compose version
  docker run --rm hello-world
  ```
- **Expected output:** a server version prints; `hello-world` prints "Hello from Docker!".
- **Verification criteria:** `docker info` returns a `ServerVersion` (no pipe/daemon error); `compose version` ≥ v2.
- **Possible errors:** `failed to connect to the docker API at npipe://…/dockerDesktopLinuxEngine` (daemon down); `permission denied /var/run/docker.sock` (Linux).
- **Recovery:** start Docker Desktop (Windows/macOS) and wait for "Engine running"; on Linux `sudo systemctl start docker` and add user to `docker` group (`sudo usermod -aG docker $USER` then re-login). Re-run Step 1.

## Step 2 — PostgreSQL + TimescaleDB setup
- **Objective:** the `db` service (PG16 + TimescaleDB, extension auto-created) running.
- **Prerequisites:** Step 1 green.
- **Exact commands:**
  ```bash
  cd "$REPO"
  docker compose -f ops/compose/docker-compose.dev.yml up -d db
  docker compose -f ops/compose/docker-compose.dev.yml exec db pg_isready -U scanner -d scanner
  docker compose -f ops/compose/docker-compose.dev.yml exec db \
    psql -U scanner -d scanner -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb';"
  ```
- **Expected output:** `db` becomes healthy; `pg_isready` → "accepting connections"; the extension query returns a version (e.g. `2.15.2`).
- **Verification criteria:** `docker compose ps db` shows `healthy`; timescaledb extension present.
- **Possible errors:** extension empty (initdb script didn't run because the volume pre-existed); port 5432 already in use.
- **Recovery:** extension missing → `docker compose -f ops/compose/docker-compose.dev.yml down -v` (wipes the volume so `ops/db/initdb/00-extensions.sql` re-runs) then re-up. Port clash → stop the other PG or change the host port mapping in a local compose override.

## Step 3 — Redis setup
- **Objective:** the `redis` service (7, AOF) running.
- **Prerequisites:** Step 1 green.
- **Exact commands:**
  ```bash
  docker compose -f ops/compose/docker-compose.dev.yml up -d redis
  docker compose -f ops/compose/docker-compose.dev.yml exec redis redis-cli ping
  ```
- **Expected output:** `PONG`.
- **Verification criteria:** `docker compose ps redis` shows `healthy`.
- **Possible errors:** port 6379 in use.
- **Recovery:** stop the conflicting Redis or remap the host port in a local override.

## Step 4 — Docker Compose stack (all 6 dev services)
- **Objective:** db + redis + the four processes healthy (`/internal/health/ready` = 200).
- **Prerequisites:** Steps 1–3; `dev.env` present (Step 5).
- **Exact commands:**
  ```bash
  scripts/dev-up.sh                 # up -d --build + waits for health
  docker compose -f ops/compose/docker-compose.dev.yml ps
  for p in 8000 8001 8002 8003; do curl -fsS "http://localhost:$p/internal/health/live" && echo " ok:$p"; done
  curl -fsS http://localhost:8000/internal/health/ready | python -m json.tool
  ```
- **Expected output:** all six services `healthy`; `live` → `{"status":"live"}` ×4; `ready` → 200 with `{"db":"ok","redis":"ok"}`.
- **Verification criteria:** four processes `healthy` within 90s; ready reports both dependencies `ok`.
- **Possible errors:** processes `unhealthy` with `db: unreachable` (DSN uses `localhost` inside the container — must be `db`); image build fails (lockfile drift).
- **Recovery:** fix `dev.env` DB/Redis hosts to `db`/`redis` (gotcha #1/#3), `scripts/dev-down.sh && scripts/dev-up.sh`; build failure → `cd backend && uv lock --check` then rebuild.

## Step 5 — Environment variable configuration
- **Objective:** a correct, gitignored `ops/env/dev.env`; staging secrets generated (age).
- **Prerequisites:** `sops` + `age` installed.
- **Exact commands:**
  ```bash
  cp ops/env/.env.example ops/env/dev.env
  # Edit ops/env/dev.env for COMPOSE (container hosts + real password):
  #   SCANNER_ENV=dev
  #   SCANNER_DB_DSN=postgresql+asyncpg://scanner:scanner-dev-only@db:5432/scanner
  #   SCANNER_REDIS_URL=redis://redis:6379/0
  #   SCANNER_API_PORT=8000   SCANNER_HEALTH_PORT=8001
  # Developer age key (private key stays local):
  age-keygen -o ~/.config/sops/age/keys.txt      # copy the "public key:" line
  # Put the public key into .sops.yaml (replace both placeholder recipients).
  ```
- **Expected output:** `dev.env` exists (gitignored); `age-keygen` prints a public key; `.sops.yaml` holds the real recipient.
- **Verification criteria:** `git status` shows `dev.env` ignored; `.sops.yaml` has no `age1placeholder…`.
- **Possible errors:** wrong password/host (gotcha #1/#2); committing `dev.env` or the private key.
- **Recovery:** correct the values; if a secret was committed, rotate it and scrub history (gitleaks will also catch it in CI).

## Step 6 — Alembic migration execution (S1 item 6 / B1+B2)
- **Objective:** apply baseline + `market` schema; prove idempotence + hypertable.
- **Prerequisites:** Step 2 (db healthy).
- **Exact commands (run migrations inside a one-off container on the compose network — avoids the host/`db` DSN split):**
  ```bash
  cd "$REPO"
  DC="docker compose -f ops/compose/docker-compose.dev.yml"
  $DC run --rm -e SCANNER_DB_DSN="$DEV_DSN_CONTAINER" api alembic upgrade head
  $DC run --rm -e SCANNER_DB_DSN="$DEV_DSN_CONTAINER" api alembic upgrade head   # re-run = no-op
  $DC exec db psql -U scanner -d scanner -c \
    "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name='candles';"
  $DC exec db psql -U scanner -d scanner -c \
    "SELECT hypertable_name, count(*) FROM timescaledb_information.chunks GROUP BY 1;"
  ```
  Alternative (host-run): `SCANNER_DB_DSN="$DEV_DSN_HOST" ; cd backend && uv run alembic upgrade head`.
- **Expected output:** first run creates `market.symbols/candles/data_incidents`; second prints no new revisions; hypertable count = **1**; compression policy present.
- **Verification criteria:** `alembic current` = `001_market_schema`; hypertable assertion returns 1; re-run applied nothing.
- **Possible errors:** `SCANNER_DB_DSN required` (env not passed); `function create_hypertable does not exist` (timescaledb extension missing — Step 2); `relation already exists` (partial prior run).
- **Recovery:** pass the DSN explicitly; extension missing → Step 2 recovery; partial state → `alembic downgrade base` then `upgrade head`, or `down -v` + Step 2.
- **Evidence:** `docs/evidence/S1/$(date -u +%Y%m%dT%H%M%SZ)-migration.txt`. **Flips item 6 → ✅.**

## Step 7 — Integration test execution (S1 item 7 / B3)
- **Objective:** run `tests/integration` (testcontainers PG16+Timescale) — COPY path + CHECK tripwires.
- **Prerequisites:** Step 1 (daemon); network to pull the timescale image.
- **Exact commands:**
  ```bash
  cd "$REPO/backend"
  uv run pytest tests/integration -m integration -v | tee ../docs/evidence/S1/integration.txt
  ```
- **Expected output:** testcontainers starts an ephemeral PG16+Timescale, 6 cases pass (COPY bulk insert, conflict-skip idempotence, CHECK constraint rejections).
- **Verification criteria:** `6 passed`; no `errors`.
- **Possible errors:** `DockerException: Error while fetching server API version` (daemon down); image pull timeout; Ryuk/resource-reaper blocked by firewall.
- **Recovery:** ensure Step 1; pre-pull `docker pull timescale/timescaledb:2.15.2-pg16`; if Ryuk blocked, set `TESTCONTAINERS_RYUK_DISABLED=true` and clean up containers manually afterward.
- **Evidence:** `docs/evidence/S1/…-integration.txt`. **Flips item 7 → ✅.**

## Step 8 — verify-s0.sh execution (G0 automated checks 1–9)
- **Objective:** run the S0.2 harness (lint/type/imports/unit/frontend/compose-config/dev-up/health/alembic).
- **Prerequisites:** Steps 1–5; toolchain + pnpm.
- **Exact commands:**
  ```bash
  cd "$REPO"
  scripts/verify-s0.sh
  ```
- **Expected output:** checks 1–9 print PASS; evidence files land in `docs/evidence/S0/`.
- **Verification criteria:** the harness reaches "AUTOMATED S0.2 CHECKS PASSED"; four processes healthy; alembic idempotent.
- **Possible errors:** step 9 alembic fails on host DSN (`db` unresolvable from host — gotcha #1); frontend step needs `corepack enable`.
- **Recovery:** the harness's alembic step uses `with-env.sh` (sources `dev.env` = `db` host) — for host execution, temporarily set `dev.env` `SCANNER_DB_DSN` to the **localhost** form for this step, or run the migration via Step 6's container method and skip harness step 9. Re-run.
- **Evidence:** `docs/evidence/S0/…`.

## Step 9 — verify-s1.sh execution (S1 items 2–8 harness)
- **Objective:** run the S1 harness end-to-end: lint/type/imports/unit → migration+hypertable → integration → staging sequence (sync-symbols → BTCUSDT backfill → verify-continuity).
- **Prerequisites:** Steps 1–7; network to `api.binance.com`.
- **Exact commands:**
  ```bash
  cd "$REPO"
  # Ensure with-env.sh resolves a reachable DB. For a local run, either point
  # dev.env SCANNER_DB_DSN at localhost, OR run against the compose db from host.
  scripts/verify-s1.sh
  ```
- **Expected output:** steps 1–8 pass; "ALL S1 CHECKS PASSED"; evidence in `docs/evidence/S1/`. Backfill reports `quarantined=0`; verify-continuity `uncovered=0`.
- **Verification criteria:** exit 0; BTCUSDT H1 backfill inserts candles with zero continuity violations; compression measurable.
- **Possible errors:** Binance HTTP 451/403 (geo-block) or 429 (rate limit); host cannot resolve `db`; long backfill time.
- **Recovery:** geo-block → run from an EU host/VPN (matches TDR EU posture); 429 is self-throttled by the rate budget (wait); DSN → gotcha #1 fix. Start with a short range (`--start 2024-01-01`) to smoke-test before a 2-year run.
- **Evidence:** `docs/evidence/S1/…`. Contributes to items 3/8.

## Step 10 — GitHub repository setup
- **Objective:** a private GitHub repo with `main` protected; the codebase pushed.
- **Prerequisites:** `gh` CLI authenticated (`gh auth login`); repo is a git repo (`git init` if not — this working copy is not yet).
- **Exact commands:**
  ```bash
  cd "$REPO"
  git init && git add -A && git commit -m "chore(s0): monorepo foundation + S1 market-data pipeline"
  git branch -M main
  gh repo create <org>/crypto-scanner --private --source=. --remote=origin --push
  gh api -X PUT repos/<org>/crypto-scanner/branches/main/protection \
    -f 'required_status_checks[strict]=true' \
    -F 'required_status_checks[contexts][]=backend' \
    -F 'required_status_checks[contexts][]=frontend' \
    -F 'required_status_checks[contexts][]=security' \
    -F 'required_status_checks[contexts][]=docker' \
    -F 'enforce_admins=true' \
    -f 'required_pull_request_reviews[required_approving_review_count]=0' \
    -F 'restrictions=' -F 'allow_force_pushes=false'
  ```
- **Expected output:** repo created; `main` protected (PR-only, 4 required checks, no force-push).
- **Verification criteria:** `gh api repos/<org>/crypto-scanner/branches/main/protection` lists the 4 contexts; a direct push to `main` is rejected.
- **Possible errors:** gitleaks-worthy secrets in history (the `.gitignore` should already exclude `dev.env`/keys — verify none staged); solo-account can't require reviews (use `required_approving_review_count=0` + `enforce_admins`).
- **Recovery:** if a secret was committed, rotate + rewrite history before pushing; adjust protection payload for account tier.

## Step 11 — GitHub Actions CI configuration
- **Objective:** `ci.yml` runs green on a PR and **red-blocks-merge** (G0 criterion #2).
- **Prerequisites:** Step 10; `GITHUB_TOKEN` (default) with `packages: write` (already declared).
- **Exact commands:**
  ```bash
  # Prove green:
  git switch -c chore/ci-smoke && git commit --allow-empty -m "chore: ci smoke" && git push -u origin HEAD
  gh pr create --fill --base main
  gh pr checks --watch
  # Prove red-blocks-merge (deliberate lint error on a throwaway branch):
  git switch -c test/red-ci
  printf '\nimport os\n' >> backend/src/scanner/__init__.py   # unused import → ruff F401
  git commit -am "test: intentional lint failure" && git push -u origin HEAD
  gh pr create --fill --base main
  gh pr checks --watch     # backend job fails; merge is blocked
  # Clean up: close PR, delete branch, revert the edit.
  ```
- **Expected output:** green PR → all 4 checks pass, mergeable; red PR → `backend` fails, "Merging is blocked".
- **Verification criteria:** protected-branch merge button disabled while a required check is red; GHCR images pushed on `main`.
- **Possible errors:** `astral-sh/setup-uv` version mismatch; `pnpm audit`/`pip-audit` flags a real CVE (blocking by design); GHCR push denied (package visibility/permissions).
- **Recovery:** pin action versions; address or triage the CVE (bump dep in a PR — not this task); enable GHCR + `packages: write`.
- **Evidence:** screenshots/links → `docs/evidence/S0/ci-blocks.md`. **Satisfies G0 criterion #2.**

## Step 12 — Hetzner VPS provisioning
- **Objective:** provision + harden the staging host from `ops/terraform/staging/`.
- **Prerequisites:** `terraform` ≥1.6; Hetzner API token; admin SSH keypair; admin IP CIDR.
  - **⚠️ FIX FIRST (gotcha #4):** `cloud-init.yaml` disables root SSH but the key lands on `root` → lockout. Before apply, add a deploy user, e.g. in `cloud-init.yaml`:
    ```yaml
    users:
      - name: deploy
        groups: [sudo, docker]
        shell: /bin/bash
        sudo: "ALL=(ALL) NOPASSWD:ALL"
        ssh_authorized_keys:
          - <admin public key>
    ```
    and set `STAGING_USER=deploy` (Step 13). (Infra-config change; no application code.)
- **Exact commands:**
  ```bash
  cd "$REPO/ops/terraform/staging"
  export TF_VAR_hcloud_token=…  TF_VAR_admin_ssh_public_key="$(cat ~/.ssh/id_ed25519.pub)"
  export TF_VAR_admin_ip_cidrs='["<your.ip>/32"]'
  terraform init && terraform validate && terraform plan && terraform apply
  terraform output staging_ipv4
  ssh deploy@$(terraform output -raw staging_ipv4) 'docker --version && whoami'
  ```
- **Expected output:** one server created; firewall allows 80/443 + admin-IP SSH only; SSH as `deploy` works; docker installed.
- **Verification criteria:** `terraform output staging_ipv4` returns an IP; SSH succeeds; DB/Redis/process ports are NOT reachable externally.
- **Possible errors:** SSH lockout (gotcha #4 not applied); `hcloud` auth failure; server-type unavailable in the chosen location.
- **Recovery:** lockout → Hetzner Cloud Console → rescue system → add the user; auth → check token; server type → change `var.server_type`/`var.location`. Back up `terraform.tfstate` (encrypted).

## Step 13 — Staging deployment
- **Objective:** first successful `deploy-staging.yml` run; stack live behind Caddy (TLS), `/internal` edge-blocked.
- **Prerequisites:** Steps 10–12; DNS A record `staging.<domain>` → `staging_ipv4`; `staging.enc.env` re-encrypted (gotcha #5); GitHub `staging` environment + secrets.
- **Exact commands:**
  ```bash
  # Populate + encrypt staging env (real values, staging hosts/secrets):
  sops ops/env/staging.enc.env      # SCANNER_ENV=staging, DSNs (db/redis compose hosts),
                                     # generated DB password, SCANNER_SENTRY_DSN, SCANNER_PUBLIC_HOST
  # GitHub staging environment + secrets:
  gh api -X PUT repos/<org>/crypto-scanner/environments/staging
  gh secret set STAGING_HOST      --env staging --body "$(cd ops/terraform/staging && terraform output -raw staging_ipv4)"
  gh secret set STAGING_USER      --env staging --body "deploy"
  gh secret set STAGING_SSH_KEY   --env staging < ~/.ssh/id_ed25519
  gh secret set SOPS_AGE_KEY      --env staging < ~/.config/sops/age/keys.txt
  # Bootstrap the host repo + trigger deploy:
  ssh deploy@<host> 'sudo mkdir -p /opt/scanner && sudo chown deploy /opt/scanner && git clone <repo> /opt/scanner'
  git commit --allow-empty -m "chore: trigger staging deploy" && git push origin main   # ci → deploy-staging
  gh run watch
  ```
- **Expected output:** `ci` green → `deploy-staging` runs → images pulled, env decrypted, `compose up`, readiness gate: all four processes 200 ≤120s.
- **Verification criteria:**
  - `curl -I https://staging.<domain>` → 200 over valid ACME TLS + HSTS header.
  - `curl -sk https://staging.<domain>/internal/health/live` → **403** (edge block); via SSH tunnel to a process port → 200.
  - Grafana (SSH-tunnelled) shows 4 processes up + a release annotation; Loki returns `{service="api",env="staging"}` lines; a one-off Sentry test event arrives tagged `environment:staging`.
- **Possible errors:** readiness gate timeout (image/env issue); ACME failure (DNS not propagated / port 80 blocked); `sops -d` fails (wrong age key); Caddy can't resolve upstream (`api`).
- **Recovery:** gate timeout → `ssh` + `docker compose -f ops/compose/docker-compose.staging.yml logs`; ACME → confirm DNS + firewall 80/443; sops → verify `SOPS_AGE_KEY` matches `.sops.yaml` recipient; roll back by re-running the workflow on the previous SHA (≤5 min).
- **Evidence:** `docs/evidence/S0/staging-deploy.md` (+ the §9 checklist in `staging-provision.md`).

## Step 14 — Gate G0 certification
- **Objective:** record evidence for all four G0 criteria.
- **Prerequisites:** Steps 8, 11, 13.
- **Exact commands / evidence to capture:**
  ```bash
  # Criterion 1 — fresh-clone ≤15 min:
  time ( git clone <repo> /tmp/g0-fresh && cd /tmp/g0-fresh && scripts/bootstrap.sh && scripts/verify-clone.sh )
  # Criterion 2 — CI red-blocks-merge:   evidence from Step 11.
  # Criterion 3 — staging auto-deploy:   evidence from Step 13 (workflow green + readiness gate).
  # Criterion 4 — Grafana heartbeats:    screenshot of the ops/platform-heartbeat dashboard (4 up).
  ```
- **Expected output:** clone→green in ≤15 min; the four evidence artifacts exist.
- **Verification criteria (all four must hold):** ① fresh-clone ≤15 min; ② red required-check blocks merge; ③ `deploy-staging` green with readiness gate passed; ④ Grafana shows four heartbeats + release annotation.
- **Possible errors:** clone exceeds 15 min (slow deps → warm the uv/pnpm caches, re-time); a criterion lacks a recorded artifact.
- **Recovery:** address the failing criterion and re-capture; **G0 is signed only when all four have recorded evidence** (Constitution: no exceptions).
- **Evidence:** create `docs/evidence/S0/G0-CERTIFICATION.md` linking artifacts ①–④; mark **Gate G0 ✅**.

## Step 15 — Sprint S1 sign-off
- **Objective:** flip S1 checklist items 6, 7, 8 to ✅ with recorded evidence (B1–B5).
- **Prerequisites:** Steps 6, 7, 9, 13.
- **Exact commands:**
  ```bash
  # B1/B2 migration+hypertable → Step 6 evidence.
  # B3 integration → Step 7 evidence.
  # B4 staging backfill (2-year BTCUSDT) on the staging host:
  ssh deploy@<host> 'cd /opt/scanner && \
    scripts/with-env.sh docker compose -f ops/compose/docker-compose.staging.yml exec -T api \
      python -m scanner.runtime.cli sync-symbols'
  ssh deploy@<host> '… python -m scanner.runtime.cli backfill --symbol BTCUSDT --timeframe H1 --start 2023-01-01'
  ssh deploy@<host> '… python -m scanner.runtime.cli verify-continuity --symbol BTCUSDT --timeframe H1 --start 2023-01-01 --end 2025-01-01'
  # Compression ratio check:
  ssh deploy@<host> "… psql -U scanner -d scanner -c \"SELECT * FROM hypertable_compression_stats('market.candles');\""
  ```
- **Expected output:** sync upserts the USDT universe; backfill `quarantined=0`; verify-continuity `uncovered=0`; compression ≥10×.
- **Verification criteria:** zero continuity violations across M5–W1 for the tested series; hypertable compressed ≥10×; every B-item has an evidence file.
- **Possible errors:** Binance rate/geo limits on a 2-year pull (self-throttled; run per-TF); disk pressure from 2y × multiple TFs.
- **Recovery:** run TF-by-TF; provision a larger volume if disk-bound; re-run `verify-continuity` after any gap incidents (they are honest history, not failures).
- **Evidence:** update `docs/evidence/S1/CHECKLIST.md` — items 6/7/8 → ✅; write `docs/evidence/S1/S1-SIGNOFF.md`. **Sprint S1 ✅.**

---

## Global prerequisites checklist (gather before Step 1)
- [ ] Docker Desktop (WSL2 on Windows) · uv · Node ≥20 + corepack · git · gh · terraform ≥1.6 · sops · age
- [ ] Hetzner Cloud API token · admin SSH keypair · admin public IP CIDR
- [ ] A DNS zone for `staging.<domain>` / `app.<domain>`
- [ ] GitHub org/account with Actions + GHCR enabled
- [ ] EU network egress (Binance geo posture) for Steps 9/15

## Evidence map (what certifies what)
| Artifact | Certifies |
|---|---|
| `docs/evidence/S0/…` (verify-s0, ci-blocks, staging-deploy, **G0-CERTIFICATION.md**) | Gate G0 ①–④ |
| `docs/evidence/S1/…` (migration, integration, verify-s1, **S1-SIGNOFF.md**) | S1 items 6/7/8 (B1–B5) |

## Non-goals (explicit)
- No application-code changes. No Sprint S2 work. `deploy-prod.yml` stays inert until S22.
