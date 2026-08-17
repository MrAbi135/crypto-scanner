# Developer Setup

Living setup document (S0.1 guide §25–§26). `scripts/bootstrap.sh` automates this
for every clone; this doc explains what it does and how to verify a clean setup.

## Prerequisites
| Tool | Version | Why |
|---|---|---|
| Git | ≥ 2.40 | version control |
| uv | latest stable | the only Python package tool (TDR §25); installs Python itself |
| Python | 3.12.x | installed **by uv**, not system (pinned in `backend/.python-version`) |
| Node.js | ≥ 20 LTS (corepack) | frontend toolchain |
| pnpm | via corepack | frontend package manager (TDR §25) |
| Docker + Compose v2 | current | dev stack (the stack itself lands in S0.2) |
| pre-commit | via uv tool | git hook runner |
| SOPS + age | current | secrets flow (§ secrets) |
| bash / POSIX shell | — | `scripts/` portability. **Windows: use WSL2.** |

> Windows note: the `scripts/` are POSIX shell. Run them under WSL2 (or Git Bash
> for the read-only checks). Docker Desktop provides Compose v2.

## First-time setup
```bash
# From the repository root:
scripts/bootstrap.sh          # checks tools, installs deps (uv sync / pnpm install), installs hooks
```

`bootstrap.sh` performs, in order:
1. Verifies required tools are present (fails with the missing tool named).
2. `backend/`: `uv python install 3.12` (respects `.python-version`), `uv sync --locked`.
3. `frontend/`: `corepack enable`, `pnpm install --frozen-lockfile`.
4. Installs pre-commit hooks (`pre-commit install` + commit-msg).
5. Copies `ops/env/.env.example` → `ops/env/dev.env` if absent (fill in real local values).

## Look at the chart (Sprint S13a)

```bash
scripts/chart.sh              # starts db + redis + api, then the chart on :5173
```

Then open **http://localhost:5173**.

The script checks that the context actually has candles before starting, because
a chart with no data and a chart of a quiet market look identical. If it is
empty it says so and prints the two commands that fill it. Override the context
it checks with `SYMBOL=ETHUSDT TIMEFRAME=M15 scripts/chart.sh`.

The dev frontend runs on the host by design — `ops/docker/frontend.Dockerfile`
is a staging/production artifact only (S0.2 §6.1) — so the chart is the one
thing not inside compose.

### Running CLI commands against the dev stack

```bash
scripts/cli.sh warmth                                    # which contexts can detect?
scripts/cli.sh sync-symbols                              # mirror the venue registry
scripts/cli.sh backfill --symbol BTCUSDT --timeframe H1 --start 2026-06-01
scripts/cli.sh engine run --symbol BTCUSDT --timeframe H1 \
  --start 2026-06-01 --end 2026-08-17
```

`cli.sh` loads `ops/env/dev.env`, rewrites the compose hostnames (`db`, `redis`)
to `localhost`, and runs inside the backend venv. All three are required and
none is obvious: without the rewrite every host-run command dies on
`getaddrinfo`, since those hostnames only resolve inside the compose network.

## Verify a clean setup (G0 evidence)
```bash
scripts/verify-clone.sh       # tools present, lockfiles install frozen, hooks run — all green
```

## Secrets (local)
Generate a developer age keypair once; the **private key stays local**:
```bash
age-keygen -o ~/.config/sops/age/keys.txt
# copy the public key line into .sops.yaml recipients (replaces the placeholder)
```

## Repository map
| Path | What |
|---|---|
| `backend/` | Python `scanner` distribution: domain, application, infrastructure, interfaces, runtime |
| `frontend/` | React 18 + TS SPA (UI from S13) |
| `ops/` | compose, Docker, Caddy, monitoring, Terraform, env catalog |
| `docs/governance/` | the nine frozen governance documents (read-only by convention) |
| `docs/adr/` | architecture decision records (immutable once merged) |
| `docs/runbooks/` | operational procedures |
| `scripts/` | repo lifecycle automation (bootstrap, verify, ADR scaffolding) |

## Verification

Sprint S0.2 verification (`scripts/verify-s0.sh` automates 1–9; 10–16 are manual
G0 evidence, recorded under `docs/evidence/S0/`):

| # | Check | Expected |
|---|---|---|
| 1 | `uv run ruff check && ruff format --check` | clean on the skeleton |
| 2 | `uv run mypy` | clean on the skeleton |
| 3 | `uv run lint-imports` | contracts pass |
| 4 | `uv run pytest -m "not integration"` | green; coverage floor enforced |
| 5 | `pnpm lint && pnpm typecheck && pnpm test && pnpm build` | green |
| 6 | `docker compose ... config -q` (dev + staging) | both compose files valid |
| 7 | `scripts/dev-up.sh` | db + redis + 4 processes healthy within 90s |
| 8 | `curl :8000..8003/internal/health/{live,ready}` + `/metrics` | live 200×4; ready 200×4 w/ detail; metrics render |
| 9 | `scripts/with-env.sh uv run alembic upgrade head` (×2) | baseline applies; re-run = no-op |
| 10 | Unset `SCANNER_DB_DSN`, start api | refuses boot, field-precise error, exit ≠ 0 |
| 11 | Stop `db`, hit `/ready` | 503 with `db: unreachable`; `live` stays 200 |
| 12 | Bad commit (secret + malformed msg) | rejected by gitleaks + commit-msg hooks |
| 13 | `dev-down.sh --wipe && dev-up.sh` | cold rebuild ≤ 15 min (G0 clock) |
| 14 | PR with a deliberate lint error | `ci.yml` blocks the PR on the failing required check |
| 15 | staging `config -q` + local run | validates; edge `curl https://localhost/internal/health/live` → **403** |
| 16 | `dev-up.sh --profile obs`, open Grafana | heartbeat dashboard shows 4 processes up; Loki returns JSON log lines |

> Without Docker/CI/staging, checks 6–16 are recorded as environment-blocked;
> checks 1–5 run fully with the local toolchain.
