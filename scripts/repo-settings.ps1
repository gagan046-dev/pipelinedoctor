<#
.SYNOPSIS
  Applies the repository settings PipelineDoctor relies on (all through the REST API with gh).

.DESCRIPTION
  - GITHUB_TOKEN defaults to read-only; workflows ask for more per job.
  - GITHUB_TOKEN may open PRs (release-please needs this).
  - Fork PRs from ANY external contributor need approval before their workflows run.
  - Squash merges only, commit title = PR title (Conventional Commits -> release-please).
  - Secret scanning + push protection, private vulnerability reporting.
  - Dependabot alerts + Dependabot security updates (version updates live in .github/dependabot.yml).
  Safe to run repeatedly (every call is idempotent).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\repo-settings.ps1
#>
param(
    [string]$Repo = 'gagan046-dev/pipelinedoctor'
)

$ErrorActionPreference = 'Stop'

function Invoke-Gh {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GhArgs)
    & gh @GhArgs
    if ($LASTEXITCODE -ne 0) { throw "gh $($GhArgs -join ' ') failed (exit $LASTEXITCODE)" }
}

function Invoke-GhJson {
    param([string]$Method, [string]$Path, [hashtable]$Body)
    $tmp = New-TemporaryFile
    try {
        # ASCII is enough here and avoids the UTF-8 BOM that PowerShell 5.1 would write.
        $Body | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $tmp -Encoding Ascii
        Invoke-Gh api -X $Method $Path --input $tmp.FullName --silent
    } finally {
        Remove-Item -LiteralPath $tmp -Force
    }
}

Write-Host '1/5 Workflow token: read-only by default, may create PRs'
Invoke-GhJson -Method PUT -Path "repos/$Repo/actions/permissions/workflow" -Body @{
    default_workflow_permissions     = 'read'
    can_approve_pull_request_reviews = $true
}

Write-Host '2/5 Fork PR workflows need approval for all external contributors'
Invoke-GhJson -Method PUT -Path "repos/$Repo/actions/permissions/fork-pr-contributor-approval" -Body @{
    approval_policy = 'all_external_contributors'
}

Write-Host '3/5 Merge settings + secret scanning'
Invoke-GhJson -Method PATCH -Path "repos/$Repo" -Body @{
    allow_squash_merge         = $true
    allow_merge_commit         = $false
    allow_rebase_merge         = $false
    squash_merge_commit_title  = 'PR_TITLE'
    squash_merge_commit_message = 'PR_BODY'
    delete_branch_on_merge     = $true
    security_and_analysis      = @{
        secret_scanning                 = @{ status = 'enabled' }
        secret_scanning_push_protection = @{ status = 'enabled' }
    }
}

Write-Host '4/5 Private vulnerability reporting'
Invoke-Gh api -X PUT "repos/$Repo/private-vulnerability-reporting" --silent

Write-Host '5/5 Dependabot alerts and security updates'
Invoke-Gh api -X PUT "repos/$Repo/vulnerability-alerts" --silent
Invoke-Gh api -X PUT "repos/$Repo/automated-security-fixes" --silent

Write-Host ''
Write-Host 'Verify:'
Invoke-Gh api "repos/$Repo/actions/permissions/workflow"
Invoke-Gh api "repos/$Repo" --jq '{squash: .allow_squash_merge, merge: .allow_merge_commit, rebase: .allow_rebase_merge, title: .squash_merge_commit_title, secret_scanning: .security_and_analysis.secret_scanning.status, push_protection: .security_and_analysis.secret_scanning_push_protection.status}'
