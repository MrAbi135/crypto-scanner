# Run a scanner CLI command against the dev stack (Sprint S3b) — PowerShell.
#
# Windows-native twin of scripts/cli.sh. Both exist because the .sh scripts are
# what CI and Linux use, and PowerShell cannot execute them: `scripts/cli.sh`
# from a PowerShell prompt fails with "cannot run a document in the middle of a
# pipeline". A wrapper that shells out to Git Bash would work only where Git
# Bash happens to be installed, so this is a real port rather than a shim.
#
#   scripts/cli.ps1 warmth
#   scripts/cli.ps1 sync-symbols
#   scripts/cli.ps1 backfill --symbol BTCUSDT --timeframe H1 --start 2026-06-01

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root 'ops/env/dev.env'

if (-not (Test-Path $envFile)) {
    Write-Error "missing $envFile -- run scripts/bootstrap.sh"
}

foreach ($line in Get-Content $envFile) {
    $trimmed = $line.Trim()

    if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }

    $split = $trimmed.IndexOf('=')

    if ($split -lt 1) { continue }

    $name = $trimmed.Substring(0, $split).Trim()
    $value = $trimmed.Substring($split + 1).Trim()

    Set-Item -Path "env:$name" -Value $value
}

# dev.env addresses the services by their compose hostnames, which is correct
# for a container and unresolvable from the host. Every host-run command that
# touches Postgres or Redis dies on getaddrinfo without this.
$env:SCANNER_DB_DSN = $env:SCANNER_DB_DSN -replace '@db:', '@localhost:'
$env:SCANNER_REDIS_URL = $env:SCANNER_REDIS_URL -replace '//redis:', '//localhost:'

# uv installs here and is not always on a fresh PATH.
$uvBin = Join-Path $HOME '.local\bin'

if (Test-Path $uvBin) { $env:PATH = "$uvBin;$env:PATH" }

Set-Location (Join-Path $root 'backend')

& uv run python -m scanner.runtime.cli @args

exit $LASTEXITCODE
