param(
    [ValidateSet('check', 'smoke', 'gui', 'factory')]
    [string]$Mode = 'smoke',
    [switch]$Headless,
    [switch]$Continuous,
    [switch]$Realtime,
    [ValidateSet('robotic', 'large', 'demo')]
    [string]$Layout = 'robotic',
    [double]$Duration = 120,
    [switch]$Minimal,
    [switch]$Record
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $projectRoot 'isaac-runtime\6.1.0'
if (-not (Test-Path (Join-Path $runtime '.factory-installed'))) {
    throw 'Install the runtime first: .\isaac\install.ps1'
}
Push-Location $runtime
try {
    switch ($Mode) {
        'check' { & '.\isaac-sim.compatibility_check.bat' '--no-window' '--/app/quitAfter=100' }
        'smoke' {
            & '.\python.bat' (Join-Path $PSScriptRoot 'smoke.py') '--headless' '--output' (Join-Path $projectRoot '.data\isaac\smoke.json')
        }
        'gui' { & '.\isaac-sim.bat' }
        'factory' {
            $factoryArgs = @((Join-Path $PSScriptRoot 'factory.py'), '--layout', $Layout, '--duration', $Duration)
            if ($Headless) { $factoryArgs += '--headless' }
            if ($Continuous) { $factoryArgs += '--continuous' }
            if ($Realtime) { $factoryArgs += '--realtime' }
            if ($Minimal) { $factoryArgs += '--minimal' }
            if ($Record) { $factoryArgs += '--record' }
            & '.\python.bat' @factoryArgs
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Isaac $Mode failed with exit code $LASTEXITCODE" }
    if ($Mode -eq 'smoke' -and -not (Test-Path (Join-Path $projectRoot '.data\isaac\smoke.json'))) {
        throw 'Isaac exited without completing the smoke check.'
    }
    if ($Mode -eq 'factory' -and -not (Test-Path (Join-Path $projectRoot '.data\isaac\factory\summary.json'))) {
        throw 'Isaac exited without completing the factory batch.'
    }
} finally {
    Pop-Location
}
