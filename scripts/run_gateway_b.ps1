# Run a second Proxion gateway instance on separate ports for local federation testing.
# Gateway A runs on the default ports (7474 WS / 8080 HTTP) from run_gateway.py.
# Gateway B uses this script: 7475 WS / 8081 HTTP, separate data + identity.
#
# Usage (from repo root):
#   Terminal A: python run_gateway.py
#   Terminal B: .\scripts\run_gateway_b.ps1
#
# Then open http://localhost:8080 (Alice) and http://localhost:8081 (Bob),
# sign each into a different Solid pod account, add each other by Proxion address.

$env:PROXION_WS_PORT            = "7475"
$env:PROXION_HTTP_PORT          = "8081"
$env:PROXION_DATA_DIR           = "$env:USERPROFILE\.proxion_b"
$env:PROXION_PUBLIC_URL         = "ws://localhost:7475"
$env:PROXION_ALLOW_PRIVATE_RELAY = "1"

# If Gateway A also needs relay permission, set it before starting:
#   $env:PROXION_ALLOW_PRIVATE_RELAY = "1"; python run_gateway.py

Write-Host "Starting Gateway B on ws://localhost:7475 / http://localhost:8081"
Write-Host "Data dir: $env:PROXION_DATA_DIR"
python run_gateway.py
