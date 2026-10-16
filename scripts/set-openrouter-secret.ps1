<#
.SYNOPSIS
  Stores the OpenRouter API key as the GitHub Actions secret OPENROUTER_API_KEY.

.DESCRIPTION
  Reads the key from a file OUTSIDE the repository, trims whitespace and pipes it to
  "gh secret set" through stdin. The key never appears on screen, in the command line
  (process list) or in PowerShell history. Afterwards only "gh secret list" is printed,
  which shows names and dates, never values.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\set-openrouter-secret.ps1
#>
param(
    [string]$KeyFile = 'C:\Users\gagan\OneDrive\Desktop\AIOPs\vitalops\secrets\openrouter_api_key.txt',
    [string]$Repo = 'gagan046-dev/pipelinedoctor'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "Key file not found: $KeyFile"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$keyFull = (Resolve-Path -LiteralPath $KeyFile).Path
if ($keyFull.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a key file inside the repository ($keyFull). Keep secrets outside the repo."
}

$key = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($key)) {
    throw "Key file is empty: $KeyFile"
}

# stdin instead of --body: a --body value would be visible in the process list.
$key | gh secret set OPENROUTER_API_KEY -R $Repo
$exit = $LASTEXITCODE
Remove-Variable key
if ($exit -ne 0) {
    throw "gh secret set failed with exit code $exit"
}

Write-Host "OPENROUTER_API_KEY updated. Current secrets of ${Repo}:"
gh secret list -R $Repo
