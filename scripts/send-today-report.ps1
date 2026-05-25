param(
    [int]$WaitForReportMinutes = 30,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$systemDir = Join-Path $root "system"
$logPath = Join-Path $systemDir "nightly-mailer.log"

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] $Message"
    New-Item -ItemType Directory -Force -Path $systemDir | Out-Null
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Find-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw "python was not found in PATH."
}

$dateText = Get-Date -Format "yyyy-MM-dd"
$reportPath = Join-Path $root "synthesis\daily_reports\$dateText.md"
$sendScript = Join-Path $root "scripts\send-daily-report.py"
$deadline = (Get-Date).AddMinutes([Math]::Max(0, $WaitForReportMinutes))

try {
    Write-Log "Nightly mailer started for $dateText."

    while (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        if ((Get-Date) -ge $deadline) {
            throw "Report not found before timeout: $reportPath"
        }
        Write-Log "Report not ready yet: $reportPath"
        Start-Sleep -Seconds 60
    }

    if ($DryRun) {
        Write-Log "Dry run: would send $reportPath."
        exit 0
    }

    $pythonPath = Find-Python
    $output = & $pythonPath $sendScript --date $dateText 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Log "send-daily-report: $line"
    }

    if ($exitCode -ne 0) {
        throw "send-daily-report.py failed with exit code $exitCode."
    }

    Write-Log "Nightly mailer finished for $dateText."
    exit 0
} catch {
    Write-Log "Nightly mailer failed: $($_.Exception.Message)"
    exit 1
}
