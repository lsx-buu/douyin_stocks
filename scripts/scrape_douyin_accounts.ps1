param(
    [string[]]$AccountUrl = @(),
    [int]$ScrollRounds = -1,
    [int]$DetailPagesPerAccount = 0,
    [switch]$NoKeywordFilter
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = Join-Path $RepoRoot "src"

$ArgsList = @("scrape-douyin-accounts", "--config", "config.json", "--scroll-rounds", "$ScrollRounds", "--detail-pages-per-account", "$DetailPagesPerAccount")
foreach ($Item in $AccountUrl) {
    if ($Item -ne "") {
        $ArgsList += @("--account-url", $Item)
    }
}
if ($NoKeywordFilter) {
    $ArgsList += "--no-keyword-filter"
}

Push-Location $RepoRoot
try {
    python -m douyin_kb.cli @ArgsList
}
finally {
    Pop-Location
}

