<#
.SYNOPSIS
  Creates or updates the repository ruleset "main-protection" from .github/rulesets/main.json.

.DESCRIPTION
  Rulesets are the modern form of branch protection: they protect main from deletion and
  force-pushes, require a pull request (squash only), and require the status checks
  ci-ok, eval-gate, secret-scan, workflow-policy, dependency-review and pr-title.
  "integration_id": 15368 pins each check to the GitHub Actions app, so nobody can satisfy
  a required check by posting a fake commit status with the same name.
  Apply it only AFTER the stage commits are pushed (stage commits go straight to main).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\apply-ruleset.ps1
#>
param(
    [string]$Repo = 'gagan046-dev/pipelinedoctor',
    [string]$RulesetFile = (Join-Path $PSScriptRoot '..\.github\rulesets\main.json')
)

$ErrorActionPreference = 'Stop'

$file = (Resolve-Path -LiteralPath $RulesetFile).Path
$name = (Get-Content -LiteralPath $file -Raw | ConvertFrom-Json).name

# Filter in PowerShell, not with --jq: PowerShell 5.1 mangles double quotes inside native arguments.
$rulesets = (gh api "repos/$Repo/rulesets" | Out-String) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not list rulesets' }
$id = ($rulesets | Where-Object { $_.name -eq $name } | Select-Object -First 1).id

if ([string]::IsNullOrWhiteSpace($id)) {
    Write-Host "Creating ruleset '$name'"
    gh api -X POST "repos/$Repo/rulesets" --input $file --jq '{id, name, enforcement}'
} else {
    Write-Host "Updating ruleset '$name' (id $id)"
    gh api -X PUT "repos/$Repo/rulesets/$id" --input $file --jq '{id, name, enforcement}'
}
if ($LASTEXITCODE -ne 0) { throw 'Applying the ruleset failed' }

Write-Host ''
Write-Host 'Rules now active on the default branch:'
gh api "repos/$Repo/rules/branches/main" --jq '.[].type'
