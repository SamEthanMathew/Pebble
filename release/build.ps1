#requires -Version 5.1
<#
.SYNOPSIS
  Build a distributable Pebble .exe via PyInstaller.

.DESCRIPTION
  Cleans previous build/dist, ensures pyinstaller is installed, runs the spec,
  and reports the path to the produced folder.

.PARAMETER Clean
  If set, removes build/ and dist/ before building.

.PARAMETER Sign
  If set and SignTool is on PATH, signs the produced Pebble.exe with the
  certificate from $env:PEBBLE_SIGN_CERT (.pfx path) using $env:PEBBLE_SIGN_PASS.

.EXAMPLE
  pwsh release/build.ps1 -Clean
#>

param(
    [switch]$Clean,
    [switch]$Sign
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if ($Clean) {
    Write-Host "Cleaning build/ dist/..."
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
}

Write-Host "Verifying PyInstaller..."
$null = python -m pip show pyinstaller 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pyinstaller..."
    python -m pip install --upgrade pyinstaller
}

Write-Host "Running PyInstaller (pebble.spec)..."
python -m PyInstaller --noconfirm pebble.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed (exit $LASTEXITCODE)"
}

$exe = Join-Path $repo 'dist\Pebble\Pebble.exe'
if (-not (Test-Path $exe)) {
    throw "Expected $exe but it was not produced."
}

if ($Sign) {
    if (-not $env:PEBBLE_SIGN_CERT -or -not $env:PEBBLE_SIGN_PASS) {
        Write-Warning "PEBBLE_SIGN_CERT or PEBBLE_SIGN_PASS not set; skipping signing."
    } else {
        $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if (-not $signtool) {
            Write-Warning "signtool.exe not on PATH; skipping signing."
        } else {
            Write-Host "Signing $exe..."
            & signtool.exe sign /f $env:PEBBLE_SIGN_CERT /p $env:PEBBLE_SIGN_PASS `
                /tr 'http://timestamp.digicert.com' /td sha256 /fd sha256 $exe
            if ($LASTEXITCODE -ne 0) { throw "signtool failed" }
        }
    }
}

Write-Host ""
Write-Host "✓ Built $exe"
Write-Host "  Distribute the entire dist\Pebble folder."
