@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\setup-environment.ps1" %*
exit /b %errorlevel%
