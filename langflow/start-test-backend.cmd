@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem FlowBridge; redistribution/modification under GNU GPL v3 or later.
rem Provided without warranty. See LICENSE.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-test-backend.ps1"
pause
