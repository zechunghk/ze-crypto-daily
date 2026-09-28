# Manual RSI Telegram alert. Cron equivalent: railway.rsi.toml
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = "python"
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
}

& $Python rsi_alert.py --mode both
