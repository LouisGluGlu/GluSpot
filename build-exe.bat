@echo off
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "ROOT=%~dp0"
cd /d "%ROOT%" || exit /b 1

set "PYEXE=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
    echo GluSpot local environment is missing.
    echo Run setup-env.bat first, then run build-exe.bat again.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%packaging\windows\build-airplay.ps1"
if errorlevel 1 (
    echo GluSpot AirPlay helper build failed. Install CMake and a Windows C++20 toolchain; see native\airplay_sender\README.md.
    exit /b 1
)

"%PYEXE%" -X utf8 "%ROOT%packaging\windows\prepare-audio-licenses.py"
if errorlevel 1 exit /b 1

"%PYEXE%" -X utf8 -m PyInstaller --noconfirm --clean --workpath "%ROOT%build\pyinstaller" --distpath "%ROOT%release" "%ROOT%GluSpot.spec"
if errorlevel 1 (
    echo GluSpot 6.403 build failed.
    pause
    exit /b 1
)

echo GluSpot 6.403 build is ready:
echo "%ROOT%release\GluSpot-6.403\GluSpot.exe"
pause
