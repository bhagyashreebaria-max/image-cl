$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (!(Test-Path ".venv\Scripts\python.exe")) {
    py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 or newer must be installed." }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check the error above." }
Write-Host "Open http://127.0.0.1:8000 in your browser. Keep this window open."
& .\.venv\Scripts\python.exe -m app
