param(
    [int]$ScrollRounds = -1,
    [int]$DetailPagesPerAccount = 0,
    [switch]$NoKeywordFilter
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

$ScrapeArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\scripts\scrape_douyin_accounts.ps1", "-ScrollRounds", "$ScrollRounds", "-DetailPagesPerAccount", "$DetailPagesPerAccount")
if ($NoKeywordFilter) {
    $ScrapeArgs += "-NoKeywordFilter"
}

Push-Location $RepoRoot
try {
    powershell.exe @ScrapeArgs
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_daily.ps1"
}
finally {
    Pop-Location
}

