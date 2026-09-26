[CmdletBinding()]
param([switch]$Cleanup)

$testProfile = Join-Path $env:LOCALAPPDATA 'BursarCashbook-Test'
if ($Cleanup) {
    if (Test-Path $testProfile) { Remove-Item -LiteralPath $testProfile -Recurse -Force }
    Write-Host "Removed test profile: $testProfile"
    exit 0
}

$env:CASHBOOK_APP_DATA_DIR = $testProfile
Write-Host "Starting isolated test profile: $testProfile"
python -m app.launcher
