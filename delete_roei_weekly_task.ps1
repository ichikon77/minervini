# 2026-11-15: 漏洩の週まとめ投稿を廃止 → タスク RoeiWeekly を削除する（RoeiScreener 平日22:00 のデッキ更新は残す）。
# 管理者権限が必要（RunLevel HIGHEST のタスクは一般権限だと E_ACCESSDENIED）。
# 起動: Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -NoExit -File C:\Users\ichik\Documents\minervini\delete_roei_weekly_task.ps1'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$s = New-Object -ComObject Schedule.Service; $s.Connect(); $f = $s.GetFolder('\')

try { $f.DeleteTask('RoeiWeekly', 0); "RoeiWeekly 削除 OK" } catch { "RoeiWeekly 削除できず: $($_.Exception.Message)" }

""
"--- 残っている Roei/Semi/Kazami 系タスク（RoeiScreener だけ残るのが正解）---"
$f.GetTasks(0) | Where-Object { $_.Name -like 'Roei*' -or $_.Name -like 'Semi*' -or $_.Name -like 'Kazami*' } | Select-Object Name, NextRunTime | Format-Table -AutoSize
"このウィンドウは閉じてよい"
