@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "ROOT=%~dp0"
set "NOPAUSE="
set "CHECKONLY="

:arguments
if "%~1"=="" goto check
if /i "%~1"=="-NoPause" goto pause_argument
if /i "%~1"=="-CheckOnly" goto check_argument
echo Usage: build-exe.bat [-CheckOnly] [-NoPause]
exit /b 2
:pause_argument
set "NOPAUSE=1"
shift
goto arguments
:check_argument
set "CHECKONLY=1"
shift
goto arguments

:check
cd /d "%ROOT%" || exit /b 2
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%packaging\windows\setup-environment.ps1" -ProjectRoot "%ROOT%." -CheckOnly -NoPause
set "RESULT=%errorlevel%"
if not "%RESULT%"=="0" goto environment_failed
if defined CHECKONLY exit /b 0
set "PYEXE=%ROOT%.venv\Scripts\python.exe"

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%packaging\windows\build-airplay.ps1"
if errorlevel 1 goto native_failed
"%PYEXE%" -X utf8 "%ROOT%packaging\windows\prepare-audio-licenses.py"
if errorlevel 1 exit /b 1
"%PYEXE%" -X utf8 -m PyInstaller --noconfirm --clean --workpath "%ROOT%build\pyinstaller" --distpath "%ROOT%release" "%ROOT%GluSpot.spec"
if errorlevel 1 goto build_failed
echo GluSpot 6.415 build is ready:
echo "%ROOT%release\GluSpot-6.415\GluSpot.exe"
if not defined NOPAUSE pause
exit /b 0

:environment_failed
echo GluSpot local environment is not ready. Run repair-env.bat before building.
if not defined NOPAUSE pause
exit /b %RESULT%
:native_failed
echo GluSpot AirPlay helper build failed. Install CMake and a Windows C++20 toolchain; see native\airplay_sender\README.md.
exit /b 1
:build_failed
echo GluSpot 6.415 build failed.
if not defined NOPAUSE pause
exit /b 1
