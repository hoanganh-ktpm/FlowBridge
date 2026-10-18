# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$langflowPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    & $pythonPath scripts/langflow_test_backend.py --prepare
    if ($LASTEXITCODE -ne 0) { throw 'Local test profile preparation failed.' }
    $profile = Get-Content -LiteralPath "$PSScriptRoot/data/local-test.json" -Raw | ConvertFrom-Json
    $running = $null
    try {
        $running = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/_ops/local-test' `
            -Headers @{ Authorization = 'Bearer ' + $profile.token } -TimeoutSec 3
    }
    catch {
        # The server may not be running yet; never print headers or credentials.
    }
    & $langflowPython langflow/connect_local_test.py
    if ($LASTEXITCODE -ne 0) { throw 'Could not connect ver2; start Langflow first.' }
    if ($profile.rag_source -eq 'postgres') {
        & $langflowPython langflow/connect_local_rag.py
        if ($LASTEXITCODE -ne 0) { throw 'Could not connect PostgreSQL RAG.' }
        if ($running -and $running.rag_source -ne 'postgres') {
            throw 'An older test backend is running without RAG. Stop that server and reopen this launcher.'
        }
    }
    if ($running.mode -eq 'langflow-local-test' -and $running.read_only) {
        Write-Host ''
        Write-Host 'Backend test already running at http://127.0.0.1:8001. Refresh Langflow.' -ForegroundColor Green
        Write-Host ("  status    : {0}" -f $running.status)
        Write-Host ("  read_only : {0}" -f $running.read_only)
        Write-Host ("  rag_source: {0}" -f $running.rag_source)
        Write-Host '  (The server itself runs in another window/process; closing this one does not stop it.)'
        return
    }
    & $pythonPath scripts/langflow_test_backend.py
    if ($LASTEXITCODE -ne 0) { throw 'Local test backend stopped with an error.' }
}
finally {
    Pop-Location
}
