param(
    [string]$TaskName = "IdeaSproutSendDailyReport",
    [string]$At = "23:00",
    [int]$WaitForReportMinutes = 30
)

$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$sendScript = Join-Path $root "scripts\send-today-report.ps1"

if (-not (Test-Path -LiteralPath $sendScript -PathType Leaf)) {
    throw "Missing send script: $sendScript"
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$sendScript`" -WaitForReportMinutes $WaitForReportMinutes"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Send the Idea Sprout daily PDF radar report by email at 23:00 local time." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "It sends today's existing report.pdf at $At and waits up to $WaitForReportMinutes minutes for the file."
