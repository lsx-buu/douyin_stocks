param(
    [string]$Url = "",
    [int]$ScrollRounds = -1,
    [int]$DetailPages = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

Push-Location $RepoRoot
try {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\scrape_douyin.ps1" -Url $Url -ScrollRounds $ScrollRounds -DetailPages $DetailPages
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_daily.ps1"
}
finally {
    Pop-Location
}

