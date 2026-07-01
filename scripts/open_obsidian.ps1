$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$EncodedPath = [uri]::EscapeDataString($RepoRoot.Path)
Start-Process "obsidian://open?path=$EncodedPath"

