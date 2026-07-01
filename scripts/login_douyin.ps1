$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$env:PYTHONPATH = Join-Path $RepoRoot "src"

Push-Location $RepoRoot
try {
    python -m douyin_kb.cli login-douyin --config "config.json" --wait-seconds 300
}
finally {
    Pop-Location
}

