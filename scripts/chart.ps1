# Open the chart (Sprint S13a) — PowerShell.
#
# Windows-native twin of scripts/chart.sh; see scripts/cli.ps1 for why both
# exist. The dev frontend is host-run by design (S0.2 §6.1), so this starts the
# backend in Docker and Vite on the host.
#
#   scripts/chart.ps1
#   $env:TIMEFRAME='M15'; scripts/chart.ps1

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'ops/compose/docker-compose.dev.yml'

$symbol = if ($env:SYMBOL) { $env:SYMBOL } else { 'BTCUSDT' }
$timeframe = if ($env:TIMEFRAME) { $env:TIMEFRAME } else { 'H1' }

Write-Host '1/4  starting db, redis and the api...'

# No --build: compose builds the image if it is missing, and rebuilding on every
# chart launch turns a two-second start into a two-minute one.
docker compose -f $compose up -d db redis api | Out-Null

Write-Host '2/4  waiting for the api to answer...'

$ready = $false

foreach ($attempt in 1..30) {
    try {
        Invoke-RestMethod 'http://localhost:8000/internal/health/ready' -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $ready) {
    docker compose -f $compose logs --tail 30 api
    Write-Error 'the api never became ready'
}

Write-Host "3/4  checking $symbol $timeframe has candles..."

$url = "http://localhost:8000/api/v1/market/candles?symbol_id=$symbol&timeframe=$timeframe&limit=400"
$count = (Invoke-RestMethod $url).data.Count

# A chart with no candles and a chart of a market where nothing happened look
# identical. Saying so costs one line and saves the confusion entirely.
if ($count -eq 0) {
    Write-Host ''
    Write-Host "  No candles stored for $symbol $timeframe, so the chart would be blank."
    Write-Host '  Fill it first:'
    Write-Host ''
    Write-Host '    scripts/cli.ps1 sync-symbols'
    Write-Host "    scripts/cli.ps1 backfill --symbol $symbol --timeframe $timeframe --start 2026-06-01"
    Write-Host ''
    Write-Host '  Then check what is ready:  scripts/cli.ps1 warmth'
    Write-Host ''
    exit 1
}

Write-Host "     $count candles ready."
Write-Host '4/4  starting the chart on http://localhost:5173 (ctrl-c to stop)'
Write-Host ''

Set-Location (Join-Path $root 'frontend')

& pnpm dev --port 5173
