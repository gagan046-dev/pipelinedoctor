<#
.SYNOPSIS
  Runs the production blue-green flow on this laptop without GitHub: build the console and
  proxy images locally, then call deploy/scripts/bluegreen.sh through Git Bash.

.DESCRIPTION
  Same script, same compose file and same smoke test as the deploy.yml production job;
  only the images come from a local `docker build` instead of signed GHCR digests.
  The console ends up on http://127.0.0.1:8090.

.EXAMPLE
  .\deploy\local\deploy-local.ps1 -Version dev            # build + deploy to the idle colour
.EXAMPLE
  .\deploy\local\deploy-local.ps1 -Version dev2           # second deploy: watch it switch colours
.EXAMPLE
  .\deploy\local\deploy-local.ps1 -Version dev3 -ExpectVersion nope   # forced smoke failure -> automatic rollback
.EXAMPLE
  .\deploy\local\deploy-local.ps1 -Action rollback        # back to the previous (stopped) colour
.EXAMPLE
  .\deploy\local\deploy-local.ps1 -Action status
.EXAMPLE
  .\deploy\local\deploy-local.ps1 -Action down -RemoveVolumes
#>
[CmdletBinding()]
param(
    [ValidateSet('deploy', 'rollback', 'status', 'down')]
    [string]$Action = 'deploy',
    [string]$Version = 'dev',
    # Use an existing image instead of building console/ (e.g. ghcr.io/...-console:0.2.0).
    [string]$ConsoleImage = '',
    [string]$ProxyImage = '',
    # Pretend the build produced a different version: the smoke test then fails after the
    # switch and bluegreen.sh rolls back automatically. For learning only.
    [string]$ExpectVersion = '',
    [switch]$RemoveVolumes
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$composeFile = Join-Path $repo 'deploy\compose.prod.yml'

# C:\Windows\System32\bash.exe is WSL, not Git Bash; look for Git for Windows explicitly.
$bash = @(
    (Join-Path $env:ProgramFiles 'Git\bin\bash.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Git\bin\bash.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe')
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $bash) { throw 'Git Bash not found (install Git for Windows: winget install Git.Git)' }

# Windows PowerShell 5.1 turns every stderr line of a native program into an ErrorRecord when the
# caller redirects output (2>$null, *>&1, | Select-String). With 'Stop' that aborts on docker's
# harmless progress output, so native calls run under 'Continue' and are judged by exit code.
function Invoke-Native {
    param([scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command } finally { $ErrorActionPreference = $previous }
}

function Invoke-BlueGreen {
    param([string[]]$Arguments)
    $env:MSYS_NO_PATHCONV = '1'
    Push-Location $repo
    try {
        # Out-Host: otherwise the script's output would become this function's return value.
        Invoke-Native { & $bash 'deploy/scripts/bluegreen.sh' @Arguments | Out-Host }
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

switch ($Action) {
    'status' {
        exit (Invoke-BlueGreen @('status'))
    }
    'down' {
        $downArgs = @('compose', '-p', 'pd-prod', '-f', $composeFile, 'down')
        if ($RemoveVolumes) { $downArgs += '-v' }
        Invoke-Native { & docker @downArgs }
        exit $LASTEXITCODE
    }
    'rollback' {
        exit (Invoke-BlueGreen @('rollback'))
    }
    'deploy' {
        if (-not $ConsoleImage) {
            $ConsoleImage = "pipelinedoctor-console:$Version-local"
            Write-Host "Building $ConsoleImage from console/ ..."
            Invoke-Native { docker build -t $ConsoleImage --build-arg "VERSION=$Version" --build-arg 'GIT_SHA=local' (Join-Path $repo 'console') }
            if ($LASTEXITCODE -ne 0) { throw 'console image build failed' }
        }
        if (-not $ProxyImage) {
            $ProxyImage = 'pipelinedoctor-proxy:local'
            Write-Host "Building $ProxyImage from deploy/proxy/ ..."
            Invoke-Native { docker build -t $ProxyImage --build-arg "VERSION=$Version" (Join-Path $repo 'deploy\proxy') }
            if ($LASTEXITCODE -ne 0) { throw 'proxy image build failed' }
        }
        $expected = if ($ExpectVersion) { $ExpectVersion } else { $Version }
        $code = Invoke-BlueGreen @('deploy', '--version', $expected, '--console-image', $ConsoleImage, '--proxy-image', $ProxyImage)
        if ($code -eq 0) {
            Write-Host ''
            Write-Host 'Deployed. Open http://127.0.0.1:8090  (version: Invoke-RestMethod http://127.0.0.1:8090/version)'
        }
        else {
            Write-Warning "bluegreen.sh exited $code - see the outcome line above (production stays on the previous colour)."
        }
        exit $code
    }
}
