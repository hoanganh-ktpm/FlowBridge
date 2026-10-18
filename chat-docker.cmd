@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem FlowBridge; redistribution/modification under GNU GPL v3 or later.
rem Provided without warranty. See LICENSE.
pushd "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\chat_stack.py %*
) else (
    python scripts\chat_stack.py %*
)
set "chatExit=%ERRORLEVEL%"
popd
exit /b %chatExit%
