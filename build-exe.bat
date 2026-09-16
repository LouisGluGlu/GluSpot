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

"%PYEXE%" -X utf8 -m PyInstaller --noconfirm --clean --workpath "%ROOT%build\pyinstaller" --distpath "%ROOT%release" "%ROOT%GluSpot.spec"
if errorlevel 1 (
    echo GluSpot 6.402 build failed.
    pause
    exit /b 1
)

echo GluSpot 6.402 build is ready:
echo "%ROOT%release\GluSpot-6.402\GluSpot.exe"
pause
