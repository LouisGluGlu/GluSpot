@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
set PYTHONDONTWRITEBYTECODE=1
"E:\gpt-island\.venv\Scripts\python.exe" -X utf8 "_demos\narrow3\demo_row.py" %*
