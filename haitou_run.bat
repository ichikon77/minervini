@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

rem -- dividend screener must run last: wait 4 min before joining the lock queue --
rem    (when delayed tasks all fire at boot, others acquire the lock first)
ping -n 241 127.0.0.1 >nul

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
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\haitou_screen.py' 2>&1 | Out-File -Append -Encoding utf8 -FilePath 'C:\Users\ichik\Documents\minervini\haitou_log2.txt'"
rmdir "%LOCKDIR%" 2>nul
