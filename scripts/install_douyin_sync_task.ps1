param(
    [string]$At = "22:30",
    [string]$TaskName = "DouyinKnowledgeSyncAndReview",
    [int]$ScrollRounds = -1,
    [int]$DetailPagesPerAccount = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ScriptPath = Join-Path $RepoRoot "scripts\sync_accounts_and_review.ps1"

$Argument = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -ScrollRounds $ScrollRounds -DetailPagesPerAccount $DetailPagesPerAccount"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Argument
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Scrape Douyin web, generate knowledge cards, and write daily review." -Force | Out-Null

Write-Host "Installed scheduled task '$TaskName' at $At."
