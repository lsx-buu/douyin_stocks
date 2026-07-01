param(
    [string]$Url = "",
    [int]$ScrollRounds = -1,
    [int]$DetailPages = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = Join-Path $RepoRoot "src"

$ArgsList = @("scrape-douyin", "--config", "config.json", "--scroll-rounds", "$ScrollRounds", "--detail-pages", "$DetailPages")
if ($Url -ne "") {
    $ArgsList += @("--url", $Url)
}

Push-Location $RepoRoot
try {
    python -m douyin_kb.cli @ArgsList
}
finally {
    Pop-Location
}

