@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
"C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe" -m pip install xlrd openpyxl --quiet
"C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe" -X utf8 -u "C:\Users\ichik\Documents\minervini\haitou_screen.py"
echo Exit code: %ERRORLEVEL%
pause
