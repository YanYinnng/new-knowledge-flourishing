param(
    [int]$LookbackDays = 2,
    [string]$MinimumDate = "",
    [switch]$ForceGenerate,
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
$logPath = Join-Path $systemDir "catchup-daily-report.log"

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] $Message"
    New-Item -ItemType Directory -Force -Path $systemDir | Out-Null
    Add-Content -Path $logPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-MinimumDate {
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    if ($MinimumDate) {
        return [datetime]::ParseExact($MinimumDate, "yyyy-MM-dd", $culture).Date
    }

    $statePath = Join-Path $systemDir "catchup-installed-date.txt"
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        $raw = (Get-Content -Path $statePath -Encoding UTF8 -TotalCount 1).Trim()
        if ($raw) {
            return [datetime]::ParseExact($raw, "yyyy-MM-dd", $culture).Date
        }
    }

    return (Get-Date).Date
}

function Get-CandidateDates {
    $now = Get-Date
    $today = $now.Date
    $minimum = Get-MinimumDate
    $lookback = [Math]::Max(0, $LookbackDays)
    $start = $today.AddDays(-1 * $lookback)
    $dates = New-Object System.Collections.Generic.List[string]
    $date = $start

    while ($date -le $today) {
        if ($date -ge $minimum) {
            $isToday = ($date -eq $today)
            $isBeforeTonight = ($now.TimeOfDay -lt ([TimeSpan]::Parse("23:00:00")))
            if (-not ($isToday -and $isBeforeTonight)) {
                $dates.Add($date.ToString("yyyy-MM-dd"))
            }
        }
        $date = $date.AddDays(1)
    }

    return $dates
}

function Find-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw "python was not found in PATH."
}

function Invoke-ReportGenerate {
    param(
        [string]$DateText,
        [string]$PythonPath
    )

    $generator = Join-Path $root "scripts\generate-radar-report.py"
    $reportPath = Join-Path $root "synthesis\daily_reports\$DateText\report.pdf"
    Write-Log "Generating radar PDF for $DateText."

    if ($DryRun) {
        Write-Log "Dry run: would run $generator --date $DateText."
        return $true
    }

    $output = & $PythonPath $generator --date $DateText 2>&1
    if ($LASTEXITCODE -ne 0) {
        foreach ($line in $output) {
            Write-Log "generate-radar-report: $line"
        }
        Write-Log "Radar report generation failed for $DateText."
        return $false
    }
    foreach ($line in $output) {
        Write-Log "generate-radar-report: $line"
    }

    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        Write-Log "Generator finished but did not create $reportPath."
        return $false
    }

    Write-Log "Report exists: $reportPath."
    return $true
}

function Invoke-ReportSend {
    param(
        [string]$DateText,
        [string]$PythonPath
    )

    $sendScript = Join-Path $root "scripts\send-daily-report.py"
    Write-Log "Sending report for $DateText immediately."

    if ($DryRun) {
        Write-Log "Dry run: would call $sendScript --date $DateText."
        return $true
    }

    $output = & $PythonPath $sendScript --date $DateText 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Log "send-daily-report: $line"
    }

    return ($exitCode -eq 0)
}

$hadFailure = $false

try {
    Write-Log "Catch-up started. Root: $root"
    $dates = Get-CandidateDates
    if ($dates.Count -eq 0) {
        Write-Log "No eligible dates. Today's report is only caught up after 23:00."
        exit 0
    }

    $pythonPath = Find-Python

    foreach ($dateText in $dates) {
        $reportPath = Join-Path $root "synthesis\daily_reports\$dateText\report.pdf"
        $sentMarker = Join-Path $root "system\email_sent\$dateText.sent"

        if (Test-Path -LiteralPath $sentMarker -PathType Leaf) {
            Write-Log "Skipping $dateText because sent marker exists."
            continue
        }

        if ((-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) -or $ForceGenerate) {
            if (-not (Invoke-ReportGenerate -DateText $dateText -PythonPath $pythonPath)) {
                $hadFailure = $true
                continue
            }
        } else {
            Write-Log "Report already exists for $dateText; only email send is needed."
        }

        if (-not (Invoke-ReportSend -DateText $dateText -PythonPath $pythonPath)) {
            Write-Log "Email send failed for $dateText."
            $hadFailure = $true
        }
    }
} catch {
    Write-Log "Catch-up failed: $($_.Exception.Message)"
    exit 1
}

if ($hadFailure) {
    exit 1
}

Write-Log "Catch-up finished."
exit 0
