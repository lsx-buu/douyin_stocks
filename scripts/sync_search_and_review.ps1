param(
    [string[]]$Query = @(),
    [int]$PerQuery = 10,
    [int]$ManualSeconds = 0,
    [int]$MinLikes = 0,
    [int]$MinComments = 0,
    [int]$MinInteractions = 0,
    [int]$ScrollRounds = 2,
    [switch]$NoKeywordFilter
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

$SearchArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", ".\scripts\scrape_douyin_search.ps1",
    "-PerQuery", "$PerQuery",
    "-ManualSeconds", "$ManualSeconds",
    "-MinLikes", "$MinLikes",
    "-MinComments", "$MinComments",
    "-MinInteractions", "$MinInteractions",
    "-ScrollRounds", "$ScrollRounds"
)
foreach ($Item in $Query) {
    if ($Item -ne "") {
        $SearchArgs += @("-Query", $Item)
    }
}
if ($NoKeywordFilter) {
    $SearchArgs += "-NoKeywordFilter"
}

Push-Location $RepoRoot
try {
    powershell.exe @SearchArgs
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_daily.ps1"
}
finally {
    Pop-Location
}
