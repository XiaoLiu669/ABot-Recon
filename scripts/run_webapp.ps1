<#
.SYNOPSIS
    Start the ABot-Recon local web app (upload a video -> reconstruct -> view 3D).

.DESCRIPTION
    Serves webapp/server.py, which lets you upload a video in the browser,
    runs the reconstruction in a worker subprocess, and embeds the bundled
    point-cloud viewer with the result.

.EXAMPLE
    .\run_webapp.ps1

.EXAMPLE
    .\run_webapp.ps1 -Port 8770 -Host 0.0.0.0 -NoBrowser
#>
param(
    [int]$Port = 8770,
    [string]$Host = '127.0.0.1',
    [string]$RunsDir = '',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot   # ...\ABot-Recon
$server = Join-Path $repo 'webapp\server.py'
if (-not (Test-Path $server)) { throw "server not found: $server" }

$checkpoint = Join-Path $repo 'checkpoints\abot_recon.safetensors'
if (-not (Test-Path $checkpoint)) {
    Write-Host "[WARN] checkpoint not found: $checkpoint"
    Write-Host "[WARN] the web app will start, but reconstruction will fail until it is in place."
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

# Flask is required by the web app.
& $python -c "import flask" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] installing Flask into $python"
    & $python -m pip install flask
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install Flask.' }
}

$url = "http://${Host}:${Port}/"
Write-Host "[INFO] python  : $python"
Write-Host "[INFO] web app : $url"
if (-not $NoBrowser) { Start-Process $url }

$arguments = @($server, '--host', $Host, '--port', "$Port")
if ($RunsDir) { $arguments += @('--runs-dir', $RunsDir) }
& $python @arguments
