<#
.SYNOPSIS
  Registers the containerised self-hosted runner "pd-laptop" (label pd-prod).

.DESCRIPTION
  1. Builds the pd-runner:local image (runner/Dockerfile).
  2. Gets a short-lived (1 hour) registration token:
       default : asks you to paste it (GitHub > Settings > Actions > Runners >
                 New self-hosted runner, copy the value after --token)
       -UseGh  : fetches it with `gh api -X POST .../actions/runners/registration-token`
     The token is never printed and never passed on a command line: it travels only in the
     RUNNER_TOKEN environment variable of a one-off `docker compose run` container.
  3. Runs `config.sh` inside that container. Credentials land in the pd-runner-home volume.

.EXAMPLE
  .\runner\register.ps1
.EXAMPLE
  .\runner\register.ps1 -UseGh
#>
[CmdletBinding()]
param(
    [string]$Repo = 'gagan046-dev/pipelinedoctor',
    [switch]$UseGh
)

$ErrorActionPreference = 'Stop'
$composeFile = Join-Path $PSScriptRoot 'compose.yml'

Write-Host 'Building pd-runner:local ...'
docker compose -f $composeFile build
if ($LASTEXITCODE -ne 0) { throw 'docker compose build failed' }

$token = $null
if ($UseGh) {
    $token = gh api -X POST "repos/$Repo/actions/runners/registration-token" --jq .token
    if ($LASTEXITCODE -ne 0) { throw 'gh could not create a registration token (admin access needed)' }
}
else {
    Write-Host "Open https://github.com/$Repo/settings/actions/runners/new and copy the token"
    Write-Host '(the value after --token in the ./config.sh example). It expires after 1 hour.'
    $secure = Read-Host -AsSecureString 'Registration token'
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if ([string]::IsNullOrWhiteSpace($token)) { throw 'empty registration token' }

try {
    $env:RUNNER_TOKEN = $token.Trim()
    # "-e RUNNER_TOKEN" without a value copies the variable from this process environment.
    docker compose -f $composeFile run --rm -e RUNNER_TOKEN runner register
    if ($LASTEXITCODE -ne 0) { throw 'runner registration failed' }
}
finally {
    Remove-Item Env:RUNNER_TOKEN -ErrorAction SilentlyContinue
    $token = $null
}

Write-Host ''
Write-Host 'Registered. Start it with .\runner\start.ps1 and check:'
Write-Host "  gh api repos/$Repo/actions/runners --jq '.runners[] | {name,status,labels:[.labels[].name]}'"
