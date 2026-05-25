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

function Test-CodexCli {
    param([string]$Path)
    try {
        & $Path --version *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-CodexCli {
    $candidates = New-Object System.Collections.Generic.List[string]
    $localBin = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"

    if (Test-Path -LiteralPath $localBin -PathType Container) {
        Get-ChildItem -Path $localBin -Recurse -Filter codex.exe -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    $command = Get-Command codex.exe -ErrorAction SilentlyContinue
    if ($command) {
        $candidates.Add($command.Source)
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-CodexCli -Path $candidate) {
            return $candidate
        }
    }

    throw "A usable codex.exe was not found."
}

function Invoke-CodexGenerate {
    param(
        [string]$DateText,
        [string]$CodexPath
    )

    $promptTemplatePath = Join-Path $root "automation\catch-up-codex-prompt.md"
    $reportPath = Join-Path $root "synthesis\daily_reports\$DateText.md"
    $codexLogPath = Join-Path $systemDir "catchup-codex-$DateText.log"

    if (-not (Test-Path -LiteralPath $promptTemplatePath -PathType Leaf)) {
        throw "Missing prompt template: $promptTemplatePath"
    }

    $prompt = (Get-Content -Path $promptTemplatePath -Encoding UTF8 -Raw).Replace("{{DATE}}", $DateText)
    Write-Log "Generating missing report for $DateText with Codex CLI."

    if ($DryRun) {
        Write-Log "Dry run: would run Codex and write $reportPath."
        return $true
    }

    $prompt | & $CodexPath exec --cd $root --sandbox danger-full-access --ask-for-approval never - *> $codexLogPath
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Codex generation failed for $DateText. See $codexLogPath."
        return $false
    }

    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        Write-Log "Codex finished but did not create $reportPath."
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
    $codexPath = $null

    foreach ($dateText in $dates) {
        $reportPath = Join-Path $root "synthesis\daily_reports\$dateText.md"
        $sentMarker = Join-Path $root "system\email_sent\$dateText.sent"

        if (Test-Path -LiteralPath $sentMarker -PathType Leaf) {
            Write-Log "Skipping $dateText because sent marker exists."
            continue
        }

        if ((-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) -or $ForceGenerate) {
            if (-not $codexPath) {
                $codexPath = Find-CodexCli
                Write-Log "Using Codex CLI: $codexPath"
            }
            if (-not (Invoke-CodexGenerate -DateText $dateText -CodexPath $codexPath)) {
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
