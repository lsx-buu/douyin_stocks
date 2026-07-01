param(
    [string[]]$Query = @(),
    [int]$PerQuery = 8,
    [int]$ManualSeconds = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = Join-Path $RepoRoot "src"

$ArgsList = @("discover-douyin-accounts", "--config", "config.json", "--per-query", "$PerQuery", "--manual-seconds", "$ManualSeconds")
foreach ($Item in $Query) {
    if ($Item -ne "") {
        $ArgsList += @("--query", $Item)
    }
}

Push-Location $RepoRoot
try {
    python -m douyin_kb.cli @ArgsList
}
finally {
    Pop-Location
}

