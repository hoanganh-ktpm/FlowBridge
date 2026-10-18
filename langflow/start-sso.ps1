# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Serve existing flows with per-request caller context; does not modify metadata or IAM.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "$PSScriptRoot/runtime;$projectRoot/backend;$env:PYTHONPATH"
$env:LANGFLOW_COMPONENTS_PATH = Join-Path $PSScriptRoot 'components'
$env:LANGFLOW_A2A_ENABLED = 'true'
$env:FLOWBRIDGE_AGENT_ENV_FILE = Join-Path $PSScriptRoot 'data/agent.env'
$env:LANGFLOW_CONFIG_DIR = Join-Path $PSScriptRoot 'data'
$env:LANGFLOW_SAVE_DB_IN_CONFIG_DIR = 'true'
$env:LANGFLOW_DO_NOT_TRACK = 'true'
$env:DO_NOT_TRACK = 'true'
& "$PSScriptRoot/.venv/Scripts/python.exe" -m uvicorn flowbridge_agent.serving:create_app `
    --factory --host 127.0.0.1 --port 7860 --no-access-log `
    --env-file "$PSScriptRoot/data/agent.env"
