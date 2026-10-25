@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
rem --- semi_screen.py / semi_post.py は自分で semi_log.txt / semi_post_log.txt に書くので、bat 側の写しは別ファイル semi_run_log.txt へ（同じファイルに tee すると二重記録になる: weekend_post_log の教訓） ---
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\semi_screen.py' 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\semi_run_log.txt' -Value $_ -Encoding UTF8 }"
rem --- 18:00 X post (drafts only while semi_post_enabled=false in x_config.json; skipped on market holidays) ---
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\semi_post.py' 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\semi_run_log.txt' -Value $_ -Encoding UTF8 }"
