# Install the pinned Windows workstation runtime, separate from the application venv.
# Source: https://docs.isaacsim.omniverse.nvidia.com/latest/installation/download.html
$ErrorActionPreference = 'Stop'
$runtimeRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'isaac-runtime'
$archive = Join-Path $runtimeRoot 'isaac-sim-6.1.0.zip'
$destination = Join-Path $runtimeRoot '6.1.0'
$installed = Join-Path $destination '.factory-installed'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

if ((Test-Path $installed) -and (Test-Path (Join-Path $destination 'python.bat'))) {
    Write-Output "Runtime already present: $destination"
} else {
    & curl.exe --fail --location --retry 3 --continue-at - --output $archive 'https://downloads.isaacsim.nvidia.com/isaac-sim-standalone-6.1.0-windows-x86_64.zip'
    if ($LASTEXITCODE -ne 0) { throw 'Runtime download failed; rerun to resume.' }
    if ((Get-FileHash -LiteralPath $archive -Algorithm MD5).Hash -ne 'a07968e980072c9ca27b2166443e2d89') {
        throw 'Archive does not match the checksum published by NVIDIA. Do not extract it.'
    }
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    # Windows tar fails on some filenames in this archive; Python preserves ZIP names.
    & python -m zipfile -e $archive $destination
    if ($LASTEXITCODE -ne 0) { throw 'Runtime extraction failed.' }
    '6.1.0' | Set-Content -LiteralPath $installed
}
& (Join-Path $destination 'kit\python\python.exe') -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Factory Python dependency installation failed.' }
Write-Output "Installed Isaac Sim 6.1.0: $destination"
Write-Output 'Run .\isaac\run.ps1 factory -Headless, or omit -Headless to watch the batch.'
