# Run daily at 8:00 AM HKT via Windows Task Scheduler
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = "python"
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
}

& $Python main.py
