# Runbook — Look at the doctrine on a chart

## Purpose

S13a's expected output is one sentence: *"The developer opens a browser, picks
BTCUSDT H1, and looks at their own doctrine on a live chart."* This is how.

Until 2026-08-28 there was no way to do it. The soak host ran the API and the
three engine processes and nothing that served the SPA, so every overlay built
in S13a — swings, pools, sweeps, zones, and the evidence panel — existed only
in tests.

## Why a tunnel and not a URL

The host firewall accepts port 22 and rejects everything else. The API's
published `8000` has never been reachable from outside either; it is used
through `docker exec` and SSH.

Opening a port would mean changing the host firewall **and** the Oracle
security list. That is a security change to a machine on the public internet,
and the console step belongs to the developer in any case.

It is also unnecessary, and the tunnel is the better answer rather than the
merely available one. The sign-in form posts a password. On an IP-only box
without a domain the alternatives are plain HTTP across the internet, or a
self-signed certificate to click past on every visit. SSH gives real encryption
with neither.

So Caddy binds `127.0.0.1:8080` on the host. Nothing outside the machine can
reach it, including anyone who finds the IP.

## Start it

Once per host — it builds the SPA, which takes a few minutes on the ARM box:

```bash
cd ~/crypto-scanner
DC="docker compose -f ops/compose/docker-compose.dev.yml"
$DC --profile ui build frontend
$DC --profile ui up -d --no-deps frontend caddy
```

`--no-deps` matters. Without it compose brings up everything the two services
declare a dependency on, which includes `api` — and during a soak an
unnecessary restart is the one thing to avoid. The engine, ingest and worker
are not touched either way; check anyway:

```bash
docker inspect -f "{{.State.StartedAt}} restarts={{.RestartCount}}" scanner-dev-engine-1
```

That timestamp must be the one the soak started at.

## Reach it

From the developer's machine, in its own terminal — it stays open:

```bash
ssh -i "<key path>" -L 8080:127.0.0.1:8080 ubuntu@141.148.205.213
```

Then open **http://localhost:8080**.

Sign in with an account created by the first-account step in
`deploy-p1b.md`. There is no registration screen: §18.1's register row is not
implemented and accounts are made on the host.

## What you should see

A candlestick chart for BTCUSDT H1, with four overlays:

| overlay | shape |
|---|---|
| swings | dots on the price — large for external, small and faded for internal |
| pools | horizontal lines at the resting level |
| sweeps | a vertical reach from the level to the penetration; dashed if reclaimed |
| zones | shaded bands running from creation to now |

Click or tab to any of them and the panel underneath shows what the engine
recorded — its own columns, then the evidence blob as stored.

## What it cannot do yet

* **A reload signs you out.** The access token is held in memory and the
  refresh cookie is not yet exchanged on load. One sign-in per tab.
* **No live updates.** The screen loads once per symbol/timeframe change.
  §19's websocket is a later sprint.
* **No golden case is written yet.** S13a's DoD asks for the first disagreement
  between the chart and the developer's reading of the SLS to be written up as
  a golden case. That is the point of the instrument, and it needs a person
  looking at it.
