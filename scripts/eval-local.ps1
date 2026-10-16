<#
.SYNOPSIS
  Run the PipelineDoctor evals locally (Windows PowerShell 5.1).

.DESCRIPTION
  Points OPENROUTER_API_KEY_FILE at the key file OUTSIDE the repository for this run only, runs
  `pipelinedoctor eval run` and `eval gate`, then removes the variable again. The key itself is
  never read, printed or copied by this script.

.EXAMPLE
  .\scripts\eval-local.ps1                      # PR subset, live LLM, cache reused
  .\scripts\eval-local.ps1 -Subset all -Judge   # nightly-like run (uses more requests)
  .\scripts\eval-local.ps1 -Llm rules           # offline baseline, no key needed
#>
param(
    [ValidateSet("pr", "all")] [string] $Subset = "pr",
    [ValidateSet("live", "cache-only", "rules")] [string] $Llm = "live",
    [switch] $Judge,
    [switch] $NoCacheRead,
    [int] $MaxRequests = 0,
    [string] $KeyFile = "C:\Users\gagan\OneDrive\Desktop\AIOPs\vitalops\secrets\openrouter_api_key.txt"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if ($Llm -eq "live") {
    if (-not (Test-Path $KeyFile)) {
        Write-Warning "Key file not found: $KeyFile. Falling back to -Llm cache-only."
        $Llm = "cache-only"
    } elseif ((Resolve-Path $KeyFile).Path.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to use a key file inside the repository: $KeyFile"
    }
}

$runArgs = @("run", "--frozen", "pipelinedoctor", "eval", "run", "--subset", $Subset, "--llm", $Llm,
    "--output", "eval-results/result.json")
if ($Judge) { $runArgs += "--judge" }
if ($NoCacheRead) { $runArgs += "--no-cache-read" }
if ($MaxRequests -gt 0) { $runArgs += @("--max-requests", "$MaxRequests") }
$mode = if ($Subset -eq "pr") { "pr" } else { "nightly" }

try {
    if ($Llm -eq "live") { $env:OPENROUTER_API_KEY_FILE = $KeyFile }
    & uv @runArgs
    if ($LASTEXITCODE -ne 0) { throw "eval run failed with exit code $LASTEXITCODE" }
    & uv run --frozen pipelinedoctor eval gate --result eval-results/result.json `
        --thresholds evals/thresholds.yaml --mode $mode --markdown-output eval-results/gate.md
    $gateExit = $LASTEXITCODE
} finally {
    Remove-Item Env:OPENROUTER_API_KEY_FILE -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Result:     eval-results/result.json"
Write-Host "Score table: eval-results/gate.md"
exit $gateExit
