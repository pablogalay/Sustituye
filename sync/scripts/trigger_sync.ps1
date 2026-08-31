# Triggers a sync run through the running app (docker compose up), the same path the
# admin's "Sincronizar EducaMadrid" button uses. Meant to be called by Task Scheduler
# for unattended runs; logs the outcome to data/trigger.log next to it.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$syncDir = Split-Path -Parent $scriptDir
$repoRoot = Split-Path -Parent $syncDir
$envPath = Join-Path $repoRoot ".env"

$envVars = @{}
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $envVars[$Matches[1]] = $Matches[2]
    }
}
$adminEmail = if ($envVars.ContainsKey('ADMIN_EMAIL')) { $envVars['ADMIN_EMAIL'] } else { 'admin@school.local' }
$adminPassword = if ($envVars.ContainsKey('ADMIN_PASSWORD')) { $envVars['ADMIN_PASSWORD'] } else { 'admin123' }
$apiUrl = "http://localhost:8000"

$dataDir = Join-Path $syncDir "data"
if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }
$logPath = Join-Path $dataDir "trigger.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

try {
    $login = Invoke-RestMethod -Uri "$apiUrl/auth/login" -Method Post -ContentType "application/json" `
        -Body (@{ email = $adminEmail; password = $adminPassword } | ConvertTo-Json)
    $headers = @{ Authorization = "Bearer $($login.access_token)" }
    $result = Invoke-RestMethod -Uri "$apiUrl/admin/sync-educamadrid" -Method Post -Headers $headers
    "$timestamp OK fetched=$($result.fetched) complete=$($result.complete) imported=$($result.imported) pending=$($result.pending)" |
        Add-Content -Path $logPath
} catch {
    "$timestamp ERROR $($_.Exception.Message)" | Add-Content -Path $logPath
    throw
}
