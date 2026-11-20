param(
    [string]$TaskName = "IdeaSproutGenerateDailyReport",
    [string]$At = "22:50"
)

$ErrorActionPreference = "Stop"

$clock = [datetime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
if ($clock.TimeOfDay -lt [TimeSpan]::Parse("22:45") -or $clock.TimeOfDay -gt [TimeSpan]::Parse("23:25")) {
    throw "The nightly DeepSeek task must start between 22:45 and 23:25 local time."
}

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$runner = Join-Path $root "scripts\run-nightly-deepseek-report.py"

if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Missing DeepSeek runner: $runner"
}

$command = "& { Set-Location -LiteralPath '$root'; python '$runner' --enforce-window }"
$arguments = "-NoProfile -ExecutionPolicy Bypass -Command `"$command`""
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
    -Description "Generate the Idea Sprout daily PDF report through the local DeepSeek API runner at $At local time." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "It generates today's report through DeepSeek at $At local time."
