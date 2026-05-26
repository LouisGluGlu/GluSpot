@echo off
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "ROOT=%~dp0"
cd /d "%ROOT%" || exit /b 1

set "VENV=%ROOT%.venv"
set "PYEXE=%VENV%\Scripts\python.exe"

if exist "%PYEXE%" (
    "%PYEXE%" -X utf8 -c "import sys" >nul 2>nul
    if errorlevel 1 (
        echo Existing local environment is broken. Recreating .venv...
        rmdir /s /q "%VENV%"
    )
)

if not exist "%PYEXE%" (
    set "BASEPY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not exist "%BASEPY%" set "BASEPY=python"

    "%BASEPY%" -m venv "%VENV%"
    if errorlevel 1 (
        echo Failed to create local Python environment.
        echo Install Python 3.12 or make python available in PATH, then run setup-env.bat again.
        pause
        exit /b 1
    )
)

"%PYEXE%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

"%PYEXE%" -X utf8 -c "import PySide6, winsdk, soundcard, numpy, pywinauto, uiautomation, PIL; print('Dependency check passed.')"
if errorlevel 1 (
    echo Dependency check failed.
    pause
    exit /b 1
)

echo Environment is ready.
pause
