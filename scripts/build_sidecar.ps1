# Build proxion-gateway.exe with PyInstaller
# Run from repo root: .\scripts\build_sidecar.ps1
#
# Prerequisites:
#   pip install pyinstaller
#   pip install -e proxion-core[gateway,http,cli]

param(
    [switch]$Install  # pass -Install to pip install deps first
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if ($Install) {
    Write-Host "Installing proxion-core with gateway extras..."
    pip install -e "$Root\proxion-core[gateway,http,cli]"
    pip install pyinstaller
}

Write-Host "Building proxion-gateway.exe..."
Set-Location $Root
pyinstaller scripts/build_gateway.spec --clean --noconfirm

Write-Host ""
Write-Host "Build complete. Output: $Root\dist\proxion-gateway.exe"
Write-Host ""
Write-Host "To run:"
Write-Host "  dist\proxion-gateway.exe --state agent.json --passphrase yourpass"
