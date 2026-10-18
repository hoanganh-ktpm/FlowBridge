# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "$PSScriptRoot/runtime;$projectRoot/backend;$env:PYTHONPATH"
$env:LANGFLOW_COMPONENTS_PATH = Join-Path $PSScriptRoot 'components'
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
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Langflow is not installed in langflow/.venv.'
}
$env:LANGFLOW_CONFIG_DIR = Join-Path $PSScriptRoot 'data'
$env:LANGFLOW_SAVE_DB_IN_CONFIG_DIR = 'true'
$env:LANGFLOW_AUTO_LOGIN = 'true'
$env:LANGFLOW_DO_NOT_TRACK = 'true'
$env:DO_NOT_TRACK = 'true'
New-Item -ItemType Directory -Path $env:LANGFLOW_CONFIG_DIR -Force | Out-Null
Push-Location $PSScriptRoot
try {
    & $pythonPath -m langflow run --host 127.0.0.1 --port 7860 --open-browser --log-file "$PSScriptRoot/data/langflow.log"
}
finally {
    Pop-Location
}
