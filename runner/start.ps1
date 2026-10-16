<#
.SYNOPSIS
  Starts the pd-runner container (registered earlier with register.ps1) and shows its status.
#>
[CmdletBinding()]
param([string]$Repo = 'gagan046-dev/pipelinedoctor')

$ErrorActionPreference = 'Stop'
$composeFile = Join-Path $PSScriptRoot 'compose.yml'

docker compose -f $composeFile up -d
if ($LASTEXITCODE -ne 0) { throw 'could not start pd-runner' }

Start-Sleep -Seconds 5
docker compose -f $composeFile logs --tail 20 runner
Write-Host ''
Write-Host 'Runners known to GitHub:'
gh api "repos/$Repo/actions/runners" --jq '.runners[] | {name,status,busy,labels:[.labels[].name]}'
Write-Host ''
Write-Host 'Stop the runner when you are not deploying: .\runner\stop.ps1'
