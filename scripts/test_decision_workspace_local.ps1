# SPDX-License-Identifier: GPL-3.0-or-later
param([ValidateSet('postgres:16', 'postgres:17')][string]$Image = 'postgres:16')
$ErrorActionPreference = 'Stop'
$taskRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRepo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install locked Python dependencies first.' }
Push-Location $taskRepo
try {
    & $taskPython -m scripts.decision_test_environment --image $Image
    if ($LASTEXITCODE -ne 0) { throw 'Isolated Workspace integration failed; inspect its evidence.' }
} finally { Pop-Location }
