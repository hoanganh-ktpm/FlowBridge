@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set "FLOWBRIDGE_AGENT_ENV_FILE=%~dp0data\agent.env"
set "PYTHONPATH=%~dp0runtime;%~dp0..\backend;%PYTHONPATH%"
set "JSON_TEST_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%JSON_TEST_PYTHON%" set "JSON_TEST_PYTHON=%~dp0..\.cache\rag\langflow-integration\venv\Scripts\python.exe"
if not exist "%JSON_TEST_PYTHON%" (
    echo Local Langflow Python environment not found.
    pause
    exit /b 1
)
"%JSON_TEST_PYTHON%" "%~dp0try_frontend_json.py" %*
pause
endlocal
