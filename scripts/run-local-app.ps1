# DEV-ONLY: This script is intended for local development and rehearsal.
# Do not use it as a production deployment method.

param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8011,
    [switch]$Restart,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path (Split-Path -Parent $repoRoot) ".venv/Scripts/python.exe"

if (Test-Path $venvPython) {
    $pythonCmd = $venvPython
}
else {
    $pythonCmd = "python"
}

if ($Restart) {
    $listener = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        Stop-Process -Id $listener.OwningProcess -Force
    }
}

$uvicornArgs = @("-m", "uvicorn", "main:app", "--host", $BindHost, "--port", $Port)
if ($Reload) {
    $uvicornArgs += "--reload"
}

Push-Location $repoRoot
try {
    & $pythonCmd @uvicornArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}