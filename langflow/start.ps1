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
Push-Location $PSScriptRoot
try {
    $langflowArgs = @('-m', 'langflow', 'run', '--host', '127.0.0.1', '--port', '7860',
        '--open-browser', '--log-file', "$PSScriptRoot/data/langflow.log")
    # The editor prepares flow metadata; application chat uses start-sso or Docker.
    & $pythonPath @langflowArgs
}
finally {
    Pop-Location
}
