@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPATH=%CD%
"E:\gpt-island\.venv\Scripts\python.exe" -X utf8 "_demos\search_t110\demo_shelves.py" %*
