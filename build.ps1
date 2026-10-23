param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Name = 'CodexUsage'
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv and install requirements-dev.txt first. See README.md.'
}
New-Item -ItemType Directory -Force assets | Out-Null
& $python -c "from codex_usage.app import tray_image; tray_image().save('assets/codex-usage.ico', sizes=[(16,16),(32,32),(48,48),(64,64)])"
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name $Name --icon assets/codex-usage.ico --hidden-import pystray._win32 main.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Write-Output (Join-Path $PSScriptRoot "dist/$Name.exe")
