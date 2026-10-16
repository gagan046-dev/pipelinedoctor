<#
.SYNOPSIS
  Breaks the demo on purpose on a fresh branch and opens a draft PR, so you can watch
  Demo CI fail and PipelineDoctor post its diagnosis on the PR.

.DESCRIPTION
  1. git fetch, new branch break/<mode>-<yyyyMMddHHmm> from origin/main
  2. python demo/break/apply.py <mode>   (a real defect, see demo/break/README.md)
  3. commit "test(demo): break <mode> on purpose", push, gh pr create --draft
  Your working tree must be clean. Afterwards: .\demo\scripts\cleanup-breaks.ps1

.EXAMPLE
  .\demo\scripts\break.ps1 -Mode test
.EXAMPLE
  .\demo\scripts\break.ps1 -Mode oom -NoPr     # push the branch only (push run, no PR comment)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('test', 'lint', 'type', 'dependency', 'missing-module', 'conflict', 'docker',
        'docker-base', 'timeout', 'oom', 'db', 'permission', 'secret', 'yaml')]
    [string]$Mode,
    [string]$Repo = 'gagan046-dev/pipelinedoctor',
    [switch]$NoPr
)

$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

$root = (git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { throw 'not inside a git repository' }
Push-Location $root
try {
    if (git status --porcelain) { throw 'working tree is not clean - commit or stash first' }

    $branch = "break/$Mode-$(Get-Date -Format 'yyyyMMddHHmm')"
    $title = "test(demo): break $Mode on purpose"

    Invoke-Checked 'git fetch' { git fetch origin main }
    Invoke-Checked 'git switch' { git switch -c $branch origin/main }
    Invoke-Checked 'apply.py' { python demo/break/apply.py $Mode }
    Invoke-Checked 'git add' { git add -A -- demo .github/workflows/demo-ci.yml }
    Invoke-Checked 'git commit' { git commit -m $title -m "Deliberate failure for the PipelineDoctor demo (mode: $Mode). Do not merge." }
    Invoke-Checked 'git push' { git push -u origin $branch }

    if (-not $NoPr) {
        $body = @"
Deliberate failure for the PipelineDoctor demo - **do not merge**.

* Mode: ``$Mode`` (see ``demo/break/README.md``)
* Expect: **Demo CI** fails, then the **Doctor** workflow posts one diagnosis comment here.

Clean up with ``.\demo\scripts\cleanup-breaks.ps1``.
"@
        Invoke-Checked 'gh pr create' { gh pr create --repo $Repo --draft --base main --head $branch --title $title --body $body }
    }

    Write-Host ''
    Write-Host 'Watch it:'
    Write-Host "  gh run list --repo $Repo --branch $branch"
    Write-Host "  gh run watch --repo $Repo `$(gh run list --repo $Repo --branch $branch --workflow demo-ci.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
    Write-Host "  gh run list --repo $Repo --workflow doctor.yml --limit 3"
    if (-not $NoPr) { Write-Host "  gh pr view --repo $Repo $branch --web" }
    Write-Host "You are now on $branch; go back with: git switch main"
}
finally {
    Pop-Location
}
