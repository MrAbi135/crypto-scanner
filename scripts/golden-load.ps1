# Load the golden datasets into the dev database so the chart can show them.
# PowerShell twin of scripts/golden-load.sh -- see scripts/cli.ps1 for why both.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root 'ops/env/dev.env'

if (-not (Test-Path $envFile)) { Write-Error "missing $envFile -- run scripts/bootstrap.sh" }

foreach ($line in Get-Content $envFile) {
    $trimmed = $line.Trim()
    if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
    $split = $trimmed.IndexOf('=')
    if ($split -lt 1) { continue }
    Set-Item -Path "env:$($trimmed.Substring(0, $split).Trim())" -Value $trimmed.Substring($split + 1).Trim()
}

# Compose hostnames do not resolve from the host -- see scripts/cli.ps1.
$env:SCANNER_DB_DSN = $env:SCANNER_DB_DSN -replace '@db:', '@localhost:'
$env:SCANNER_REDIS_URL = $env:SCANNER_REDIS_URL -replace '//redis:', '//localhost:'

$uvBin = Join-Path $HOME '.local\bin'
if (Test-Path $uvBin) { $env:PATH = "$uvBin;$env:PATH" }

Set-Location (Join-Path $root 'backend')

& uv run python tools_golden_to_db.py @args

exit $LASTEXITCODE
