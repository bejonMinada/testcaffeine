param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$PythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

if ($Clean) {
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r requirements-dev.txt
& $PythonExe -m PyInstaller --noconfirm --clean TestCaffeine.spec

Write-Host "Build completed: dist/TestCaffeine.exe"
