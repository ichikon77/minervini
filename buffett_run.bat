@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\buffett_screen.py' 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\buffett_log.txt' -Value $_ -Encoding UTF8 }"
