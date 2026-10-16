<#
.SYNOPSIS
  Creates the GitHub Environments "staging" and "production".

.DESCRIPTION
  staging    : no reviewers, deployable only from main.
  production : required reviewer gagan046-dev, deployable only from main and v* tags.
               prevent_self_review is false because a solo learner must approve their own
               deployments (in a team you would set it to true).
  Environments are what make GitHub record Deployments (used for DORA metrics) and what
  gate the self-hosted runner behind a manual approval.
  Safe to run repeatedly.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-environments.ps1
#>
param(
    [string]$Repo = 'gagan046-dev/pipelinedoctor',
    [string]$Reviewer = 'gagan046-dev'
)

$ErrorActionPreference = 'Stop'

function Invoke-Gh {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GhArgs)
    $out = & gh @GhArgs
    if ($LASTEXITCODE -ne 0) { throw "gh $($GhArgs -join ' ') failed (exit $LASTEXITCODE)" }
    return $out
}

function Invoke-GhJson {
    param([string]$Method, [string]$Path, [hashtable]$Body)
    $tmp = New-TemporaryFile
    try {
        $Body | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $tmp -Encoding Ascii
        Invoke-Gh api -X $Method $Path --input $tmp.FullName --silent | Out-Null
    } finally {
        Remove-Item -LiteralPath $tmp -Force
    }
}

function Set-BranchPolicies {
    param([string]$Environment, [object[]]$Policies)
    $json = Invoke-Gh api "repos/$Repo/environments/$Environment/deployment-branch-policies"
    $existing = @(($json | Out-String | ConvertFrom-Json).branch_policies | ForEach-Object { "$($_.type):$($_.name)" })
    foreach ($p in $Policies) {
        $id = "$($p.type):$($p.name)"
        if ($existing -contains $id) {
            Write-Host "   policy $id already present"
        } else {
            Invoke-GhJson -Method POST -Path "repos/$Repo/environments/$Environment/deployment-branch-policies" -Body $p
            Write-Host "   policy $id added"
        }
    }
}

$reviewerId = [int64](Invoke-Gh api "users/$Reviewer" --jq .id)
Write-Host "Reviewer $Reviewer has id $reviewerId"

Write-Host 'staging'
Invoke-GhJson -Method PUT -Path "repos/$Repo/environments/staging" -Body @{
    wait_timer               = 0
    deployment_branch_policy = @{ protected_branches = $false; custom_branch_policies = $true }
}
# Same refs as production: production `needs: staging`, so a v* tag dispatch must pass staging too.
Set-BranchPolicies -Environment staging -Policies @(
    @{ name = 'main'; type = 'branch' },
    @{ name = 'v*'; type = 'tag' }
)

Write-Host 'production'
Invoke-GhJson -Method PUT -Path "repos/$Repo/environments/production" -Body @{
    wait_timer               = 0
    prevent_self_review      = $false
    reviewers                = @(@{ type = 'User'; id = $reviewerId })
    deployment_branch_policy = @{ protected_branches = $false; custom_branch_policies = $true }
}
Set-BranchPolicies -Environment production -Policies @(
    @{ name = 'main'; type = 'branch' },
    @{ name = 'v*'; type = 'tag' }
)

Write-Host ''
Write-Host 'Verify:'
gh api "repos/$Repo/environments" --jq '.environments[] | {name, rules: [.protection_rules[].type]}'
