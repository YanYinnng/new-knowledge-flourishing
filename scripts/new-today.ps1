$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$today = Get-Date -Format "yyyy-MM-dd"
$inboxDir = Join-Path $root "inbox"
$templatePath = Join-Path $root "templates\daily-input.md"
$targetPath = Join-Path $inboxDir "$today.md"

if (-not (Test-Path -LiteralPath $templatePath)) {
    Write-Error "Missing template: $templatePath"
    exit 1
}

if (-not (Test-Path -LiteralPath $inboxDir)) {
    New-Item -ItemType Directory -Path $inboxDir -Force | Out-Null
}

if (Test-Path -LiteralPath $targetPath) {
    Write-Host "Today's input already exists:"
    Write-Host $targetPath
    exit 0
}

$content = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8
$content = $content -replace "YYYY-MM-DD", $today
Set-Content -LiteralPath $targetPath -Value $content -Encoding UTF8

Write-Host "Created today's input:"
Write-Host $targetPath
