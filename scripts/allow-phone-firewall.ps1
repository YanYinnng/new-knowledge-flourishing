$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    Write-Error "Run this script from an elevated PowerShell window."
    exit 1
}

if ($env:IDEA_SPROUT_PORT) {
    $port = [int]$env:IDEA_SPROUT_PORT
} else {
    $port = 3000
}

$ruleName = "Idea Sprout Local Web $port"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Firewall rule already exists: $ruleName"
    $existing | Select-Object DisplayName, Enabled, Action, Profile | Format-Table -AutoSize
    exit 0
}

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $port `
    -Profile Private,Public | Out-Null

Write-Host "Created firewall rule: $ruleName"
Write-Host "Allowed inbound TCP port $port for Private and Public network profiles."
