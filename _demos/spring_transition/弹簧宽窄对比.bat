@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
set PYTHONDONTWRITEBYTECODE=1
rem 清掉离屏平台，窗口开在真实桌面上。
set QT_QPA_PLATFORM=
if exist "E:\gpt-island\.venv\Scripts\python.exe" (
  "E:\gpt-island\.venv\Scripts\python.exe" -X utf8 "_demos\spring_transition\demo.py" %*
) else (
  python -X utf8 "_demos\spring_transition\demo.py" %*
)
