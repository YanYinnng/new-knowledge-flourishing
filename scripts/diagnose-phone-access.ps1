$ErrorActionPreference = "Continue"

$port = 3000
if ($env:IDEA_SPROUT_PORT) {
    $port = [int]$env:IDEA_SPROUT_PORT
}

Write-Host "== Idea Sprout phone access diagnostics =="
Write-Host ""

Write-Host "1. Web server listener"
$listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -eq 0) {
    Write-Host "WARN: No listener found on port $port. Start with .\scripts\start-web.ps1 first."
}
if ($listeners.Count -gt 0) {
    $listeners | Select-Object LocalAddress, LocalPort, State, OwningProcess | Format-Table -AutoSize
    $listenAll = $false
    foreach ($listener in $listeners) {
        if ($listener.LocalAddress -eq "0.0.0.0") {
            $listenAll = $true
        }
    }
    if ($listenAll) {
        Write-Host "OK: Server listens on all IPv4 interfaces."
    }
    if (-not $listenAll) {
        Write-Host "WARN: Server is not listening on 0.0.0.0. Start with .\scripts\start-web.ps1."
    }
}

Write-Host ""
Write-Host "2. Recommended phone links"
$excludedAdapters = "VMware|Virtual|vEthernet|Loopback|Bluetooth"
$ips = @(
    Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            ($_.AddressState -eq "Preferred") -and
            ($_.IPAddress -notlike "127.*") -and
            ($_.IPAddress -notlike "169.254.*") -and
            ($_.InterfaceAlias -notmatch $excludedAdapters)
        } |
        Sort-Object InterfaceAlias, IPAddress
)

if ($ips.Count -eq 0) {
    Write-Host "WARN: No physical Wi-Fi/Ethernet IPv4 address detected."
}
if ($ips.Count -gt 0) {
    foreach ($ip in $ips) {
        Write-Host ("  http://{0}:{1}  ({2})" -f $ip.IPAddress, $port, $ip.InterfaceAlias)
    }
}

Write-Host ""
Write-Host "3. Wi-Fi and network profile"
Get-NetConnectionProfile | Select-Object Name, InterfaceAlias, NetworkCategory, IPv4Connectivity | Format-Table -AutoSize
try {
    netsh wlan show interfaces
} catch {
    Write-Host "Could not read WLAN info: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "4. Python / port firewall hints"
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    Write-Host "WARN: python command not found."
}
if ($pythonCommand) {
    Write-Host "Python: $($pythonCommand.Source)"
    $rules = @(
        Get-NetFirewallRule -Enabled True -Direction Inbound -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match "Python|Idea Sprout" }
    )
    if ($rules.Count -eq 0) {
        Write-Host "WARN: No Python or Idea Sprout inbound allow rule found. Run .\scripts\allow-phone-firewall.ps1 from an elevated PowerShell if firewall is suspected."
    }
    if ($rules.Count -gt 0) {
        foreach ($rule in $rules) {
            Write-Host "---"
            $rule | Select-Object DisplayName, Action, Profile, Enabled | Format-List
            Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue |
                Select-Object Program |
                Format-List
            Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue |
                Select-Object Protocol, LocalPort |
                Format-List
        }
    }
}

Write-Host ""
Write-Host "5. Local self-test"
$testIps = @("127.0.0.1")
foreach ($ip in $ips) {
    $testIps += $ip.IPAddress
}
foreach ($ip in $testIps) {
    $url = "http://$ip`:$port/api/auth/status"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        Write-Host "OK: $url -> $($response.StatusCode)"
    } catch {
        Write-Host "FAIL: $url -> $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "Interpretation:"
Write-Host "- If local self-test is OK but phone cannot open it and the server log shows no phone request, suspect firewall, VPN, or Wi-Fi client isolation."
Write-Host "- Campus/company WPA2-Enterprise Wi-Fi often blocks device-to-device access. Use a phone hotspot or Windows mobile hotspot, then rerun .\scripts\start-web.ps1."
Write-Host "- If firewall is the issue, run .\scripts\allow-phone-firewall.ps1 from an elevated PowerShell."
