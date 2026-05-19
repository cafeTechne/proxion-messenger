# Proxion Caffeine Script
# ======================
# This script simulates a harmless keypress (F15) every 60 seconds
# to prevent Windows from detecting an idle state and locking/sleeping.
# 
# Run this in a dedicated terminal window and keep it open.

Add-Type -AssemblyName System.Windows.Forms

Write-Host "--- Caffeine Mode: ACTIVE ---" -ForegroundColor Green
Write-Host "Simulating F15 keypress every 60 seconds to prevent sleep/lock." -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow

$wsh = New-Object -ComObject WScript.Shell

while ($true) {
    # Send F15 key (harmless, no-op in 99.9% of apps)
    $wsh.SendKeys('{F15}')
    $timestamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$timestamp] Prevented idle timeout." -ForegroundColor DarkGray
    Start-Sleep -Seconds 60
}
