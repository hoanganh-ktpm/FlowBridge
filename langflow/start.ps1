# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "$PSScriptRoot/runtime;$projectRoot/backend;$env:PYTHONPATH"
$env:LANGFLOW_COMPONENTS_PATH = Join-Path $PSScriptRoot 'components'
$env:LANGFLOW_A2A_ENABLED = 'true'
$env:FLOWBRIDGE_AGENT_ENV_FILE = Join-Path $PSScriptRoot 'data/agent.env'
try {
    $running = Invoke-RestMethod -Uri 'http://127.0.0.1:7860/api/v1/version' -TimeoutSec 2
    if ($running.version) {
        Start-Process 'http://127.0.0.1:7860'
        return
    }
}
catch {
    # Start a new server when Langflow is not already running.
}
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$configPath = Join-Path $PSScriptRoot 'data'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = Join-Path $projectRoot '.cache/rag/langflow-integration/venv/Scripts/python.exe'
    $existingState = Join-Path $projectRoot '.cache/rag/langflow-integration/langflow-state'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'No local Langflow environment found in langflow/.venv or .cache/rag/langflow-integration/venv.'
    }
    if (Test-Path -LiteralPath (Join-Path $existingState 'langflow.db')) {
        $configPath = $existingState
    }
}
$env:LANGFLOW_CONFIG_DIR = $configPath
$env:LANGFLOW_SAVE_DB_IN_CONFIG_DIR = 'true'
$env:LANGFLOW_AUTO_LOGIN = 'true'
$env:LANGFLOW_DO_NOT_TRACK = 'true'
$env:DO_NOT_TRACK = 'true'
if (-not $env:OMP_NUM_THREADS) { $env:OMP_NUM_THREADS = '4' }
New-Item -ItemType Directory -Path $env:LANGFLOW_CONFIG_DIR -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'data') -Force | Out-Null
$identityWorker = $null
Push-Location $PSScriptRoot
try {
    $identityDir = Join-Path $projectRoot '.cache/rag/langflow-integration/identity'
    $backendPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if ((Test-Path -LiteralPath (Join-Path $identityDir 'operator.json')) -and
        (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'data/local-test.json')) -and
        (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'data/ver2-install.json')) -and
        (Test-Path -LiteralPath $backendPython)) {
        $identityWorker = Start-Process -FilePath $backendPython `
            -ArgumentList @("`"$PSScriptRoot/local_identity_session.py`"", '--watch') `
            -WorkingDirectory $projectRoot -WindowStyle Hidden `
            -RedirectStandardOutput "$PSScriptRoot/data/identity-session.out.log" `
            -RedirectStandardError "$PSScriptRoot/data/identity-session.err.log" -PassThru
    }
    $langflowArgs = @('-m', 'langflow', 'run', '--host', '127.0.0.1', '--port', '7860',
        '--open-browser', '--log-file', "$PSScriptRoot/data/langflow.log")
    # Components read FLOWBRIDGE_AGENT_ENV_FILE on each call, including refreshed
    # local tokens. Do not freeze those tokens into process environment variables.
    & $pythonPath @langflowArgs
}
finally {
    if ($identityWorker -and -not $identityWorker.HasExited) {
        Stop-Process -Id $identityWorker.Id -ErrorAction SilentlyContinue
    }
    Pop-Location
}
