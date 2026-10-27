@echo off
chcp 65001 >nul
rem Q/W/E/R = gentle/medium/strong/vocal, 1-4 = cover color, Space = stop rotating covers
call "%~dp0tools\sea_live.bat" --audio "C:\Users\Liwuju\Videos\gluspot-video\public\audio\clip.mp3" %*
