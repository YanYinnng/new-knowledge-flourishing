param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}

$root = Split-Path -Parent $scriptDir
$reportDir = Join-Path $root "synthesis\daily_reports\$Date"
$texPath = Join-Path $reportDir "report.tex"
$configPath = Join-Path $root "system\report_config.json"

if (-not (Test-Path -LiteralPath $texPath -PathType Leaf)) {
    throw "Missing report.tex: $texPath"
}

$engine = "xelatex"
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $config = Get-Content -Path $configPath -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($config.latex_engine) {
        $engine = [string]$config.latex_engine
    }
}

$latexmk = Get-Command latexmk -ErrorAction SilentlyContinue
if ($latexmk) {
    $engineArg = if ($engine -eq "lualatex") { "-lualatex" } else { "-xelatex" }
    $cmd = $latexmk.Source
    $args = @($engineArg, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "report.tex")
} else {
    $engineCmd = Get-Command $engine -ErrorAction SilentlyContinue
    if (-not $engineCmd) {
        throw "Missing LaTeX engine: $engine. Install TeX Live or MiKTeX, or update system/report_config.json."
    }
    $cmd = $engineCmd.Source
    $args = @("-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "report.tex")
}

Push-Location $reportDir
try {
    if ($Clean -and $latexmk) {
        & $latexmk.Source -C | Out-Null
    }
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $cmd @args 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldErrorActionPreference
    $output | Set-Content -Path (Join-Path $reportDir "compile.out.log") -Encoding UTF8
    if ($exitCode -ne 0) {
        $tail = ($output | Select-Object -Last 30) -join "`n"
        throw "LaTeX compile failed. report.tex was kept. Log tail:`n$tail"
    }
} finally {
    Pop-Location
}

$pdfPath = Join-Path $reportDir "report.pdf"
if (-not (Test-Path -LiteralPath $pdfPath -PathType Leaf)) {
    throw "LaTeX command finished but report.pdf was not created: $pdfPath"
}

Write-Host "PDF generated: $pdfPath"
