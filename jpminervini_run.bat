@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

rem ── 排他ロック: 他のスクリーナー実行中は待つ（最大60分、以後強行）──
set LOCKDIR=C:\Users\ichik\Documents\minervini\_screener.lock
set /a tries=0
:acquire
mkdir "%LOCKDIR%" 2>nul && goto run
set /a tries+=1
if %tries% GEQ 120 goto run
ping -n 31 127.0.0.1 >nul
goto acquire

:run
"C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe" -m pip install xlrd openpyxl --quiet
powershell -Command "& 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\jpminervini_screen.py' 2>&1 | Tee-Object -Append -FilePath 'C:\Users\ichik\Documents\minervini\jpminervini_log.txt'"
rmdir "%LOCKDIR%" 2>nul
