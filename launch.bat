@echo off
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "ROOT=%~dp0"
cd /d "%ROOT%" || exit /b 1

set "LOGDIR=%ROOT%logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\GluSpot-%RANDOM%.log"

set "PYEXE=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
    echo GluSpot local environment is missing.
    echo Please run setup-env.bat first, then launch again.
    echo Log: "%LOG%"
    pause
    exit /b 1
)

"%PYEXE%" -X utf8 -c "import PySide6, winsdk, soundcard, numpy, pywinauto, uiautomation, PIL" >nul 2>>"%LOG%"
if errorlevel 1 (
    echo GluSpot local environment is incomplete or broken.
    echo Please run setup-env.bat first, then launch again.
    echo Log: "%LOG%"
    pause
    exit /b 1
)

start "" /B "%PYEXE%" -X utf8 "%ROOT%local\main.py" 1>>"%LOG%" 2>&1
