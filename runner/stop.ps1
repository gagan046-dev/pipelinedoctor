<#
.SYNOPSIS
  Stops the pd-runner container. The registration is kept; start.ps1 brings it back.
  A stopped runner cannot pick up any job, which is the simplest way to shrink the
  self-hosted attack surface while you are not deploying.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$composeFile = Join-Path $PSScriptRoot 'compose.yml'

docker compose -f $composeFile stop
if ($LASTEXITCODE -ne 0) { throw 'could not stop pd-runner' }
Write-Host 'pd-runner stopped (GitHub will show it as offline).'
