# 2026-11-03: RoeiWeekly（日曜18:30 漏洩の週まとめ）を登録し、廃止した WeekendWeekly / WeekendPreview を削除する。
# 管理者権限が必要（RunLevel HIGHEST のタスクは一般権限だと E_ACCESSDENIED）。
# 起動: Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -NoExit -File C:\Users\ichik\Documents\minervini\setup_roei_weekly_task.ps1'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$s = New-Object -ComObject Schedule.Service; $s.Connect(); $f = $s.GetFolder('\')

$d = $s.NewTask(0)
$d.RegistrationInfo.Description = 'roei weekly recap post (Sun 18:30, since 2026-11-03)'
$tr = $d.Triggers.Create(3); $tr.DaysOfWeek = 1; $tr.WeeksInterval = 1; $tr.StartBoundary = '2026-11-08T18:30:00'
$a = $d.Actions.Create(0); $a.Path = 'C:\Users\ichik\Documents\minervini\roei_week_run.bat'
$d.Settings.WakeToRun = $true; $d.Settings.StartWhenAvailable = $true; $d.Settings.ExecutionTimeLimit = 'PT1H'
$d.Principal.RunLevel = 1
$f.RegisterTaskDefinition('RoeiWeekly', $d, 6, $null, $null, 3) | Out-Null
"RoeiWeekly 登録"

foreach ($n in 'WeekendWeekly', 'WeekendPreview') {
    try { $f.DeleteTask($n, 0); "$n 削除" } catch { "$n 削除できず: $($_.Exception.Message)" }
}

""
"--- 残っているタスク ---"
$f.GetTasks(0) | Where-Object { $_.Name -like 'Weekend*' -or $_.Name -like 'Roei*' -or $_.Name -like 'Kazami*' -or $_.Name -like 'Semi*' } | Select-Object Name, NextRunTime | Format-Table -AutoSize
"このウィンドウは閉じてよい"
