<#
.SYNOPSIS
    Serve an ABot-Recon reconstruction with the bundled web point-cloud viewer.

.DESCRIPTION
    Starts a local static HTTP server rooted at the repository's tools/ folder
    and opens the viewer pointed at the manifest produced by
    scripts/export_point_cloud_chunks.py.

    The viewer needs an HTTP origin (it fetches the manifest and chunk files),
    so opening the .html file directly from disk will not work.

.EXAMPLE
    .\serve_point_cloud.ps1 -OutputDir "D:\work\abot_recon\outputs\run"
#>
param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$repo = 'D:\work\abot_recon\ABot-Recon'
$chunks = Join-Path $OutputDir 'point_cloud_chunks'
$manifest = Join-Path $chunks 'manifest.json'

if (-not (Test-Path $manifest)) {
    throw "manifest not found: $manifest`nRun scripts/export_point_cloud_chunks.py first."
}

# Serve the repo root so both tools/*.html and the chunk folder are reachable
# under one origin; the viewer resolves the manifest path relative to the page.
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "[WARN] port $Port is already in use; trying $Port+1"
    $Port = $Port + 1
}

$url = "http://127.0.0.1:$Port/tools/point_cloud_viewer.html"
Write-Host "[INFO] serving $repo on port $Port"
Write-Host "[INFO] open  : $url"
Write-Host "[INFO] manifest: $manifest"

if (-not $NoBrowser) {
    Start-Process $url
}

Push-Location $repo
try {
    $job = Start-Job -ScriptBlock {
        param($root, $port)
        Set-Location $root
        python -m http.server $port --bind 127.0.0.1
    } -ArgumentList $repo, $Port
    Write-Host "[INFO] server job id $($job.Id); press Ctrl+C to stop"
    Wait-Job $job | Out-Null
} finally {
    Pop-Location
}
