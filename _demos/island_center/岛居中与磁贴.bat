@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
set PYTHONDONTWRITEBYTECODE=1
if exist "E:\gpt-island\.venv\Scripts\python.exe" (
  "E:\gpt-island\.venv\Scripts\python.exe" -X utf8 "_demos\island_center\demo.py" %*
) else (
  python -X utf8 "_demos\island_center\demo.py" %*
)
