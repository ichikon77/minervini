@echo off
powershell -ExecutionPolicy Bypass -Command "& 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' 'C:\Users\ichik\Documents\minervini\minervini_screen.py' 2>&1 | Tee-Object -Append -FilePath 'C:\Users\ichik\Documents\minervini\log.txt'"
pause
