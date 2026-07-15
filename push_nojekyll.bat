@echo off
cd /d "C:\Users\ichik\Documents\minervini"
type nul > .nojekyll
git add .nojekyll
git commit -m "add .nojekyll to disable Jekyll"
git push
pause
