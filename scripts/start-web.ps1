$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$env:IDEA_SPROUT_HOST = "0.0.0.0"
if (-not $env:IDEA_SPROUT_PORT) {
    $env:IDEA_SPROUT_PORT = "3000"
}

Write-Host "Starting 点子发芽 web service for computer and phone access..."
Write-Host "Project root: $root"
Write-Host ""
Write-Host "Recommended phone links for the same Wi-Fi / LAN:"

$excludedAdapters = "VMware|Virtual|vEthernet|Loopback|Bluetooth"
$recommendedIps = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.AddressState -eq "Preferred" `
            -and $_.IPAddress -notlike "127.*" `
            -and $_.IPAddress -notlike "169.254.*" `
            -and $_.InterfaceAlias -notmatch $excludedAdapters
    } |
    Sort-Object InterfaceAlias, IPAddress

if ($recommendedIps) {
    foreach ($ip in $recommendedIps) {
        Write-Host ("  http://{0}:{1}  ({2})" -f $ip.IPAddress, $env:IDEA_SPROUT_PORT, $ip.InterfaceAlias)
    }
} else {
    Write-Host "  No physical Wi-Fi/Ethernet IPv4 address was detected."
    Write-Host "  Check Windows Settings > Network & internet for your IPv4 address."
}

Write-Host ""
Write-Host "VPN note: if your phone cannot open the link, enable LAN/local-network access in the VPN app or temporarily pause the VPN."
Write-Host "Windows Firewall may also ask whether Python can access Private networks; allow it for phone access."
Write-Host ""
python (Join-Path $root "app.py")
