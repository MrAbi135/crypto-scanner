<#
.SYNOPSIS
    Read the ingest watch's findings on the VM from Windows, or run it once.

.DESCRIPTION
    Twin of ingest_watch.sh, which runs on the host from cron every minute.
    By default this only READS: the last lines of ~/soak-logs/ingest_watch.alerts
    and of its run log. -RunOnce runs the checkout's ingest_watch.sh one time
    (the same thing cron does: read-only queries, writes only its own files).

    Unlike check_invariants.ps1 it uploads nothing. The VM checkout moves only
    by an exact-SHA pull (owner rule 2026-10-18); an uploaded copy would leave
    the tree dirty and the deploy script refuses a dirty tree.

.PARAMETER Lines
    How many trailing lines to show (default 30).

.PARAMETER KeyPath
    Path to the SSH private key. Only ever passed to `ssh -i`; never read.

.EXAMPLE
    ./ops/soak/ingest_watch.ps1

.EXAMPLE
    ./ops/soak/ingest_watch.ps1 -RunOnce
#>

[CmdletBinding()]
param(
    [int] $Lines = 30,
    [switch] $RunOnce,
    [string] $VmHost = 'ubuntu@141.148.205.213',
    [string] $KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-08-20.asc"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $KeyPath)) {
    Write-Error "SSH key not found at $KeyPath. Pass -KeyPath if it lives elsewhere."
    exit 2
}

$gitSsh = 'C:\Program Files\Git\usr\bin\ssh.exe'
$ssh = if (Test-Path -LiteralPath $gitSsh) { $gitSsh } else { 'ssh' }

if ($RunOnce) {
    & $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost 'cd ~/crypto-scanner && bash ops/soak/ingest_watch.sh; echo "exit $?"'
    exit $LASTEXITCODE
}

$read = @"
echo '== ingest_watch.alerts (last $Lines)'
tail -n $Lines ~/soak-logs/ingest_watch.alerts 2>/dev/null || echo '(no alerts file yet -- nothing found, or cron not installed)'
echo
echo '== ingest_watch.log (last 5)'
tail -n 5 ~/soak-logs/ingest_watch.log 2>/dev/null || echo '(no run log yet)'
echo
echo '== cron line'
crontab -l 2>/dev/null | grep ingest_watch || echo '(not in crontab)'
"@ -replace "`r`n", "`n"

& $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost $read
exit $LASTEXITCODE
