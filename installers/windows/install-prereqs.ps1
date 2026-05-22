#requires -Version 5.0
<#
.SYNOPSIS
  Install ffmpeg / Python 3.11 / Node.js 20 via winget if any are missing.

.DESCRIPTION
  Run before the first-run setup GUI on Windows. winget ships with Windows
  10 1809+ and 11 by default. If it's missing (rare on modern systems),
  we fall back to direct .msi/.exe downloads.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Has-Command($name) {
  Get-Command $name -ErrorAction SilentlyContinue
}

function Has-Winget {
  Has-Command winget
}

function Install-Winget($id) {
  Write-Host "==> winget install $id" -ForegroundColor Cyan
  & winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
}

function Refresh-Path {
  $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
              [System.Environment]::GetEnvironmentVariable('Path', 'User')
}

# Refresh PATH first in case the user already installed something in this session.
Refresh-Path

$missing = @()
if (-not (Has-Command python)) { $missing += 'python' }
if (-not (Has-Command node))   { $missing += 'node' }
if (-not (Has-Command ffmpeg)) { $missing += 'ffmpeg' }

if ($missing.Count -eq 0) {
  Write-Host "All prerequisites already installed." -ForegroundColor Green
  exit 0
}

Write-Host "Missing prerequisites:" $missing -ForegroundColor Yellow

if (-not (Has-Winget)) {
  Write-Host @"
ERROR: winget (Windows Package Manager) is not available on this system.
Install the following manually and re-run the launcher:
  - Python 3.11+   https://www.python.org/downloads/
  - Node.js 20+    https://nodejs.org/
  - ffmpeg          https://www.gyan.dev/ffmpeg/builds/ (add to PATH)
"@ -ForegroundColor Red
  exit 1
}

if ($missing -contains 'python') { Install-Winget 'Python.Python.3.11' }
if ($missing -contains 'node')   { Install-Winget 'OpenJS.NodeJS.LTS' }
if ($missing -contains 'ffmpeg') { Install-Winget 'Gyan.FFmpeg' }

Refresh-Path
Write-Host "Prerequisites installed." -ForegroundColor Green
