# 2026-11-11: MarginScreener を 1日2回（16:40 と 08:55）に変更。JPX の銘柄別信用残 PDF は T+1 の 16時ごろ公表なので
# 16:40 で当日分を拾い（デッキが 2営業日遅れ→1営業日遅れに）、08:55 は公表が遅れた日の保険として残す。
# 管理者権限が必要（RunLevel HIGHEST）。
# 起動: Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -NoExit -File C:\Users\ichik\Documents\minervini\setup_margin_1640_task.ps1'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$s = New-Object -ComObject Schedule.Service; $s.Connect(); $f = $s.GetFolder('\')
$t = $f.GetTask('MarginScreener'); $d = $t.Definition
"変更前: " + (($d.Triggers | ForEach-Object { $_.StartBoundary }) -join ', ')
$d.Triggers.Clear()
$tr1 = $d.Triggers.Create(2); $tr1.DaysInterval = 1; $tr1.StartBoundary = '2026-11-11T16:40:00'
$tr2 = $d.Triggers.Create(2); $tr2.DaysInterval = 1; $tr2.StartBoundary = '2026-11-12T08:55:00'
$d.Settings.WakeToRun = $true; $d.Settings.StartWhenAvailable = $true; $d.Settings.ExecutionTimeLimit = 'PT2H'
$d.Settings.MultipleInstances = 2   # 前の実行が残っていたら新しい方は起動しない
$f.RegisterTaskDefinition('MarginScreener', $d, 4, $null, $null, 3) | Out-Null
$t = $f.GetTask('MarginScreener')
"変更後: " + (($t.Definition.Triggers | ForEach-Object { $_.StartBoundary }) -join ', ')
"次回実行: " + $t.NextRunTime
"このウィンドウは閉じてよい"
