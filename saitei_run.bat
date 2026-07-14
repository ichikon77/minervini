@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
powershell -Command "& 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\saitei_screen.py' 2>&1 | Tee-Object -Append -FilePath 'C:\Users\ichik\Documents\minervini\saitei_log.txt'"
