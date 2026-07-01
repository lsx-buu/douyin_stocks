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
$env:PYTHONPATH = Join-Path $RepoRoot "src"

$ArgsList = @(
    "scrape-douyin-search",
    "--config", "config.json",
    "--per-query", "$PerQuery",
    "--manual-seconds", "$ManualSeconds",
    "--min-likes", "$MinLikes",
    "--min-comments", "$MinComments",
    "--min-interactions", "$MinInteractions",
    "--scroll-rounds", "$ScrollRounds"
)
foreach ($Item in $Query) {
    if ($Item -ne "") {
        $ArgsList += @("--query", $Item)
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
