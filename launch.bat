@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "ROOT=%~dp0"
set "CHECKONLY="
set "NOPAUSE="

:arguments
if "%~1"=="" goto check
if /i "%~1"=="-CheckOnly" goto check_argument
if /i "%~1"=="-NoPause" goto pause_argument
echo Usage: launch.bat [-CheckOnly] [-NoPause]
exit /b 2
:check_argument
set "CHECKONLY=1"
shift
goto arguments
:pause_argument
set "NOPAUSE=1"
shift
goto arguments

:check
cd /d "%ROOT%" || exit /b 2
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%packaging\windows\setup-environment.ps1" -ProjectRoot "%ROOT%." -CheckOnly -NoPause
set "RESULT=%errorlevel%"
if defined CHECKONLY exit /b %RESULT%
if "%RESULT%"=="0" goto ready
if "%RESULT%"=="4" goto failed
echo Repairing this GluSpot folder's local Python environment...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%packaging\windows\setup-environment.ps1" -ProjectRoot "%ROOT%." -NoPause
set "RESULT=%errorlevel%"
if not "%RESULT%"=="0" goto failed

:ready
set "LOGDIR=%ROOT%logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if not exist "%LOGDIR%" exit /b 7
set "LOG=%LOGDIR%\GluSpot-%RANDOM%.log"
set "PYEXE=%ROOT%.venv\Scripts\python.exe"
start "" /B "%PYEXE%" -X utf8 "%ROOT%local\main.py" 1>>"%LOG%" 2>&1
exit /b %errorlevel%

:failed
echo GluSpot environment is not ready. Run repair-env.bat to inspect or repair it.
if not defined NOPAUSE pause
exit /b %RESULT%
