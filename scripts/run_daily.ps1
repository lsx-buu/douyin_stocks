$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = Join-Path $RepoRoot "src"

Push-Location $RepoRoot
try {
    python -m douyin_kb.cli run --config "config.json"
    & (Join-Path $RepoRoot "scripts\curate_video_cards.ps1")
}
finally {
    Pop-Location
}
