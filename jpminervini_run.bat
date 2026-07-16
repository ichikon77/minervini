@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

rem -- exclusive lock: wait while another screener is running (max 60 min, then force) --
set LOCKDIR=C:\Users\ichik\Documents\minervini\_screener.lock
set /a tries=0
:acquire
mkdir "%LOCKDIR%" 2>nul && goto run
set /a tries+=1
if %tries% GEQ 120 goto run
ping -n 31 127.0.0.1 >nul
goto acquire

:run
"C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe" -m pip install xlrd openpyxl --quiet --disable-pip-version-check
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\jpminervini_screen.py' 2>&1 | Out-File -Append -Encoding utf8 -FilePath 'C:\Users\ichik\Documents\minervini\jpminervini_log.txt'"
rmdir "%LOCKDIR%" 2>nul
