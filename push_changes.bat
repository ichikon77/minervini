@echo off
rem -- commit and push all local changes to GitHub --
rem -- used both manually (double-click) and by NightlyGitPush task --
cd /d "%~dp0"
git add -A >> push_log.txt 2>&1
git commit -m "nightly auto-commit" >> push_log.txt 2>&1
git pull --rebase >> push_log.txt 2>&1
git push >> push_log.txt 2>&1
