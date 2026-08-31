$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$syncDir = Split-Path -Parent $scriptDir
Set-Location $syncDir

$venvActivate = Join-Path $syncDir ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
    Write-Error "Virtual environment not found at $venvActivate. Run: python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -r requirements.txt; playwright install chromium"
    exit 1
}
& $venvActivate

python -m educamadrid_sync.run
exit $LASTEXITCODE
