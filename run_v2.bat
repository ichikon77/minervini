@echo off

powershell -Command "& 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' 'C:\Users\ichik\Documents\minervini\minervini_screen_v2.py' 2>&1 | Tee-Object -Append -FilePath 'C:\Users\ichik\Documents\minervini\log_v2.txt'"

pause
