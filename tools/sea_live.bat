@echo off
cd /d "%~dp0.."
set PYTHONDONTWRITEBYTECODE=1
"E:\gpt-island\.venv\Scripts\python.exe" -X utf8 tools\sea_live.py %*
