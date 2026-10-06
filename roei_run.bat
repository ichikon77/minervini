@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\roei_screen.py' --card 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\roei_log.txt' -Value $_ -Encoding UTF8 }"
rem --- 20:00 X post (drafts only while roei_post_enabled=false in x_config.json; skipped on market holidays) ---
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\roei_post.py' 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\roei_log.txt' -Value $_ -Encoding UTF8 }"
