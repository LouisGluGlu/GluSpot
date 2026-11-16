@echo off
chcp 65001 >nul
rem Q/W/E/R = gentle/medium/strong/vocal, 1-4 = cover color, Space = stop rotating covers
rem Usage: drag an audio file onto this script, or pass its path as the first argument.
call "%~dp0tools\sea_live.bat" --audio "%~1" %2 %3 %4 %5 %6 %7 %8 %9
