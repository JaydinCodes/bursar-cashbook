[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$BuildInstaller
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python is required on the build machine.'
}

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'Install build dependency first: python -m pip install -r requirements-build.txt'
}

if (-not $SkipTests) {
    python -m unittest discover -s tests -v

    if ($LASTEXITCODE -ne 0) {
        throw 'Tests failed.'
    }
}

python -m PyInstaller --noconfirm --clean BursarCashbook.spec

if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller build failed.'
}

New-Item -ItemType Directory -Force -Path release | Out-Null
$version = python -c "from app.version import APP_VERSION; print(APP_VERSION)"
Write-Host "Release version: $version"

$possibleIsccPaths = @(
    'C:\Program Files\Inno Setup 7\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 7\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)

$isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue

if ($isccCommand) {
    $isccPath = $isccCommand.Source
} else {
    $isccPath = $possibleIsccPaths |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
}

if ($BuildInstaller -or $isccPath) {
    if (-not $isccPath) {
        throw 'Inno Setup compiler (ISCC.exe) was requested but was not found.'
    }

    Write-Host "Using Inno Setup compiler: $isccPath"

    & $isccPath 'installer\BursarCashbook.iss'

    if ($LASTEXITCODE -ne 0) {
        throw 'Inno Setup build failed.'
    }

    Write-Host ''
    Write-Host 'Build complete.'
    Write-Host "Executable: $root\dist\BursarCashbook.exe"
    $installer = Get-ChildItem "$root\release\BursarCashbook-$version-Setup.exe" | Select-Object -First 1
    Write-Host "Installer:  $($installer.FullName)"
    Write-Host "SHA-256:   $((Get-FileHash $installer.FullName -Algorithm SHA256).Hash)"
} else {
    Write-Host ''
    Write-Host 'Executable built successfully.'
    Write-Host "Executable: $root\dist\BursarCashbook.exe"
    Write-Host ''
    Write-Host 'Install Inno Setup 6/7 or run with -BuildInstaller to produce the installer.'
}
