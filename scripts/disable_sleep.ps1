# Proxion Keep-Awake Script
# ========================
# Run this from an Administrative PowerShell terminal to disable 
# all sleep, hibernation, and lock screen behaviors.

$ErrorActionPreference = "Stop"

# Check for Administrator privileges
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script MUST be run as an Administrator."
}

Write-Host "--- Disabling Sleep and Hibernation (PowerCFG) ---" -ForegroundColor Cyan

# Disable hibernation entirely (removes hiberfil.sys and prevents hibernate sleep states)
powercfg /hibernate off

# Set all timeouts to 0 (Never) for both AC (Plugged In) and DC (Battery)
# -standby-timeout: System sleep
# -hibernate-timeout: Hibernation transition
# -monitor-timeout: Screen turn off
# -disk-timeout: Hard disk spin down

powercfg /x -standby-timeout-ac 0
powercfg /x -standby-timeout-dc 0
powercfg /x -hibernate-timeout-ac 0
powercfg /x -hibernate-timeout-dc 0
powercfg /x -monitor-timeout-ac 0
powercfg /x -monitor-timeout-dc 0
powercfg /x -disk-timeout-ac 0
powercfg /x -disk-timeout-dc 0

Write-Host "--- Disabling Lock Screen and Screen Saver (Registry) ---" -ForegroundColor Cyan

# Disable Lock Screen (GPO Policy override)
$RegistryPath = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization"
if (-not (Test-Path $RegistryPath)) {
    New-Item -Path $RegistryPath -Force | Out-Null
}
Set-ItemProperty -Path $RegistryPath -Name "NoLockScreen" -Value 1

# Disable Screen Saver and "On Resume, display logon screen"
$DesktopPath = "HKCU:\Control Panel\Desktop"
Set-ItemProperty -Path $DesktopPath -Name "ScreenSaveActive" -Value 0
Set-ItemProperty -Path $DesktopPath -Name "ScreenSaverIsSecure" -Value 0
Set-ItemProperty -Path $DesktopPath -Name "ScreenSaveTimeOut" -Value 0

Write-Host "--- System is now configured to STAY AWAKE ---" -ForegroundColor Green
Write-Host "Note: Some corporate Group Policies may override these settings." -ForegroundColor Yellow
Write-Host "If the computer still locks, check your 'Screen saver settings' in the control panel manually." -ForegroundColor Gray
