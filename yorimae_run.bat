@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
rem --- wait for network (up to 90s) in case the PC was just woken from sleep ---
powershell -Command "$ok=$false; for($i=0;$i -lt 18;$i++){ if(Test-Connection 8.8.8.8 -Count 1 -Quiet){ $ok=$true; break }; Start-Sleep 5 }; if(-not $ok){ Write-Host 'network not ready after 90s, continuing anyway' }"
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\yorimae_screen.py' 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\yorimae_log.txt' -Value $_ -Encoding UTF8 }"
rem --- kabuchiwa morning post to X (drafts only while post_enabled=false in x_config.json) ---
powershell -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; & 'C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe' -X utf8 -u 'C:\Users\ichik\Documents\minervini\kabuchiwa_post.py' 2>&1 | ForEach-Object { $_; Add-Content -Path 'C:\Users\ichik\Documents\minervini\yorimae_log.txt' -Value $_ -Encoding UTF8 }"
