# Invoked by Windows Task Scheduler. Secrets remain in LocalAppData.
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
$homeDir = Join-Path $env:LOCALAPPDATA 'InvenTrack'
$keyPath = Join-Path $homeDir 'backup.key'
if (-not (Test-Path $keyPath)) { throw 'Backup key missing; run scripts\install_backup_task.ps1' }
$env:INVENTRACK_BACKUP_KEY = (Get-Content -Raw $keyPath).Trim()
$env:INVENTRACK_BACKUP_DIR = Join-Path $homeDir 'Backups'
$env:INVENTRACK_DB = Join-Path $project 'backend\inventory.sqlite3'
& py -m backend.backup create --keep 30
if ($LASTEXITCODE -ne 0) { throw 'Backup command failed' }
