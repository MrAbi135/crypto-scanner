<#
.SYNOPSIS
    Run ops/soak/shards_off.sh on the VM from Windows: two engine shards back to one engine.

.DESCRIPTION
    Thin twin of shards_off.sh. It runs the copy in the VM's checkout, never a
    working-tree copy: the way back must be the code that was deployed. Without
    -Switch it is a dry run that reads the stream's groups and prints where the
    single engine would resume, changing nothing.

    Exit code mirrors the remote script: 0 done (or dry run), 1 a check refused.

.PARAMETER Switch
    Do the switch. Requires -Reason. Resets T0.

.PARAMETER Reason
    Why one engine again; printed into the run's output.

.PARAMETER KeyPath
    Path to the SSH private key. Only ever passed to `ssh -i`; never read.

.EXAMPLE
    ./ops/soak/shards_off.ps1
.EXAMPLE
    ./ops/soak/shards_off.ps1 -Switch -Reason 'shard 1 crash-looping, owner approved'
#>

[CmdletBinding()]
param(
    [switch] $Switch,
    [string] $Reason = '',
    [string] $VmHost = 'ubuntu@141.148.205.213',
    [string] $KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-08-20.asc"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $KeyPath)) {
    Write-Error "SSH key not found at $KeyPath. Pass -KeyPath if it lives elsewhere."
    exit 2
}

if ($Switch -and [string]::IsNullOrWhiteSpace($Reason)) {
    Write-Error '-Switch needs -Reason: going back to one engine resets T0.'
    exit 2
}

$gitSsh = 'C:\Program Files\Git\usr\bin\ssh.exe'
$ssh = if (Test-Path -LiteralPath $gitSsh) { $gitSsh } else { 'ssh' }

if ($Switch) {
    # The reason travels base64-encoded so no quote in it can break the remote command line.
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Reason))
    $remote = "cd ~/crypto-scanner && CONFIRM=shards-off REASON=`"`$(echo $b64 | base64 -d)`" bash ops/soak/shards_off.sh"
} else {
    $remote = 'cd ~/crypto-scanner && bash ops/soak/shards_off.sh'
}

& $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost $remote
exit $LASTEXITCODE
