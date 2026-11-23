<#
.SYNOPSIS
    Serve an ABot-Recon reconstruction with the bundled web point-cloud viewer.

.DESCRIPTION
    The viewer (tools/point_cloud_viewer.html) fetches the manifest and the
    point-cloud chunks over HTTP, so it cannot be opened from file://.

    This script starts a local static HTTP server and opens the viewer with
    the manifest URL filled in.

    The server root is chosen as the deepest common ancestor of the repository
    and the output directory, so that both the viewer page and the chunk files
    are reachable under one origin no matter where the outputs live.

.EXAMPLE
    .\serve_point_cloud.ps1 -OutputDir "D:\work\abot_recon\outputs\run"

.EXAMPLE
    .\serve_point_cloud.ps1 -OutputDir "D:\work\abot_recon\outputs\run" -Port 8123 -NoBrowser
#>
param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot   # ...\ABot-Recon
$outputDir = (Resolve-Path -LiteralPath $OutputDir).Path
$chunks = Join-Path $outputDir 'point_cloud_chunks'
$manifest = Join-Path $chunks 'manifest.json'

if (-not (Test-Path $manifest)) {
    throw "manifest not found: $manifest`nRun scripts/export_point_cloud_chunks.py first."
}

# Deepest common ancestor of the repo and the output directory.
$repoResolved = (Resolve-Path -LiteralPath $repo).Path
$root = $outputDir
while ($root -ne '' -and -not $repoResolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    $parent = Split-Path -Parent $root
    if ($parent -eq $root) { break }
    $root = $parent
}
if (-not $repoResolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    $root = Split-Path -Parent $repoResolved
}

# URL path of the viewer page and of the manifest, relative to the root.
# NOTE: cast AFTER reading .Path -- ([string](Resolve-Path ...)).Path would
# stringify the PathInfo first and then read a non-existent .Path property.
$viewerPath = [string]((Resolve-Path -LiteralPath (Join-Path $repo 'tools\point_cloud_viewer.html')).Path)
$trajectoryPath = [string](Join-Path $chunks 'trajectory.json')
$rootPrefix = [string]$root
if (-not $rootPrefix.EndsWith('\')) { $rootPrefix = $rootPrefix + '\' }

$viewerRel = $viewerPath.Substring($rootPrefix.Length).Replace('\', '/')
$manifestRel = ([string]$manifest).Substring($rootPrefix.Length).Replace('\', '/')
$trajectoryRel = $trajectoryPath.Substring($rootPrefix.Length).Replace('\', '/')

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "[WARN] port $Port is in use; trying $Port+1"
    $Port = $Port + 1
}

# The viewer reads ?manifest= / ?trajectory= from the query string and loads
# them automatically.
$manifestUrl = "http://127.0.0.1:${Port}/${manifestRel}"
$trajectoryUrl = "http://127.0.0.1:${Port}/${trajectoryRel}"
# Use ${...} braces: a bare $viewerRel?manifest= would be parsed as a variable
# named "viewerRel?manifest" and silently expand to nothing.
$url = "http://127.0.0.1:${Port}/${viewerRel}?manifest=$([uri]::EscapeDataString($manifestUrl))&trajectory=$([uri]::EscapeDataString($trajectoryUrl))"

Write-Host "[INFO] server root : $root"
Write-Host "[INFO] viewer      : $url"
Write-Host "[INFO] manifest    : $manifestUrl"

# The viewer accepts a manifest URL; pass it through the query string when the
# page supports it, otherwise the user can paste it into the manifest field.
if (-not $NoBrowser) {
    Start-Process $url
}

# Locate a Python interpreter: prefer a venv next to the repository, then the
# one on PATH.
$python = $null
$searchDirs = @(
    (Split-Path -Parent $repo),                               # ...\abot_recon
    $repo                                                    # ...\ABot-Recon
)
foreach ($d in $searchDirs) {
    foreach ($rel in @('venv\Scripts\python.exe', '.venv\Scripts\python.exe')) {
        $candidate = Join-Path $d $rel
        if (Test-Path $candidate) { $python = $candidate; break }
    }
    if ($python) { break }
}
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source }
}
if (-not $python) { throw 'No Python interpreter found (looked for venv\Scripts\python.exe and python on PATH).' }
Write-Host "[INFO] python: $python"

Write-Host "[INFO] serving on http://127.0.0.1:$Port/  (Ctrl+C to stop)"
Set-Location $root
& $python -m http.server $Port --bind 127.0.0.1
