@echo off
rem Weekly fundamentals fetch for margin.html (Saturday 07:00)
cd /d C:\Users\ichik\Documents\minervini
echo [%date% %time%] margin_weekly start >> margin_weekly_log.txt
python margin_weekly.py >> margin_weekly_log.txt 2>&1
echo [%date% %time%] margin_weekly end >> margin_weekly_log.txt
