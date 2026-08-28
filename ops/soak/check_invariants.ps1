<#
.SYNOPSIS
    Run the soak correctness invariants on the staging VM from Windows.

.DESCRIPTION
    The checks themselves live on the VM in `check_invariants.sh`, next to the
    database and Redis they interrogate. This is the twin that lets them be run
    from a PowerShell prompt without opening a session by hand, because the
    developer works on Windows and a check that only runs from someone else's
    shell is a check that does not get run.

    It uploads the current working-tree copies first, so editing the scripts
    locally and running this is one step rather than three. Pass -NoUpload to
    run whatever is already on the host.

    Exit code mirrors the remote script: 0 when every invariant is clean,
    1 when any fired, 2 when the run itself could not happen.

.PARAMETER KeyPath
    Path to the SSH private key. Only ever passed to `ssh -i`; never read.

.EXAMPLE
    ./ops/soak/check_invariants.ps1

.EXAMPLE
    ./ops/soak/check_invariants.ps1 -NoUpload
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

# Windows' own OpenSSH refuses a key whose ACL grants anything to another
# account -- "bad permissions", then "Permission denied (publickey)". A key
# that arrived through a browser download usually inherits exactly such an ACL.
#
# Git for Windows ships an OpenSSH that does not enforce it, so prefer that
# pair when it is installed. Tightening the ACL is the alternative, and it is
# the developer's call to make on their own credential rather than this
# script's:
#
#   icacls "<key>" /inheritance:r /grant:r "$($env:USERNAME):(R)"
#
# Either way the key is only ever handed to ssh as a path.
$gitSsh = 'C:\Program Files\Git\usr\bin\ssh.exe'
$gitScp = 'C:\Program Files\Git\usr\bin\scp.exe'

if ((Test-Path -LiteralPath $gitSsh) -and (Test-Path -LiteralPath $gitScp)) {
    $ssh = $gitSsh
    $scp = $gitScp
} else {
    $ssh = 'ssh'
    $scp = 'scp'
}

$here = Split-Path -Parent $PSCommandPath
$files = @('invariants.sql', 'check_invariants.sh', 'leg_invariant.py')

if (-not $NoUpload) {
    Write-Host 'uploading working-tree copies...' -ForegroundColor DarkGray

    foreach ($file in $files) {
        $local = Join-Path $here $file

        if (-not (Test-Path -LiteralPath $local)) {
            Write-Error "missing $local"
            exit 2
        }

        & $scp -i $KeyPath -o StrictHostKeyChecking=no $local "${VmHost}:/tmp/$file" | Out-Null

        if ($LASTEXITCODE -ne 0) {
            Write-Error "scp failed for $file (using $scp)"
            exit 2
        }
    }

    # CRLF from a Windows checkout makes bash reject the script with an error
    # that names a line having nothing wrong with it, so strip it on arrival
    # rather than trusting git's autocrlf to have done it.
    $install = @'
cd ~/crypto-scanner || exit 2
cp /tmp/invariants.sql /tmp/check_invariants.sh /tmp/leg_invariant.py ops/soak/
sed -i 's/\r$//' ops/soak/check_invariants.sh ops/soak/acknowledged.txt
$//' ops/soak/check_invariants.sh ops/soak/acknowledged.txt
chmod +x ops/soak/check_invariants.sh
bash -n ops/soak/check_invariants.sh
'@ -replace "`r`n", "`n"

    & $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost $install

    if ($LASTEXITCODE -ne 0) {
        Write-Error 'install or syntax check failed on the host'
        exit 2
    }
}

& $ssh -i $KeyPath -o StrictHostKeyChecking=no $VmHost 'bash ~/crypto-scanner/ops/soak/check_invariants.sh'
$remote = $LASTEXITCODE

Write-Host ''

switch ($remote) {
    0 { Write-Host 'all invariants clean' -ForegroundColor Green }
    1 { Write-Host 'invariants FIRED - read the !! lines above' -ForegroundColor Yellow }
    default { Write-Host "the run did not complete (exit $remote)" -ForegroundColor Red }
}

exit $remote
