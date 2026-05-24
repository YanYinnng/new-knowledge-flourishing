$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$missing = New-Object System.Collections.Generic.List[string]

$requiredDirs = @(
    "inbox",
    "library",
    "library\nodes",
    "library\sources",
    "library\seeds",
    "reports",
    "reports\daily",
    "tracking",
    "templates",
    "automation",
    "scripts"
)

$requiredFiles = @(
    "PLAN.md",
    "README.md",
    "tracking\topics.md",
    "templates\daily-input.md",
    "templates\knowledge-node.md",
    "templates\source.md",
    "templates\idea-seed.md",
    "templates\daily-report.md",
    "automation\nightly-codex-prompt.md",
    "scripts\new-today.ps1",
    "scripts\validate-structure.ps1"
)

foreach ($dir in $requiredDirs) {
    $path = Join-Path $root $dir
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        $missing.Add("Directory: $dir")
    }
}

foreach ($file in $requiredFiles) {
    $path = Join-Path $root $file
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $missing.Add("File: $file")
    }
}

if ($missing.Count -gt 0) {
    Write-Host "Structure check failed. Missing items:"
    foreach ($item in $missing) {
        Write-Host "- $item"
    }
    exit 1
}

Write-Host "Structure check passed. All required MVP directories and files exist."
