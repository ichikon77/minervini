@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\roei_screen.py' 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\roei_log.txt' -Value $_ -Encoding UTF8 }"
rem --- 2026-11-15: X 投稿は全廃（11-03 日次廃止 → 11-15 週まとめも廃止）。カード画像（--card）も作らない。このbatはデッキ roei.html の更新と push だけ。手動投稿: python roei_screen.py --card --nopush → python roei_post.py --force [--lead CODE] ---
