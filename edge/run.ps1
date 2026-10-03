param(
    [ValidateSet('setup', 'brain', 'enroll', 'agent', 'inspect', 'status')]
    [string]$Mode = 'agent',
    [string]$Config = '.data/edge/config.json',
    [string]$AgentId = 'factory-edge',
    [string[]]$Area = @(),
    [string]$Journal = '.data/edge/source'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    # Same local database settings as the existing compose/API scaffold.
    if (Test-Path -LiteralPath '.env') {
        foreach ($line in Get-Content -LiteralPath '.env') {
            if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
                [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"', "'"), 'Process')
            }
        }
    }
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    $operatorFile = Join-Path $projectRoot '.data/edge/operator.token'
    if ($Mode -eq 'setup') {
        New-Item -ItemType Directory -Force '.data/edge' | Out-Null
        if (-not (Test-Path -LiteralPath $operatorFile)) {
            $token = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
            [IO.File]::WriteAllText($operatorFile, $token)
        }
        & $python -m factory_intelligence.edge.cli init-db
        if ($LASTEXITCODE -ne 0) { throw 'Database setup failed. Start the project PostgreSQL service.' }
        if (-not (Test-Path -LiteralPath $Config)) {
            $arguments = @('init', '--config', $Config, '--agent-id', $AgentId, '--journal', $Journal)
            foreach ($prefix in $Area) { $arguments += @('--area', $prefix) }
            & $python -m factory_intelligence.edge.cli @arguments
            if ($LASTEXITCODE -ne 0) { throw 'Agent configuration creation failed.' }
        }
        Write-Output 'Ready. Run brain, then enroll once, then agent in a separate terminal.'
        return
    }
    if (Test-Path -LiteralPath $operatorFile) {
        $env:FACTORY_BRAIN_ADMIN_TOKEN = [IO.File]::ReadAllText($operatorFile)
    }
    if ($Mode -eq 'brain') {
        & $python -m uvicorn factory_intelligence.api:app --host 127.0.0.1 --port 8000
    } else {
        $configuration = Get-Content -Raw -LiteralPath $Config | ConvertFrom-Json
        $tokenFile = Join-Path $projectRoot ".data/edge/$($configuration.agent_id).token"
        if ($Mode -eq 'enroll') {
            & $python -m factory_intelligence.edge.cli enroll --config $Config --token-file $tokenFile
        } else {
            if (Test-Path -LiteralPath $tokenFile) {
                [Environment]::SetEnvironmentVariable(
                    $configuration.token_env, [IO.File]::ReadAllText($tokenFile), 'Process'
                )
            }
            $action = if ($Mode -eq 'agent') { 'run' } else { $Mode }
            & $python -m factory_intelligence.edge.cli $action --config $Config
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Edge $Mode failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
