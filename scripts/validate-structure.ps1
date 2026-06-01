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
    "scripts",
    "system\email_sent",
    "system\locks",
    "system",
    "memory",
    "tasks",
    "review_queue",
    "knowledge",
    "synthesis",
    "synthesis\daily_reports",
    "synthesis\idea_seeds"
)

$requiredFiles = @(
    "PLAN.md",
    "README.md",
    "CODEX_HANDOFF.md",
    "tracking\README.md",
    "tracking\topics.md",
    "templates\README.md",
    "templates\daily-input.md",
    "templates\knowledge-node.md",
    "templates\idea-seed.md",
    "templates\report-brief.json",
    "automation\catch-up-codex-prompt.md",
    "automation\nightly-codex-prompt.md",
    "config\email_auth.example.json",
    "scripts\allow-phone-firewall.ps1",
    "scripts\catch-up-daily-report.ps1",
    "scripts\compile-radar-report.ps1",
    "scripts\context_builder.py",
    "scripts\diagnose-phone-access.ps1",
    "scripts\generate-radar-report.py",
    "scripts\install-startup-catchup.ps1",
    "scripts\install-nightly-mailer.ps1",
    "scripts\new-today.ps1",
    "scripts\send-daily-report.py",
    "scripts\send-today-report.ps1",
    "scripts\start-web.ps1",
    "scripts\sync-knowledge-from-report.py",
    "scripts\uninstall-nightly-mailer.ps1",
    "scripts\uninstall-startup-catchup.ps1",
    "scripts\validate-structure.ps1",
    "system\report_quality_rules.md",
    "system\report_config.json",
    "system\architecture.md",
    "memory\profile.md",
    "memory\themes.md",
    "memory\preferences.jsonl",
    "tasks\tasks.jsonl"
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
