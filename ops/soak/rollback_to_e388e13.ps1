<#
.SYNOPSIS
    Run the rollback to e388e13 on the VM from Windows. Dry run unless -Execute.

.DESCRIPTION
    Thin twin of rollback_to_e388e13.sh. It runs the copy already in the VM's
    checkout (no upload: an upload would dirty the tree the script refuses to
    check out over). Without -Execute the remote script runs only its
    read-only preconditions and prints every command it would run.

    Exit code mirrors the remote script: 0 rolled back and verified (or a clean
    dry run), 1 a check refused, 2 the run itself could not happen.

.PARAMETER Execute
    Really roll back. Requires -Reason, which goes into ~/soak-logs/rollback.log.

.PARAMETER KeyPath
    Path to the SSH private key. Only ever passed to `ssh -i`; never read.

.EXAMPLE
    ./ops/soak/rollback_to_e388e13.ps1
    ./ops/soak/rollback_to_e388e13.ps1 -Execute -Reason 'owner: v9 OBs wrong on BTCUSDT H1'
#>

[CmdletBinding()]
param(
    [switch] $Execute,
    [string] $Reason = '',
    [string] $VmHost = 'ubuntu@141.148.205.213',
    [string] $KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-08-20.asc"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $KeyPath)) {
    Write-Error "SSH key not found at $KeyPath. Pass -KeyPath if it lives elsewhere."
    exit 2
}

if ($Execute -and -not $Reason) {
    Write-Error '-Execute needs -Reason: a rollback interrupts the soak and says why.'
    exit 2
}

$gitSsh = 'C:\Program Files\Git\usr\bin\ssh.exe'
$ssh = if (Test-Path -LiteralPath $gitSsh) { $gitSsh } else { 'ssh' }

$remote = 'bash ~/crypto-scanner/ops/soak/rollback_to_e388e13.sh'

if ($Execute) {
    # Single quotes inside the reason would end the remote quoting; refuse them.
    if ($Reason.Contains("'")) {
        Write-Error 'the reason may not contain a single quote'
        exit 2
    }

    $remote = "CONFIRM=rollback-e388e13 RESTART_REASON='$Reason' $remote"
}

& $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost $remote
$code = $LASTEXITCODE

Write-Host ''

switch ($code) {
    0 { Write-Host ($(if ($Execute) { 'rolled back and verified' } else { 'dry run clean - nothing changed' })) -ForegroundColor Green }
    1 { Write-Host 'a precondition or verification refused - read the !! line above' -ForegroundColor Yellow }
    default { Write-Host "the run did not complete (exit $code)" -ForegroundColor Red }
}

exit $code
