<#
.SYNOPSIS
  Closes the draft PRs opened by break.ps1 and deletes every break/* branch (remote and local).

.EXAMPLE
  .\demo\scripts\cleanup-breaks.ps1 -WhatIf     # show what would be removed
.EXAMPLE
  .\demo\scripts\cleanup-breaks.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$Repo = 'gagan046-dev/pipelinedoctor')

$ErrorActionPreference = 'Stop'

$prs = gh pr list --repo $Repo --state open --limit 100 --json number,headRefName `
    --jq '.[] | select(.headRefName | startswith("break/")) | "\(.number) \(.headRefName)"'
if ($LASTEXITCODE -ne 0) { throw 'gh pr list failed' }

foreach ($line in @($prs | Where-Object { $_ })) {
    $number, $branch = $line -split ' ', 2
    if ($PSCmdlet.ShouldProcess("PR #$number ($branch)", 'close and delete branch')) {
        gh pr close $number --repo $Repo --delete-branch --comment 'Demo break cleaned up by cleanup-breaks.ps1.'
    }
}

# Branches pushed with -NoPr (or whose PR was closed by hand) still exist on the remote.
$remote = git ls-remote --heads origin 'refs/heads/break/*' | ForEach-Object { ($_ -split "`t")[1] -replace '^refs/heads/', '' }
foreach ($branch in @($remote | Where-Object { $_ })) {
    if ($PSCmdlet.ShouldProcess("origin/$branch", 'delete remote branch')) {
        git push origin --delete $branch
    }
}

$current = git rev-parse --abbrev-ref HEAD
$local = git branch --list 'break/*' --format '%(refname:short)'
foreach ($branch in @($local | Where-Object { $_ })) {
    if ($branch -eq $current) {
        Write-Warning "skipping $branch (checked out) - run: git switch main"
        continue
    }
    if ($PSCmdlet.ShouldProcess($branch, 'delete local branch')) {
        git branch -D $branch
    }
}
Write-Host 'Done.'
