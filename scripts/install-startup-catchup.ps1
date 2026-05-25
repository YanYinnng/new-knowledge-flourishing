param(
    [string]$TaskName = "IdeaSproutCatchUpDailyReport",
    [int]$LookbackDays = 2
)

$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$systemDir = Join-Path $root "system"
$catchupScript = Join-Path $root "scripts\catch-up-daily-report.ps1"
$statePath = Join-Path $systemDir "catchup-installed-date.txt"

if (-not (Test-Path -LiteralPath $catchupScript -PathType Leaf)) {
    throw "Missing catch-up script: $catchupScript"
}

New-Item -ItemType Directory -Force -Path $systemDir | Out-Null
Set-Content -Path $statePath -Value (Get-Date -Format "yyyy-MM-dd") -Encoding UTF8

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$catchupScript`" -LookbackDays $LookbackDays"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Generate and email missed Idea Sprout daily reports at next Windows logon." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Catch-up install date: $(Get-Content -Path $statePath -Encoding UTF8 -TotalCount 1)"
Write-Host "It will run at Windows logon and send eligible missed reports immediately."
