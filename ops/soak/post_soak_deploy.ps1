<#
.SYNOPSIS
    Run the soak-end deploy sequence on the VM from Windows.

.DESCRIPTION
    Thin twin of post_soak_deploy.sh, in the same shape as
    check_invariants.ps1: upload the working-tree copy, syntax-check it on the
    host, run it there. The checks live in the bash script because they
    interrogate docker and the database, which live on the VM.

    The script refuses to run before the engine container has 72 hours up,
    refuses a dirty tree, and verifies every fix against the RUNNING
    containers -- so running this early or twice is safe: it stops at the
    first precondition instead of half-deploying.

    Exit code mirrors the remote script: 0 deployed-and-verified, 1 a check
    refused, 2 the run itself could not happen.

.PARAMETER KeyPath
    Path to the SSH private key. Only ever passed to `ssh -i`; never read.

.EXAMPLE
    ./ops/soak/post_soak_deploy.ps1
#>

[CmdletBinding()]
param(
    [string] $VmHost = 'ubuntu@141.148.205.213',
    [string] $KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-08-20.asc",
    [switch] $NoUpload
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $KeyPath)) {
    Write-Error "SSH key not found at $KeyPath. Pass -KeyPath if it lives elsewhere."
    exit 2
}

$gitSsh = 'C:\Program Files\Git\usr\bin\ssh.exe'
$gitScp = 'C:\Program Files\Git\usr\bin\scp.exe'

if ((Test-Path -LiteralPath $gitSsh) -and (Test-Path -LiteralPath $gitScp)) {
    $ssh = $gitSsh
    $scp = $gitScp
} else {
    $ssh = 'ssh'
    $scp = 'scp'
}

if (-not $NoUpload) {
    $local = Join-Path (Split-Path -Parent $PSCommandPath) 'post_soak_deploy.sh'

    if (-not (Test-Path -LiteralPath $local)) {
        Write-Error "missing $local"
        exit 2
    }

    & $scp -i $KeyPath -o StrictHostKeyChecking=no $local "${VmHost}:/tmp/post_soak_deploy.sh" | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Write-Error 'scp failed'
        exit 2
    }

    $install = @'
cd ~/crypto-scanner || exit 2
cp /tmp/post_soak_deploy.sh ops/soak/
sed -i 's/\r$//' ops/soak/post_soak_deploy.sh
chmod +x ops/soak/post_soak_deploy.sh
bash -n ops/soak/post_soak_deploy.sh
'@ -replace "`r`n", "`n"

    & $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost $install

    if ($LASTEXITCODE -ne 0) {
        Write-Error 'install or syntax check failed on the host'
        exit 2
    }
}

& $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost 'bash ~/crypto-scanner/ops/soak/post_soak_deploy.sh'
$remote = $LASTEXITCODE

Write-Host ''

switch ($remote) {
    0 { Write-Host 'deployed and verified against the running containers' -ForegroundColor Green }
    1 { Write-Host 'a precondition or verification refused - read the !! line above' -ForegroundColor Yellow }
    default { Write-Host "the run did not complete (exit $remote)" -ForegroundColor Red }
}

exit $remote
