<#
.SYNOPSIS
  Unregisters the runner from GitHub and removes its container (and, with -DeleteVolume,
  its home volume with the stored credentials).

.DESCRIPTION
  Uses a removal token: pasted by you (default) or fetched with -UseGh via
  `gh api -X POST .../actions/runners/remove-token`. The token is never printed.

.EXAMPLE
  .\runner\remove.ps1 -UseGh -DeleteVolume
#>
[CmdletBinding()]
param(
    [string]$Repo = 'gagan046-dev/pipelinedoctor',
    [switch]$UseGh,
    [switch]$DeleteVolume
)

$ErrorActionPreference = 'Stop'
$composeFile = Join-Path $PSScriptRoot 'compose.yml'

# config.sh remove refuses to run while the listener is running.
docker compose -f $composeFile stop

$token = $null
if ($UseGh) {
    $token = gh api -X POST "repos/$Repo/actions/runners/remove-token" --jq .token
    if ($LASTEXITCODE -ne 0) { throw 'gh could not create a removal token' }
}
else {
    Write-Host "Open https://github.com/$Repo/settings/actions/runners, select pd-laptop,"
    Write-Host 'choose Remove and copy the token shown in the ./config.sh remove command.'
    $secure = Read-Host -AsSecureString 'Removal token'
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if ([string]::IsNullOrWhiteSpace($token)) { throw 'empty removal token' }

try {
    $env:RUNNER_TOKEN = $token.Trim()
    docker compose -f $composeFile run --rm -e RUNNER_TOKEN runner unregister
    if ($LASTEXITCODE -ne 0) { Write-Warning 'config.sh remove failed; delete the runner in the GitHub UI instead.' }
}
finally {
    Remove-Item Env:RUNNER_TOKEN -ErrorAction SilentlyContinue
    $token = $null
}

if ($DeleteVolume) {
    docker compose -f $composeFile down -v
}
else {
    docker compose -f $composeFile down
}
Write-Host 'Runner removed.'
